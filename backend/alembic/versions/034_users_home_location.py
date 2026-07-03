"""Add home_location_key to users

Хранит catalog identity key («catalog:<uuid>» или «location:<uuid>») выбранной
пользователем домашней локации. NULL — домашняя локация определяется
автоматически по наибольшему числу пробежек.

Revision ID: 034_users_home_location
Revises: 033_run_results_gender_position
Create Date: 2026-07-03
"""

import sqlalchemy as sa
from alembic import op

revision = "034_users_home_location"
down_revision = "033_run_results_gender_position"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("home_location_key", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "home_location_key")
