"""Add per-km pace to run_results."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "013_run_result_pace"
down_revision = "012_user_auto_sync"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("run_results")}
    if "pace_sec_per_km" not in columns:
        op.add_column("run_results", sa.Column("pace_sec_per_km", sa.Integer(), nullable=True))
    if "pace_display" not in columns:
        op.add_column("run_results", sa.Column("pace_display", sa.String(length=16), nullable=True))

    from app.pace import resolve_run_pace

    rows = bind.execute(
        sa.text(
            """
            SELECT id, finish_time_sec, pace_sec_per_km, pace_display
            FROM run_results
            WHERE finish_time_sec IS NOT NULL
            """
        )
    ).fetchall()

    for row in rows:
        pace_sec, pace_display = resolve_run_pace(row.pace_sec_per_km, row.pace_display, row.finish_time_sec)
        if pace_sec is None:
            continue
        bind.execute(
            sa.text(
                """
                UPDATE run_results
                SET pace_sec_per_km = :pace_sec, pace_display = :pace_display
                WHERE id = :id
                """
            ),
            {"pace_sec": pace_sec, "pace_display": pace_display, "id": row.id},
        )


def downgrade() -> None:
    op.drop_column("run_results", "pace_display")
    op.drop_column("run_results", "pace_sec_per_km")
