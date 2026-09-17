"""Прогноз погоды на ближайший старт локации

Идея Дмитрия 17.09.2026: архив отвечает на вопрос «какая тут погода бывает», а
человек перед субботой хочет знать, что будет ЗАВТРА — и во что одеться.
Раз в сутки спрашиваем прогноз на ближайшую субботу по каждой локации
периметра и храним строку на локацию и дату старта.

Таблица отдельно от start_weather сознательно: там факт, который дозревает и
больше не меняется, здесь — предсказание, которое переписывается каждый день
и после старта становится ненужным (его чистит та же задача).

Revision ID: 089_start_weather_forecast
Revises: 088_location_series
Create Date: 2026-09-17
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "089_start_weather_forecast"
down_revision = "088_location_series"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "start_weather_forecast",
        sa.Column(
            "location_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("locations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("target_date", sa.Date(), primary_key=True),
        sa.Column("start_time_local", sa.Time(), nullable=False),
        sa.Column("temperature_c", sa.Numeric(5, 1)),
        sa.Column("apparent_temperature_c", sa.Numeric(5, 1)),
        sa.Column("humidity_pct", sa.SmallInteger()),
        # Осадки в окне старта (час до — два часа после) и максимальная
        # вероятность осадков в том же окне: «дождь» у прогноза вероятностный.
        sa.Column("precipitation_mm", sa.Numeric(6, 2)),
        sa.Column("precipitation_probability_pct", sa.SmallInteger()),
        sa.Column("snowfall_cm", sa.Numeric(6, 2)),
        sa.Column("weather_code", sa.SmallInteger()),
        sa.Column("cloud_cover_pct", sa.SmallInteger()),
        sa.Column("wind_speed_ms", sa.Numeric(5, 1)),
        sa.Column("wind_gusts_ms", sa.Numeric(5, 1)),
        # За сколько суток до старта снят прогноз: за неделю он гадание,
        # накануне — почти факт, и на витрине это честно подписывается.
        sa.Column("horizon_days", sa.SmallInteger(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_start_weather_forecast_target_date", "start_weather_forecast", ["target_date"])


def downgrade() -> None:
    op.drop_index("ix_start_weather_forecast_target_date", table_name="start_weather_forecast")
    op.drop_table("start_weather_forecast")
