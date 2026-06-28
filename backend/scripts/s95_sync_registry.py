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

from scripts.script_runtime import add_bootstrap_args, apply_bootstrap_args, bootstrap_from_env
from app.db.session import get_session_factory
from app.sync.s95_locations_registry import S95LocationRegistrySyncOptions, sync_s95_locations_registry


def main() -> int:
    bootstrap_from_env()
    parser = argparse.ArgumentParser(description="Sync S95 location registry via JSON API")
    add_bootstrap_args(parser)
    parser.add_argument("--limit", type=int, default=None, help="Process only first N entries")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    apply_bootstrap_args(args)

    db = get_session_factory()()
    try:
        result = sync_s95_locations_registry(
            db,
            S95LocationRegistrySyncOptions(limit=args.limit),
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
