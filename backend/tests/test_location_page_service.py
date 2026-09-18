from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any

import fakeredis
import pytest

from app.services import location_page_service
from app.services.location_page_service import (
    LOCATIONS_INDEX_CACHE_KEY,
    LOCATIONS_INDEX_CACHE_TTL_SECONDS,
    UNKNOWN_DISPLAY_NAMES,
    _age_group_sort_key,
    _read_locations_index_cache,
    _sort_identity_locations,
    _start_point_url,
    _write_locations_index_cache,
    age_group_key,
    build_location_age_group_standings,
    build_location_events,
    build_location_leaders,
    build_location_page,
    build_locations_index,
    invalidate_location_page_cache,
    invalidate_locations_index_cache,
    location_events_cache_key,
    location_leaders_cache_key,
    location_page_cache_key,
    normalize_age_group,
)


def _location(external_key: str) -> SimpleNamespace:
    return SimpleNamespace(external_key=external_key)


def test_normalize_age_group_five_verst() -> None:
    assert normalize_age_group("М18-24") == "18–24"
    assert normalize_age_group("Ж35-39") == "35–39"
    assert normalize_age_group("М75+") == "75+"


def test_normalize_age_group_parkrun_and_runpark() -> None:
    assert normalize_age_group("SM25-29") == "25–29"
    assert normalize_age_group("VW45-49") == "45–49"
    assert normalize_age_group("VM35-39") == "35–39"


def test_normalize_age_group_under_ten() -> None:
    """«М10» — строго «младше 10»: десятилетний бежит уже в «10-14»."""
    assert normalize_age_group("М10") == "<10"
    assert normalize_age_group("Ж10") == "<10"
    assert normalize_age_group("JM10") == "<10"
    assert normalize_age_group("М10-14") == "10–14"


def test_normalize_age_group_keeps_absurd_bands() -> None:
    """«М110-114» показываем как есть — это то, что стоит в протоколе.

    Такую группу 5 вёрст печатает участникам без даты рождения. Прятать её
    не надо (решение Дмитрия 27.07.2026), но и вытаскивать из неё «10–11»
    поиском подстроки нельзя — отсюда якорь по всей строке.
    """
    assert normalize_age_group("М110-114") == "110–114"
    assert normalize_age_group("Ж110-114") == "110–114"
    assert normalize_age_group("М120-124") == "120–124"
    # Тот же сорт данных одним числом.
    assert normalize_age_group("М120") == "<120"


def test_age_group_sort_key_puts_under_before_range() -> None:
    """«<10» и «10–14» дают одно число — порядок между ними фиксирован."""
    assert sorted(["35–39", "10–14", "<10", "75+", "110–114"], key=_age_group_sort_key) == [
        "<10",
        "10–14",
        "35–39",
        "75+",
        "110–114",
    ]


def test_normalize_age_group_unknown() -> None:
    assert normalize_age_group(None) is None
    assert normalize_age_group("") is None
    assert normalize_age_group("хостел") is None
    # Age grade parkrun — не возрастная группа, под правило «≤N» попасть не должен.
    assert normalize_age_group("54.38%") is None


def test_age_group_key_is_anchor_safe() -> None:
    """Ключ группы уходит в id строки таблицы — спецсимволов там быть не должно."""
    assert age_group_key("male", "30–34") == "male-30-34"
    assert age_group_key("female", "75+") == "female-75plus"
    assert age_group_key("male", "<10") == "male-under10"
    assert age_group_key("male", "110–114") == "male-110-114"


def test_build_location_age_group_standings_without_events() -> None:
    """Локация без протоколов с возрастной категорией — плиток нет."""
    assert build_location_age_group_standings(db=None, user_id=None, event_ids=[]) == []  # type: ignore[arg-type]


def test_sort_identity_locations_prefers_catalog_active_platform() -> None:
    catalog = SimpleNamespace(active_platform="s95")
    locations = [
        (_location("kuzminki-parkrun"), "parkrun"),
        (_location("kuzminki"), "s95"),
    ]
    ordered = _sort_identity_locations(catalog, locations)  # type: ignore[arg-type]
    assert ordered[0][1] == "s95"


def test_sort_identity_locations_platform_order_without_catalog() -> None:
    locations = [
        (_location("loc-pr"), "parkrun"),
        (_location("loc-rp"), "runpark"),
        (_location("loc-fv"), "five_verst"),
    ]
    ordered = _sort_identity_locations(None, locations)  # type: ignore[arg-type]
    assert [code for _loc, code in ordered] == ["five_verst", "runpark", "parkrun"]


def test_unknown_display_names_match_whole_name_only() -> None:
    """Заглушку убираем, настоящую фамилию — нет.

    Отсечка «неизвестных» из топов локации сравнивает имя целиком. Проверка
    ровно об этом: «Андрей НЕИЗВЕСТНЫХ» — живой участник Чебоксар с 25
    пробежками, и фильтр по подстроке «неизвест» выкинул бы его вместе с
    безымянными строками протокола.
    """
    assert "неизвестный" in UNKNOWN_DISPLAY_NAMES
    assert "неизвестный бегун" in UNKNOWN_DISPLAY_NAMES
    assert "андрей неизвестных" not in UNKNOWN_DISPLAY_NAMES
    assert not any(name.startswith("андрей") for name in UNKNOWN_DISPLAY_NAMES)


def test_start_point_url() -> None:
    assert _start_point_url(55.7, 37.6) == "https://yandex.ru/maps/?pt=37.6,55.7&z=16&l=map"
    assert _start_point_url(None, 37.6) is None


def test_locations_index_cache_round_trip(fake_redis: fakeredis.FakeRedis) -> None:
    invalidate_locations_index_cache()
    assert _read_locations_index_cache() is None

    payload = {
        "items": [{"slug": "test", "first_event_date": date(2022, 6, 11), "events_count": 5}],
        "total": 1,
    }
    _write_locations_index_cache(payload)

    cached = _read_locations_index_cache()
    assert cached is not None
    # Даты сериализуются в ISO-строки (json.dumps(default=str)) — Pydantic
    # умеет парсить их обратно в date при валидации ответа.
    assert cached["items"][0]["first_event_date"] == "2022-06-11"  # type: ignore[index]
    assert cached["total"] == 1

    ttl = int(fake_redis.ttl(LOCATIONS_INDEX_CACHE_KEY))  # type: ignore[arg-type]
    assert 0 < ttl <= LOCATIONS_INDEX_CACHE_TTL_SECONDS


def test_invalidate_locations_index_cache() -> None:
    _write_locations_index_cache({"items": [], "total": 0})
    assert _read_locations_index_cache() is not None

    invalidate_locations_index_cache()
    assert _read_locations_index_cache() is None


def test_build_locations_index_uses_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    invalidate_locations_index_cache()
    calls = {"count": 0}

    def fake_compute(_db: Any) -> dict[str, object]:
        calls["count"] += 1
        return {"items": [], "total": 0}

    monkeypatch.setattr(location_page_service, "_compute_locations_index", fake_compute)

    first = build_locations_index(db=None)  # type: ignore[arg-type]
    second = build_locations_index(db=None)  # type: ignore[arg-type]

    assert calls["count"] == 1
    assert first == second == {"items": [], "total": 0}


def test_build_locations_index_bypasses_cache_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    invalidate_locations_index_cache()
    calls = {"count": 0}

    def fake_compute(_db: Any) -> dict[str, object]:
        calls["count"] += 1
        return {"items": [], "total": 0}

    monkeypatch.setattr(location_page_service, "_compute_locations_index", fake_compute)

    build_locations_index(db=None, use_cache=False)  # type: ignore[arg-type]
    build_locations_index(db=None, use_cache=False)  # type: ignore[arg-type]

    assert calls["count"] == 2


def test_build_locations_index_refresh_rewrites_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Прогрев обязан класть свежий каталог в Redis, а не считать его вхолостую.

    До 06.08.2026 задача прогрева ходила с use_cache=False (не читать И не
    писать): кэш наполняли только посетители, платя за это холодным расчётом
    дольше таймаута фронтенда.
    """
    _write_locations_index_cache({"items": [], "total": 0, "stale": True})

    def fake_compute(_db: Any) -> dict[str, object]:
        return {"items": [], "total": 0, "stale": False}

    monkeypatch.setattr(location_page_service, "_compute_locations_index", fake_compute)

    build_locations_index(db=None, refresh=True)  # type: ignore[arg-type]

    assert _read_locations_index_cache() == {"items": [], "total": 0, "stale": False}


# --- Кэш страницы/журнала/рейтингов одной локации (ключ на slug) ---
#
# Кэш здесь не косметика: без него каждое открытие страницы гоняло полтора
# десятка запросов к БД и упиралось в таймаут фронтенда, поэтому он покрыт
# тестами так же, как кэш индекса.

_PER_SLUG_CASES = [
    ("_compute_location_page", build_location_page),
    ("_compute_location_events", build_location_events),
    ("_compute_location_leaders", build_location_leaders),
]


@pytest.mark.parametrize(("compute_name", "build_func"), _PER_SLUG_CASES)
def test_build_location_per_slug_uses_cache(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: fakeredis.FakeRedis,
    compute_name: str,
    build_func: Any,
) -> None:
    invalidate_location_page_cache("izmailovo")
    calls = {"count": 0}

    def fake_compute(_db: Any, slug: str, **_kwargs: Any) -> dict[str, object]:
        calls["count"] += 1
        return {"slug": slug, "name": "Измайлово"}

    monkeypatch.setattr(location_page_service, compute_name, fake_compute)

    first = build_func(None, "izmailovo")
    second = build_func(None, "izmailovo")

    assert calls["count"] == 1
    assert first == second == {"slug": "izmailovo", "name": "Измайлово"}


@pytest.mark.parametrize(("compute_name", "build_func"), _PER_SLUG_CASES)
def test_build_location_per_slug_bypasses_cache_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: fakeredis.FakeRedis,
    compute_name: str,
    build_func: Any,
) -> None:
    invalidate_location_page_cache("izmailovo")
    calls = {"count": 0}

    def fake_compute(_db: Any, slug: str, **_kwargs: Any) -> dict[str, object]:
        calls["count"] += 1
        return {"slug": slug}

    monkeypatch.setattr(location_page_service, compute_name, fake_compute)

    build_func(None, "izmailovo", use_cache=False)
    build_func(None, "izmailovo", use_cache=False)

    assert calls["count"] == 2


@pytest.mark.parametrize(("compute_name", "build_func"), _PER_SLUG_CASES)
def test_build_location_per_slug_refresh_rewrites_cache(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: fakeredis.FakeRedis,
    compute_name: str,
    build_func: Any,
) -> None:
    """refresh=True — единственный режим прогрева: пересчитать и положить обратно."""
    invalidate_location_page_cache("izmailovo")

    def stale_compute(_db: Any, slug: str, **_kwargs: Any) -> dict[str, object]:
        return {"slug": slug, "stale": True}

    monkeypatch.setattr(location_page_service, compute_name, stale_compute)
    build_func(None, "izmailovo")

    def fresh_compute(_db: Any, slug: str, **_kwargs: Any) -> dict[str, object]:
        return {"slug": slug, "stale": False}

    monkeypatch.setattr(location_page_service, compute_name, fresh_compute)
    build_func(None, "izmailovo", refresh=True)

    # Следующий обычный вызов обязан увидеть уже свежий payload из кэша,
    # а не пересчитывать его сам.
    monkeypatch.setattr(location_page_service, compute_name, stale_compute)
    assert build_func(None, "izmailovo") == {"slug": "izmailovo", "stale": False}


def test_build_location_page_cache_is_isolated_per_slug(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: fakeredis.FakeRedis,
) -> None:
    """Ключ кэша включает slug — иначе одна локация отдавалась бы вместо другой."""
    invalidate_location_page_cache("izmailovo")
    invalidate_location_page_cache("kuzminki")

    def fake_compute(_db: Any, slug: str, **_kwargs: Any) -> dict[str, object]:
        return {"slug": slug}

    monkeypatch.setattr(location_page_service, "_compute_location_page", fake_compute)

    assert build_location_page(None, "izmailovo") == {"slug": "izmailovo"}  # type: ignore[arg-type]
    assert build_location_page(None, "kuzminki") == {"slug": "kuzminki"}  # type: ignore[arg-type]


def test_build_location_page_does_not_cache_missing_location(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: fakeredis.FakeRedis,
) -> None:
    """404 не кэшируем: иначе опечатка в slug залипала бы в Redis на 3 часа."""
    invalidate_location_page_cache("nope")
    calls = {"count": 0}

    def fake_compute(_db: Any, _slug: str, **_kwargs: Any) -> dict[str, object] | None:
        calls["count"] += 1
        return None

    monkeypatch.setattr(location_page_service, "_compute_location_page", fake_compute)

    assert build_location_page(None, "nope") is None  # type: ignore[arg-type]
    assert build_location_page(None, "nope") is None  # type: ignore[arg-type]
    assert calls["count"] == 2


def test_invalidate_location_page_cache_clears_all_three(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: fakeredis.FakeRedis,
) -> None:
    def fake_compute(_db: Any, slug: str, **_kwargs: Any) -> dict[str, object]:
        return {"slug": slug}

    for compute_name, _build in _PER_SLUG_CASES:
        monkeypatch.setattr(location_page_service, compute_name, fake_compute)

    build_location_page(None, "izmailovo")  # type: ignore[arg-type]
    build_location_events(None, "izmailovo")  # type: ignore[arg-type]
    build_location_leaders(None, "izmailovo")  # type: ignore[arg-type]

    # Ключи берём у тех же строителей, что и продовый код: раньше тест сверялся
    # с захардкоженными v3/v1, которых писатели давно не пишут, и «ничего не
    # удалилось» проходило как успех.
    keys = [
        location_page_cache_key("izmailovo"),
        location_events_cache_key("izmailovo"),
        location_leaders_cache_key("izmailovo"),
    ]
    for key in keys:
        assert fake_redis.exists(key), key

    invalidate_location_page_cache("izmailovo")

    for key in keys:
        assert not fake_redis.exists(key), key


# --- Удержание новичков по каждому старту (колонка «Вернулись») ---------------
# Запрос Егора (17.09.2026): на праздничный старт приходит толпа дебютантов, и
# руками считать, кто из них вернулся, невозможно.


def _seed_retention_location(db_session: Any) -> str:
    """Три старта подряд: двое новичков на первом (вернулся один), один на втором."""
    from uuid import uuid4

    from app.models import Event, Location, Participant, Platform, RunResult

    suffix = str(uuid4().int % 1_000_000)
    platform = db_session.query(Platform).filter(Platform.code == "five_verst").one_or_none()
    if platform is None:
        platform = Platform(code="five_verst", name="5 вёрст")
        db_session.add(platform)
        db_session.flush()

    external_key = f"retention-{suffix}"
    location = Location(
        platform_id=platform.id,
        external_key=external_key,
        name=f"Удержание {suffix}",
        country="Россия",
    )
    db_session.add(location)
    db_session.flush()

    events = {}
    for number, event_date in enumerate(
        (date(2026, 8, 1), date(2026, 8, 8), date(2026, 8, 15)), start=1
    ):
        event = Event(
            platform_id=platform.id,
            location_id=location.id,
            external_event_key=f"{external_key}:{number}",
            event_date=event_date,
            event_number=number,
            title="Старт",
        )
        db_session.add(event)
        db_session.flush()
        events[number] = event

    def runner(tag: str) -> Participant:
        participant = Participant(
            platform_id=platform.id,
            external_user_id=f"{external_key}-{tag}",
            display_name=f"Бегун {tag}",
        )
        db_session.add(participant)
        db_session.flush()
        return participant

    def run(participant: Participant, number: int, *, debut: bool) -> None:
        db_session.add(
            RunResult(
                event_id=events[number].id,
                participant_id=participant.id,
                external_result_key=f"{external_key}-{participant.external_user_id}-{number}",
                position=1,
                finish_time_sec=1500,
                is_first_run=debut,
                is_first_run_at_location=debut,
            )
        )

    came_back = runner("returned")
    one_off = runner("one-off")
    late = runner("late")

    run(came_back, 1, debut=True)
    run(came_back, 2, debut=False)
    run(one_off, 1, debut=True)
    run(late, 3, debut=True)
    db_session.flush()
    return external_key


def test_journal_counts_returned_newcomers_per_start(db_session: Any) -> None:
    """«Вернулись» — доля новичков старта, прибежавших сюда ещё раз."""
    slug = _seed_retention_location(db_session)
    payload = location_page_service._compute_location_events(db_session, slug)
    assert payload is not None
    by_date = {str(item["event_date"]): item for item in payload["items"]}

    first = by_date["2026-08-01"]
    assert first["debutants"] == 2
    assert first["debut_returned"] == 1
    assert first["debut_return_pct"] == 50

    # На втором старте новичков не было — делить нечего.
    second = by_date["2026-08-08"]
    assert second["debutants"] == 0
    assert second["debut_returned"] == 0
    assert second["debut_return_pct"] is None


def test_journal_leaves_the_last_start_without_a_retention_number(db_session: Any) -> None:
    """У новичков последнего старта следующей субботы ещё не было — прочерк.

    Ноль здесь читался бы как «никто не вернулся», хотя вернуться было некуда.
    """
    slug = _seed_retention_location(db_session)
    payload = location_page_service._compute_location_events(db_session, slug)
    assert payload is not None
    last = max(payload["items"], key=lambda item: str(item["event_date"]))

    assert str(last["event_date"]) == "2026-08-15"
    assert last["debutants"] == 1
    assert last["debut_returned"] is None
    assert last["debut_return_pct"] is None
