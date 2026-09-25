#!/usr/bin/env bash
# Ночной бэкап боевой базы на домашнем сервере → холодный S3 Timeweb.
#
# Домашняя версия /usr/local/bin/pg_backup.sh с прода. Отличия:
#   * база в контейнере, поэтому pg_dump зовём через compose, а не sudo -u postgres;
#   * работает от dmitry, конфиг rclone — в ~/.config/rclone/rclone.conf;
#   * пока сайт не дома (нет маркера, см. ниже), молча выходим: это не авария.
#
# Проект compose — srs_home, явно через -p, как в cutover_to_home.sh и
# deploy_home.sh. В том же каталоге ~/srs-prod живёт стек домашних воркеров
# сбора, и без -p compose взял бы имя проекта по каталогу — srs-prod, где
# postgres нет никогда: после переезда живую базу не бэкапили бы ни разу.
#
# Бэкапим, только пока лежит маркер «сайт дома» ~/.srs-site-at-home (кладёт шаг
# mark в scripts/cutover_to_home.sh, снимает откат — scripts/cutover_rollback.sh
# now). Живого контейнера для этого мало: откат нарочно оставляет postgres
# проекта srs_home работать — из него снимает дамп шаг back-dump. Без маркера
# сюда каждую ночь уезжал бы дамп БРОШЕННОЙ домашней базы — в тот же путь
# бакета, что и у прода, и при восстановлении «последнего дампа» легко взять
# не тот. На репетициях стек поднят без маркера — бэкапить тоже нечего.
#
# Путь в бакете тот же, что у прода, — чтобы история дампов не разрывалась
# переездом, — а имя своё, с «_home_»: у VPS файл называется так же по дате
# и минуте (saturday_runs_lk_2026-09-24_2330.dump, крон в то же 23:30), и
# забытый на VPS крон залил бы дамп ЗАМОРОЖЕННОЙ базы поверх живого домашнего
# под тем же именем (проверено по бакету 25.09.2026). ВАЖНО: крон бэкапа на VPS
# (crontab root, pg_backup.sh) всё равно выключают
# на шаге mark — иначе он продолжит лить туда дампы замороженной базы — и
# включают обратно при откате: без маркера этот скрипт базу не бэкапит. Об
# обоих напоминают сами шаги: cutover_to_home.sh mark и cutover_rollback.sh now.
#
# Восстановление: как шаг db в scripts/cutover_to_home.sh — pg_restore
# --no-owner --no-acl, затем scripts/create_report_ro_role.sql. Роли в дамп
# не входят, и без скрипта report_ro останется без прав.
set -euo pipefail

HOME_DIR="${HOME_PROD_DIR:-$HOME/srs-prod}"
DIR="${HOME_BACKUP_DIR:-$HOME/srs-backups/pg}"
REMOTE="${BACKUP_REMOTE:-backup:backup-popov-servers/postgres}"
DB="${POSTGRES_DB:-saturday_runs_lk}"
USER_="${POSTGRES_USER:-saturday_runs}"
MARKER="${SITE_AT_HOME_MARKER:-$HOME/.srs-site-at-home}"
KEEP_LOCAL_DAYS=3
KEEP_REMOTE_DAYS=30
COMPOSE=(docker compose -p "${HOME_PROJECT:-srs_home}" -f docker-compose.yml -f docker-compose.home-site.yml)

cd "$HOME_DIR"
echo "=== $(date '+%F %T') START ==="

if [ ! -f "$MARKER" ]; then
    echo "-- нет маркера $MARKER: сайт не дома (ещё не переехал или откачен), его базу бэкапит прод"
    echo "=== $(date '+%F %T') SKIP ==="
    exit 0
fi

# С маркером база обязана быть живой: сайт работает из неё. Не видим её —
# значит, сайт лежит или стек поднят под другим именем проекта (так выглядел
# вызов без -p). Это ошибка, а не SKIP: юнит должен упасть и попасть в
# systemctl --failed. stderr compose не глушим — причина будет в журнале.
if ! "${COMPOSE[@]}" ps --status running --services | grep -qx postgres; then
    echo "ОШИБКА: маркер стоит, а compose не видит запущенного postgres в проекте ${HOME_PROJECT:-srs_home} — бэкап не снят"
    exit 1
fi

mkdir -p "$DIR"
F="$DIR/${DB}_home_$(date +%F_%H%M).dump"
echo "-- dump $DB -> $F"
"${COMPOSE[@]}" exec -T postgres pg_dump -U "$USER_" -Fc -Z6 "$DB" > "$F"
echo "   размер: $(du -h "$F" | cut -f1)"

# Дамп нулевого размера хуже отсутствия дампа: он вытеснит из ротации живой.
[ -s "$F" ] || { echo "ОШИБКА: дамп пустой, не заливаю"; rm -f "$F"; exit 1; }

echo "-- upload"
rclone copy "$F" "$REMOTE/$DB/" --s3-no-check-bucket

echo "-- ротация: локально >${KEEP_LOCAL_DAYS}д, в бакете >${KEEP_REMOTE_DAYS}д"
find "$DIR" -name '*.dump' -mtime +$KEEP_LOCAL_DAYS -delete
rclone delete "$REMOTE" --min-age ${KEEP_REMOTE_DAYS}d
echo "=== $(date '+%F %T') OK ==="
