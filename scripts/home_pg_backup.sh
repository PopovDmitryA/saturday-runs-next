#!/usr/bin/env bash
# Ночной бэкап боевой базы на домашнем сервере → холодный S3 Timeweb.
#
# Домашняя версия /usr/local/bin/pg_backup.sh с прода. Отличия:
#   * база в контейнере, поэтому pg_dump зовём через compose, а не sudo -u postgres;
#   * работает от dmitry, конфиг rclone — в ~/.config/rclone/rclone.conf;
#   * если стек не поднят, молча выходим: это не авария, а «сайт ещё не переехал».
#
# Путь в бакете тот же, что у прода, — чтобы история дампов не разрывалась
# переездом. ВАЖНО: в момент переезда крон на проде надо выключить, иначе он
# продолжит лить туда дампы замороженной базы.
set -euo pipefail

HOME_DIR="${HOME_PROD_DIR:-$HOME/srs-prod}"
DIR="${HOME_BACKUP_DIR:-$HOME/srs-backups/pg}"
REMOTE="${BACKUP_REMOTE:-backup:backup-popov-servers/postgres}"
DB="${POSTGRES_DB:-saturday_runs_lk}"
USER_="${POSTGRES_USER:-saturday_runs}"
KEEP_LOCAL_DAYS=3
KEEP_REMOTE_DAYS=30
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.home-site.yml)

cd "$HOME_DIR"
echo "=== $(date '+%F %T') START ==="

if ! "${COMPOSE[@]}" ps --status running --services 2>/dev/null | grep -qx postgres; then
    echo "-- контейнер postgres не запущен, бэкапить нечего (сайт ещё не дома)"
    echo "=== $(date '+%F %T') SKIP ==="
    exit 0
fi

mkdir -p "$DIR"
F="$DIR/${DB}_$(date +%F_%H%M).dump"
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
