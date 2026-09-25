#!/usr/bin/env bash
# Откат переезда: вернуть сайт на VPS.
#
# Работает, пока прод цел — а он остаётся целым до 21 октября. Цена отката
# зависит от того, сколько сайт прожил дома: всё, что люди записали дома,
# на проде отсутствует. Поэтому в первые минуты откат бесплатный, через час
# он уже означает перенос свежих данных обратно (шаг back-dump).
#
#   bash scripts/cutover_rollback.sh now        # поднять прод как было
#   bash scripts/cutover_rollback.sh back-dump  # если дома уже писали
#
# «Как было» касается и дома. now отменяет шаг mark сценария переезда: снимает
# маркер «сайт дома», возвращает .env боевого клона, а воркеры сбора снова
# работают дома в стеке srs-prod — против базы и брокера VPS, как до переезда.
# Без этого разбор очереди профилей продолжал бы писать в брошенную домашнюю
# базу: прогоны зелёные, а на сайте профилей нет. Бэкап базы возвращается к
# крону VPS: его выключали на шаге mark, включают руками под root — now
# напоминает. Домашний бэкап без маркера молчит сам.
#
# Порядок: сначала now, потом back-dump. Маркер и .env шагу back-dump не нужны —
# дамп он снимает через compose exec из контейнера postgres проекта srs_home,
# который now как раз не гасит. Очередь профилей после now пишет уже в базу
# VPS; перезальёт её back-dump — разобранное в промежутке пропадёт, но в
# домашнем дампе те же строки остались в очереди, и она пройдёт их ещё раз.
set -uo pipefail

VPS="${VPS_HOST:-viewer@195.58.34.112}"
REMOTE_DIR="${VPS_DIR:-/opt/saturday-runs-next}"
HOME_DIR="${HOME_PROD_DIR:-$HOME/srs-prod}"
DUMP_DIR="${HOME_DUMP_DIR:-$HOME/srs-prod-db}"
MARKER="${SITE_AT_HOME_MARKER:-$HOME/.srs-site-at-home}"
COMPOSE=(docker compose -p "${HOME_PROJECT:-srs_home}" -f docker-compose.yml -f docker-compose.home-site.yml --profile telegram)
say() { echo "$(date '+%H:%M:%S') $*"; }

case "${1:-now}" in

now)
  cd "$HOME_DIR" || { echo "СТОП: нет $HOME_DIR — откат запускается на домашнем сервере" >&2; exit 1; }
  say "гашу домашний стек (кроме базы — она понадобится для back-dump)"
  # Первым делом — край: пока он слушает 80/443, к нам продолжают приходить
  # те, у кого DNS ещё не обновился, и пишут в базу, которую мы бросаем.
  "${COMPOSE[@]}" stop edge nginx api beat bot worker worker-warm \
    worker-s95 worker-five-verst worker-five-verst-fresh worker-five-verst-user \
    worker-parkrun worker-runpark ||
    say "✗ домашний стек не остановился — проверь, что край больше не слушает 80/443"

  # На VPS поднимаем то же, что держал там последний деплой: всё, кроме воркеров
  # сбора. Их список — HOME_SERVICES из remote_deploy.sh самого VPS. Голый
  # `up -d` поднял бы и их: к s95.ru пошли бы с адреса, который он уже банил
  # (AGENTS.md §5), а первый же деплой погасил бы их снова — и собирать стало
  # бы некому. Заглушка снимается последней: пока сервисы не встали, сайт
  # остаётся закрыт.
  say "поднимаю прод (без воркеров сбора — они возвращаются домой)"
  if ! ssh -o BatchMode=yes "$VPS" bash -s -- "$REMOTE_DIR" <<'VPS_UP'
set -e
cd "$1"
home=$(sed -n 's/^HOME_SERVICES="\([^"]*\)".*/\1/p' scripts/remote_deploy.sh)
[ -n "$home" ] || { echo "нет HOME_SERVICES в scripts/remote_deploy.sh" >&2; exit 1; }
# </dev/null: скрипт приходит на stdin, и compose не должен его доесть.
compose() { docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile telegram "$@" </dev/null; }
svcs=$(compose config --services | grep -vxF "$(printf '%s\n' $home)")
echo "   на VPS: $(echo $svcs)"
echo "   не поднимаю (живут дома): $home"
compose up -d $svcs
rm -f deploy/nginx/maintenance_on deploy/nginx/maintenance_current.html
VPS_UP
  then
    say "✗ прод не поднялся. Маркер «сайт дома» и .env НЕ трогаю: база сайта по-прежнему домашняя"
    say "  дома остановлено всё, кроме базы: повторить now — или вернуть сайт домой (cutover_to_home.sh start, start-full)"
    exit 1
  fi
  # Дальше сбои не останавливают откат (прод уже поднят), но код возврата
  # будет ненулевым — чтобы «✗» в выводе нельзя было пропустить.
  status=0
  code=""
  for _ in $(seq 1 10); do
    code=$(curl -s -o /dev/null -m 10 -w '%{http_code}' --resolve run5k.run:443:195.58.34.112 https://run5k.run/ || true)
    [ "$code" = "200" ] && break
    sleep 3
  done
  say "прод отвечает: $code (проверено в обход DNS)"
  [ "$code" = "200" ] || { status=1; say "✗ прод не отдаёт 200 — разобраться ДО перевода DNS"; }

  # Дом возвращается в роль «воркеры сбора при сайте на VPS» — отменяем шаг
  # mark из cutover_to_home.sh. Только теперь: пока прод не встал, база
  # сайта — домашняя. .env — раньше воркеров: docker-compose.home.yml
  # подставляет им DATABASE_URL из .env, и с домашним адресом compose
  # пересоздал бы их смотрящими не туда. PROD_DATABASE_URL остаётся — он
  # безвреден и нужен шагу db, если переезд повторят.
  env_ok=1
  python3 - .env <<'PYENV' || env_ok=0
import pathlib, sys, urllib.parse
p = pathlib.Path(sys.argv[1])
if not p.exists():
    print("   ✗ .env: файла нет — воркерам сбора неоткуда взять адрес базы")
    sys.exit(1)
lines = p.read_text().splitlines()
prod_url = next((l.split("=", 1)[1] for l in reversed(lines) if l.startswith("PROD_DATABASE_URL=")), "")

def at_home(url):
    return urllib.parse.urlsplit(url.strip("'\"")).hostname in ("127.0.0.1", "localhost", "postgres")

out, notes, stuck = [], [], False
for line in lines:
    if line.startswith("DATABASE_URL=") and at_home(line.split("=", 1)[1]):
        if prod_url and not at_home(prod_url):
            line = "DATABASE_URL=" + prod_url
            notes.append("DATABASE_URL вернулся на базу VPS (из PROD_DATABASE_URL)")
        else:
            stuck = True
            notes.append("✗ DATABASE_URL смотрит на домашнюю базу, а адреса VPS в PROD_DATABASE_URL нет — поправить руками")
    elif line.startswith("REPORT_DATABASE_URL=") and "@postgres:5432/" in line:
        line = line.replace("@postgres:5432/", "@172.17.0.1:5432/")
        notes.append("REPORT_DATABASE_URL снова смотрит на хостовый Postgres VPS")
    out.append(line)
if out != lines:
    p.write_text("\n".join(out) + "\n")
print("\n".join("   .env: " + n for n in notes) or "   .env: уже смотрит на VPS")
sys.exit(1 if stuck else 0)
PYENV

  # Маркер решает, куда пишет разбор очереди профилей и с кем сверяются
  # домашние синхронизаторы. Оставленный, он держал бы очередь в брошенной
  # домашней базе, а воркеры сбора — выключенными.
  if rm -f "$MARKER"; then
    say "маркер «сайт дома» снят: очередь профилей снова пишет в базу VPS"
  else
    status=1
    say "✗ маркер не снялся — убрать руками: rm $MARKER"
  fi

  # Воркеры сбора — обратно в стек srs-prod, на том коде, что сейчас в клоне:
  # из него же идут откат и back-dump, сдвигать его посреди отката нельзя.
  if [ "$env_ok" = 1 ]; then
    bash scripts/home_workers_sync.sh --keep-code || {
      status=1
      say "✗ воркеры сбора дома не поднялись — повторить: bash scripts/home_workers_sync.sh --keep-code"
    }
  else
    status=1
    say "✗ воркеры сбора не поднимаю: .env не вернулся. Поправить DATABASE_URL и: bash scripts/home_workers_sync.sh --keep-code"
  fi

  # Дома код мог уйти вперёд VPS (deploy_home.sh после переезда). Тогда воркеры
  # и база VPS расходятся по схеме, пока прод не выкатят заново.
  vps_sha=$(ssh -o BatchMode=yes "$VPS" "cat $REMOTE_DIR/.deployed_sha 2>/dev/null" | tr -d '\r\n')
  home_sha=$(git rev-parse HEAD 2>/dev/null)
  if [ -n "$vps_sha" ] && [ "$vps_sha" != "$home_sha" ]; then
    say "! код дома (${home_sha:0:7}) не совпадает с выкаченным на VPS (${vps_sha:0:7}): выкатить прод (deploy_prod.sh),"
    say "  а если будет back-dump — после заливки: база приедет со схемой домашнего кода"
  fi

  say "ВЕРНИ DNS: A run5k.run → 195.58.34.112, AAAA 2a03:6f00:a::47dc"
  say "  если дома уже писали — сначала back-dump и заливка на проде, DNS потом:"
  say "  заливка перезаписывает базу VPS целиком, и принятое продом до неё пропадёт"
  say "проверка: curl -s -o /dev/null -w '%{http_code}' https://run5k.run/"
  # Шаг mark просил выключить крон бэкапа на VPS, а домашний бэкап без маркера
  # молчит: не вернёшь крон — живую базу не бэкапит никто.
  say "ВЕРНИ бэкап на VPS (под root): crontab -e — строка с pg_backup.sh должна быть без #"
  exit "$status"
  ;;

back-dump)
  say "снимаю домашнюю базу и заливаю обратно на прод"
  stamp=$(date +%Y%m%d-%H%M%S)
  dump="home_back_${stamp}.sql.gz"; ro_sql="home_back_${stamp}_report_ro.sql"
  cd "$HOME_DIR"
  # Обычный SQL, а не -Fc: архив pg_dump 16 (формат 1.15) pg_restore 14 на VPS
  # не читает — «unsupported version (1.15) in file header», причём уже после
  # dropdb. Владельцев и права не везём: объекты создаёт сама роль сайта
  # (SET ROLE ниже), права report_ro выдаёт её скрипт.
  "${COMPOSE[@]}" exec -T postgres pg_dump -U "${POSTGRES_USER:-saturday_runs}" \
    --no-owner --no-acl "${POSTGRES_DB:-saturday_runs_lk}" | gzip > "$DUMP_DIR/$dump" || exit 1
  scp -q -o BatchMode=yes "$DUMP_DIR/$dump" "$VPS:/tmp/$dump" || exit 1
  # Скрипт роли везём из домашнего клона: его список закрытых таблиц должен
  # совпадать со схемой этой базы, а на проде лежит код последнего деплоя.
  scp -q -o BatchMode=yes scripts/create_report_ro_role.sql "$VPS:/tmp/$ro_sql" || exit 1
  # Обрезанный файл psql может доиграть без единой ошибки (обрыв на границе
  # строки) и закоммитить полбазы — проверяем до того, как сносить базу прода.
  ssh -o BatchMode=yes "$VPS" "gzip -t /tmp/$dump" || { say "дамп на проде битый — не восстанавливать"; exit 1; }
  say "дамп на проде цел: $(du -h "$DUMP_DIR/$dump" | cut -f1)"
  # Без SET ROLE всё восстановленное досталось бы postgres, а права владельца
  # pg_dump не пишет: сайт получил бы permission denied на каждую таблицу,
  # миграции — must be owner. --force: после шага now api и воркеры прода
  # держат соединения, и простой dropdb отказывает. -1: упало — база пустая,
  # команду можно повторить.
  say "дальше НА ПРОДЕ, под root (база перезаписывается целиком):"
  say "  touch $REMOTE_DIR/deploy/nginx/maintenance_on"
  say "  sudo -u postgres dropdb --force saturday_runs_lk && sudo -u postgres createdb -O saturday_runs saturday_runs_lk"
  say "  zcat /tmp/$dump | sudo -u postgres psql -X -1 -v ON_ERROR_STOP=1 -d saturday_runs_lk -c 'SET ROLE saturday_runs' -f - >/dev/null"
  say "  sudo -u postgres psql -X -q -d saturday_runs_lk -v ON_ERROR_STOP=1 -f - < /tmp/$ro_sql"
  say "  rm -f $REMOTE_DIR/deploy/nginx/maintenance_on"
  ;;
esac
