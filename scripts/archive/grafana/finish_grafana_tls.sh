#!/bin/bash
# Issue TLS for grafana.run5k.run after DNS A record is added. Run on server.
set -euo pipefail
ROOT="${ROOT:-/opt/saturday-runs-next}"

sudo_cmd() {
  if [[ -n "${SUDO_PASS:-}" ]]; then
    echo "$SUDO_PASS" | sudo -S "$@"
  else
    sudo "$@"
  fi
}

if ! host grafana.run5k.run >/dev/null 2>&1; then
  echo "DNS NXDOMAIN: add A record grafana.run5k.run → $(curl -s ifconfig.me || echo SERVER_IP)" >&2
  exit 1
fi

sudo_cmd cp "$ROOT/deploy/nginx/grafana.run5k.run.http.conf" /etc/nginx/sites-available/grafana.run5k.run
sudo_cmd ln -sf /etc/nginx/sites-available/grafana.run5k.run /etc/nginx/sites-enabled/grafana.run5k.run
sudo_cmd nginx -t && sudo_cmd systemctl reload nginx
sudo_cmd certbot --nginx -d grafana.run5k.run --non-interactive --agree-tos -m admin@run5k.run --redirect
sudo_cmd cp "$ROOT/deploy/nginx/grafana.run5k.run.conf" /etc/nginx/sites-available/grafana.run5k.run
sudo_cmd nginx -t && sudo_cmd systemctl reload nginx
curl -sI https://grafana.run5k.run/api/health | head -5
echo OK
