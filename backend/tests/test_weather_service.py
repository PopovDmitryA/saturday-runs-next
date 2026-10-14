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
    dates_to_fetch,
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
    assert row["source"] == "archive"
    assert row["temperature_c"] == Decimal("-48.2")
    assert row["snow_depth_cm"] == Decimal("21.0")
    assert row["precipitation_mm"] == Decimal("0.3")
    # Дождь на дистанции — только час забега: метка 10 (09:00–10:00). Ливень в
    # 3.5 мм прошёл часом раньше и дождливым этот старт больше не делает.
    assert row["precipitation_run_mm"] == Decimal("0")
    # Мокрая трасса — три часа до старта: метки 07, 08, 09.
    assert row["precipitation_before_mm"] == Decimal("4.5")
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
    weekly = format_run_report(done, when=when, backfill_reported=True)
    assert "завершён" not in weekly and "Еженедельная докачка" in weekly


def test_fetch_archive_retries_transport_errors(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import httpx

    from app.services import weather_service

    calls = {"n": 0}

    class FakeClient:
        def get(self, url, params, timeout):  # type: ignore[no-untyped-def]
            calls["n"] += 1
            if calls["n"] < 3:
                raise httpx.ConnectTimeout("handshake timed out")
            return httpx.Response(200, json={"hourly": {"time": []}}, request=httpx.Request("GET", url))

    monkeypatch.setattr(weather_service._time, "sleep", lambda s: None)
    payload = weather_service.fetch_archive(FakeClient(), 55.7, 37.6, date(2025, 1, 4), date(2025, 1, 4))  # type: ignore[arg-type]
    assert payload == {"hourly": {"time": []}}
    assert calls["n"] == 3


def test_observation_dates_upper_override_and_dates_to_fetch() -> None:
    events = [date(2026, 9, 5), date(2026, 9, 12)]
    # Предварительный прогон в субботу: граница — сегодня, суббота 12.09 входит.
    assert observation_dates(events, today=date(2026, 9, 12), upper=date(2026, 9, 12))[-1] == date(2026, 9, 12)
    # Архивный прогон в ту же субботу: 12.09 ещё за границей лага.
    assert observation_dates(events, today=date(2026, 9, 12))[-1] == date(2026, 9, 5)

    # Лестница источников: окончательна только строка era5, всё остальное
    # архивный прогон обязан переписать.
    stored = {
        date(2026, 9, 5): "era5",
        date(2026, 9, 12): "archive",
        date(2026, 9, 19): "forecast",
    }
    dates = [date(2026, 9, 5), date(2026, 9, 12), date(2026, 9, 19), date(2026, 9, 26)]
    assert dates_to_fetch(dates, stored, preliminary=False) == [
        date(2026, 9, 12),
        date(2026, 9, 19),
        date(2026, 9, 26),
    ]
    # Предварительный: пустые даты и свои предварительные строки — утреннюю
    # погоду, положенную загрузкой протокола, вечерний прогон переписывает.
    assert dates_to_fetch(dates, stored, preliminary=True) == [date(2026, 9, 19), date(2026, 9, 26)]


def test_chunks_split_fresh_dates_from_history() -> None:
    """Год ради одной свежей субботы не просим: чанк режется по разрыву."""
    from app.services.weather_service import _year_chunks

    dates = [date(2026, 1, 3), date(2026, 1, 10), date(2026, 1, 17), date(2026, 9, 12)]
    chunks = _year_chunks(dates)
    assert [(start, end) for start, end, _ in chunks] == [
        (date(2026, 1, 3), date(2026, 1, 17)),
        (date(2026, 9, 12), date(2026, 9, 12)),
    ]
    # Разные годы не склеиваются даже впритык.
    assert len(_year_chunks([date(2025, 12, 27), date(2026, 1, 3)])) == 2


def test_preliminary_report_wording() -> None:
    summary = ScopeRunSummary(preliminary=True, rows_written=250, locations_touched=250, api_calls=250)
    text = format_run_report(summary, when=datetime(2026, 9, 12, 17, 5))
    assert "предварительно" in text and "архив заменит" in text


def test_running_locations_drops_silent_venues() -> None:
    """Прогноз собираем только там, где в субботу побегут.

    Порог тот же, что у статуса «не действует» на всём сайте: иначе на закрытой
    площадке висело бы «Прогноз на старт», а статус рядом говорил обратное.
    """
    from app.services.weather_forecast_service import running_locations

    def loc(name: str) -> WeatherLocation:
        return WeatherLocation(
            id=uuid4(),
            name=name,
            platform_code="five_verst",
            country="Россия",
            latitude=55.0,
            longitude=37.0,
            schedule=None,
        )

    today = date(2026, 9, 17)
    alive, seasonal, closed, never = loc("живая"), loc("сезонная"), loc("закрытая"), loc("без стартов")
    last = {
        alive.id: date(2026, 9, 12),
        # Ровно на пороге (100 дней) — ещё в строю.
        seasonal.id: date(2026, 6, 9),
        closed.id: date(2025, 5, 10),
    }
    result = running_locations([alive, seasonal, closed, never], last, today)
    assert [item.name for item in result] == ["живая", "сезонная"]


def test_call_weight_counts_two_week_blocks() -> None:
    """Лимит Open-Meteo считается вызовами по две недели, а не запросами.

    Пока это не учитывалось, ночной прогон «918 запросов» выглядел дешёвым, а
    на деле выбирал все 10 000 суточных вызовов.
    """
    from app.services.weather_service import call_weight

    assert call_weight(date(2026, 1, 1), date(2026, 1, 1)) == 1
    assert call_weight(date(2026, 1, 1), date(2026, 1, 14)) == 1
    assert call_weight(date(2026, 1, 1), date(2026, 1, 15)) == 2
    # Год целиком — самый частый чанк пересборки.
    assert call_weight(date(2025, 1, 1), date(2025, 12, 31)) == 27


def test_snow_depth_request_is_cheap() -> None:
    """Покров из ERA5-Land — одна переменная: суббота стоит вызов, год — три.

    Из запроса ERA5 покров убран (он там всегда пустой), поэтому год
    окончательных строк стоит те же 27 вызовов, что и до второго запроса.
    """
    from app.services.weather_service import call_weight, final_call_weight

    assert call_weight(date(2026, 1, 3), date(2026, 1, 3), 1) == 1
    assert call_weight(date(2025, 1, 1), date(2025, 12, 31), 1) == 3
    assert final_call_weight(date(2026, 1, 3), date(2026, 1, 3)) == 2
    assert final_call_weight(date(2025, 1, 1), date(2025, 12, 31)) == 27


def test_liquid_window_does_not_count_snow_as_rain() -> None:
    """Зимний старт под снегопадом — не дождливый.

    Вопрос Наталии Тумковской 18.09.2026: Мещерский 17.12.2022 показывал «дождь
    2.6 мм» при −5.7°. В сумме осадков Open-Meteo снег идёт наравне с дождём,
    поэтому водный эквивалент снега вычитаем.
    """
    from app.services.weather_service import liquid_window

    hours = [f"2022-12-17T{h:02d}:00" for h in range(24)]
    index = {stamp: i for i, stamp in enumerate(hours)}
    precipitation = [0.0] * 24
    snowfall = [0.0] * 24
    precipitation[10] = 0.9
    snowfall[10] = 0.63  # ровно водный эквивалент 0.9 мм
    window = range(10, 11)
    assert liquid_window(precipitation, snowfall, index, date(2022, 12, 17), window) == Decimal("0")
    # Мокрый снег: часть выпала дождём — её и считаем.
    snowfall[10] = 0.35
    assert liquid_window(precipitation, snowfall, index, date(2022, 12, 17), window) == Decimal("0.40")
    # Без снега окно ведёт себя как обычная сумма.
    snowfall[10] = 0.0
    assert liquid_window(precipitation, snowfall, index, date(2022, 12, 17), window) == Decimal("0.9")


def test_liquid_window_needs_the_whole_window() -> None:
    """Дыра в часах — это «не знаем», а не ноль: иначе пропуск выглядел бы сухим."""
    from app.services.weather_service import liquid_window

    hours = ["2022-12-17T09:00"]
    index = {stamp: i for i, stamp in enumerate(hours)}
    assert liquid_window([0.5], [0.0], index, date(2022, 12, 17), range(10, 11)) is None


def test_rain_thresholds_are_set_for_one_hour() -> None:
    """Пороги живут на часе забега, а не на четырёхчасовом окне.

    Когда окно сузилось вчетверо, старый порог в 1 мм стал вчетверо строже:
    «Нижний пруд 23.09.2023 — сильный дождь» (Наталья Волкова) при 0.7 мм за
    час забега уезжал в сухие. На выборке из 199 стартов порог 0.3 мм/ч
    сохраняет прежнюю долю дождливых стартов — 6.0% против 6.5%.
    """
    from app.services.start_weather_service import is_rain, rain_kind

    assert rain_kind(0.0) == "dry"
    assert rain_kind(0.05) == "dry"
    assert rain_kind(0.2) == "drizzle"
    assert rain_kind(0.7) == "rain"  # тот самый Нижний пруд
    assert rain_kind(3.0) == "downpour"
    assert is_rain(0.7) and not is_rain(0.2)


def test_old_rows_keep_the_old_rain_threshold() -> None:
    """Переходный период: строка судится по правилу, по которому посчитана.

    До 18.09.2026 precipitation_run_mm был суммой за четыре часа. Новый порог
    в 0.3 мм, применённый к такой строке, удваивал долю дождливых стартов —
    на выборке из 200 она прыгала с 6.5% до 13%.
    """
    from datetime import datetime, timedelta, timezone

    from app.services.start_weather_service import is_rain, rain_kind

    msk = timezone(timedelta(hours=3))
    old_row = datetime(2026, 9, 15, 3, 30, tzinfo=msk)
    new_row = datetime(2026, 9, 19, 3, 30, tzinfo=msk)

    # 0.5 мм: по старому правилу это морось, по новому — дождь.
    assert not is_rain(0.5, old_row)
    assert rain_kind(0.5, old_row) == "drizzle"
    assert is_rain(0.5, new_row)
    assert rain_kind(0.5, new_row) == "rain"
    # Без отметки времени считаем строку новой — так ведут себя свежие данные.
    assert is_rain(0.5)


def test_event_weather_needed_only_for_fresh_starts() -> None:
    from app.services.weather_service import event_weather_needed

    today = date(2026, 9, 26)
    assert event_weather_needed(date(2026, 9, 26), today=today)
    assert event_weather_needed(date(2026, 9, 25), today=today)
    # Дальше лага архива — погоду берёт ночной архивный прогон.
    assert not event_weather_needed(date(2026, 9, 24), today=today)
    assert not event_weather_needed(date(2026, 9, 27), today=today)


def _one_day_payload(day: date) -> dict[str, object]:
    return {
        "timezone": "Europe/Moscow",
        "hourly": {
            "time": [f"{day.isoformat()}T{h:02d}:00" for h in range(24)],
            "temperature_2m": [12.0] * 24,
            "apparent_temperature": [11.0] * 24,
            "relative_humidity_2m": [70] * 24,
            "precipitation": [0.0] * 24,
            "snowfall": [0.0] * 24,
            "snow_depth": [0.0] * 24,
            "weather_code": [1] * 24,
            "cloud_cover": [20] * 24,
            "wind_speed_10m": [3.0] * 24,
            "wind_gusts_10m": [5.0] * 24,
        },
    }


def test_collect_event_weather_writes_forecast_row_once(db_session) -> None:  # type: ignore[no-untyped-def]
    """Загрузка протокола кладёт предварительную погоду; второй раз в сеть не ходит."""
    import httpx
    import pytest

    from app.models import Event, Location, Platform, StartWeather
    from app.services.weather_service import SOURCE_FORECAST, collect_event_weather

    platform = db_session.query(Platform).filter(Platform.code == "five_verst").one_or_none()
    if platform is None:
        pytest.skip("five_verst platform not seeded")
    location = Location(
        platform_id=platform.id,
        external_key=f"weather-event-{uuid4().hex[:8]}",
        name="Погодный парк",
        country="Россия",
        latitude=55.667,
        longitude=37.404,
        source_url="https://5verst.ru/weather-event/",
    )
    db_session.add(location)
    db_session.flush()
    day = date(2026, 9, 26)
    db_session.add(
        Event(
            platform_id=platform.id,
            location_id=location.id,
            external_event_key=f"{location.external_key}:1:{day.isoformat()}",
            event_date=day,
            title="Погодный парк",
        )
    )
    # Сбор откатывает транзакцию перед походом в сеть — подготовку фиксируем.
    db_session.commit()

    calls: list[dict[str, str]] = []

    class FakeClient:
        def get(self, url, params, timeout):  # type: ignore[no-untyped-def]
            calls.append(params)
            return httpx.Response(200, json=_one_day_payload(day), request=httpx.Request("GET", url))

    assert collect_event_weather(db_session, FakeClient(), location.id, day, today=day) == 1  # type: ignore[arg-type]
    row = db_session.query(StartWeather).filter(StartWeather.location_id == location.id).one()
    assert row.source == SOURCE_FORECAST
    assert row.temperature_c == Decimal("12.0")
    assert calls[0]["start_date"] == calls[0]["end_date"] == "2026-09-26"

    assert collect_event_weather(db_session, FakeClient(), location.id, day, today=day) == 0  # type: ignore[arg-type]
    assert len(calls) == 1
    # Старт старше лага архива — сеть не трогаем вовсе.
    assert collect_event_weather(db_session, FakeClient(), location.id, date(2026, 9, 12), today=day) == 0  # type: ignore[arg-type]
    assert len(calls) == 1


def _weather_location(db_session, name: str):  # type: ignore[no-untyped-def]
    """Локация пятёрки со стартом 10.10.2026 — субботой с покровом в Якутске."""
    import pytest

    from app.models import Event, Location, Platform

    platform = db_session.query(Platform).filter(Platform.code == "five_verst").one_or_none()
    if platform is None:
        pytest.skip("five_verst platform not seeded")
    location = Location(
        platform_id=platform.id,
        external_key=f"weather-snow-{uuid4().hex[:8]}",
        name=name,
        country="Россия",
        latitude=62.035638,
        longitude=129.714129,
        source_url="https://5verst.ru/weather-snow/",
    )
    db_session.add(location)
    db_session.flush()
    day = date(2026, 10, 10)
    db_session.add(
        Event(
            platform_id=platform.id,
            location_id=location.id,
            external_event_key=f"{location.external_key}:1:{day.isoformat()}",
            event_date=day,
            title=name,
        )
    )
    return location, day


def _archive_row(location_id, day: date, snow_cm: str) -> dict[str, object]:  # type: ignore[no-untyped-def]
    return {
        "location_id": location_id,
        "obs_date": day,
        "start_time_local": time(9, 0),
        "source": "archive",
        "temperature_c": Decimal("-5.1"),
        "snow_depth_cm": Decimal(snow_cm),
        "fetched_at": datetime(2026, 10, 12, tzinfo=UTC),
    }


def test_era5_rewrite_keeps_snow_cover(db_session) -> None:  # type: ignore[no-untyped-def]
    """Пустой покров окончательной строки не стирает покров от best_match.

    До 14.10.2026 перепись ERA5 обнуляла его у всех: клетка «По снегу» за
    10.10.2026 в Якутске, Мирном и Тынде пропала бы через неделю.
    """
    from app.models import StartWeather
    from app.services.weather_service import upsert_rows

    location, day = _weather_location(db_session, "Снежный парк")
    db_session.flush()
    upsert_rows(db_session, [_archive_row(location.id, day, "21.0")])

    era5 = {**_archive_row(location.id, day, "0"), "source": "era5", "temperature_c": Decimal("-6.0")}
    upsert_rows(db_session, [{**era5, "snow_depth_cm": None}])
    db_session.expire_all()
    row = db_session.get(StartWeather, (location.id, day))
    assert row.source == "era5"
    assert row.temperature_c == Decimal("-6.0")
    assert row.snow_depth_cm == Decimal("21.0")

    # Настоящее значение, даже ноль, по-прежнему перезаписывает.
    upsert_rows(db_session, [era5])
    db_session.expire_all()
    assert db_session.get(StartWeather, (location.id, day)).snow_depth_cm == Decimal("0.0")


def test_final_rows_take_snow_depth_from_era5_land(db_session) -> None:  # type: ignore[no-untyped-def]
    """Окончательная строка: погода ERA5, покров ERA5-Land по тем же часам."""
    from datetime import timedelta

    import httpx

    from app.models import StartWeather
    from app.services.weather_service import (
        FINAL_MODEL,
        SNOW_DEPTH_MODEL,
        WeatherLocation,
        collect_location_weather,
        upsert_rows,
    )

    location, day = _weather_location(db_session, "Снежный парк ERA5")
    db_session.flush()
    upsert_rows(db_session, [_archive_row(location.id, day, "1.0")])
    # Сбор откатывает транзакцию перед походом в сеть — подготовку фиксируем.
    db_session.commit()
    target = WeatherLocation(
        id=location.id,
        name=location.name,
        platform_code="five_verst",
        country="Россия",
        latitude=62.035638,
        longitude=129.714129,
        schedule=None,
    )
    calls: list[dict[str, str]] = []
    snow_m: dict[str, float | None] = {"value": 0.33}

    class FakeClient:
        def get(self, url, params, timeout):  # type: ignore[no-untyped-def]
            calls.append(params)
            start = date.fromisoformat(params["start_date"])
            days = (date.fromisoformat(params["end_date"]) - start).days + 1
            stamps = [f"{start + timedelta(days=d)}T{h:02d}:00" for d in range(days) for h in range(24)]
            if params["models"] == SNOW_DEPTH_MODEL:
                # Ряд покрова задом наперёд: склейка обязана идти по меткам, а не по позиции.
                hourly = {"time": stamps[::-1], "snow_depth": [snow_m["value"]] * len(stamps)}
            else:
                hourly = {"time": stamps, **{var: [-6.0] * len(stamps) for var in params["hourly"].split(",")}}
            body = {"timezone": "Asia/Yakutsk", "hourly": hourly}
            return httpx.Response(200, json=body, request=httpx.Request("GET", url))

    today = date(2026, 10, 17)  # ERA5 дошёл до 11.10: 10.10 — окончательная
    stats = collect_location_weather(db_session, FakeClient(), target, today=today, pause_seconds=0)  # type: ignore[arg-type]
    era5_call, snow_call = calls
    assert era5_call["models"] == FINAL_MODEL
    assert "snow_depth" not in era5_call["hourly"].split(",")
    assert snow_call["models"] == SNOW_DEPTH_MODEL
    assert snow_call["hourly"] == "snow_depth"
    # Часовой пояс второго запроса — из первого ответа, чтобы метки совпали.
    assert snow_call["timezone"] == "Asia/Yakutsk"
    assert stats.api_calls == 2 and stats.weighted_calls == 2
    db_session.expire_all()
    row = db_session.get(StartWeather, (location.id, day))
    assert row.source == "era5"
    assert row.temperature_c == Decimal("-6.0")
    assert row.snow_depth_cm == Decimal("33.0")

    # ERA5-Land промолчал — покров остаётся прежним, а не пустым.
    snow_m["value"] = None
    calls.clear()
    collect_location_weather(db_session, FakeClient(), target, today=today, resume=False, pause_seconds=0)  # type: ignore[arg-type]
    assert len(calls) == 2
    db_session.expire_all()
    assert db_session.get(StartWeather, (location.id, day)).snow_depth_cm == Decimal("33.0")


def test_backfill_snow_depth_fills_only_empty_final_rows(db_session) -> None:  # type: ignore[no-untyped-def]
    """Дозаливка истории: только пустой покров строк era5, на час старта строки."""
    from datetime import timedelta

    import httpx
    import pytest

    from app.models import Location, Platform, StartWeather
    from app.services.weather_service import SNOW_DEPTH_MODEL, backfill_snow_depth, snow_gap_locations, upsert_rows

    platform = db_session.query(Platform).filter(Platform.code == "five_verst").one_or_none()
    if platform is None:
        pytest.skip("five_verst platform not seeded")
    location = Location(
        platform_id=platform.id,
        external_key=f"weather-backfill-{uuid4().hex[:8]}",
        name=f"Снежная дозаливка {uuid4().hex[:6]}",
        country="Россия",
        latitude=62.035638,
        longitude=129.714129,
        timezone="Asia/Yakutsk",
        source_url="https://5verst.ru/weather-backfill/",
    )
    db_session.add(location)
    db_session.flush()
    old_fetch = datetime(2026, 9, 18, tzinfo=UTC)

    def row(day: date, source: str, snow: str | None, start: time = time(9, 0)) -> dict[str, object]:
        return {
            "location_id": location.id,
            "obs_date": day,
            "start_time_local": start,
            "source": source,
            "temperature_c": Decimal("-30.0"),
            "snow_depth_cm": Decimal(snow) if snow is not None else None,
            "fetched_at": old_fetch,
        }

    upsert_rows(
        db_session,
        [
            row(date(2025, 1, 4), "era5", None),
            row(date(2025, 1, 11), "era5", None, start=time(8, 0)),  # летнее расписание не при чём: час строки
            row(date(2025, 1, 18), "era5", "5.0"),  # уже есть — не трогаем
            row(date(2025, 1, 25), "archive", None),  # не окончательная — её перепишет сбор
            row(date(2025, 2, 1), "era5", None),  # у ERA5-Land нет значения — остаётся пустой
        ],
    )
    # Дозаливка откатывает транзакцию перед походом в сеть — подготовку фиксируем.
    db_session.commit()
    gap = next(loc for loc in snow_gap_locations(db_session) if loc.id == location.id)
    assert gap.rows == 3 and gap.timezone == "Asia/Yakutsk"

    calls: list[dict[str, str]] = []

    class FakeClient:
        def get(self, url, params, timeout):  # type: ignore[no-untyped-def]
            calls.append(params)
            start = date.fromisoformat(params["start_date"])
            days = (date.fromisoformat(params["end_date"]) - start).days + 1
            stamps = [f"{start + timedelta(days=d)}T{h:02d}:00" for d in range(days) for h in range(24)]
            # Покров в метрах = час / 100: по значению видно, какой час взят.
            depth = [None if s.startswith("2025-02-01") else int(s[11:13]) / 100 for s in stamps]
            body = {"timezone": params["timezone"], "hourly": {"time": stamps, "snow_depth": depth}}
            return httpx.Response(200, json=body, request=httpx.Request("GET", url))

    stats = backfill_snow_depth(db_session, FakeClient(), gap)  # type: ignore[arg-type]
    (call,) = calls
    assert call["models"] == SNOW_DEPTH_MODEL
    assert call["hourly"] == "snow_depth"
    assert call["timezone"] == "Asia/Yakutsk"
    assert (call["start_date"], call["end_date"]) == ("2025-01-04", "2025-02-01")
    assert (stats.rows_missing, stats.rows_filled, stats.rows_with_cover, stats.rows_no_data) == (3, 2, 2, 1)
    assert stats.weighted_calls == 1

    db_session.expire_all()
    got = {r.obs_date: r for r in db_session.query(StartWeather).filter(StartWeather.location_id == location.id).all()}
    assert got[date(2025, 1, 4)].snow_depth_cm == Decimal("9.0")
    assert got[date(2025, 1, 11)].snow_depth_cm == Decimal("8.0")
    assert got[date(2025, 1, 18)].snow_depth_cm == Decimal("5.0")
    assert got[date(2025, 1, 25)].snow_depth_cm is None
    assert got[date(2025, 2, 1)].snow_depth_cm is None
    # Остальное в строке не меняется: по fetched_at выбирается порог дождя.
    assert got[date(2025, 1, 4)].fetched_at == old_fetch
    assert got[date(2025, 1, 4)].temperature_c == Decimal("-30.0")
