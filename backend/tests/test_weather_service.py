"""Чистые функции сбора погоды: даты, время старта, разбор ответа Open-Meteo."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.services.weather_service import (
    ScopeRunSummary,
    WeatherLocation,
    build_rows,
    format_run_report,
    observation_dates,
    resolve_start_time,
    sample_hour,
    saturdays_between,
    sun_times,
)


def test_saturdays_between_starts_from_first_saturday() -> None:
    assert saturdays_between(date(2025, 1, 1), date(2025, 1, 20)) == [
        date(2025, 1, 4),
        date(2025, 1, 11),
        date(2025, 1, 18),
    ]
    assert saturdays_between(date(2025, 1, 4), date(2025, 1, 4)) == [date(2025, 1, 4)]
    assert saturdays_between(date(2025, 1, 5), date(2025, 1, 4)) == []


def test_observation_dates_adds_non_saturday_starts_and_respects_lag() -> None:
    events = [date(2025, 1, 1), date(2025, 1, 4), date(2025, 1, 11)]
    got = observation_dates(events, today=date(2025, 1, 19))
    # Архив отстаёт на 2 суток: 18.01 при «сегодня» 19.01 ещё не просим.
    assert got == [date(2025, 1, 1), date(2025, 1, 4), date(2025, 1, 11)]
    assert observation_dates(events, today=date(2025, 1, 18), events_only=True, since=date(2025, 1, 2)) == [
        date(2025, 1, 4),
        date(2025, 1, 11),
    ]


def test_resolve_start_time_uses_schedule_or_default() -> None:
    schedule = [{"from_month": 6, "to_month": 8, "time": "08:00"}, {"from_month": 9, "to_month": 5, "time": "09:00"}]
    assert resolve_start_time(schedule, date(2025, 7, 5)) == (time(8, 0), "schedule")
    assert resolve_start_time(schedule, date(2025, 12, 6)) == (time(9, 0), "schedule")
    assert resolve_start_time(None, date(2025, 7, 5)) == (time(9, 0), "default")
    assert sample_hour(time(8, 30)) == 9
    assert sample_hour(time(9, 15)) == 9


def test_sun_times_matches_open_meteo() -> None:
    # Open-Meteo daily за те же дни: Якутск 01.01.2025 09:45 / 15:04, Белград 15.06.2025 ~04:52 / 20:26.
    sunrise, sunset = sun_times(62.035638, 129.714129, date(2025, 1, 1), ZoneInfo("Asia/Yakutsk"))
    assert sunrise is not None and sunset is not None
    assert abs(sunrise.hour * 60 + sunrise.minute - (9 * 60 + 45)) <= 3
    assert abs(sunset.hour * 60 + sunset.minute - (15 * 60 + 4)) <= 3
    sunrise, sunset = sun_times(44.819492, 20.457273, date(2025, 6, 15), ZoneInfo("Europe/Belgrade"))
    assert sunrise is not None and sunset is not None
    assert abs(sunrise.hour * 60 + sunrise.minute - (4 * 60 + 52)) <= 4
    assert abs(sunset.hour * 60 + sunset.minute - (20 * 60 + 26)) <= 4
    # Полярная ночь: Мурманск в декабре.
    assert sun_times(68.97, 33.07, date(2025, 12, 15), ZoneInfo("Europe/Moscow")) == (None, None)


def test_build_rows_picks_start_hour_and_skips_missing() -> None:
    location = WeatherLocation(
        id=uuid4(),
        name="Якутск Дохсун",
        platform_code="five_verst",
        country="Россия",
        latitude=62.035638,
        longitude=129.714129,
        schedule=None,
    )
    hours = [f"2025-01-04T{h:02d}:00" for h in range(24)] + ["2025-01-11T09:00"]
    payload = {
        "timezone": "Asia/Yakutsk",
        "utc_offset_seconds": 32400,
        "hourly": {
            "time": hours,
            "temperature_2m": [-50.0] * 9 + [-48.2] + [-47.0] * 14 + [None],
            "apparent_temperature": [-53.0] * 25,
            "relative_humidity_2m": [70] * 25,
            # метки 06..11 = 0.2, 0.7, 3.5, 0.3, 0.0, 0.2 — Брянск 05.09.2026
            "precipitation": [0.0] * 6 + [0.2, 0.7, 3.5, 0.3, 0.0, 0.2] + [0.0] * 13,
            "snowfall": [0.0] * 25,
            "snow_depth": [0.21] * 25,
            "weather_code": [3] * 25,
            "cloud_cover": [80] * 25,
            "wind_speed_10m": [4.0] * 25,
            "wind_gusts_10m": [7.5] * 25,
        },
    }
    rows, skipped = build_rows(payload, location, [date(2025, 1, 4), date(2025, 1, 11)], fetched_at=datetime.now(UTC))
    assert skipped == 1
    assert len(rows) == 1
    row = rows[0]
    assert row["obs_date"] == date(2025, 1, 4)
    assert row["start_time_local"] == time(9, 0)
    assert row["temperature_c"] == Decimal("-48.2")
    assert row["snow_depth_cm"] == Decimal("21.0")
    assert row["precipitation_mm"] == Decimal("0.3")
    assert row["precipitation_run_mm"] == Decimal("0.5")  # метки 09, 10, 11
    assert row["precipitation_before_mm"] == Decimal("4.4")  # метки 06, 07, 08
    # Суточные агрегаты считаются из 24 часовых значений.
    assert row["day_temperature_min_c"] == Decimal("-50.0")
    assert row["day_temperature_max_c"] == Decimal("-47.0")
    assert row["day_precipitation_mm"] == Decimal("4.9")
    assert row["day_precipitation_hours"] == Decimal("5")
    assert row["day_weather_code"] == 3
    assert row["sunrise_local"] is not None and 9 <= row["sunrise_local"].hour <= 10
    assert "timezone" not in row


def test_format_run_report_states_outcome() -> None:
    when = datetime(2026, 9, 8, 3, 25)
    partial = ScopeRunSummary(
        scope_locations=423,
        locations_complete=250,
        locations_touched=40,
        rows_written=5000,
        api_calls=380,
        rows_total=40000,
        locations_total=260,
        stopped_by_limit=True,
    )
    text = format_run_report(partial, when=when)
    assert "записано 5000 строк по 40 локациям" in text
    assert "собрано полностью 250 из 423" in text
    assert "продолжу завтра" in text
    assert not partial.finished

    done = ScopeRunSummary(scope_locations=423, locations_complete=423, rows_total=60000, locations_total=423)
    assert done.finished
    assert "Сбор всего периметра завершён" in format_run_report(done, when=when)
    assert "Сессия Claude" in format_run_report(done, when=when)
