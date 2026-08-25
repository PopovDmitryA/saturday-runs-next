"""Имя пользователя — из профилей беговых систем, а не из провайдера входа

До сих пор users.display_name заполнялся от VK/Яндекса/Telegram, и на разборе
прод-БД это оказались в основном логины: 121 из 489 привязанных пользователей
показывались как `m4rtynovadian`, `a.kor90`, `leo1973@spartak.ru`, причём 107 из
них имя ни разу не трогали. Свободный ввод имени работал костылём к неудачному
источнику по умолчанию: из 79 ручных правок 55 — это люди, вписавшие своё же ФИО.

Теперь имя считается из профилей (см. app.services.user_display_name_service), а
вместо свободного ввода — выбор из готовых вариантов.

Источник имени ФИКСИРУЕТСЯ (display_name_platform_id) и пересматривается только
при привязке/отвязке профиля, а не при каждом фоновом пересчёте: иначе имя
человека могло бы меняться само по себе хоть каждый день. Если фоновый пересчёт
считает, что лучше подошла бы другая система, имя не меняется — человеку
показывается баннер со ссылкой в настройки (display_name_dismissed_name помнит
отклонённое предложение, чтобы не звать второй раз с тем же).

Эта миграция добавляет поля для такого выбора; сами имена проставляет отдельный скрипт
scripts/backfill_display_names.py — так прогон можно сначала посмотреть
с --dry-run и повторить, не откатывая схему.

display_name_customized не удаляется: прежние ручные значения остаются архивом
на случай отката.

Revision ID: 066_display_name_from_profile
Revises: 065_user_geo_pings
Create Date: 2026-08-25
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from alembic import op

revision = "066_display_name_from_profile"
down_revision = "065_user_geo_pings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "display_name_style",
            sa.String(length=16),
            nullable=False,
            server_default="auto",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "display_name_platform_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("platforms.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "display_name_source_manual",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "users",
        sa.Column("display_name_notice", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("display_name_dismissed_name", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "display_name_dismissed_name")
    op.drop_column("users", "display_name_notice")
    op.drop_column("users", "display_name_source_manual")
    op.drop_column("users", "display_name_platform_id")
    op.drop_column("users", "display_name_style")
