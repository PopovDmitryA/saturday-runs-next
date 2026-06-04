from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.core.rate_limit import get_redis

_STATS_TTL_SECONDS = 400 * 86400


def _day_key(day: date, metric: str) -> str:
    return f"stats:day:{day.isoformat()}:{metric}"


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _incr_day(metric: str, *, day: date | None = None, amount: int = 1) -> None:
    redis = get_redis()
    key = _day_key(day or _today(), metric)
    value = int(redis.incrby(key, amount))
    if value == amount:
        redis.expire(key, _STATS_TTL_SECONDS)


def classify_page_path(path: str) -> str:
    normalized = (path or "/").split("?", 1)[0].rstrip("/") or "/"
    if normalized == "/":
        return "landing"
    if normalized.startswith("/demo"):
        return "demo"
    if normalized == "/login":
        return "login"
    if normalized == "/about":
        return "about"
    if normalized.startswith("/admin"):
        return "admin"
    if normalized in {
        "/dashboard",
        "/profiles",
        "/runs",
        "/volunteering",
        "/maps",
        "/settings",
        "/sync",
    }:
        return "app"
    return "other"


def _normalize_visitor_key(visitor_key: str | None) -> str | None:
    if not visitor_key:
        return None
    cleaned = visitor_key.strip()
    if not cleaned or len(cleaned) > 80:
        return None
    if cleaned.startswith("u:"):
        user_part = cleaned[2:]
        if 8 <= len(user_part) <= 40 and all(ch in "0123456789abcdef-" for ch in user_part.lower()):
            return cleaned.lower()
        return None
    if cleaned.startswith("a:"):
        anon_part = cleaned[2:]
        if 8 <= len(anon_part) <= 64:
            return cleaned
        return None
    return None


def _record_unique_visitor(visitor_key: str, *, day: date | None = None) -> None:
    redis = get_redis()
    key = _day_key(day or _today(), "uv")
    added = int(redis.pfadd(key, visitor_key))
    if added:
        redis.expire(key, _STATS_TTL_SECONDS)


def count_unique_visitors(day: date) -> int:
    redis = get_redis()
    return int(redis.pfcount(_day_key(day, "uv")))


def record_pageview(path: str, *, authenticated: bool, visitor_key: str | None = None) -> None:
    category = classify_page_path(path)
    _incr_day("pv:total")
    _incr_day(f"pv:{category}")
    if authenticated:
        _incr_day("pv:authenticated")
    else:
        _incr_day("pv:anonymous")
    normalized_visitor = _normalize_visitor_key(visitor_key)
    if normalized_visitor is not None:
        _record_unique_visitor(normalized_visitor)


def record_login() -> None:
    _incr_day("login")


def record_login_request() -> None:
    _incr_day("login_request")


def read_daily_series(metric: str, *, days: int) -> list[dict[str, object]]:
    redis = get_redis()
    today = _today()
    series: list[dict[str, object]] = []
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        raw = redis.get(_day_key(day, metric))
        series.append({"date": day.isoformat(), "value": int(raw) if raw else 0})
    return series


def read_pageviews_by_day(*, days: int) -> list[dict[str, object]]:
    categories = ("total", "landing", "demo", "app", "login", "about", "admin", "other", "authenticated", "anonymous")
    today = _today()
    redis = get_redis()
    rows: list[dict[str, object]] = []
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        row: dict[str, object] = {"date": day.isoformat()}
        for category in categories:
            metric = "pv:total" if category == "total" else f"pv:{category}"
            raw = redis.get(_day_key(day, metric))
            row[category] = int(raw) if raw else 0
        row["unique_visitors"] = int(redis.pfcount(_day_key(day, "uv")))
        rows.append(row)
    return rows
