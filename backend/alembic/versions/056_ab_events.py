"""AB-события главной: сырые события экспериментов (вариант, скролл, клики, конверсия)

Revision ID: 056_ab_events
Revises: 053_participant_gender
Create Date: 2026-07-25

ВНИМАНИЕ ПРИ МЕРЖЕ: на момент создания ветки в параллельных сессиях были
незакоммиченные миграции 054_user_avatar и 055_login_events. Если к моменту
мержа они уже в main — перецепить down_revision на актуальный head
(например "055_login_events"), иначе alembic получит два head и деплой упадёт.
"""

import sqlalchemy as sa

from alembic import op

revision = "056_ab_events"
down_revision = "053_participant_gender"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ab_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("experiment", sa.String(length=32), nullable=False),
        sa.Column("variant", sa.String(length=8), nullable=False),
        sa.Column("visitor_key", sa.String(length=80), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("value", sa.String(length=128), server_default="", nullable=False),
        sa.Column("path", sa.String(length=256), server_default="", nullable=False),
        sa.Column("cohort", sa.String(length=16), server_default="", nullable=False),
        sa.Column(
            "viewer_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
    )
    op.create_index("ix_ab_events_experiment_ts", "ab_events", ["experiment", "ts"])
    op.create_index("ix_ab_events_visitor", "ab_events", ["visitor_key"])


def downgrade() -> None:
    op.drop_index("ix_ab_events_visitor", table_name="ab_events")
    op.drop_index("ix_ab_events_experiment_ts", table_name="ab_events")
    op.drop_table("ab_events")
