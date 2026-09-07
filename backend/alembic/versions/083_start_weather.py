"""Погода на стартах — архив по координатам локации и дате

Инициатива Дмитрия 06.09.2026: участник, планируя поездку, должен видеть, какая
погода была на этом старте в прошлые годы («в Якутске в январе −40», «год назад
бежали под ливнем»). Источник один — Open-Meteo Historical API (реанализ
ERA5 по сетке ~10 км, часовые данные с 1940 г.); сверка со станцией аэропорта
Якутска 04.01.2025 09:00 дала −47…−48° против −49.0°.

* start_weather — одна строка = локация × дата, ключ составной. Собираем все
  субботы с первого старта локации плюс даты внесубботних стартов (1 января
  и т.п.): суббота без старта в −45 — тоже часть картины. Поля без префикса
  сняты в ближайший к времени старта час (расписание 5 вёрст из
  location_descriptions.schedule_parsed, иначе 09:00 местного), поля day_* —
  за календарный день по местному времени (считаются из часовых значений,
  восход и закат — по уравнению NOAA). Ничего производного не храним:
  дождь = осадки − снег, «светло ли» = старт между восходом и закатом.

* locations.timezone — имя зоны IANA (Europe/Moscow, Asia/Yakutsk): свойство
  локации, а не строки погоды; заполняется из ответа API при первом сборе.

Revision ID: 083_start_weather
Revises: 082_email_login_requests
Create Date: 2026-09-06
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "083_start_weather"
down_revision = "082_email_login_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("locations", sa.Column("timezone", sa.String(length=64), nullable=True))

    op.create_table(
        "start_weather",
        sa.Column(
            "location_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("locations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("obs_date", sa.Date(), primary_key=True),
        # Время старта по расписанию локации на эту дату (местное). Часовые
        # поля ниже сняты в ближайший к нему целый час.
        sa.Column("start_time_local", sa.Time(), nullable=False),
        # --- час старта ---
        sa.Column("temperature_c", sa.Numeric(5, 1)),
        sa.Column("apparent_temperature_c", sa.Numeric(5, 1)),
        sa.Column("humidity_pct", sa.SmallInteger()),
        sa.Column("precipitation_mm", sa.Numeric(6, 2)),
        # Окна вокруг старта: «бежали под дождём» — старт−1ч…старт+2ч (для 9:00
        # это 8–11), «трасса мокрая» — старт−4ч…старт−1ч (5–8). Реанализ
        # размазывает осадки по часам, один час старта — слабый признак.
        sa.Column("precipitation_run_mm", sa.Numeric(6, 2)),
        sa.Column("precipitation_before_mm", sa.Numeric(6, 2)),
        sa.Column("snowfall_cm", sa.Numeric(6, 2)),
        sa.Column("snow_depth_cm", sa.Numeric(6, 1)),
        sa.Column("weather_code", sa.SmallInteger()),
        sa.Column("cloud_cover_pct", sa.SmallInteger()),
        sa.Column("wind_speed_ms", sa.Numeric(5, 1)),
        sa.Column("wind_gusts_ms", sa.Numeric(5, 1)),
        # --- сутки по местному времени ---
        sa.Column("day_temperature_min_c", sa.Numeric(5, 1)),
        sa.Column("day_temperature_max_c", sa.Numeric(5, 1)),
        sa.Column("day_precipitation_mm", sa.Numeric(6, 2)),
        sa.Column("day_snowfall_cm", sa.Numeric(6, 2)),
        sa.Column("day_precipitation_hours", sa.Numeric(4, 1)),
        sa.Column("day_wind_speed_max_ms", sa.Numeric(5, 1)),
        sa.Column("day_wind_gusts_max_ms", sa.Numeric(5, 1)),
        sa.Column("day_weather_code", sa.SmallInteger()),
        sa.Column("sunrise_local", sa.Time()),
        sa.Column("sunset_local", sa.Time()),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_start_weather_obs_date", "start_weather", ["obs_date"])


def downgrade() -> None:
    op.drop_index("ix_start_weather_obs_date", table_name="start_weather")
    op.drop_table("start_weather")
    op.drop_column("locations", "timezone")
