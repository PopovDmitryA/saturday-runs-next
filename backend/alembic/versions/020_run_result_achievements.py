"""Add 5verst protocol achievements and club to run_results."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "020_run_result_achievements"
down_revision = "019_location_merge_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "run_results",
        sa.Column("is_first_run", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "run_results",
        sa.Column("is_first_run_at_location", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("run_results", sa.Column("club_name", sa.String(length=256), nullable=True))
    op.add_column(
        "run_results",
        sa.Column("achievement_labels", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("run_results", "achievement_labels")
    op.drop_column("run_results", "club_name")
    op.drop_column("run_results", "is_first_run_at_location")
    op.drop_column("run_results", "is_first_run")
