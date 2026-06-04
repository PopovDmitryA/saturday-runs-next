"""Add protocol_sync_states for 5verst reconcile."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "021_protocol_sync_state"
down_revision = "020_run_result_achievements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "protocol_sync_states",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("event_summary_id", sa.UUID(), nullable=True),
        sa.Column("last_protocol_fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_protocol_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("protocol_source_hash", sa.String(length=64), nullable=True),
        sa.Column("finishers_at_fetch", sa.Integer(), nullable=True),
        sa.Column("run_results_count", sa.Integer(), nullable=True),
        sa.Column("volunteer_results_count", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["event_summary_id"], ["event_summaries.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_protocol_sync_states_event_id"),
    )
    op.create_index("ix_protocol_sync_states_last_check", "protocol_sync_states", ["last_protocol_check_at"])


def downgrade() -> None:
    op.drop_index("ix_protocol_sync_states_last_check", table_name="protocol_sync_states")
    op.drop_table("protocol_sync_states")
