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
from app.sync.five_verst_locations import LocationRegistrySyncOptions, sync_locations_registry


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync 5verst location registry from /events/")
    parser.add_argument("--limit", type=int, default=None, help="Process only first N registry entries")
    parser.add_argument("--no-coords-fetch", action="store_true", help="Do not fetch /course/ for existing rows missing coords")
    parser.add_argument("--no-duplicate-check", action="store_true", help="Skip duplicate slug detection")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    db = get_session_factory()()
    try:
        result = sync_locations_registry(
            db,
            LocationRegistrySyncOptions(
                fetch_missing_coordinates=not args.no_coords_fetch,
                detect_duplicates=not args.no_duplicate_check,
                limit=args.limit,
            ),
        )
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    finally:
        db.close()

    payload = asdict(result)
    indent = 2 if args.pretty else None
    print(json.dumps(payload, ensure_ascii=False, indent=indent))
    return 0 if not result.errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
