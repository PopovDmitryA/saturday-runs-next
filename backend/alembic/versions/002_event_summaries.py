"""Add event_summaries table for lightweight protocol diff sync."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

sync_status_enum = postgresql.ENUM(
    "pending", "ok", "error", "unchanged", name="sync_status_enum", create_type=False
)


def upgrade() -> None:
    op.create_table(
        "event_summaries",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("platform_id", sa.UUID(), nullable=False),
        sa.Column("location_id", sa.UUID(), nullable=False),
        sa.Column("event_id", sa.UUID(), nullable=True),
        sa.Column("external_event_key", sa.String(length=512), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("event_number", sa.Integer(), nullable=True),
        sa.Column("finishers_count", sa.Integer(), nullable=True),
        sa.Column("volunteers_count", sa.Integer(), nullable=True),
        sa.Column("avg_time_sec", sa.Integer(), nullable=True),
        sa.Column("avg_time_display", sa.String(length=32), nullable=True),
        sa.Column("best_female_time_sec", sa.Integer(), nullable=True),
        sa.Column("best_female_time_display", sa.String(length=32), nullable=True),
        sa.Column("best_male_time_sec", sa.Integer(), nullable=True),
        sa.Column("best_male_time_display", sa.String(length=32), nullable=True),
        sa.Column("summary_hash", sa.String(length=64), nullable=False),
        sa.Column("source_url", sa.String(length=1024), nullable=True),
        sa.Column("parser_version", sa.String(length=32), nullable=True),
        sa.Column("source_hash", sa.String(length=64), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_status", sync_status_enum, nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"]),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"]),
        sa.ForeignKeyConstraint(["platform_id"], ["platforms.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("platform_id", "external_event_key", name="uq_event_summaries_platform_external_key"),
    )
    op.create_index("ix_event_summaries_location_event_date", "event_summaries", ["location_id", "event_date"])
    op.create_index("ix_event_summaries_summary_hash", "event_summaries", ["summary_hash"])


def downgrade() -> None:
    op.drop_index("ix_event_summaries_summary_hash", table_name="event_summaries")
    op.drop_index("ix_event_summaries_location_event_date", table_name="event_summaries")
    op.drop_table("event_summaries")
