"""Уведомления об отменах стартов: выбор мест

Отмены рассылались по всей стране с фильтром только по системам, и людям
приходили отмены в городах, куда они не собираются. Теперь можно выбрать
конкретные локации и целиком города, регионы или страны; ни одной строки —
по-прежнему вся страна (умолчание, ничего у подписанных не меняется).

Отдельная таблица, а не JSONB в user_notification_prefs: ключ локации — ключ
узла каталога (`catalog:<id>`), и импорт каталога должен видеть ссылки на
узел обычным запросом (CATALOG_KEY_REFERENCES).

Revision ID: 111_cancellation_places
Revises: 110_location_status_override
Create Date: 2026-10-21
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "111_cancellation_places"
down_revision = "110_location_status_override"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_cancellation_places",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("place_key", sa.String(length=160), primary_key=True),
        sa.Column("label", sa.String(length=256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_user_cancellation_places_key", "user_cancellation_places", ["place_key"])


def downgrade() -> None:
    op.drop_index("ix_user_cancellation_places_key", table_name="user_cancellation_places")
    op.drop_table("user_cancellation_places")
