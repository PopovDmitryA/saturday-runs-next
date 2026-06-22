"""Activate RunPark platform for data import"""

from __future__ import annotations

from alembic import op

revision = "028_runpark_activate_platform"
down_revision = "027_user_profile_private"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE platforms SET is_active = true WHERE code = 'runpark'")


def downgrade() -> None:
    op.execute("UPDATE platforms SET is_active = false WHERE code = 'runpark'")
