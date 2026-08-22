"""Add login_events (журнал входов и выходов пользователей)

Revision ID: 054_login_events
Revises: 053_participant_gender
Create Date: 2026-07-25
"""

import sqlalchemy as sa

from alembic import op

revision = "054_login_events"
down_revision = "053_participant_gender"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "login_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("event_type", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("session_ref", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("ip", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("user_agent", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("device_ref", sa.String(length=32), nullable=False, server_default=""),
    )
    op.create_index("ix_login_events_user_ts", "login_events", ["user_id", "ts"])
    op.create_index("ix_login_events_ts", "login_events", ["ts"])


def downgrade() -> None:
    op.drop_index("ix_login_events_ts", table_name="login_events")
    op.drop_index("ix_login_events_user_ts", table_name="login_events")
    op.drop_table("login_events")
