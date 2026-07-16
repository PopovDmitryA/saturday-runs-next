"""User personal on/off toggle for «Моя история» milestone kinds.

Persist a per-user list of milestone kinds the user has hidden from their own
timeline. Mirrors the admin-level history_milestone_settings, but per user and
stored as a JSONB array on users (list of disabled kind strings). Semantics:
kind absent from the list = enabled (default). Empty array = everything on.

Revision ID: 047_user_history_disabled_kinds
Revises: 046_run_result_pr_levels
Create Date: 2026-07-15
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "047_user_history_disabled_kinds"
down_revision = "046_run_result_pr_levels"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "history_disabled_kinds",
            postgresql.JSONB(),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "history_disabled_kinds")
