#!/usr/bin/env bash
# Manual fallback for deploying the current commit over SSH (GETTING_STARTED.md §8.1). Normally
# a merge to main deploys itself, through .github/workflows/ci.yml.
#
#   deploy/deploy.sh 13.58.35.224          (HOST=other.name overrides the host name)
#
# Only committed code is sent: the working tree must be clean. A restart clears in-memory bundles
# and test sessions, so don't deploy while another team is testing us.
set -euo pipefail

IP="${1:?usage: deploy/deploy.sh <elastic-ip>}"
KEY_FILE="${KEY_FILE:-$HOME/.ssh/team22-ec2}"
REMOTE=ubuntu@$IP
# sslip.io resolves any name ending in <ip-with-dashes>.sslip.io to that IP; "team22." is our label
HOST="${HOST:-team22.${IP//./-}.sslip.io}"
SSH=(ssh -i "$KEY_FILE" -o StrictHostKeyChecking=accept-new)

cd "$(git rev-parse --show-toplevel)"
if [[ -n "$(git status --porcelain)" ]]; then
  echo "Working tree has uncommitted changes; commit or stash them first." >&2
  exit 1
fi

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
VERSION="$(deploy/build_release.sh "$STAGE/release.tar.gz")"
scp -i "$KEY_FILE" -q "$STAGE/release.tar.gz" deploy/remote_deploy.sh "$REMOTE:/tmp/"
"${SSH[@]}" "$REMOTE" "sudo HOST=$HOST bash /tmp/remote_deploy.sh /tmp/release.tar.gz; status=\$?; rm -f /tmp/release.tar.gz /tmp/remote_deploy.sh; exit \$status"

if ! python3 deploy/smoke_test.py "https://$HOST/system" "$VERSION"; then
  "${SSH[@]}" "$REMOTE" "sudo bash /home/ubuntu/cra/remote_deploy.sh rollback"
  exit 1
fi
echo "Deployed $VERSION to https://$HOST"
