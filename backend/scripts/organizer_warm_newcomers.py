#!/usr/bin/env python3
"""Пересчитать панель дебютантов кабинета организатора и отдать готовый кэш.

Печатает `<redis-ключ><TAB><json>` — тем же ключом и тем же телом, что кладёт
сам сервис. Нужен, чтобы прогреть площадку, не дожидаясь деплоя: пока на проде
живёт старый (медленный) запрос, каждый заход организатора запускает его заново
и копии выедают пул соединений.

    make prod-run ARGS="scripts/organizer_warm_newcomers.py --slug komsomolskiyprud --days 180 --dry-run"

Кэш пишется не отсюда: локальный запуск видит локальный Redis, а не прод.
Готовую строку кладут на сервере:

    docker exec -i saturday-runs-next-redis-1 redis-cli -x SETEX '<ключ>' 10800
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import get_session_factory
from app.services.location_page_service import resolve_location_identity
from app.services.organizer_service import (
    NEWCOMERS_CACHE_TTL_SECONDS,
    NEWCOMERS_DEFAULT_DAYS,
    build_location_newcomers,
    newcomers_cache_key,
)
from scripts.script_runtime import add_bootstrap_args, apply_bootstrap_args, bootstrap_from_env


def main() -> int:
    bootstrap_from_env()
    parser = argparse.ArgumentParser(description="Warm organizer newcomers cache")
    add_bootstrap_args(parser)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--days", type=int, default=NEWCOMERS_DEFAULT_DAYS)
    parser.add_argument("--dry-run", action="store_true", help="Не писать в свой Redis, только напечатать")
    args = parser.parse_args()
    apply_bootstrap_args(args)

    db = get_session_factory()()
    try:
        identity = resolve_location_identity(db, args.slug)
        if identity is None:
            print(json.dumps({"error": f"локация {args.slug} не найдена"}, ensure_ascii=False), file=sys.stderr)
            return 1
        payload = build_location_newcomers(
            db,
            identity,
            days=args.days,
            use_cache=not args.dry_run,
            refresh=not args.dry_run,
        )
    finally:
        db.close()

    key = newcomers_cache_key(identity.identity_key, args.days)
    print(f"{key}\t{json.dumps(payload, default=str, ensure_ascii=False)}")
    print(
        f"# ttl={NEWCOMERS_CACHE_TTL_SECONDS}s, дебютантов={payload.get('total')}, "
        f"удержание={payload.get('retention_pct')}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
