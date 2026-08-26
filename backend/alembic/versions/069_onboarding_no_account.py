"""Онбординг: отметка «в этой системе у меня нет аккаунта»

Revision ID: 069_onboarding_no_account
Revises: 068_onboarding_name_search
Create Date: 2026-08-25
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "069_onboarding_no_account"
down_revision = "068_onboarding_name_search"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "onboarding_no_account_platforms",
            JSONB(),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "onboarding_no_account_platforms")
