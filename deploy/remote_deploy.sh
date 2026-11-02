#!/usr/bin/env bash
# Runs on the server as root, from GitHub Actions through SSM or from deploy.sh over SSH.
#
#   HOST=team22.13-58-35-224.sslip.io bash remote_deploy.sh <release.tar.gz>
#   bash remote_deploy.sh rollback
#
# Each release gets its own folder and virtual environment, and `current` points at the live one.
# Nothing the live app uses changes until the new release is fully installed, and switching back
# is just moving the symlink. A restart clears in-memory bundles and test sessions.
set -euo pipefail

ROOT=/home/ubuntu/cra
RELEASES=$ROOT/releases
# Where deploy.sh used to install; the first deploy keeps it as the release to roll back to
LEGACY_DIR=/home/ubuntu/ECE-30861-Team-22
APP_USER=ubuntu
LOCAL_URL=http://127.0.0.1:8000/system
KEEP_RELEASES=3

switch_to() {
  # Rename instead of overwriting, so `current` always points somewhere
  ln -sfn "$1" "$ROOT/current.new"
  mv -Tf "$ROOT/current.new" "$ROOT/current"
}

wait_for_app() {
  local url=$1 expected=$2
  for _ in $(seq 1 30); do
    if curl -fsS --max-time 3 "$url" 2>/dev/null | grep -qF "$expected"; then
      return 0
    fi
    sleep 2
  done
  return 1
}

rollback() {
  local target
  target="$(readlink "$ROOT/previous" || true)"
  if [[ -z "$target" || ! -d "$target" ]]; then
    echo "No previous release to roll back to." >&2
    return 1
  fi
  echo "Rolling back to $target"
  switch_to "$target"
  rm -f "$ROOT/previous"
  systemctl restart cra
  # The legacy release has no /version, so only health can be checked here
  if ! wait_for_app "$LOCAL_URL/health" '"ok"'; then
    echo "Rollback failed: the previous release isn't answering." >&2
    journalctl -u cra -n 30 --no-pager >&2 || true
    return 1
  fi
  echo "Rolled back to $target"
}

prune_releases() {
  local current previous kept=0 name
  current="$(readlink "$ROOT/current" || true)"
  previous="$(readlink "$ROOT/previous" || true)"
  # Names start with a timestamp, so reverse order is newest first
  for name in $(ls -1 "$RELEASES" | sort -r); do
    kept=$((kept + 1))
    if [[ "$kept" -gt "$KEEP_RELEASES" && "$RELEASES/$name" != "$current" && "$RELEASES/$name" != "$previous" ]]; then
      rm -rf "${RELEASES:?}/$name"
    fi
  done
}

mkdir -p "$RELEASES"
chown "$APP_USER:$APP_USER" "$ROOT" "$RELEASES"
exec 9>"$ROOT/deploy.lock"
if ! flock -n 9; then
  echo "Another deploy is running." >&2
  exit 1
fi

if [[ "${1:-}" == rollback ]]; then
  rollback
  exit
fi

TARBALL="${1:?usage: remote_deploy.sh <release.tar.gz> | rollback}"
: "${HOST:?set HOST to the public host name}"

# 1. Install the release next to the live one
VERSION="$(tar -xzOf "$TARBALL" ./VERSION)"
RELEASE="$RELEASES/$(date -u +%Y%m%d-%H%M%S)-${VERSION##*@}"
ACTIVATED=no
remove_unused_release() {
  if [[ "$ACTIVATED" == no ]]; then
    rm -rf "$RELEASE"
  fi
}
trap remove_unused_release EXIT
mkdir "$RELEASE"
tar -xzf "$TARBALL" --no-same-owner -C "$RELEASE"
chown -R "$APP_USER:$APP_USER" "$RELEASE"
runuser -u "$APP_USER" -- python3 -m venv "$RELEASE/.venv"
runuser -u "$APP_USER" -- "$RELEASE/.venv/bin/pip" install -q -r "$RELEASE/requirements.txt"

# 2. First deploy under this layout: the old install becomes the rollback target
if [[ ! -e "$ROOT/current" ]]; then
  if [[ -d "$LEGACY_DIR/.venv" ]]; then
    switch_to "$LEGACY_DIR"
  fi
fi

# 3. Switch and restart
PREVIOUS="$(readlink "$ROOT/current" || true)"
if [[ -n "$PREVIOUS" ]]; then
  ln -sfn "$PREVIOUS" "$ROOT/previous"
fi
switch_to "$RELEASE"
ACTIVATED=yes
HOST="$HOST" bash "$RELEASE/deploy/server_setup.sh"
systemctl restart cra

# 4. Keep the release only if it's the one answering
if ! wait_for_app "$LOCAL_URL/version" "\"$VERSION\""; then
  echo "$VERSION didn't start." >&2
  journalctl -u cra -n 30 --no-pager >&2 || true
  rollback
  exit 1
fi

# Kept outside the releases, so a rollback can run even if the live release is broken
cp "$RELEASE/deploy/remote_deploy.sh" "$ROOT/remote_deploy.sh"
prune_releases
echo "Deployed $VERSION to $RELEASE"
