#!/usr/bin/env bash
# One-time server setup (GETTING_STARTED.md §8.5–8.7), run on the EC2 instance by deploy.sh.
# Safe to re-run. Installs python venv tooling and Caddy, the cra systemd service, and the
# Caddyfile for $HOST (an sslip.io name, so Caddy can get a Let's Encrypt certificate).
#
#   sudo HOST=13-58-35-224.sslip.io bash server_setup.sh
set -euo pipefail

: "${HOST:?set HOST to the public host name, e.g. 13-58-35-224.sslip.io}"
APP_DIR=/home/ubuntu/ECE-30861-Team-22

export DEBIAN_FRONTEND=noninteractive
if ! command -v caddy >/dev/null; then
  apt-get update -q
  apt-get install -yq python3-venv debian-keyring debian-archive-keyring apt-transport-https curl gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
    | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -q
  apt-get install -yq caddy
fi

# Keep --workers 1: sessions and bundles live in memory, so a second process would not see them
cat > /etc/systemd/system/cra.service <<EOF
[Unit]
Description=Team 22 CRA system + tester
After=network.target

[Service]
User=ubuntu
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/gunicorn --workers 1 --threads 8 --timeout 180 --bind 127.0.0.1:8000 wsgi:application
Restart=always

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/caddy/Caddyfile <<EOF
$HOST {
    reverse_proxy 127.0.0.1:8000
}
EOF

systemctl daemon-reload
systemctl enable cra caddy
systemctl reload-or-restart caddy
