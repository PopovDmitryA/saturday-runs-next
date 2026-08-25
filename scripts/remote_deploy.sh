#!/usr/bin/env bash
# Серверная часть деплоя. Запускается НА ПРОДЕ скриптом scripts/deploy_prod.sh,
# уже после того, как рабочее дерево прода переведено на деплоящийся коммит.
#
# Почему отдельный файл, а не heredoc внутри deploy_prod.sh (разделено 18.07.2026):
# heredoc уезжал на сервер с диска той папки, из которой запущен deploy_prod.sh.
# Если правка сценария деплоя уже в origin/main, а локальная папка её не подтянула,
# на прод приезжал новый КОД со старым СЦЕНАРИЕМ. Так и случилось: сервис worker
# добавили в компоуз и в список пересоздаваемых, но деплой запустили из папки со
# старым скриптом — контейнер просто не поднялся, а деплой отрапортовал успех.
# Теперь сценарий берётся из задеплоенного коммита и всегда соответствует коду.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Функция, а не eval: eval ломает кавычки внутри --format / python -c.
# --profile telegram: сервисы bot и tg-proxy сидят под профилем, чтобы НЕ подниматься
# на Маке (второй long-poll на том же токене воровал бы апдейты у прод-бота). На проде
# они обязательны, поэтому профиль включён для всех compose-команд — иначе up их не
# создаст, а smoke ниже не заметит, что бота нет.
compose() { docker compose --profile telegram -f docker-compose.yml -f docker-compose.prod.yml "$@"; }

# Заглушка 502 у host nginx: на время деплоя показываем «Обновляемся» вместо
# дежурной «Что-то сломалось» (см. deploy/nginx/run5k.run.conf, try_files).
# Файл гарантированно убирается по завершении — и при успехе, и при падении,
# и при обрыве SSH (HUP/INT/TERM), иначе авария маскировалась бы под деплой.
MAINT_CURRENT="$ROOT/deploy/nginx/maintenance_current.html"
cp "$ROOT/deploy/nginx/maintenance_deploy.html" "$MAINT_CURRENT"
trap 'rm -f "$MAINT_CURRENT"' EXIT HUP INT TERM

# Сервисы, которые пересоздаём. nginx собирать нельзя — он на готовом образе
# (nginx:1.27-alpine), build-контекст есть только у python-сервисов.
# tg-proxy (xray) — тоже готовый образ; без него бот не видит Telegram и ляжет,
# поэтому он в списке и попадает в проверку «все ли running» ниже.
SERVICES="worker worker-s95 worker-five-verst worker-five-verst-user worker-parkrun worker-runpark api nginx beat tg-proxy bot"

# NB: `docker compose exec/run -T` всё равно цепляет контейнер к stdin, поэтому
# каждый exec/run обязан читать из /dev/null — иначе он сожрёт остаток скрипта.
echo "--- queue lengths ---"
compose exec -T redis redis-cli LLEN five_verst </dev/null || true
compose exec -T redis redis-cli LLEN five_verst_user </dev/null || true
compose exec -T redis redis-cli LLEN s95 </dev/null || true
compose exec -T redis redis-cli LLEN s95_user </dev/null || true

echo "--- frontend build ---"
# Собираем в контейнере, куда примонтирована только папка frontend. Флагов
# VITE_* сейчас нет; если появятся — Vite зашивает их в бандл во время сборки,
# корневой .env сюда не попадает, значит читать и передавать через -e надо здесь.
docker run --rm -v "$PWD/frontend:/app" -w /app \
  node:22-alpine sh -c "npm ci && npm run build"

echo "--- build api image (нужен свежий образ для миграций) ---"
compose build api

echo "--- migrations (свежим образом, ДО recreate) ---"
# Порядок важен: миграцию накатываем НОВЫМ образом, но пока крутится СТАРЫЙ код.
# Если сделать наоборот (recreate раньше upgrade) — будет окно, где новый код
# бьётся в ещё не созданную таблицу и отдаёт 500. Старый код переживает
# аддитивную схему спокойно. Упадёт миграция — деплой встанет здесь (set -e),
# прод останется на старом рабочем коде.
compose run --rm -T api alembic upgrade head </dev/null

echo "--- recreate services ---"
# --force-recreate гарантирует, что контейнеры реально перезапустятся на новом
# коде (обычный up -d может переиспользовать старый контейнер).
compose up -d --build --force-recreate $SERVICES
compose restart nginx
# Уборка сервисов, которых больше нет в compose (иначе контейнер остался бы
# висеть на старом коде). worker-five-verst-user из этого списка убран: с
# 08.2026 он снова живой сервис и стоит в $SERVICES выше — пользовательские
# синки 5 вёрст обслуживает он, а батч на это время встаёт на паузу.
compose stop worker-s95-user 2>/dev/null || true
compose rm -f worker-s95-user 2>/dev/null || true

echo "--- host nginx (run5k.run Grafana redirects) ---"
if sudo -n cp deploy/nginx/run5k.run.conf /etc/nginx/sites-available/run5k.run 2>/dev/null; then
  sudo -n nginx -t
  sudo -n systemctl reload nginx
  echo "host nginx reloaded"
else
  echo "WARN: passwordless sudo unavailable — run on server:"
  echo "  sudo cp deploy/nginx/run5k.run.conf /etc/nginx/sites-available/run5k.run"
  echo "  sudo nginx -t && sudo systemctl reload nginx"
fi

echo "--- smoke ---"
compose ps --format '{{.Service}}: {{.Status}}'

# Все ли сервисы прод-компоуза реально подняты. Раньше smoke смотрел только на
# /health, поэтому невзлетевший (или вовсе не созданный) воркер деплой не валил:
# 18.07.2026 сервис worker так и не поднялся, а деплой отрапортовал успех.
missing=""
for svc in $SERVICES redis; do
  state="$(compose ps --status running --services 2>/dev/null | grep -Fx "$svc" || true)"
  [ -z "$state" ] && missing="${missing} ${svc}"
done
if [ -n "$missing" ]; then
  echo "DEPLOY FAILED: сервисы не в состоянии running:${missing}" >&2
  compose ps >&2 || true
  exit 1
fi

# API поднимается несколько секунд после recreate. Раньше smoke стрелял сразу и
# ловил 502/000 на живом проде — ложная тревога, которую легко принять за аварию.
# Ждём настоящий ответ до 60с. И smoke больше НЕ `|| true`: если прод не встал,
# деплой обязан упасть с ненулевым кодом, а не отрапортовать «deploy done».
health=""
for _ in $(seq 1 30); do
  health="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/health || true)"
  [ "$health" = "200" ] && break
  sleep 2
done
if [ "$health" != "200" ]; then
  echo "DEPLOY FAILED: health=${health:-нет ответа} спустя 60с — прод не поднялся." >&2
  echo "--- api logs (last 30) ---" >&2
  compose logs api --tail 30 >&2 || true
  exit 1
fi
echo "health: 200 (ok)"
curl -sI 'https://run5k.run/d/de1hu8dabny80c/karta-turistov' 2>/dev/null | head -1 || true

echo "--- prod git == deployed commit ---"
git log --oneline -1

# Уборка docker-мусора. СТРОГО после успешного health — пока прод не подтверждён
# живым, ничего не удаляем (может понадобиться откат). Любая ошибка уборки не
# должна ронять уже успешный деплой, отсюда `|| true`.
#
# Осознанно НЕ используем:
#   -a (все неиспользуемые)  — снесёт node:22-alpine, которым собирается фронт,
#                              и его пришлось бы качать заново каждый деплой;
#   --volumes                — там живут данные (pm_pgdata с parkrun_world, redis).
# Кэш сборки чистим только старше недели: свежий кратно ускоряет пересборку.
echo "--- cleanup docker ---"
docker image prune -f 2>/dev/null | tail -1 || true
docker builder prune -f --filter "until=168h" 2>/dev/null | tail -1 || true
