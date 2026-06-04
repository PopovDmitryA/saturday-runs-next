from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.models import ProfileFetchPendingOperation
from app.services.profile_fetch_pending_service import ensure_parkrun_pending_queue_row


@dataclass(frozen=True)
class LegacyParkrunAccount:
    athlete_id: str
    display_name: str | None


# Fast path: Telegram users with parkrun binding (49 rows in legacy audit).
LEGACY_PARKRUN_TG_SQL = """
SELECT
    t.parkrun_user_id::text AS athlete_id,
    COALESCE(NULLIF(TRIM(u.actual_name_runner), ''), NULLIF(TRIM(u.name_runner), '')) AS display_name
FROM tg_user_profile t
LEFT JOIN parkrun_users u ON u.user_id::text = t.parkrun_user_id::text
WHERE t.parkrun_user_id IS NOT NULL
  AND t.parkrun_user_id::text <> ''
ORDER BY t.parkrun_user_id
LIMIT :limit
"""

# Slower: any profile with runs, sorted by last run date.
LEGACY_PARKRUN_ACTIVE_SQL = """
SELECT
    pu.user_id::text AS athlete_id,
    COALESCE(NULLIF(TRIM(pu.actual_name_runner), ''), NULLIF(TRIM(pu.name_runner), '')) AS display_name
FROM parkrun_users pu
WHERE pu.user_id IS NOT NULL
  AND EXISTS (
      SELECT 1 FROM parkrun_details_protocol p WHERE p.user_id = pu.user_id
  )
ORDER BY (
    SELECT MAX(p.date_event) FROM parkrun_details_protocol p WHERE p.user_id = pu.user_id
) DESC NULLS LAST
LIMIT :limit
"""


def legacy_database_url() -> str:
    url = os.environ.get("LEGACY_DATABASE_URL", "").strip()
    if url:
        return url
    # Same Postgres host, legacy DB name (local dump / tunnel).
    main = os.environ.get("DATABASE_URL", "").strip()
    if main and "saturday_runs_lk" in main:
        return main.replace("saturday_runs_lk", "five_verst_stats")
    return ""


def fetch_parkrun_accounts_from_legacy(
    *,
    limit: int = 5,
    source: str = "tg_bindings",
) -> list[LegacyParkrunAccount]:
    url = legacy_database_url()
    if not url:
        raise RuntimeError(
            "Задайте LEGACY_DATABASE_URL (read-only) или DATABASE_URL с БД saturday_runs_lk "
            "— тогда будет использована five_verst_stats на том же хосте."
        )
    sql = LEGACY_PARKRUN_TG_SQL if source == "tg_bindings" else LEGACY_PARKRUN_ACTIVE_SQL
    engine = create_engine(url, pool_pre_ping=True)
    try:
        return _load_accounts(engine, limit=limit, sql=sql)
    finally:
        engine.dispose()


def _load_accounts(engine: Engine, *, limit: int, sql: str) -> list[LegacyParkrunAccount]:
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"limit": limit}).mappings().all()
    accounts: list[LegacyParkrunAccount] = []
    for row in rows:
        athlete_id = str(row["athlete_id"]).strip()
        if not athlete_id:
            continue
        accounts.append(
            LegacyParkrunAccount(
                athlete_id=athlete_id,
                display_name=(row.get("display_name") or None),
            )
        )
    if not accounts:
        raise RuntimeError("В legacy parkrun_users не найдено профилей с пробежками.")
    return accounts


def seed_parkrun_queue_from_legacy(
    db: Session,
    accounts: list[LegacyParkrunAccount],
    *,
    operation: ProfileFetchPendingOperation = ProfileFetchPendingOperation.activity_import,
) -> list[str]:
    labels: list[str] = []
    for account in accounts:
        row = ensure_parkrun_pending_queue_row(
            db,
            account.athlete_id,
            operation=operation,
            note="seed from legacy parkrun_users",
        )
        label = account.display_name or account.athlete_id
        labels.append(f"{row.external_user_id or account.athlete_id} ({label})")
    db.commit()
    return labels
