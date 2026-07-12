"""Add history_milestone_settings table for admin on/off toggle per milestone kind

Revision ID: 044_history_milestone_settings
Revises: 043_profile_checked_at
Create Date: 2026-07-12
"""

import sqlalchemy as sa
from alembic import op

revision = "044_history_milestone_settings"
down_revision = "043_profile_checked_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "history_milestone_settings",
        sa.Column("kind", sa.String(length=64), primary_key=True),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("history_milestone_settings")
