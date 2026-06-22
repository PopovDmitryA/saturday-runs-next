#!/usr/bin/env bash
# Point local Docker API back at the local postgres container.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

docker compose up -d --force-recreate api

echo "" >&2
echo "✓ API → local postgres (docker compose default DATABASE_URL)" >&2
