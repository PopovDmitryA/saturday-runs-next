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


def test_normalize_age_group_unknown() -> None:
    assert normalize_age_group(None) is None
    assert normalize_age_group("") is None
    assert normalize_age_group("хостел") is None


def test_age_group_key_is_anchor_safe() -> None:
    """Ключ группы уходит в id строки таблицы — типографских тире там быть не должно."""
    assert age_group_key("male", "30–34") == "male-30-34"
    assert age_group_key("female", "75+") == "female-75plus"


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
