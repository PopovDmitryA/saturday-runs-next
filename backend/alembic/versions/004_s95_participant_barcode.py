"""participant barcode fields and activate s95 platform

Revision ID: 004_s95_participant_barcode
Revises: 003_is_test_event
Create Date: 2026-05-25

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "004_s95_participant_barcode"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("participants", sa.Column("barcode_id", sa.String(length=16), nullable=True))
    op.add_column("participants", sa.Column("planning_location", sa.String(length=256), nullable=True))
    op.create_index("ix_participants_barcode_id", "participants", ["barcode_id"], unique=False)
    op.execute(
        sa.text("UPDATE platforms SET is_active = true WHERE code = 's95'")
    )


def downgrade() -> None:
    op.execute(
        sa.text("UPDATE platforms SET is_active = false WHERE code = 's95'")
    )
    op.drop_index("ix_participants_barcode_id", table_name="participants")
    op.drop_column("participants", "planning_location")
    op.drop_column("participants", "barcode_id")
