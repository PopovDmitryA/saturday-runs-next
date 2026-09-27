"""Воронка «Это вы?» из поиска по сайту

Строки людей из протоколов в поиске стали нажимаемыми: человек нажимает свою,
входит и привязывает именно её. Чтобы видеть, где он сходит с пути (нажал, но
не вошёл; вошёл, но привязка не прошла), сервер пишет этапы по токену строки.
Анонимно, как search_query_log: ни пользователя, ни IP, ни участника — только
ref (случайная часть токена), связывающий этапы одного захода.

Revision ID: 104_search_claim_events
Revises: 103_participant_name_fold_trgm
Create Date: 2026-09-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "104_search_claim_events"
down_revision = "103_participant_name_fold_trgm"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "search_claim_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("ref", sa.String(length=24), nullable=False),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("is_authed", sa.Boolean(), nullable=False),
        sa.Column("platform_code", sa.String(length=16), nullable=True),
        sa.Column("status", sa.SmallInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_search_claim_events_created_at", "search_claim_events", ["created_at"])
    op.create_index("ix_search_claim_events_ref", "search_claim_events", ["ref"])


def downgrade() -> None:
    op.drop_index("ix_search_claim_events_ref", table_name="search_claim_events")
    op.drop_index("ix_search_claim_events_created_at", table_name="search_claim_events")
    op.drop_table("search_claim_events")
