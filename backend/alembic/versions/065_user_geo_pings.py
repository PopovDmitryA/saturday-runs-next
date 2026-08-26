"""Огрублённая геопозиция участника — по одной отметке в сутки

Карта умеет показывать «где я», если человек разрешил браузеру определять
положение. Эта таблица хранит след того же определения — чтобы понимать, в
каких городах есть участники, но нет площадки, и проверять, верно ли сайт
угадывает домашнюю локацию (она выбирается автоматически, по числу пробежек).

Точные координаты не хранятся: широта и долгота округляются до двух знаков —
клетка примерно километр на километр. На таком масштабе видно город и район,
но не дом и не работу, а для обоих вопросов выше этого достаточно.

Уникальная пара «участник + дата» держит обещание «не чаще раза в сутки»: с
ON CONFLICT DO NOTHING первая отметка дня остаётся, остальные не пишутся.

Revision ID: 065_user_geo_pings
Revises: 064_location_upcoming
Create Date: 2026-08-22
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from alembic import op

revision = "065_user_geo_pings"
down_revision = "064_location_upcoming"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_geo_pings",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("observed_on", sa.Date(), nullable=False),
        # Уже округлённые до двух знаков — точнее в базу не попадает.
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        # Погрешность самого определения, метры: у Wi-Fi и вышек она в километрах,
        # и такие отметки при разборе стоит отличать от честного GPS.
        sa.Column("accuracy_m", sa.Integer(), nullable=True),
        # Ближайшая площадка каталога и расстояние до неё: ради этих двух чисел
        # всё и заводится — по ним видно города без старта поблизости.
        sa.Column("nearest_identity_key", sa.String(length=128), nullable=True),
        sa.Column("nearest_distance_km", sa.Float(), nullable=True),
        # Домашняя локация на момент отметки и расстояние до неё: проверка того,
        # насколько верен автоматический выбор дома.
        sa.Column("home_identity_key", sa.String(length=128), nullable=True),
        sa.Column("home_distance_km", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", "observed_on", name="uq_user_geo_pings_user_day"),
    )
    op.create_index("ix_user_geo_pings_observed_on", "user_geo_pings", ["observed_on"])


def downgrade() -> None:
    op.drop_index("ix_user_geo_pings_observed_on", table_name="user_geo_pings")
    op.drop_table("user_geo_pings")
