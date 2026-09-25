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
MARKER="${SITE_AT_HOME_MARKER:-$HOME/.srs-site-at-home}"
COMPOSE=(docker compose -p "${HOME_PROJECT:-srs_home}" -f docker-compose.yml -f docker-compose.home-site.yml --profile telegram)
TARGET="${1:-origin/main}"

say() { echo "$(date '+%H:%M:%S') $*"; }
die() { echo "ДЕПЛОЙ НЕ ПРОШЁЛ: $*" >&2; exit 1; }

# Пока сайт на VPS (до переезда или после отката), этот скрипт поднял бы дома
# второго бота на боевом токене и второй планировщик — ровно так 24.09.2026
# репетиция три минуты дралась с продом за getUpdates. Деплой тогда — с VPS
# (scripts/deploy_prod.sh). Осознанно дома без маркера: FORCE_HOME_DEPLOY=1.
if [ ! -f "$MARKER" ] && [ "${FORCE_HOME_DEPLOY:-0}" != "1" ]; then
  die "сайт не дома (нет $MARKER) — выкат на VPS: bash scripts/deploy_prod.sh"
fi

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
echo "$sha" > "$HOME_DIR/.frontend-build-sha"

# Упала миграция — стоп, как на VPS (там set -e): новый код поверх старой
# схемы отдавал бы 500, а /health в базу не ходит и этого не заметил бы.
say "миграции"
mlog=$(mktemp)
"${COMPOSE[@]}" run --rm -T api alembic upgrade head </dev/null >"$mlog" 2>&1 ||
  { tail -20 "$mlog" >&2; rm -f "$mlog"; die "миграция упала — стек не трогал, код на диске уже $sha"; }
tail -2 "$mlog"; rm -f "$mlog"

say "пересобираю и поднимаю"
"${COMPOSE[@]}" up -d --build || die "стек не поднялся"
# nginx и край — на готовом образе с конфигами-файлами: compose их не
# пересоздаёт, и правки nginx/conf.d и edge.conf без этого до людей не доехали
# бы. Край адрес nginx спрашивает у DNS (resolver), рестарт nginx ему не страшен.
"${COMPOSE[@]}" restart nginx >/dev/null || die "nginx не перезапустился"
"${COMPOSE[@]}" exec -T edge nginx -s reload || die "край не перечитал конфиг"

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
