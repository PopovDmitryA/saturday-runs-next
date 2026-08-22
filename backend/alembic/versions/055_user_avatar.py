"""users.avatar_path — имя файла аватарки в хранилище на диске

Сам файл лежит в settings.avatars_dir (том ./data:/data), в БД — только имя
вида "{user_id}-{token}.jpg". Токен случайный: при замене аватарки имя меняется,
что ломает браузерный кэш старой картинки, а прежний файл удаляется с диска.

Revision ID: 055_user_avatar
Revises: 054_login_events
Create Date: 2026-07-25
"""

import sqlalchemy as sa

from alembic import op

revision = "055_user_avatar"
down_revision = "054_login_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("avatar_path", sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "avatar_path")
