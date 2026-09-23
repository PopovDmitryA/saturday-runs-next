#!/usr/bin/env bash
# Деплой сайта на домашнем сервере — то же, что remote_deploy.sh делал на VPS.
#
# После переезда «прод» — это ~/srs-prod на saturday-run. Деплой здесь
# локальный: ни ssh, ни пароля, ни ограничения «один коннект».
#
#   bash scripts/deploy_home.sh            # на вершину origin/main
#   bash scripts/deploy_home.sh <коммит>   # на конкретный коммит
#
# Маркер .deployed_sha пишется ПОСЛЕ успешного health-check: по нему
# scripts/home_sync.sh подтягивает код для разбора очереди профилей.
set -uo pipefail

HOME_DIR="${HOME_PROD_DIR:-$HOME/srs-prod}"
COMPOSE=(docker compose -p "${HOME_PROJECT:-srs_home}" -f docker-compose.yml -f docker-compose.home-site.yml --profile telegram)
TARGET="${1:-origin/main}"

say() { echo "$(date '+%H:%M:%S') $*"; }
die() { echo "ДЕПЛОЙ НЕ ПРОШЁЛ: $*" >&2; exit 1; }

cd "$HOME_DIR" || die "нет $HOME_DIR"
say "== деплой дома: $TARGET =="

git fetch -q origin || die "git fetch не прошёл"
sha=$(git rev-parse "$TARGET") || die "нет такого коммита: $TARGET"
git checkout -q --detach "$sha" || die "не переключился на $sha"
say "код: $(git log --oneline -1)"

# Фронт собираем тем же образом, что и на VPS. --user обязателен: под root
# node_modules и dist становятся root-овыми, и потом git не может их тронуть.
say "собираю фронт"
docker run --rm --user "$(id -u):$(id -g)" -e npm_config_cache=/tmp/.npm -e HOME=/tmp \
  -v "$HOME_DIR/frontend:/app" -w /app node:22-alpine \
  sh -c "npm ci --no-audit --no-fund && npm run build" >/dev/null || die "сборка фронта упала"

say "миграции"
"${COMPOSE[@]}" run --rm -T api alembic upgrade head </dev/null | tail -2

say "пересобираю и поднимаю"
"${COMPOSE[@]}" up -d --build || die "стек не поднялся"

health=""
for _ in $(seq 1 30); do
  health=$(curl -s -o /dev/null -m 5 --resolve run5k.run:443:127.0.0.1 -w '%{http_code}' https://run5k.run/health || true)
  [ "$health" = "200" ] && break
  sleep 2
done
[ "$health" = "200" ] || die "health=$health — сайт не поднялся, откат: git checkout <прошлый sha> && $0 <прошлый sha>"

# Все ли сервисы действительно работают: невзлетевший воркер не должен
# проходить за успешный деплой (на VPS этим уже обжигались 18.07.2026).
missing=""
for svc in $("${COMPOSE[@]}" config --services); do
  "${COMPOSE[@]}" ps --status running --services 2>/dev/null | grep -qx "$svc" || missing="$missing $svc"
done
[ -n "$missing" ] && die "сервисы не в состоянии running:$missing"

echo "$sha" > "$HOME_DIR/.deployed_sha"
say "✓ health 200, маркер обновлён: $sha"
