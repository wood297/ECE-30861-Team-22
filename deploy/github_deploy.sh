#!/usr/bin/env bash
# Deploys the current commit through S3 and SSM. GitHub Actions runs this as the team22-cra-deploy
# user. It works from a laptop too, with `aws login`.
#
#   deploy/github_deploy.sh deploy     prints the deployed version to $GITHUB_OUTPUT when set
#   deploy/github_deploy.sh rollback
#   deploy/github_deploy.sh check      tests the credentials in use, and changes nothing on the app
set -euo pipefail

REGION="${REGION:-us-east-2}"
INSTANCE_ID="${INSTANCE_ID:-i-09eef96d3f2df685a}"
BUCKET="${BUCKET:-team22-cra-deploy-685253389272}"
HOST="${HOST:-team22.13-58-35-224.sslip.io}"
ROOT=/home/ubuntu/cra

# Sends the shell lines in $@ to the server, waits for them, and prints their output
run_on_server() {
  local parameters command_id status=Pending
  parameters="$(python3 -c 'import json, sys; print(json.dumps({"commands": sys.argv[1:], "executionTimeout": ["600"]}))' "$@")"
  command_id="$(aws --region "$REGION" ssm send-command --instance-ids "$INSTANCE_ID" \
    --document-name AWS-RunShellScript --comment "team22 deploy" --timeout-seconds 120 \
    --parameters "$parameters" --query Command.CommandId --output text)"
  for _ in $(seq 1 130); do
    sleep 5
    status="$(aws --region "$REGION" ssm get-command-invocation --command-id "$command_id" \
      --instance-id "$INSTANCE_ID" --query Status --output text 2>/dev/null || echo Pending)"
    case "$status" in
      Pending|InProgress|Delayed) ;;
      *) break ;;
    esac
  done
  aws --region "$REGION" ssm get-command-invocation --command-id "$command_id" --instance-id "$INSTANCE_ID" \
    --query '[StandardOutputContent,StandardErrorContent]' --output text
  echo "Server command finished: $status"
  [[ "$status" == Success ]]
}

case "${1:-}" in
  deploy)
    cd "$(git rev-parse --show-toplevel)"
    TARBALL="$(mktemp)"
    trap 'rm -f "$TARBALL"' EXIT
    VERSION="$(deploy/build_release.sh "$TARBALL" "${GITHUB_REF_NAME:-}")"
    KEY="releases/$(git rev-parse HEAD).tar.gz"
    aws --region "$REGION" s3 cp --only-show-errors "$TARBALL" "s3://$BUCKET/$KEY"
    # The server has no AWS CLI, so it gets a link that stops working after 15 minutes
    URL="$(aws --region "$REGION" s3 presign "s3://$BUCKET/$KEY" --expires-in 900)"
    run_on_server \
      'set -e' \
      'WORK="$(mktemp -d)"' \
      'trap "rm -rf \"$WORK\"" EXIT' \
      "curl -fsS --retry 3 -o \"\$WORK/release.tar.gz\" '$URL'" \
      'tar -xzOf "$WORK/release.tar.gz" ./deploy/remote_deploy.sh > "$WORK/remote_deploy.sh"' \
      "HOST='$HOST' bash \"\$WORK/remote_deploy.sh\" \"\$WORK/release.tar.gz\""
    echo "Deployed $VERSION"
    if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
      echo "version=$VERSION" >> "$GITHUB_OUTPUT"
    fi
    ;;
  rollback)
    run_on_server "bash $ROOT/remote_deploy.sh rollback"
    ;;
  check)
    PROBE="$(mktemp)"
    trap 'rm -f "$PROBE"' EXIT
    echo "Using $(aws --region "$REGION" sts get-caller-identity --query Arn --output text)"
    aws --region "$REGION" s3 cp --only-show-errors "$PROBE" "s3://$BUCKET/releases/access-check"
    echo "S3 upload works"
    run_on_server 'echo "SSM works on $(hostname)"'
    ;;
  *)
    echo "usage: deploy/github_deploy.sh deploy|rollback|check" >&2
    exit 2
    ;;
esac
