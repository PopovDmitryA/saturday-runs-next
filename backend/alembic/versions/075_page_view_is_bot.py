"""Метка робота на сыром событии просмотра

Фильтр по user-agent (075) отсекает краулеров на входе, но в уже накопленных
page_view_events они лежат вперемешку с людьми, а user-agent мы никогда не
хранили — распознать их задним числом можно только по поведению. Поэтому не
удаляем, а помечаем: разметку видно, её можно проверить и откатить, а дневные
агрегаты пересобираются уже без помеченных строк.

Revision ID: 075_page_view_is_bot
Revises: 074_location_cancel_reason
Create Date: 2026-08-31
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "075_page_view_is_bot"
down_revision = "074_location_cancel_reason"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "page_view_events",
        sa.Column("is_bot", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Пересборка дня фильтрует по is_bot, а чистка ботов ищет кандидатов по
    # visitor_key — частичный индекс держит и то, и другое дешёвым.
    op.create_index(
        "ix_page_view_events_is_bot",
        "page_view_events",
        ["is_bot"],
        postgresql_where=sa.text("is_bot"),
    )


def downgrade() -> None:
    op.drop_index("ix_page_view_events_is_bot", table_name="page_view_events")
    op.drop_column("page_view_events", "is_bot")
