"""unique event per platform location and date

Revision ID: 009_events_loc_date
Revises: 008_display_parkrun
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "009_events_loc_date"
down_revision = "008_display_parkrun"
branch_labels = None
depends_on = None

_MERGE_DUPLICATE_EVENTS = """
WITH ranked AS (
    SELECT
        id,
        platform_id,
        location_id,
        event_date,
        ROW_NUMBER() OVER (
            PARTITION BY platform_id, location_id, event_date
            ORDER BY created_at ASC NULLS LAST, id::text ASC
        ) AS rn
    FROM events
),
keepers AS (
    SELECT platform_id, location_id, event_date, id AS keep_id
    FROM ranked
    WHERE rn = 1
),
drop_map AS (
    SELECT r.id AS drop_id, k.keep_id
    FROM ranked r
    JOIN keepers k
      ON r.platform_id = k.platform_id
     AND r.location_id = k.location_id
     AND r.event_date = k.event_date
    WHERE r.rn > 1
)
"""


def upgrade() -> None:
    op.execute(
        sa.text(
            _MERGE_DUPLICATE_EVENTS
            + """
            UPDATE run_results rr
            SET event_id = dm.keep_id
            FROM drop_map dm
            WHERE rr.event_id = dm.drop_id
            """
        )
    )
    op.execute(
        sa.text(
            _MERGE_DUPLICATE_EVENTS
            + """
            UPDATE volunteer_results vr
            SET event_id = dm.keep_id
            FROM drop_map dm
            WHERE vr.event_id = dm.drop_id
            """
        )
    )
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY platform_id, location_id, event_date
                        ORDER BY created_at ASC NULLS LAST, id::text ASC
                    ) AS rn
                FROM events
            )
            DELETE FROM events e
            USING ranked r
            WHERE e.id = r.id AND r.rn > 1
            """
        )
    )
    op.create_index(
        "uq_events_platform_location_event_date",
        "events",
        ["platform_id", "location_id", "event_date"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_events_platform_location_event_date", table_name="events")
