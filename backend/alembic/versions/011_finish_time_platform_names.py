"""Normalize finish_time_display and platform display names."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "011_finish_time_names"
down_revision = "010_display_custom"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.time_format import normalize_finish_time_display

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT id, finish_time_sec, finish_time_display
            FROM run_results
            WHERE finish_time_sec IS NOT NULL
               OR finish_time_display IS NOT NULL
            """
        )
    ).fetchall()

    for row in rows:
        normalized = normalize_finish_time_display(row.finish_time_sec, row.finish_time_display)
        if normalized != row.finish_time_display:
            bind.execute(
                sa.text(
                    "UPDATE run_results SET finish_time_display = :display WHERE id = :id"
                ),
                {"display": normalized, "id": row.id},
            )

    bind.execute(
        sa.text("UPDATE platforms SET name = 'с95' WHERE code = 's95'")
    )
    bind.execute(
        sa.text("UPDATE platforms SET name = '5 вёрст' WHERE code = 'five_verst'")
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("UPDATE platforms SET name = 'S95' WHERE code = 's95'"))
