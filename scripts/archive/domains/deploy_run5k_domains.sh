#!/bin/bash
# Switch production domains:
#   run5k.run          → new LK (Docker 127.0.0.1:8080)
#   grafana.run5k.run  → legacy Grafana (127.0.0.1:9000)
#   app.run5k.run      → 301 → run5k.run
#
# Prerequisites:
#   DNS A/AAAA: run5k.run, www.run5k.run, grafana.run5k.run → this server
#   After deploy: update APP_BASE_URL, VK/Yandex OAuth redirect URIs
#
# Run on server: bash scripts/deploy_run5k_domains.sh
set -euo pipefail

ROOT="${ROOT:-/opt/saturday-runs-next}"
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
CERTBOT_EMAIL="${CERTBOT_EMAIL:-admin@run5k.run}"

cd "$ROOT"

ensure_websocket_map() {
  if grep -q 'connection_upgrade' /etc/nginx/nginx.conf 2>/dev/null; then
    return 0
  fi
  echo "=== nginx: add WebSocket map for Grafana ==="
  sudo sed -i '/http {/a\
    map $http_upgrade $connection_upgrade {\
        default upgrade;\
        '"''"'      close;\
    }' /etc/nginx/nginx.conf
}

patch_env_base_url() {
  if [[ ! -f .env ]]; then
    echo "WARN: .env not found — set APP_BASE_URL=https://run5k.run manually"
    return 0
  fi
  if grep -q '^APP_BASE_URL=' .env; then
    sudo sed -i 's|^APP_BASE_URL=.*|APP_BASE_URL=https://run5k.run|' .env
  else
    echo 'APP_BASE_URL=https://run5k.run' | sudo tee -a .env >/dev/null
  fi
  sudo sed -i 's|https://app\.run5k\.run|https://run5k.run|g' .env
  echo "=== .env APP_BASE_URL → https://run5k.run ==="
}

echo "=== frontend build ==="
if command -v npm >/dev/null 2>&1; then
  (cd frontend && npm ci && npm run build)
else
  docker run --rm -v "$ROOT/frontend:/app" -w /app node:22-alpine sh -c "npm ci && npm run build"
fi

echo "=== docker stack ==="
$COMPOSE up -d --build redis api worker-s95 worker-five-verst worker-parkrun beat bot
$COMPOSE up -d --force-recreate nginx

echo "=== wait for api health ==="
for _ in $(seq 1 30); do
  if curl -sf http://127.0.0.1:8080/health >/dev/null; then
    echo "health OK"
    break
  fi
  sleep 2
done
curl -s http://127.0.0.1:8080/health || true

patch_env_base_url
$COMPOSE up -d --force-recreate api bot nginx

ensure_websocket_map

echo "=== host nginx vhosts ==="
sudo cp "$ROOT/deploy/nginx/run5k.run.conf" /etc/nginx/sites-available/run5k.run
sudo cp "$ROOT/deploy/nginx/app.run5k.run.conf" /etc/nginx/sites-available/app.run5k.run
sudo ln -sf /etc/nginx/sites-available/run5k.run /etc/nginx/sites-enabled/run5k.run
sudo ln -sf /etc/nginx/sites-available/app.run5k.run /etc/nginx/sites-enabled/app.run5k.run

if [[ ! -f /etc/letsencrypt/live/grafana.run5k.run/fullchain.pem ]]; then
  echo "=== grafana.run5k.run TLS bootstrap (HTTP only) ==="
  sudo cp "$ROOT/deploy/nginx/grafana.run5k.run.http.conf" /etc/nginx/sites-available/grafana.run5k.run
  sudo ln -sf /etc/nginx/sites-available/grafana.run5k.run /etc/nginx/sites-enabled/grafana.run5k.run
  sudo nginx -t
  sudo systemctl reload nginx
  if command -v certbot >/dev/null 2>&1; then
    sudo certbot --nginx -d grafana.run5k.run --non-interactive --agree-tos -m "$CERTBOT_EMAIL" --redirect || \
      echo "certbot failed — add DNS A record grafana.run5k.run, then re-run"
  fi
fi

if [[ -f /etc/letsencrypt/live/grafana.run5k.run/fullchain.pem ]]; then
  sudo cp "$ROOT/deploy/nginx/grafana.run5k.run.conf" /etc/nginx/sites-available/grafana.run5k.run
  sudo ln -sf /etc/nginx/sites-available/grafana.run5k.run /etc/nginx/sites-enabled/grafana.run5k.run
fi

sudo nginx -t
sudo systemctl reload nginx

echo "=== Grafana root_url → grafana.run5k.run ==="
sudo mkdir -p /etc/systemd/system/grafana-server.service.d
sudo cp "$ROOT/deploy/grafana/systemd-override.conf" /etc/systemd/system/grafana-server.service.d/run5k-subdomain.conf
sudo systemctl daemon-reload
sudo systemctl restart grafana-server

echo ""
echo "=== Done ==="
echo "Smoke tests:"
echo "  curl -sI https://run5k.run/health"
echo "  curl -sI https://grafana.run5k.run/api/health"
echo "  curl -sI https://app.run5k.run/  # → run5k.run"
echo ""
echo "Manual steps:"
echo "  1. VK ID: redirect URI → https://run5k.run/oauth/vk/callback"
echo "  2. Yandex OAuth: callback → https://run5k.run/oauth/yandex/callback"
echo "  3. Verify OAuth login on https://run5k.run"
