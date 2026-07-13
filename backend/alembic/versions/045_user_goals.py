"""Add user_goals (цели на год)

Пользователь выбирает цели на календарный год из готовых пресетов (goal_type —
код пресета, без свободного текста). target_value — числовая планка цели в
единицах пресета: количество пробежек/волонтёрств/локаций/рекордов, секунды
для целей на время, проценты для регулярности. Прогресс не храним — он
считается на лету из run_results/volunteer_results.

Revision ID: 045_user_goals
Revises: 044_history_milestone_settings
Create Date: 2026-07-12
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import func

revision = "045_user_goals"
down_revision = "044_history_milestone_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_goals",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=func.gen_random_uuid(),
        ),
        sa.Column("user_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("goal_type", sa.String(length=64), nullable=False),
        sa.Column("target_value", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=func.now(),
            onupdate=func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "year", "goal_type", name="uq_user_goals_user_year_type"),
        sa.CheckConstraint("target_value > 0", name="ck_user_goals_target_positive"),
    )
    op.create_index("ix_user_goals_user_year", "user_goals", ["user_id", "year"])


def downgrade() -> None:
    op.drop_index("ix_user_goals_user_year", table_name="user_goals")
    op.drop_table("user_goals")
