#!/usr/bin/env python3
"""Сверка с каталогом parkrun руками — то же, что задача parkrun_catalog.sync.

    # только посмотреть, что вылечилось бы на сайте (каталог в базе не трогаем)
    docker compose exec api python scripts/parkrun_catalog_run.py --heal-preview
    # сверка из файла (events.json с Мака или копия monitoring) без сообщения
    docker compose exec api python scripts/parkrun_catalog_run.py --from-file /data/events.json --no-notify
    # каталог показался урезанным, а parkrun правда столько закрыл
    docker compose exec api python scripts/parkrun_catalog_run.py --force
    # лечение отказалось: план больше обычного (порог в parkrun_catalog_heal) —
    # посмотреть --heal-preview и, если всё верно, применить без порога
    docker compose exec api python scripts/parkrun_catalog_run.py --heal-no-limit

Без --from-file каталог скачивается так же, как в задаче (на VPS напрямую,
дома — только через PARKRUN_CATALOG_PROXY_URL).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import get_session_factory  # noqa: E402
from app.services.parkrun_catalog_heal import heal_parkrun_locations  # noqa: E402
from app.services.parkrun_catalog_service import run_catalog_sync  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from-file", type=Path, default=None, help="events.json с диска вместо скачивания")
    parser.add_argument("--force", action="store_true", help="Применить, даже если каталог выглядит урезанным")
    parser.add_argument("--no-notify", action="store_true", help="Без сообщения в Telegram")
    parser.add_argument("--no-heal", action="store_true", help="Без лечения локаций сайта")
    parser.add_argument("--heal-preview", action="store_true", help="Только показать, что вылечилось бы")
    parser.add_argument("--heal-no-limit", action="store_true", help="Лечить без порога на число слияний и переименований")
    args = parser.parse_args()
    session_factory = get_session_factory()

    if args.heal_preview:
        report = heal_parkrun_locations(session_factory, apply=False, geocode=False, limits=False)
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=1))
        return 0

    payload = args.from_file.read_bytes() if args.from_file else None
    result = run_catalog_sync(
        session_factory,
        payload=payload,
        force=args.force,
        heal=not args.no_heal,
        notify=not args.no_notify,
        heal_limits=not args.heal_no_limit,
    )
    print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
    return 0 if result.get("status") in ("ok", "unchanged") else 1


if __name__ == "__main__":
    raise SystemExit(main())
