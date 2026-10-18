#!/usr/bin/env bash
# Deploy the current commit to the EC2 server (GETTING_STARTED.md §8.8).
#
#   deploy/deploy.sh 13.58.35.224
#
# Only committed code is sent: the working tree must be clean, and the files come from
# `git archive HEAD`. The commit is written to VERSION on the server. Tests run there before the
# restart, and a restart clears in-memory bundles and test sessions, so don't deploy while
# another team is testing us.
set -euo pipefail

IP="${1:?usage: deploy/deploy.sh <elastic-ip>}"
KEY_FILE="${KEY_FILE:-$HOME/.ssh/team22-ec2}"
REMOTE=ubuntu@$IP
APP_DIR=/home/ubuntu/ECE-30861-Team-22
HOST="${IP//./-}.sslip.io"
SSH=(ssh -i "$KEY_FILE" -o StrictHostKeyChecking=accept-new)

cd "$(git rev-parse --show-toplevel)"
if [[ -n "$(git status --porcelain)" ]]; then
  echo "Working tree has uncommitted changes; commit or stash them first." >&2
  exit 1
fi
VERSION="$(git rev-parse --abbrev-ref HEAD)@$(git rev-parse --short HEAD)"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
git archive HEAD | tar -x -C "$STAGE"
echo "$VERSION" > "$STAGE/VERSION"

"${SSH[@]}" "$REMOTE" "mkdir -p $APP_DIR"
rsync -az --delete --exclude .venv -e "${SSH[*]}" "$STAGE/" "$REMOTE:$APP_DIR/"

"${SSH[@]}" "$REMOTE" bash -s <<EOF
set -euo pipefail
cd $APP_DIR
sudo HOST=$HOST bash deploy/server_setup.sh
[[ -d .venv ]] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
.venv/bin/pytest -q
sudo systemctl restart cra
sleep 2
curl -fsS http://127.0.0.1:8000/ && echo
EOF

echo "Deployed $VERSION to https://$HOST"
