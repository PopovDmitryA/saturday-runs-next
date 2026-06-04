"""telegram message ids for coordinate reply threading

Revision ID: 007_coord_reply
Revises: 006_loc_coords
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "007_coord_reply"
down_revision = "006_loc_coords"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "location_coordinate_requests",
        sa.Column("request_telegram_message_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "location_coordinate_requests",
        sa.Column("verify_telegram_message_id", sa.BigInteger(), nullable=True),
    )
    op.create_index(
        "ix_location_coord_req_request_msg",
        "location_coordinate_requests",
        ["request_telegram_message_id"],
    )
    op.create_index(
        "ix_location_coord_req_verify_msg",
        "location_coordinate_requests",
        ["verify_telegram_message_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_location_coord_req_verify_msg", table_name="location_coordinate_requests")
    op.drop_index("ix_location_coord_req_request_msg", table_name="location_coordinate_requests")
    op.drop_column("location_coordinate_requests", "verify_telegram_message_id")
    op.drop_column("location_coordinate_requests", "request_telegram_message_id")
