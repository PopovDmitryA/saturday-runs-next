"""Погода на стартах: источник строки — архив или прогнозная модель

Субботним вечером архив Open-Meteo субботу ещё не отдаёт (отстаёт на ~2 суток),
а страница «последней пробежки» нужна именно в субботу. Поэтому в 17:00 МСК
субботы снимаем предварительную погоду из прогнозной модели (source =
'forecast'), а ночной архивный прогон в понедельник перезаписывает такие
строки окончательным реанализом (source = 'archive'). Колонка нужна, чтобы
сборщик знал, какие строки предварительные и подлежат замене.

Revision ID: 087_start_weather_source
Revises: 086_admin_resync_requests
Create Date: 2026-09-14
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "087_start_weather_source"
down_revision = "086_admin_resync_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "start_weather",
        sa.Column("source", sa.String(length=16), nullable=False, server_default="archive"),
    )


def downgrade() -> None:
    op.drop_column("start_weather", "source")
