#!/usr/bin/env bash
# Store a test_account.json connection's token in SSM, where the server reads it
# (docs/PHASE5_SYSTEM_GUIDE.md §6). Run from the repository root on a laptop signed in to AWS
# (AWS CLI v2, `aws login`), with the same account the deploy scripts use.
#
#   deploy/put_connection_token.sh status        which tokens are stored (never prints a value)
#   deploy/put_connection_token.sh jenkins       asks for the token without echoing it, then stores it
#   deploy/put_connection_token.sh github
#   deploy/put_connection_token.sh confluence
#   deploy/put_connection_token.sh grant-read    only if the server gets AccessDenied reading it:
#                                                lets the instance role read <prefix>/orgs/* and no more
#
# The parameter is <prefix>/orgs/<org_id>/conn/<connection id>, both ids taken from
# test_account.json. The token goes through a private temporary file, never a command line, so
# it isn't in the process list or shell history. Storing it again replaces it (rotation).
set -euo pipefail

REGION="${REGION:-us-east-2}"
PREFIX="${PREFIX:-/team22-cra}"
ROLE="${ROLE:-team22-cra-instance}"
ACCOUNT_FILE="${ACCOUNT_FILE:-test_account.json}"
TYPES=(jenkins github confluence)

aws() { command aws --region "$REGION" --output text "$@"; }

# The SSM parameter name for the account's one connection of type $1
parameter() {
  python3 - "$ACCOUNT_FILE" "$1" "$PREFIX" <<'EOF'
import json, sys
path, kind, prefix = sys.argv[1:]
account = json.load(open(path, encoding="utf-8"))
ids = [conn_id for conn_id, spec in account["connections"].items() if spec.get("type") == kind]
if len(ids) != 1:
    sys.exit(f"{path} should have exactly one {kind} connection, found {len(ids)}")
print(f"{prefix}/orgs/{account['org_id']}/conn/{ids[0]}")
EOF
}

case "${1:-}" in
  status)
    for kind in "${TYPES[@]}"; do
      name="$(parameter "$kind")"
      if changed="$(aws ssm get-parameter --name "$name" --query Parameter.LastModifiedDate 2>/dev/null)"; then
        printf '%-11s stored (last changed %s)  %s\n' "$kind" "$changed" "$name"
      else
        printf '%-11s NOT stored                    %s\n' "$kind" "$name"
      fi
    done
    ;;

  jenkins|github|confluence)
    name="$(parameter "$1")"
    read -r -s -p "$1 token (input hidden): " token
    echo
    [[ -n "$token" ]] || { echo "no token given; nothing stored" >&2; exit 1; }
    [[ "$token" =~ ^[[:graph:]]+$ ]] || { echo "the token has spaces or control characters; nothing stored" >&2; exit 1; }
    dir="$(mktemp -d)"
    chmod 700 "$dir"
    trap 'rm -rf "$dir"' EXIT
    # printf is a shell builtin, so the token never appears in a process's arguments
    ( umask 077
      printf '%s' "$token" | python3 -c '
import json, sys
print(json.dumps({"Name": sys.argv[1], "Type": "SecureString", "Tier": "Standard",
                  "Overwrite": True, "Value": sys.stdin.read()}))' "$name" >"$dir/parameter.json" )
    unset token
    aws ssm put-parameter --cli-input-json "file://$dir/parameter.json" >/dev/null
    echo "stored $1 token at $name"
    echo "The server reads it on the next bundle; no restart needed."
    ;;

  grant-read)
    account_id="$(aws sts get-caller-identity --query Account)"
    aws iam put-role-policy --role-name "$ROLE" --policy-name cra-connection-tokens --policy-document "{
      \"Version\": \"2012-10-17\",
      \"Statement\": [{\"Effect\": \"Allow\", \"Action\": \"ssm:GetParameter\",
        \"Resource\": \"arn:aws:ssm:$REGION:$account_id:parameter$PREFIX/orgs/*\"}]
    }"
    echo "$ROLE may now read $PREFIX/orgs/* (policy cra-connection-tokens)"
    ;;

  *)
    sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
