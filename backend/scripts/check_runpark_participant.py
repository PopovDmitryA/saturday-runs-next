#!/usr/bin/env python3
"""Diagnose why a RunPark barcode is / isn't resolvable for profile binding.

Answers the question: "is the search broken, or is this participant simply not
in our DB?" — by tracing the exact steps the preview lookup performs.

Usage:
    docker compose exec api python scripts/check_runpark_participant.py A6871786
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: check_runpark_participant.py <barcode>  (e.g. A6871786)")
        sys.exit(2)

    raw = sys.argv[1]
    normalized = raw.strip().upper()
    if normalized.isdigit():
        normalized = "A" + normalized

    from app.db.session import get_session_factory
    from app.models import Participant, Platform
    from app.runpark.mssql_client import runpark_query
    from app.sync.runpark_global_sync import _get_location_mapping

    print(f"Input barcode:   {raw!r}")
    print(f"Normalized:      {normalized!r}")
    print("-" * 60)

    Session = get_session_factory()
    with Session() as db:
        platform = db.query(Platform).filter(Platform.code == "runpark").one_or_none()
        if platform is None:
            print("FATAL: runpark platform row missing")
            sys.exit(1)

        # Step 1 — our DB by barcode_id (what the participant row stores)
        by_barcode = (
            db.query(Participant)
            .filter(Participant.platform_id == platform.id, Participant.barcode_id == normalized)
            .first()
        )
        print(f"[1] participants.barcode_id == {normalized!r}: "
              f"{'FOUND ' + by_barcode.external_user_id if by_barcode else 'not found'}")

        # Step 2 — MSSQL: resolve barcode -> participant_id, then our DB by external_user_id
        pid = None
        for view in ("api.vw_run_results", "api.vw_volunteer_results"):
            rows = runpark_query(
                f"SELECT TOP 1 participant_id FROM {view} WHERE barcode_id = %s",
                (normalized,),
            )
            if rows and rows[0].get("participant_id"):
                pid = str(rows[0]["participant_id"]).upper()
                print(f"[2] MSSQL {view}: participant_id = {pid}")
                break
        if pid is None:
            print(f"[2] MSSQL: NO row with barcode_id = {normalized!r} "
                  "(barcode format / value mismatch — check the actual stored barcode)")
        else:
            by_pid = (
                db.query(Participant)
                .filter(Participant.platform_id == platform.id, Participant.external_user_id == pid)
                .first()
            )
            print(f"    participants.external_user_id == {pid}: "
                  f"{'FOUND (barcode=' + repr(by_pid.barcode_id) + ')' if by_pid else 'NOT in our DB'}")

        # Step 3 — where does this participant actually appear, and are those locations tracked?
        if pid is not None:
            tracked = {k for k in _get_location_mapping(db)}  # upper runpark_location_id set
            loc_rows = runpark_query(
                "SELECT DISTINCT e.location_id "
                "FROM api.vw_run_results r JOIN api.vw_events e "
                "  ON UPPER(CAST(r.event_id AS nvarchar(64))) = UPPER(CAST(e.event_id AS nvarchar(64))) "
                "WHERE UPPER(CAST(r.participant_id AS nvarchar(64))) = %s",
                (pid,),
            )
            locs = {str(r["location_id"]).upper() for r in loc_rows}
            print("-" * 60)
            print(f"[3] participant runs at {len(locs)} location(s); tracked map has {len(tracked)}")
            tracked_hits = locs & tracked
            print(f"    tracked locations for this participant: {len(tracked_hits)}")
            if not tracked_hits:
                print("    => participant only ran at NON-tracked locations -> never imported "
                      "(expected: cannot bind until one of their locations is tracked)")
            else:
                print(f"    => has tracked locations {sorted(tracked_hits)} -> should be importable "
                      "(if absent in [2], migration predates these events or was incomplete)")


if __name__ == "__main__":
    main()
