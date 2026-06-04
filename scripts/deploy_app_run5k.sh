#!/bin/bash
# Run on server as user with docker + sudo (e.g. viewer).
set -euo pipefail

ROOT="${ROOT:-/opt/saturday-runs-next}"
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

cd "$ROOT"
git pull origin main

echo "=== frontend build ==="
if command -v npm >/dev/null 2>&1; then
  (cd frontend && npm ci && npm run build)
else
  docker run --rm -v "$ROOT/frontend:/app" -w /app node:22-alpine sh -c "npm ci && npm run build"
fi

echo "=== docker stack ==="
$COMPOSE up -d --build redis api bot worker worker-s95 worker-five-verst worker-parkrun beat
# Recreate nginx after api so upstream DNS is fresh (see nginx/conf.d resolver).
$COMPOSE up -d --force-recreate nginx

echo "=== wait for api health ==="
for i in $(seq 1 30); do
  if curl -sf http://127.0.0.1:8080/health >/dev/null; then
    echo "health OK"
    break
  fi
  sleep 2
done
curl -s http://127.0.0.1:8080/health || true

echo "=== host nginx ==="
sudo cp "$ROOT/deploy/nginx/app.run5k.run.conf" /etc/nginx/sites-available/app.run5k.run
sudo ln -sf /etc/nginx/sites-available/app.run5k.run /etc/nginx/sites-enabled/app.run5k.run
sudo nginx -t
sudo systemctl reload nginx

echo "=== TLS (requires DNS A record app.run5k.run -> this server) ==="
if command -v certbot >/dev/null 2>&1; then
  sudo certbot --nginx -d app.run5k.run --non-interactive --agree-tos -m admin@run5k.run --redirect || \
    echo "certbot failed — set DNS first, then: sudo certbot --nginx -d app.run5k.run"
else
  echo "install certbot: sudo apt install certbot python3-certbot-nginx"
fi

echo "Done. Smoke: curl -sI https://app.run5k.run/health"
