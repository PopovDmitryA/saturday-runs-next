"""Add users.home_location_changed_at (когда человек менял домашнюю локацию)

Таблица рейтинга дальности живёт в Redis-снапшоте с TTL 6 часов, а «моя» строка
считается вживую. Из-за этого сразу после смены дома человек видит в таблице
километры от прежней площадки и считает это ошибкой. Отметка времени даёт
рейтингу возможность сказать честно: «вы сменили дом тогда-то, таблица
пересчитается в течение стольких-то часов».

NULL — домашнюю локацию руками не трогали (или трогали до этой миграции).

Revision ID: 061_home_loc_changed_at
Revises: 060_site_releases
Create Date: 2026-08-07
"""

import sqlalchemy as sa

from alembic import op

revision = "061_home_loc_changed_at"
down_revision = "060_site_releases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("home_location_changed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "home_location_changed_at")
