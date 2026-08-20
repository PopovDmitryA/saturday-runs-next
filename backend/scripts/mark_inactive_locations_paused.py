#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import get_session_factory
from app.services.location_activity_status import INACTIVE_AFTER_DAYS
from app.sync.mark_inactive_locations_paused import mark_inactive_locations_paused


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Проставить «не действует» площадкам, молчащим дольше порога (и снять статус с вернувшихся)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=INACTIVE_AFTER_DAYS,
        help=f"Порог молчания в днях (по умолчанию {INACTIVE_AFTER_DAYS})",
    )
    parser.add_argument(
        "--platform",
        action="append",
        dest="platforms",
        help="Limit to platform code (repeatable: five_verst, s95, runpark)",
    )
    parser.add_argument("--commit", action="store_true", help="Persist changes (default: dry-run rollback)")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    db = get_session_factory()()
    try:
        platform_codes = tuple(args.platforms) if args.platforms else None
        result = mark_inactive_locations_paused(db, inactive_days=args.days, platform_codes=platform_codes)
        if args.commit:
            db.commit()
        else:
            db.rollback()
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    finally:
        db.close()

    payload = asdict(result)
    payload["committed"] = args.commit
    indent = 2 if args.pretty else None
    print(json.dumps(payload, ensure_ascii=False, indent=indent))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
