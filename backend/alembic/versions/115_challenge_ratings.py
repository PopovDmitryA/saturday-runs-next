"""Рейтинги челленджей: готовые снимки

p-индекс, V-индекс, Уилсон и ещё шесть челленджей получили рейтинги по всем
участникам протоколов. Посчитать всех — несколько минут одним проходом, в
запросе так не сделать, поэтому сетку «челлендж × вариант × система × активность» пересчитывает фоновая
задача и кладёт сюда целиком (app/services/challenge_rating_service.py).

Ссылок на users нет: строки «Вы» лежат в JSONB по строковому user_id и
пропадают со следующим пересчётом, как и у остальных кэшей рейтингов.

Revision ID: 115_challenge_ratings
Revises: 114_test_event_crosslinks
Create Date: 2026-10-26
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "115_challenge_ratings"
down_revision = "114_test_event_crosslinks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "challenge_rating_snapshots",
        sa.Column("code", sa.String(length=32), primary_key=True),
        sa.Column("variant", sa.String(length=16), primary_key=True),
        sa.Column("platform", sa.String(length=16), primary_key=True),
        sa.Column("activity", sa.String(length=8), primary_key=True),
        sa.Column("rows", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("site_rows", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("entrants", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latest_event_date", sa.Date(), nullable=True),
        sa.Column("week_start", sa.Date(), nullable=True),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("challenge_rating_snapshots")
