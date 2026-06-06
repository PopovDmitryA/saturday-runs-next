from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.migration.legacy_db import get_legacy_engine, legacy_scalar
from app.models import Event, RunResult, VolunteerResult
from app.sync import upsert


@dataclass(frozen=True)
class CountCheck:
    label: str
    legacy_sql: str
    target_sql: str | None = None
    target_count: int | None = None


FIVE_VERST_CHECKS = [
    CountCheck(
        "5v runs",
        "SELECT COUNT(*) FROM details_protocol WHERE user_id IS NOT NULL AND TRIM(user_id) <> ''",
    ),
    CountCheck(
        "5v volunteers",
        "SELECT COUNT(*) FROM details_vol WHERE user_id IS NOT NULL AND TRIM(user_id) <> ''",
    ),
    CountCheck(
        "5v events (list_all_events)",
        "SELECT COUNT(*) FROM list_all_events WHERE date_event IS NOT NULL",
    ),
    CountCheck(
        "5v locations",
        "SELECT COUNT(*) FROM general_location WHERE link_point IS NOT NULL",
    ),
]

PARKRUN_CHECKS = [
    CountCheck(
        "parkrun runs",
        """
        SELECT COUNT(*) FROM parkrun_details_protocol
        WHERE user_id IS NOT NULL AND TRIM(user_id::text) <> ''
        """,
    ),
    CountCheck(
        "parkrun vol summary",
        """
        SELECT COUNT(*) FROM parkrun_vol_summary
        WHERE user_id IS NOT NULL AND count_vol > 0
        """,
    ),
    CountCheck(
        "parkrun users",
        "SELECT COUNT(*) FROM parkrun_users WHERE user_id IS NOT NULL",
    ),
]

S95_CHECKS = [
    CountCheck(
        "s95 locations",
        "SELECT COUNT(*) FROM s95_location WHERE link_point IS NOT NULL",
    ),
    CountCheck(
        "s95 events",
        "SELECT COUNT(*) FROM s95_list_all_events WHERE date_event IS NOT NULL",
    ),
    CountCheck(
        "s95 runs",
        """
        SELECT COUNT(*) FROM s95_details_protocol
        WHERE user_id IS NOT NULL AND TRIM(user_id) <> ''
           OR status_runner = 'unknown_runner'
        """,
    ),
    CountCheck(
        "s95 volunteers",
        """
        SELECT COUNT(*) FROM s95_details_vol
        WHERE user_id IS NOT NULL AND TRIM(user_id) <> ''
          AND vol_role IS NOT NULL AND TRIM(vol_role) <> ''
        """,
    ),
    CountCheck(
        "s95 runners",
        "SELECT COUNT(*) FROM s95_runners WHERE s95_id IS NOT NULL AND TRIM(s95_id) <> ''",
    ),
]


def _target_run_count(db: Session, platform_code: str) -> int:
    platform = upsert.get_platform(db, platform_code)
    return (
        db.query(RunResult)
        .join(Event, RunResult.event_id == Event.id)
        .filter(Event.platform_id == platform.id)
        .count()
    )


def _target_vol_count(db: Session, platform_code: str) -> int:
    platform = upsert.get_platform(db, platform_code)
    return (
        db.query(VolunteerResult)
        .join(Event, VolunteerResult.event_id == Event.id)
        .filter(Event.platform_id == platform.id)
        .count()
    )


def _target_event_count(db: Session, platform_code: str) -> int:
    platform = upsert.get_platform(db, platform_code)
    return db.query(Event).filter(Event.platform_id == platform.id).count()


def _target_location_count(db: Session, platform_code: str) -> int:
    platform = upsert.get_platform(db, platform_code)
    from app.models import Location

    return db.query(Location).filter(Location.platform_id == platform.id).count()


def validate_platform(db: Session, platform: str) -> list[dict[str, object]]:
    engine = get_legacy_engine()
    rows: list[dict[str, object]] = []
    try:
        with engine.connect() as conn:
            if platform == "five_verst":
                checks = FIVE_VERST_CHECKS
                target_fns = {
                    "5v runs": lambda: _target_run_count(db, "five_verst"),
                    "5v volunteers": lambda: _target_vol_count(db, "five_verst"),
                    "5v events (list_all_events)": lambda: _target_event_count(db, "five_verst"),
                    "5v locations": lambda: _target_location_count(db, "five_verst"),
                }
            elif platform == "parkrun":
                checks = PARKRUN_CHECKS
                target_fns = {
                    "parkrun runs": lambda: _target_run_count(db, "parkrun"),
                    "parkrun vol summary": lambda: _target_vol_count(db, "parkrun"),
                    "parkrun users": lambda: _target_participant_count(db, "parkrun"),
                }
            elif platform == "s95":
                checks = S95_CHECKS
                target_fns = {
                    "s95 locations": lambda: _target_location_count(db, "s95"),
                    "s95 events": lambda: _target_event_count(db, "s95"),
                    "s95 runs": lambda: _target_run_count(db, "s95"),
                    "s95 volunteers": lambda: _target_vol_count(db, "s95"),
                    "s95 runners": lambda: _target_participant_count(db, "s95"),
                }
            else:
                raise ValueError(f"Unknown platform for validate: {platform}")

            for check in checks:
                legacy_count = int(legacy_scalar(conn, check.legacy_sql) or 0)
                target_count = target_fns[check.label]()
                rows.append(
                    {
                        "label": check.label,
                        "legacy": legacy_count,
                        "target": target_count,
                        "delta": target_count - legacy_count,
                        "ok": target_count >= legacy_count,
                    }
                )
    finally:
        engine.dispose()
    return rows


def _target_participant_count(db: Session, platform_code: str) -> int:
    from app.models import Participant

    platform = upsert.get_platform(db, platform_code)
    return db.query(Participant).filter(Participant.platform_id == platform.id).count()


def validate_all(db: Session) -> dict[str, list[dict[str, object]]]:
    return {
        "five_verst": validate_platform(db, "five_verst"),
        "parkrun": validate_platform(db, "parkrun"),
        "s95": validate_platform(db, "s95"),
    }
