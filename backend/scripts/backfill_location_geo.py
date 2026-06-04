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
from app.services.location_geo_service import MAP_PLATFORMS, backfill_location_geo


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill location region/city/country and retry missing coords")
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Max locations to process in one run",
    )
    parser.add_argument(
        "--platform",
        action="append",
        choices=[*MAP_PLATFORMS, "all"],
        help="Limit to platform(s); default: all map platforms",
    )
    parser.add_argument(
        "--fetch-coords",
        action="store_true",
        help="Retry /course/ for five_verst locations missing coordinates",
    )
    parser.add_argument(
        "--include-unofficial",
        action="store_true",
        help="Include locations not marked is_official_map",
    )
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    if args.platform:
        selected = {value for value in args.platform if value != "all"}
        platform_codes = tuple(code for code in MAP_PLATFORMS if code in selected) or MAP_PLATFORMS
    else:
        platform_codes = MAP_PLATFORMS

    db = get_session_factory()()
    try:
        result = backfill_location_geo(
            db,
            limit=args.limit,
            platform_codes=platform_codes,
            official_map_only=not args.include_unofficial,
            fetch_missing_coordinates=args.fetch_coords,
        )
        payload = asdict(result)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    finally:
        db.close()

    indent = 2 if args.pretty else None
    print(json.dumps(payload, ensure_ascii=False, indent=indent))
    return 0 if not payload.get("errors") else 2


if __name__ == "__main__":
    raise SystemExit(main())
