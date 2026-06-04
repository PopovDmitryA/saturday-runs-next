#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.sync.sample import SampleSyncOptions, run_sample_sync


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch a small 5verst sample for data structure review")
    parser.add_argument("--slug", default="babushkinskynayauze", help="Park slug, e.g. babushkinskynayauze")
    parser.add_argument("--summaries", type=int, default=3, help="How many event summaries from /results/all/")
    parser.add_argument("--runs", type=int, default=10, help="Max run results per event (0 = all)")
    parser.add_argument("--volunteers", type=int, default=10, help="Max volunteer rows per event (0 = all)")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    args = parser.parse_args()

    options = SampleSyncOptions(
        location_slug=args.slug,
        summaries_limit=max(args.summaries, 1),
        run_results_limit=None if args.runs == 0 else args.runs,
        volunteer_results_limit=None if args.volunteers == 0 else args.volunteers,
    )

    try:
        result = run_sample_sync(options)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

    payload = result.to_dict()
    indent = 2 if args.pretty else None
    print(json.dumps(payload, ensure_ascii=False, indent=indent, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
