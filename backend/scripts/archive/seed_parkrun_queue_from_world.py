#!/usr/bin/env python3
"""Ставит в очередь на импорт parkrun-атлетов, найденных мировым обходом.

Обход диапазона ID (репозиторий parkrun-monitoring, staging-база parkrun_world)
нашёл людей, которые бегали на российских локациях, но в базу сайта никогда не
попадали: легаси-выгрузка их не застала, потому что регистрировались они прямо
перед закрытием parkrun в России. Скрипт отбирает таких и кладёт в
profile_fetch_pending — дальше их разбирает Mac-демон (`make parkrun`, пункт 1).

Почему через очередь, а не переносом строк из parkrun_world: краулер собирал
только то, что нужно было для поиска (место, время, дата), а сайту нужны ещё
пол, темп, клуб и метки достижений. Живой парсер профиля берёт всё это сам, и
это тот же код, которым сайт импортирует профиль по просьбе пользователя.
Фетчить с прода нельзя — там parkrun под капчей и действует охлаждение.

Отбор: source='crawl' AND NOT legacy_seed (то есть найден обходом, а не пришёл
из легаси) + хотя бы один забег на российской локации. Слаг в runs собран из
НАЗВАНИЯ события (с дефисами), а канонический parkrun-слаг без разделителей,
поэтому джойн со снятием не-алфанумерики — иначе матч около 40% вместо 91%.

Запуск на проде (в контейнере api есть и DATABASE_URL, и PM_WORLD_DSN):
  docker exec -w /app saturday-runs-next-api-1 \
      python scripts/seed_parkrun_queue_from_world.py --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psycopg

from app.db.session import get_session_factory
from app.models import Participant, Platform, ProfileFetchPendingOperation
from app.services.profile_fetch_pending_service import ensure_parkrun_pending_queue_row

WORLD_SQL = """
SELECT a.athlete_id,
       a.name,
       a.total_runs,
       count(*) FILTER (WHERE lower(ec.iso2) = 'ru') AS ru_runs
  FROM athletes a
  JOIN runs r ON r.athlete_id = a.athlete_id
  JOIN event_country ec
    ON ec.slug = regexp_replace(r.event_slug, '[^a-z0-9]', '', 'g')
 WHERE a.source = 'crawl'
   AND NOT a.legacy_seed
 GROUP BY a.athlete_id, a.name, a.total_runs
HAVING count(*) FILTER (WHERE lower(ec.iso2) = 'ru') > 0
 ORDER BY a.athlete_id
"""


def load_from_world(dsn: str) -> list[tuple[int, str | None, int | None, int]]:
    with psycopg.connect(dsn) as conn:
        # Параллельные воркеры на этой базе падают на /dev/shm 64МБ в контейнере.
        conn.execute("SET max_parallel_workers_per_gather = 0")
        return conn.execute(WORLD_SQL).fetchall()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="только показать, ничего не писать")
    ap.add_argument("--limit", type=int, help="взять первые N (для пробного прогона)")
    ap.add_argument("--dsn", default=os.environ.get("PM_WORLD_DSN", ""), help="DSN parkrun_world")
    args = ap.parse_args()

    if not args.dsn:
        print("нет PM_WORLD_DSN в окружении (и не передан --dsn)", file=sys.stderr)
        return 1

    rows = load_from_world(args.dsn)
    if args.limit:
        rows = rows[: args.limit]
    if not rows:
        print("в parkrun_world нет crawl-атлетов с российскими забегами — нечего ставить в очередь")
        return 0

    db = get_session_factory()()
    try:
        platform = db.query(Platform).filter(Platform.code == "parkrun").one()
        known = {
            pid
            for (pid,) in db.query(Participant.external_user_id).filter(
                Participant.platform_id == platform.id,
                Participant.external_user_id.in_([str(r[0]) for r in rows]),
            )
        }

        queued = 0
        for athlete_id, name, total_runs, ru_runs in rows:
            mark = "есть в базе" if str(athlete_id) in known else "НОВЫЙ"
            print(f"  {athlete_id:>9}  {(name or '—'):<24} забегов={total_runs or 0:<3} РФ={ru_runs:<3} {mark}")
            if args.dry_run:
                continue
            ensure_parkrun_pending_queue_row(
                db,
                str(athlete_id),
                operation=ProfileFetchPendingOperation.activity_import,
                note="seed from parkrun_world sweep (Russian runs found)",
            )
            queued += 1

        if args.dry_run:
            print(f"\n--dry-run: нашлось {len(rows)}, из них новых для сайта {len(rows) - len(known)}. Ничего не записано.")
            return 0

        db.commit()
        print(f"\nпоставлено в очередь: {queued} (новых для сайта {len(rows) - len(known)})")
        print("дальше: make parkrun → пункт 1 (демон разберёт очередь с домашнего IP)")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
