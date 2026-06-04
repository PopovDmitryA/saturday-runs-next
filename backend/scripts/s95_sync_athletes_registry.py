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

from app.config import get_settings
from app.db.session import get_session_factory
from app.sync.s95_athletes_registry import S95AthletesRegistrySyncOptions, sync_s95_athletes_registry


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Refresh stale S95 athlete profiles (registry batch)")
    parser.add_argument(
        "--limit",
        type=int,
        default=settings.s95_athletes_registry_batch_limit,
        help="Max participants to refresh in one run",
    )
    parser.add_argument(
        "--mismatch-check-runs",
        type=int,
        default=settings.s95_athlete_mismatch_check_runs,
        help="Compare last N profile runs against DB and re-fetch protocols on mismatch",
    )
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    db = get_session_factory()()
    try:
        result = sync_s95_athletes_registry(
            db,
            S95AthletesRegistrySyncOptions(
                limit=args.limit,
                mismatch_check_limit=args.mismatch_check_runs,
            ),
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
