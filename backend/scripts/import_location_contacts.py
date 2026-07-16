#!/usr/bin/env python3
"""Влить легаси-контакты 5 вёрст в location_contacts / location_announce_settings.

Идемпотентен: добавляет ссылку, только если у локации ещё нет такой же, и
включает do_not_disturb, только если он был выключен — ручные правки не трогает.

    docker compose exec api python scripts/import_location_contacts.py --dry-run
    docker compose exec api python scripts/import_location_contacts.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import get_session_factory  # noqa: E402
from app.services.location_contacts_service import import_legacy_contacts  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="показать план без записи")
    parser.add_argument("--seed", type=Path, default=None, help="путь к JSON-сиду")
    args = parser.parse_args()

    db = get_session_factory()()
    try:
        result = import_legacy_contacts(db, seed_path=args.seed, dry_run=args.dry_run)
    finally:
        db.close()

    prefix = "[dry-run] " if args.dry_run else ""
    print(f"{prefix}локаций (5вёрст/s95/runpark): {result['locations_total']}")
    print(f"{prefix}добавлено ссылок: {result['contact_links_created']}")
    print(f"{prefix}обновлено настроек «не беспокоить»: {result['announce_settings_touched']}")
    print(f"{prefix}легаси-строк всего: {result['legacy_rows']}")
    if result["legacy_unmatched"]:
        print(f"\n{prefix}не сопоставлено легаси-имён: {len(result['legacy_unmatched'])} — заполнить вручную:")
        for item in result["legacy_unmatched"]:
            link = item["telegram_url"] or "без ссылки"
            print(f"  - {item['name_point']}: {link}")
            for candidate in item["candidates"]:
                print(f"      кандидат: {candidate}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
