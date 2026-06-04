#!/usr/bin/env python3
"""Save parkrun cookies from Chrome remote debugging (CDP).

From Mac host (Chrome on localhost:9222), use network host so Docker reaches Chrome:

  make parkrun-save-cdp-host

From api container (only if host.docker.internal:9222 returns JSON, not HTTP 500):

  docker compose exec api python scripts/parkrun_save_cdp_session.py \\
    --cdp-url http://host.docker.internal:9222
"""
from __future__ import annotations

import argparse
import json
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Save parkrun session from Chrome CDP")
    parser.add_argument(
        "--cdp-url",
        default="http://127.0.0.1:9222",
        help="Chrome DevTools URL (default: http://127.0.0.1:9222)",
    )
    args = parser.parse_args()

    from app.parkrun.fetch.cdp_session import ParkrunCdpSessionError, save_session_from_chrome_cdp

    try:
        payload = save_session_from_chrome_cdp(args.cdp_url)
    except ParkrunCdpSessionError as exc:
        print(exc.message, file=sys.stderr)
        return 1

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
