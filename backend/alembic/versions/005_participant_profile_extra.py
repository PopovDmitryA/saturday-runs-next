"""participant profile_extra jsonb

Revision ID: 005_participant_profile_extra
Revises: 004_s95_participant_barcode
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "005_participant_profile_extra"
down_revision = "004_s95_participant_barcode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "participants",
        sa.Column("profile_extra", JSONB, nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("participants", "profile_extra")
