#!/usr/bin/env bash
# Переезд сайта с VPS на домашний сервер. Шаги — по одному, каждый идемпотентен.
#
#   bash scripts/cutover_to_home.sh preflight   # накануне: что готово, что нет
#   bash scripts/cutover_to_home.sh certs       # забрать сертификаты с прода
#   bash scripts/cutover_to_home.sh media       # первый прогон rsync (373 МБ)
#   bash scripts/cutover_to_home.sh freeze      # заглушка на проде, стоп записи
#   bash scripts/cutover_to_home.sh db          # финальный дамп → восстановление
#   bash scripts/cutover_to_home.sh start       # поднять ВЕБ-часть (безопасно)
#   bash scripts/cutover_to_home.sh mark        # маркер «сайт дома» + стоп старых воркеров
#   bash scripts/cutover_to_home.sh start-full  # фон: бот, beat, воркеры — ТОЛЬКО после freeze
#   bash scripts/cutover_to_home.sh warm        # прогреть кэши ДО переключения
#   bash scripts/cutover_to_home.sh verify      # проверить дом до перевода DNS
#   ... перевод DNS руками: A → домашний IP, AAAA удалить ...
#   bash scripts/cutover_to_home.sh outside     # проверить снаружи, с прода
#
# Откат: scripts/cutover_rollback.sh (поднять прод, отменить mark, DNS обратно).
set -uo pipefail

VPS="${VPS_HOST:-viewer@195.58.34.112}"
REMOTE_DIR="${VPS_DIR:-/opt/saturday-runs-next}"
HOME_DIR="${HOME_PROD_DIR:-$HOME/srs-prod}"
DUMP_DIR="${HOME_DUMP_DIR:-$HOME/srs-prod-db}"
HOME_IP="${HOME_PUBLIC_IP:-95.165.143.93}"
COMPOSE=(docker compose -p "${HOME_PROJECT:-srs_home}" -f docker-compose.yml -f docker-compose.home-site.yml --profile telegram)

step="${1:-}"
say() { echo "$(date '+%H:%M:%S') $*"; }
die() { echo "СТОП: $*" >&2; exit 1; }
# Ночной бэкап базы дома — scripts/home_pg_backup.sh по таймеру srs-pg-backup.
# Таймер ставится руками и один раз; до шага mark бэкап молчит (нет маркера),
# поэтому включать его можно заранее.
backup_timer() {
  systemctl is-active --quiet srs-pg-backup.timer &&
    say "✓ таймер домашнего бэкапа базы включён (srs-pg-backup.timer, 23:30)" ||
    say "✗ таймер домашнего бэкапа базы не включён: sudo cp $HOME_DIR/deploy/systemd/srs-pg-backup.* /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now srs-pg-backup.timer"
}
cd "$HOME_DIR" 2>/dev/null || die "нет $HOME_DIR"

case "$step" in

preflight)
  say "== готовность =="
  [ -f "$HOME_DIR/.env" ] && say "✓ .env на месте" || say "✗ нет .env"
  # Конфиг прокси к Telegram в git не хранится (секреты) и едет отдельно.
  # Без него бот дома молчит: api.telegram.org из домашней сети недоступен.
  [ -f "$HOME_DIR/deploy/tg-proxy/config.json" ] && say "✓ конфиг tg-proxy на месте" ||
    say "✗ нет deploy/tg-proxy/config.json — забрать с прода"
  # Шаг db создаёт report_ro с паролем из REPORT_DATABASE_URL (или REPORT_RO_PASSWORD).
  grep -qE '^(REPORT_RO_PASSWORD=.|REPORT_DATABASE_URL=[^:]+://[^:@/]+:[^@]+@)' "$HOME_DIR/.env" &&
    say "✓ пароль report_ro есть в .env" ||
    say "✗ нет пароля report_ro в .env — отчёты и выборки для дайджеста дома не войдут"
  sudo -n test -d /etc/letsencrypt/live/run5k.run &&
    say "✓ сертификаты run5k.run на месте" || say "✗ сертификатов нет — шаг certs"
  [ -d "$HOME_DIR/data/og" ] && say "✓ медиа: $(du -sh "$HOME_DIR/data" | cut -f1)" || say "✗ медиа не скопированы — шаг media"
  backup_timer
  ttl=$(dig +noall +answer A run5k.run | head -1 | awk '{print $2}')
  [ "${ttl:-999}" -le 120 ] 2>/dev/null && say "✓ TTL $ttl" || say "✗ TTL ${ttl:-?} — снизить до 60 заранее"
  aaaa=$(dig +short AAAA run5k.run | head -1)
  [ -n "$aaaa" ] && say "! AAAA ещё есть ($aaaa) — удалить в момент переключения" || say "✓ AAAA нет"
  "${COMPOSE[@]}" config --quiet && say "✓ compose валиден"
  ;;

certs)
  say "забираю сертификаты"
  # На проде sudo у viewer с паролем, поэтому архив собирает Дмитрий одной
  # командой в своём root-шелле:
  #   tar -czf /tmp/le.tgz -C /etc letsencrypt && chmod 644 /tmp/le.tgz
  ssh -o BatchMode=yes "$VPS" 'test -r /tmp/le.tgz' ||
    die "нет /tmp/le.tgz на проде — собери его root-ом: tar -czf /tmp/le.tgz -C /etc letsencrypt && chmod 644 /tmp/le.tgz"
  scp -o BatchMode=yes "$VPS:/tmp/le.tgz" /tmp/le.tgz || die "не скачался"
  sudo tar -xzf /tmp/le.tgz -C /etc || die "не распаковался"
  ssh -o BatchMode=yes "$VPS" 'rm -f /tmp/le.tgz'; rm -f /tmp/le.tgz
  sudo ls /etc/letsencrypt/live/ | tr '\n' ' '; echo
  ;;

media)
  # --no-o --no-g: принимающая сторона не root, и попытка сохранить владельца
  # роняет весь прогон (код 23). Плюс сами каталоги дома могли достаться от
  # контейнера, который ходит root-ом, — тогда rsync не сможет в них писать.
  say "синхронизирую data/ (первый прогон — минуты, повторный — секунды)"
  sudo -n chown -R "$(id -u):$(id -g)" "$HOME_DIR/data" 2>/dev/null
  rsync -a --no-o --no-g --info=stats2 -e "ssh -o BatchMode=yes" \
    "$VPS:$REMOTE_DIR/data/" "$HOME_DIR/data/" | tail -4
  ;;

freeze)
  say "== заморозка прода =="
  # Плановое окно: пока на диске лежит maintenance_on, контейнерный nginx
  # отдаёт всем «обновляемся» (у нас есть обход по куке /__maint/bypass).
  # Без этого люди продолжали бы писать в базу, которую мы уже сняли дампом,
  # и эти записи потерялись бы при переезде.
  ssh -o BatchMode=yes "$VPS" "cd $REMOTE_DIR && touch deploy/nginx/maintenance_on && docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile telegram stop beat bot worker worker-warm worker-five-verst-user worker-parkrun" ||
    die "не удалось заморозить прод (если ssh отказал — выполнить команду руками на проде)"
  code=$(curl -s -o /dev/null -m 15 -w '%{http_code}' https://run5k.run/)
  say "фон остановлен, окно обслуживания включено; сайт отдаёт $code"
  say "ВАЖНО: с этой минуты записи на проде нет — всё, что запишется, потеряется"
  ;;

db)
  t0=$(date +%s)
  stamp=$(date +%Y%m%d-%H%M%S)
  dump="saturday_runs_lk_cutover_${stamp}.dump"
  mkdir -p "$DUMP_DIR"

  # Дамп снимаем ОТСЮДА правами приложения через tailnet: root на проде не
  # нужен, а 335 МБ приезжают за ~40 с (замер 24.09.2026). Раньше здесь был
  # ssh + sudo -u postgres, и шаг упирался в пароль viewer.
  say "снимаю дамп прод-базы (правами приложения, по tailnet)"
  set -a; . "$HOME_DIR/.env"; set +a
  PROD_URL="${PROD_DATABASE_URL:-$DATABASE_URL}"
  PROD_URL="${PROD_URL/postgresql+psycopg/postgresql}"
  case "$PROD_URL" in *127.0.0.1*|*localhost*)
      die "PROD_DATABASE_URL смотрит на локальную базу — дамп снялся бы сам с себя" ;;
  esac
  pg_dump "$PROD_URL" -Fc -Z6 -f "$DUMP_DIR/$dump" || die "дамп не снялся"
  say "дамп: $(du -h "$DUMP_DIR/$dump" | cut -f1) за $(( $(date +%s) - t0 )) с"

  "${COMPOSE[@]}" up -d postgres || die "база не поднялась"
  for _ in $(seq 1 60); do "${COMPOSE[@]}" exec -T postgres pg_isready -U "${POSTGRES_USER:-saturday_runs}" >/dev/null 2>&1 && break; sleep 1; done

  # База пересоздаётся: на репетициях том уже наполнен, и pg_restore поверх
  # существующих строк сыплет конфликтами первичных ключей. Дома в этот момент
  # только копия прода — терять нечего.
  "${COMPOSE[@]}" exec -T postgres psql -U "${POSTGRES_USER:-saturday_runs}" -d postgres \
    -c "drop database if exists ${POSTGRES_DB:-saturday_runs_lk}" >/dev/null || die "не удалось снести старую копию"
  "${COMPOSE[@]}" exec -T postgres psql -U "${POSTGRES_USER:-saturday_runs}" -d postgres \
    -c "create database ${POSTGRES_DB:-saturday_runs_lk} owner ${POSTGRES_USER:-saturday_runs}" >/dev/null || die "не создалась база"

  # Права из дампа не везём (--no-acl): в нём права по умолчанию висят на роли
  # postgres, которой в контейнерной базе нет, и из-за этого таблицы новых
  # миграций остались бы для report_ro закрыты, как на VPS. Права report_ro
  # целиком выдаёт скрипт роли сразу после восстановления.
  say "восстанавливаю"
  "${COMPOSE[@]}" exec -T postgres pg_restore -U "${POSTGRES_USER:-saturday_runs}" \
    -d "${POSTGRES_DB:-saturday_runs_lk}" -j 6 --no-owner --no-acl "/dump/$dump" 2>&1 | tail -2

  # Пароль report_ro — тот же, что в REPORT_DATABASE_URL: под ним после
  # переезда входят отчётный API и выборки для дайджеста.
  # Сайт от этой роли не зависит, поэтому сбой здесь шаг не валит.
  ro_pw="${REPORT_RO_PASSWORD:-$(python3 -c 'import os, urllib.parse as u; print(u.unquote(u.urlsplit(os.environ.get("REPORT_DATABASE_URL", "")).password or ""))')}"
  if [ -z "$ro_pw" ]; then
    say "✗ report_ro не создана: нет ни REPORT_RO_PASSWORD, ни пароля в REPORT_DATABASE_URL"
  elif ! "${COMPOSE[@]}" exec -T postgres psql -U "${POSTGRES_USER:-saturday_runs}" -d "${POSTGRES_DB:-saturday_runs_lk}" \
      -q -At -v ON_ERROR_STOP=1 -v report_password="$ro_pw" -f - <scripts/create_report_ro_role.sql | tail -2; then
    say "✗ report_ro не настроилась — повторить scripts/create_report_ro_role.sql руками (команда в его шапке)"
  fi
  rows=$("${COMPOSE[@]}" exec -T postgres psql -U "${POSTGRES_USER:-saturday_runs}" -d "${POSTGRES_DB:-saturday_runs_lk}" -At -c "select count(*) from run_results")
  say "run_results дома: $rows; весь шаг занял $(( $(date +%s) - t0 )) с"
  ;;

start)
  # ТОЛЬКО веб: база, кэш, api, nginx, край. Ни бота, ни beat, ни воркеров —
  # у них боевые токены и общий Telegram. 24.09.2026 репетиция подняла всё
  # разом: бот-дубль три минуты дрался с боевым за getUpdates (18 конфликтов
  # в журнале прода), а beat успел поставить задачу. Обошлось — ни одного
  # апдейта дубль не обработал, наружу воркеры не сходили. Больше так нельзя:
  # фоновую часть поднимает отдельный шаг start-full, и только после freeze.
  say "поднимаю ВЕБ-часть (без бота, планировщика и воркеров)"
  "${COMPOSE[@]}" up -d --build postgres redis api nginx edge certbot || die "стек не поднялся"
  "${COMPOSE[@]}" run --rm -T api alembic upgrade head </dev/null | tail -2
  for _ in $(seq 1 60); do
    code=$(curl -s -o /dev/null -m 5 --resolve run5k.run:443:127.0.0.1 -w '%{http_code}' https://run5k.run/health)
    [ "$code" = "200" ] && { say "✓ health 200 через край"; break; }
    sleep 2
  done
  ;;

mark)
  # Маркер «сайт дома»: по нему разбор очереди профилей начинает писать в
  # локальную базу, а синхронизаторы перестают тянуть код с VPS. Без него
  # очередь молча продолжила бы писать в БРОШЕННУЮ базу на проде.
  # Всё, что делает этот шаг, отменяет откат: scripts/cutover_rollback.sh now
  # снимает маркер, возвращает .env, поднимает srs-prod обратно и напоминает
  # включить крон бэкапа на VPS.
  touch "${SITE_AT_HOME_MARKER:-$HOME/.srs-site-at-home}"
  say "маркер поставлен: ${SITE_AT_HOME_MARKER:-$HOME/.srs-site-at-home}"

  # .env домашнего клона всё ещё указывает на базу VPS (по нему работали
  # воркеры до переезда). Сервисам compose адрес перекрывает, а вот ручной
  # скрипт внутри контейнера (docker compose exec api python scripts/…)
  # прочитал бы именно .env и ушёл писать на прод. Переписываем: боевой адрес
  # становится локальным, прежний остаётся под именем PROD_DATABASE_URL —
  # он нужен шагу db, чтобы снимать дамп.
  #
  # REPORT_DATABASE_URL compose НЕ перекрывает, api читает его прямо из .env.
  # На VPS это хостовый Postgres за мостом docker0 (172.17.0.1), а дома там
  # слушает только 127.0.0.1 — отчётный API упёрся бы в отказ соединения.
  # Дома база — сервис postgres в той же docker-сети.
  report_on_vps=$(grep -c '^REPORT_DATABASE_URL=.*@172\.17\.0\.1:5432/' "$HOME_DIR/.env")
  python3 - "$HOME_DIR/.env" <<'PYENV'
import pathlib, sys, re
p = pathlib.Path(sys.argv[1])
lines = p.read_text().splitlines()
out, prod_url, report_moved = [], None, False
for line in lines:
    if line.startswith("DATABASE_URL=") and "@100.93.200.8:" in line:
        prod_url = line.split("=", 1)[1]
        out.append(re.sub(r"@100\.93\.200\.8:5432", "@127.0.0.1:5433", line))
    elif line.startswith("REPORT_DATABASE_URL=") and "@172.17.0.1:5432/" in line:
        out.append(line.replace("@172.17.0.1:5432/", "@postgres:5432/"))
        report_moved = True
    else:
        out.append(line)
if prod_url and not any(l.startswith("PROD_DATABASE_URL=") for l in out):
    out.append("# Адрес прод-базы на VPS: нужен шагу db сценария переезда и откату.")
    out.append(f"PROD_DATABASE_URL={prod_url}")
p.write_text("\n".join(out) + "\n")
print("   .env: DATABASE_URL переключён на локальную базу" if prod_url else "   .env: боевой адрес уже локальный")
if report_moved:
    print("   .env: REPORT_DATABASE_URL смотрит на сервис postgres")
PYENV
  # Настройки api читает один раз при старте — без перезапуска адрес не подхватится.
  if [ "$report_on_vps" != 0 ]; then
    "${COMPOSE[@]}" restart api >/dev/null && say "api перезапущен: отчётный API ходит в домашнюю базу"
  fi
  # Старый стек воркеров (проект srs-prod) смотрит в базу на VPS по tailnet —
  # после переезда его работа уходила бы в никуда.
  docker compose -p srs-prod -f docker-compose.yml -f docker-compose.home.yml stop 2>/dev/null | tail -2
  say "старый стек домашних воркеров остановлен — они теперь часть общего стека"

  # Бэкап базы переезжает вместе с сайтом: с маркером ночной бэкап снимает
  # домашнюю базу — в тот же путь бакета, куда пишет крон VPS. Тот с этой минуты
  # лил бы туда дампы замороженной базы; он в crontab root, выключается руками.
  backup_timer
  say "ВЫКЛЮЧИ бэкап на VPS (под root): crontab -e — закомментировать строку с pg_backup.sh"
  ;;

start-full)
  # Фоновая часть. Зовётся ТОЛЬКО после freeze: пока прод жив, второй бот и
  # второй планировщик — это дубль боевых токенов и двойные походы наружу.
  ssh -o BatchMode=yes "$VPS" "cd $REMOTE_DIR && docker compose ps --status running --services 2>/dev/null" |
    grep -qxE "bot|beat" &&
    die "на проде ещё живы bot/beat — сначала freeze, иначе получим два бота на один токен"
  say "поднимаю фоновую часть: планировщик, воркеры, бот"
  "${COMPOSE[@]}" up -d --build || die "фоновая часть не поднялась"
  "${COMPOSE[@]}" ps --format '{{.Service}}: {{.Status}}'
  ;;

warm)
  say "прогреваю кэши ДО перевода DNS (холодная главная считается ~34 с)"
  for u in /api/portal/home /api/locations/index /api/fastest /api/location-records /api/protocol/week; do
    printf '  %-26s' "$u"
    curl -s -o /dev/null -m 180 --resolve run5k.run:443:127.0.0.1 \
      -w '%{http_code} за %{time_total}s\n' "https://run5k.run$u"
  done
  ;;

verify)
  say "== дом до переключения DNS =="
  for u in / /health /api/locations/index /api/portal/home; do
    printf '  %-24s' "$u"
    curl -s -o /dev/null -m 120 --resolve run5k.run:443:127.0.0.1 \
      -w '%{http_code} за %{time_total}s\n' "https://run5k.run$u"
  done
  say "теперь DNS: A run5k.run → $HOME_IP, AAAA удалить (дома IPv6 нет)"
  ;;

outside)
  say "== снаружи (глазами прода) =="
  ssh -o BatchMode=yes "$VPS" "curl -s -o /dev/null -m 15 -w 'https://run5k.run/ %{http_code} за %{time_total}s\n' https://run5k.run/ --resolve run5k.run:443:$HOME_IP"
  dig +short A run5k.run | sed 's/^/  A: /'
  dig +short AAAA run5k.run | sed 's/^/  AAAA (должно быть пусто): /'
  ;;

*)
  grep '^#' "$0" | sed 's/^# \{0,1\}//' | head -20
  ;;
esac
