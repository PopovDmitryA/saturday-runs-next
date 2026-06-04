"""location map_url and coordinate request workflow

Revision ID: 006_loc_coords
Revises: 005_participant_profile_extra
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "006_loc_coords"
down_revision = "005_participant_profile_extra"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("locations", sa.Column("map_url", sa.String(length=1024), nullable=True))

    op.create_table(
        "location_coordinate_requests",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("location_id", UUID(as_uuid=True), sa.ForeignKey("locations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("map_url", sa.String(length=1024), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="awaiting_coordinates",
        ),
        sa.Column("proposed_latitude", sa.Float(), nullable=True),
        sa.Column("proposed_longitude", sa.Float(), nullable=True),
        sa.Column("admin_telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index(
        "ix_location_coord_req_location_status",
        "location_coordinate_requests",
        ["location_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_location_coord_req_location_status", table_name="location_coordinate_requests")
    op.drop_table("location_coordinate_requests")
    op.drop_column("locations", "map_url")
