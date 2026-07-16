from __future__ import annotations

from app.services.portal_home_service import (
    PORTAL_HOME_CACHE_KEY,
    _read_portal_home_cache,
    _write_portal_home_cache,
    clean_time_display,
    format_finish_time,
    invalidate_portal_home_cache,
)


def test_format_finish_time_minutes() -> None:
    assert format_finish_time(16 * 60 + 21) == "16:21"
    assert format_finish_time(59) == "0:59"


def test_format_finish_time_hours() -> None:
    assert format_finish_time(3600 + 5 * 60 + 7) == "1:05:07"


def test_clean_time_display_prefers_seconds() -> None:
    assert clean_time_display("00:16:21", 16 * 60 + 21) == "16:21"


def test_clean_time_display_strips_leading_hours() -> None:
    assert clean_time_display("00:16:21", None) == "16:21"
    assert clean_time_display("00:09:59", None) == "9:59"
    assert clean_time_display(None, None) == ""


def test_cache_round_trip() -> None:
    payload = {"hero": {"finishes_total": 1}, "generated_at": "2026-07-13T00:00:00"}
    assert _read_portal_home_cache() is None
    _write_portal_home_cache(payload)
    assert _read_portal_home_cache() == payload


def test_cache_invalidate(fake_redis) -> None:  # type: ignore[no-untyped-def]
    _write_portal_home_cache({"hero": {}})
    assert fake_redis.get(PORTAL_HOME_CACHE_KEY) is not None
    invalidate_portal_home_cache()
    assert fake_redis.get(PORTAL_HOME_CACHE_KEY) is None
    assert _read_portal_home_cache() is None
