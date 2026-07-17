"""Page analytics: raw pageview events + daily aggregates

Revision ID: 049_page_analytics
Revises: 048_location_contacts
Create Date: 2026-07-17
"""

import sqlalchemy as sa
from alembic import op

revision = "049_page_analytics"
down_revision = "048_location_contacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "page_view_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("view_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("path", sa.String(length=256), nullable=False),
        sa.Column("page_type", sa.String(length=32), nullable=False),
        sa.Column("entity_key", sa.String(length=128), server_default="", nullable=False),
        sa.Column("visitor_key", sa.String(length=80)),
        sa.Column(
            "viewer_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("is_self", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("duration_sec", sa.Integer()),
    )
    op.create_index("ix_page_view_events_ts", "page_view_events", ["ts"])

    op.create_table(
        "page_stats_daily",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("page_type", sa.String(length=32), nullable=False),
        sa.Column("entity_key", sa.String(length=128), server_default="", nullable=False),
        sa.Column("views", sa.Integer(), server_default="0", nullable=False),
        sa.Column("unique_viewers", sa.Integer(), server_default="0", nullable=False),
        sa.Column("self_views", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_duration_sec", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("duration_views", sa.Integer(), server_default="0", nullable=False),
        sa.UniqueConstraint("date", "page_type", "entity_key", name="uq_page_stats_daily_date_type_entity"),
    )
    op.create_index("ix_page_stats_daily_type_entity", "page_stats_daily", ["page_type", "entity_key"])


def downgrade() -> None:
    op.drop_index("ix_page_stats_daily_type_entity", table_name="page_stats_daily")
    op.drop_table("page_stats_daily")
    op.drop_index("ix_page_view_events_ts", table_name="page_view_events")
    op.drop_table("page_view_events")
