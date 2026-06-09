#!/bin/bash
set -euo pipefail
ROOT="${ROOT:-/opt/saturday-runs-next}"
cd "$ROOT"

sudo_cmd() {
  if [[ -n "${SUDO_PASS:-}" ]]; then
    echo "$SUDO_PASS" | sudo -S "$@"
  else
    sudo "$@"
  fi
}

if ! grep -q connection_upgrade /etc/nginx/nginx.conf 2>/dev/null; then
  sudo_cmd sed -i '/http {/a\
    map $http_upgrade $connection_upgrade {\
        default upgrade;\
        '"''"'      close;\
    }' /etc/nginx/nginx.conf
fi

sudo_cmd cp "$ROOT/deploy/nginx/run5k.run.conf" /etc/nginx/sites-available/run5k.run
sudo_cmd cp "$ROOT/deploy/nginx/app.run5k.run.conf" /etc/nginx/sites-available/app.run5k.run
sudo_cmd ln -sf /etc/nginx/sites-available/run5k.run /etc/nginx/sites-enabled/run5k.run
sudo_cmd ln -sf /etc/nginx/sites-available/app.run5k.run /etc/nginx/sites-enabled/app.run5k.run

if [[ ! -f /etc/letsencrypt/live/grafana.run5k.run/fullchain.pem ]]; then
  sudo_cmd cp "$ROOT/deploy/nginx/grafana.run5k.run.http.conf" /etc/nginx/sites-available/grafana.run5k.run
  sudo_cmd ln -sf /etc/nginx/sites-available/grafana.run5k.run /etc/nginx/sites-enabled/grafana.run5k.run
  sudo_cmd nginx -t
  sudo_cmd systemctl reload nginx
  if command -v certbot >/dev/null 2>&1; then
    sudo_cmd certbot --nginx -d grafana.run5k.run --non-interactive --agree-tos -m admin@run5k.run --redirect || true
  fi
fi

if [[ -f /etc/letsencrypt/live/grafana.run5k.run/fullchain.pem ]]; then
  sudo_cmd cp "$ROOT/deploy/nginx/grafana.run5k.run.conf" /etc/nginx/sites-available/grafana.run5k.run
  sudo_cmd ln -sf /etc/nginx/sites-available/grafana.run5k.run /etc/nginx/sites-enabled/grafana.run5k.run
fi

sudo_cmd nginx -t
sudo_cmd systemctl reload nginx

sudo_cmd mkdir -p /etc/systemd/system/grafana-server.service.d
sudo_cmd cp "$ROOT/deploy/grafana/systemd-override.conf" /etc/systemd/system/grafana-server.service.d/run5k-subdomain.conf
sudo_cmd systemctl daemon-reload
sudo_cmd systemctl restart grafana-server

sed -i 's|https://app.run5k.run|https://run5k.run|g' "$ROOT/.env" || true
grep -q '^APP_BASE_URL=' "$ROOT/.env" && sed -i 's|^APP_BASE_URL=.*|APP_BASE_URL=https://run5k.run|' "$ROOT/.env" || echo 'APP_BASE_URL=https://run5k.run' >> "$ROOT/.env"

docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --force-recreate api vk-bot nginx

docker run --rm -v "$ROOT/frontend:/app" -w /app node:22-alpine sh -c "npm ci && npm run build" 2>&1 | tail -3
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --force-recreate nginx

echo "=== smoke ==="
curl -sf http://127.0.0.1:8080/health && echo
curl -sI https://run5k.run/health | head -3 || true
curl -sI https://app.run5k.run/ | head -3 || true
if [[ -f /etc/letsencrypt/live/grafana.run5k.run/fullchain.pem ]]; then
  curl -sI https://grafana.run5k.run/api/health | head -3 || true
else
  echo "grafana.run5k.run: TLS pending (add DNS A record, then re-run certbot)"
fi
echo DEPLOY_DOMAINS_OK
