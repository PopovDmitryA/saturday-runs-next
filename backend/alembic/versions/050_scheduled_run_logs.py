"""Scheduled run logs: история запусков задач автообновления

Revision ID: 050_scheduled_run_logs
Revises: 049_page_analytics
Create Date: 2026-07-18
"""

import sqlalchemy as sa
from alembic import op

revision = "050_scheduled_run_logs"
down_revision = "049_page_analytics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scheduled_run_logs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("pipeline", sa.String(length=128), nullable=False),
        sa.Column("platform", sa.String(length=32), server_default="other", nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("records_updated", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("skip_reason", sa.String(length=64)),
        sa.Column("metrics", sa.dialects.postgresql.JSONB()),
        sa.Column("errors", sa.dialects.postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_scheduled_run_logs_started_at", "scheduled_run_logs", ["started_at"])
    op.create_index(
        "ix_scheduled_run_logs_platform_started_at",
        "scheduled_run_logs",
        ["platform", "started_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_scheduled_run_logs_platform_started_at", table_name="scheduled_run_logs")
    op.drop_index("ix_scheduled_run_logs_started_at", table_name="scheduled_run_logs")
    op.drop_table("scheduled_run_logs")
