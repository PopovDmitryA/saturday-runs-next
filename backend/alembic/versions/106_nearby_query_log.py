"""Журнал «где ищут старт» для бота

Бот отвечает на геопозицию списком ближайших локаций. Каждый такой запрос
пишется строкой — анонимно, с точкой, огрублённой до клетки ~5 км, — чтобы
в админке видеть белые пятна: места, где ищут субботний старт, а ближайшая
локация за десятки километров.

Revision ID: 106_nearby_query_log
Revises: 105_search_claim_events
Create Date: 2026-10-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "106_nearby_query_log"
down_revision = "105_search_claim_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nearby_query_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("source", sa.String(length=8), nullable=False),
        sa.Column("cell_latitude", sa.Float(), nullable=False),
        sa.Column("cell_longitude", sa.Float(), nullable=False),
        sa.Column("nearest_identity_key", sa.String(length=64), nullable=True),
        sa.Column("nearest_distance_km", sa.Float(), nullable=True),
        sa.Column("within_radius", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("is_linked", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("place_label", sa.String(length=200), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_nearby_query_log_created_at", "nearby_query_log", ["created_at"])
    op.create_index("ix_nearby_query_log_cell", "nearby_query_log", ["cell_latitude", "cell_longitude"])


def downgrade() -> None:
    op.drop_index("ix_nearby_query_log_cell", table_name="nearby_query_log")
    op.drop_index("ix_nearby_query_log_created_at", table_name="nearby_query_log")
    op.drop_table("nearby_query_log")
