"""users.avatar_full_path — оригинал аватарки (открывается по клику).

Превью (avatar_path) остаётся сжатым квадратом 256×256 для интерфейса,
оригинал хранится как есть. Старые значения avatar_path (имя файла на диске,
без "/") продолжают работать — см. models._media_url.

Revision ID: 059_avatar_full
Revises: 058_photos
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "059_avatar_full"
down_revision = "058_photos"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("avatar_full_path", sa.String(length=512), nullable=True))
    # Ключ превью теперь длиннее имени файла ("avatars/2026/07/<32 hex>.jpg").
    op.alter_column(
        "users",
        "avatar_path",
        existing_type=sa.String(length=128),
        type_=sa.String(length=512),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "users",
        "avatar_path",
        existing_type=sa.String(length=512),
        type_=sa.String(length=128),
        existing_nullable=True,
    )
    op.drop_column("users", "avatar_full_path")
