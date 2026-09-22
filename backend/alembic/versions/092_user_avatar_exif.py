"""Метаданные аватарки — у себя, файл в бакете — чистый

Аудит 09.2026, находка SEC-04. Оригинал аватарки клали в ПУБЛИЧНЫЙ бакет
байт-в-байт, вместе с EXIF снимка: координаты съёмки (обычно дом), модель
телефона, время. Прочитать их мог любой, кто открыл картинку по прямой
ссылке. Решение Дмитрия 21.09.2026: теги хранить у себя (приватная колонка,
наружу через API не отдаётся), а в хранилище класть файл без метаданных.

avatar_exif — словарь {datetime_original, make, model, software, orientation,
gps: {lat, lon, alt}}; отсутствующие теги не пишутся, NULL — аватарки нет или
у снимка не было EXIF.

Revision ID: 092_user_avatar_exif
Revises: 091_ratings_survive_resync
Create Date: 2026-09-21
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "092_user_avatar_exif"
down_revision = "091_ratings_survive_resync"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("avatar_exif", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "avatar_exif")
