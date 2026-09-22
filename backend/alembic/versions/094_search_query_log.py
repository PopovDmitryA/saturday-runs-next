"""Журнал запросов поиска по сайту

Владелец проекта хочет видеть, что ищут люди и что не находят, — по этому
журналу решается, каких страниц и синонимов не хватает. Анонимно: ни
пользователя, ни посетителя, ни IP в таблице нет. Объём — единицы строк в
день, поэтому чистки нет, храним вечно.

Revision ID: 094_search_query_log
Revises: 093_drop_sync_log_entries
Create Date: 2026-09-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "094_search_query_log"
down_revision = "093_drop_sync_log_entries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "search_query_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("query", sa.String(length=100), nullable=False),
        sa.Column("corrected_query", sa.String(length=100), nullable=True),
        sa.Column("pages_found", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("locations_found", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("people_found", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("clicked_kind", sa.String(length=16), nullable=True),
        sa.Column("clicked_target", sa.String(length=200), nullable=True),
        sa.Column("is_authed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_mobile", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_search_query_log_created_at", "search_query_log", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_search_query_log_created_at", table_name="search_query_log")
    op.drop_table("search_query_log")
