#!/usr/bin/env python3
"""Queue parkrun athletes that RunPark's historical archive knows but we don't.

RunPark's MSSQL keeps the parkrun Russia archive (schema Pakrun, 2014-03..2022-02)
in base tables — it is a *fuller* protocol-level source than our own parkrun data,
which was collected athlete-by-athlete and therefore only ever contained the
runners we already tracked. Comparing the two surfaces athletes we never fetched
(and athletes we have, but with fewer results than the archive lists).

This only seeds profile_fetch_pending — the actual import still happens through
`make parkrun`, which fetches each athlete's parkrun.org profile and upserts the
full history. So the archive is used as a *worklist*, not as an import source.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

if sys.version_info < (3, 12):
    print("Needs Python 3.12+", file=sys.stderr)
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import get_session_factory
from app.models import Event, Location, Participant, Platform, ProfileFetchPendingOperation, RunResult
from app.runpark.mssql_client import fix_varchar_encoding, runpark_query
from app.services.profile_fetch_pending_service import (
    RUNPARK_SEED_NOTE_PREFIX,
    ensure_parkrun_pending_queue_row,
)

PLATFORM_CODE = "parkrun"

# Pakrun writes slugs squashed ("severnoetushino"), our DB keeps the hyphenated
# parkrun.org slug ("severnoe-tushino"). Compare on the normalized form only —
# a literal match silently "loses" ~20 locations and ~94k rows.
_NORM_RE = re.compile(r"[^a-z0-9]")


def _norm(value: str | None) -> str:
    return _NORM_RE.sub("", (value or "").lower())


def fetch_archive_counts() -> tuple[dict[str, int], dict[str, str], set[str]]:
    """Returns (athlete_id -> result count, athlete_id -> name, RU location slugs)."""
    counts = {
        str(r["ir"]): int(r["c"])
        for r in runpark_query(
            "SELECT id_runner AS ir, COUNT(*) AS c FROM Pakrun.RunResults "
            "WHERE id_runner <> 0 GROUP BY id_runner"
        )
    }
    names = {
        str(r["id"]): (fix_varchar_encoding(r["title"]) or "").strip()
        for r in runpark_query("SELECT id, title FROM Pakrun.Runners")
    }
    slugs = {_norm(r["loc"]) for r in runpark_query("SELECT DISTINCT Location AS loc FROM Pakrun.Runs")}
    return counts, names, slugs


def fetch_our_counts(db, slugs: set[str]) -> tuple[dict[str, int], set[str]]:
    """Per-athlete result counts on the archive's locations, plus all known athlete ids."""
    platform = db.query(Platform).filter(Platform.code == PLATFORM_CODE).one()
    location_ids = [
        loc.id
        for loc in db.query(Location).filter(Location.platform_id == platform.id).all()
        if _norm(loc.external_key) in slugs
    ]
    known = {
        row[0]
        for row in db.query(Participant.external_user_id).filter(Participant.platform_id == platform.id).all()
    }
    counts: dict[str, int] = {}
    if location_ids:
        rows = (
            db.query(Participant.external_user_id, RunResult.id)
            .join(RunResult, RunResult.participant_id == Participant.id)
            .join(Event, Event.id == RunResult.event_id)
            .filter(Event.platform_id == platform.id, Event.location_id.in_(location_ids))
            .all()
        )
        for external_user_id, _ in rows:
            counts[external_user_id] = counts.get(external_user_id, 0) + 1
    return counts, known


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed parkrun queue from RunPark's Pakrun archive")
    parser.add_argument("--limit", type=int, default=0, help="Max athletes to queue (0 = all)")
    parser.add_argument("--dry-run", action="store_true", help="Only report, do not touch the queue")
    parser.add_argument(
        "--absent-only",
        action="store_true",
        help="Queue only athletes with no participant row (skip those we have but with gaps)",
    )
    parser.add_argument(
        "--preview-only",
        action="store_true",
        help="Queue profile_preview instead of activity_import",
    )
    args = parser.parse_args()

    print("Читаю архив Pakrun из RunPark MSSQL…", flush=True)
    archive_counts, names, slugs = fetch_archive_counts()
    print(f"  атлетов в архиве: {len(archive_counts)}, локаций: {len(slugs)}", flush=True)

    Session = get_session_factory()
    with Session() as db:
        our_counts, known = fetch_our_counts(db, slugs)
        print(f"  наших parkrun-атлетов: {len(known)}", flush=True)

        absent: list[str] = []
        gaps: list[str] = []
        for athlete_id, archive_n in archive_counts.items():
            if athlete_id not in known:
                absent.append(athlete_id)
            elif archive_n > our_counts.get(athlete_id, 0):
                gaps.append(athlete_id)

        missing_rows = sum(archive_counts[a] for a in absent)
        gap_rows = sum(archive_counts[a] - our_counts.get(a, 0) for a in gaps)
        print(f"\nНет у нас вовсе:        {len(absent):>5} атлетов ({missing_rows} результатов в архиве)")
        print(f"Есть, но с пробелами:   {len(gaps):>5} атлетов (~{gap_rows} недостающих результатов)")

        selected = absent if args.absent_only else absent + gaps
        # Fullest archives first: those athletes recover the most history per fetch.
        selected.sort(key=lambda a: -archive_counts[a])
        if args.limit > 0:
            selected = selected[: args.limit]
        print(f"\nК постановке в очередь:  {len(selected)}", flush=True)

        if args.dry_run:
            for athlete_id in selected[:20]:
                print(f"  · {athlete_id} — {names.get(athlete_id) or '—'} ({archive_counts[athlete_id]} рез.)")
            if len(selected) > 20:
                print(f"  … и ещё {len(selected) - 20}")
            print("\n--dry-run: очередь не изменена", flush=True)
            return 0

        operation = (
            ProfileFetchPendingOperation.profile_preview
            if args.preview_only
            else ProfileFetchPendingOperation.activity_import
        )
        queued = 0
        for athlete_id in selected:
            ensure_parkrun_pending_queue_row(
                db,
                athlete_id,
                operation=operation,
                note=RUNPARK_SEED_NOTE_PREFIX,
            )
            queued += 1
            if queued % 100 == 0:
                db.commit()
                print(f"  … {queued}/{len(selected)}", flush=True)
        db.commit()

    print(f"\nВ очереди (pending): {queued}", flush=True)
    print("Запустите: make parkrun LIMIT=300", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
