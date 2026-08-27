"""Способ привязки профиля: поиск, ссылка, тизер главной

Нужен, чтобы после релиза онбординга (/welcome с поиском по ФИО) можно было
измерить, каким путём люди реально привязывают аккаунты. Задним числом способ
не восстановить — поэтому колонка заводится вместе с релизом, а всё, что
привязано до неё, помечается 'legacy'.

Revision ID: 073_platform_link_method
Revises: 072_protocol_watch
Create Date: 2026-08-25
"""

import sqlalchemy as sa

from alembic import op

revision = "073_platform_link_method"
down_revision = "072_protocol_watch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "platform_links",
        sa.Column(
            "link_method",
            sa.String(length=16),
            nullable=False,
            server_default="legacy",
        ),
    )
    op.create_index("ix_platform_links_link_method", "platform_links", ["link_method"])


def downgrade() -> None:
    op.drop_index("ix_platform_links_link_method", table_name="platform_links")
    op.drop_column("platform_links", "link_method")
