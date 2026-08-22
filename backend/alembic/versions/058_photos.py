"""Фото в отзывах на локации и в карточках бэклога.

Revision ID: 058_photos
Revises: 057_ab_events
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "058_photos"
down_revision = "057_ab_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "location_rating_photos",
        sa.Column("id", sa.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("rating_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["rating_id"], ["location_ratings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_location_rating_photos_rating", "location_rating_photos", ["rating_id", "sort_order"]
    )

    op.create_table(
        "backlog_card_photos",
        sa.Column("id", sa.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("card_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["card_id"], ["backlog_cards.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_backlog_card_photos_card", "backlog_card_photos", ["card_id", "sort_order"])


def downgrade() -> None:
    op.drop_index("ix_backlog_card_photos_card", table_name="backlog_card_photos")
    op.drop_table("backlog_card_photos")
    op.drop_index("ix_location_rating_photos_rating", table_name="location_rating_photos")
    op.drop_table("location_rating_photos")
