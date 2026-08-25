from __future__ import annotations

import datetime as dt
import re
from datetime import date
from urllib.parse import urlparse

from app.time_format import format_finish_time_display, parse_finish_time

FIVE_VERST_SLUG_RE = re.compile(r"5verst\.ru/([^/\"'\s?#]+)", re.I)
S95_SLUG_RE = re.compile(r"s95\.(?:ru|by|rs)/events/([^/\"'\s?#]+)", re.I)
PARKRUN_SLUG_RE = re.compile(r"parkrun\.(?:org\.uk|ru|com)/([^/\"'\s?#]+)", re.I)

RESERVED_SLUGS = frozenset(
    {
        "events",
        "parks",
        "results",
        "roadmap",
        "feed",
        "comments",
        "wp-admin",
        "userstats",
        "news",
        "about",
        "contacts",
    }
)


def slug_from_5verst_url(url: str | None) -> str | None:
    if not url:
        return None
    match = FIVE_VERST_SLUG_RE.search(url.strip())
    if match is None:
        return None
    slug = match.group(1).lower()
    if slug in RESERVED_SLUGS:
        return None
    return slug


def slug_from_s95_url(url: str | None) -> str | None:
    if not url:
        return None
    match = S95_SLUG_RE.search(url.strip())
    return match.group(1).lower() if match else None


S95_DOMAIN_COUNTRY = {
    "s95.rs": "Сербия",
    "s95.by": "Беларусь",
    "s95.ru": "Россия",
}


def s95_country_from_url(url: str | None) -> str:
    """Страна локации s95 по домену: страну сама система нигде не отдаёт, а домен
    её задаёт однозначно — s95.rs это Сербия, s95.by Беларусь, s95.ru Россия.

    Раньше сверка шла по подстроке «s95.by/» — голый домен без пути (а именно он
    приходит из реестра как `entry.domain`) в неё не попадал и молча становился
    Россией. Сравниваем хост целиком.
    """
    if not url:
        return "Россия"
    lowered = url.strip().lower()
    host = urlparse(lowered if "//" in lowered else f"//{lowered}").hostname or ""
    host = host.removeprefix("www.")
    if host in S95_DOMAIN_COUNTRY:
        return S95_DOMAIN_COUNTRY[host]
    for domain, country in S95_DOMAIN_COUNTRY.items():
        if host.endswith(f".{domain}"):
            return country
    return "Россия"


def is_five_verst_unknown_runner(
    *,
    user_id: str | None,
    name_runner: str | None,
    status_runner: str | None,
) -> bool:
    cleaned_user_id = (user_id or "").strip()
    if cleaned_user_id:
        return False
    status = (status_runner or "").strip().lower()
    if status == "unknown_runner":
        return True
    name = (name_runner or "").strip().lower()
    return "неизвест" in name


def five_verst_unknown_user_id(slug: str, event_date: date, position: int) -> str:
    return f"unknown:{slug}:{event_date.isoformat()}:{position}"


def slugify_parkrun_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower().strip())
    return slug.strip("-") or "unknown"


def normalize_role_key(role: str) -> str:
    return re.sub(r"[^\w]+", "_", role.lower(), flags=re.UNICODE).strip("_") or "volunteer"


def five_verst_event_key(slug: str, event_date: date, event_number: int | None) -> str:
    if event_number is not None:
        return f"{slug}:{event_number}:{event_date.isoformat()}"
    return f"{slug}:{event_date.isoformat()}"


def five_verst_run_key(slug: str, event_date: date, user_id: str) -> str:
    return f"{slug}:{event_date.isoformat()}:{user_id}"


def five_verst_volunteer_key(slug: str, event_date: date, user_id: str, role: str) -> str:
    role_key = normalize_role_key(role)
    return f"{slug}:{event_date.isoformat()}:vol:{user_id}:{role_key}"


def s95_event_key(slug: str, event_date: date) -> str:
    return f"{slug}:{event_date.isoformat()}"


def s95_run_key(slug: str, event_date: date, user_id: str, position: int | None) -> str:
    return f"{slug}:{event_date.isoformat()}:{user_id}:{position or 0}"


def s95_unknown_user_id(slug: str, event_date: date, position: int) -> str:
    return f"unknown:{slug}:{event_date.isoformat()}:{position}"


def s95_volunteer_key(slug: str, event_date: date, user_id: str, role: str) -> str:
    """Same shape as app.s95.parsers.protocol — one row per event/role in DB."""
    from app.s95.parsers.volunteer_roles import s95_volunteer_role_key

    role_key = s95_volunteer_role_key(role)
    return f"vol:{slug}:{event_date.isoformat()}:{user_id}:{role_key}"


def is_s95_unknown_runner(
    *,
    user_id: str | None,
    status_runner: str | None,
) -> bool:
    cleaned_user_id = (user_id or "").strip()
    if cleaned_user_id:
        return False
    return (status_runner or "").strip().lower() == "unknown_runner"


def legacy_time_to_seconds(value: object | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, dt.timedelta):
        return int(value.total_seconds())
    if isinstance(value, dt.time):
        return value.hour * 3600 + value.minute * 60 + value.second
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    parsed_sec, _ = parse_finish_time(text)
    return parsed_sec


def legacy_time_display(value: object | None, seconds: int | None) -> str | None:
    if seconds is not None:
        return format_finish_time_display(seconds)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def legacy_timestamp_to_date(value: object | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, dt.datetime):
        return value
    if isinstance(value, dt.datetime):
        return value.date()
    text = str(value).strip()[:10]
    if len(text) == 10 and text[4] == "-":
        return date.fromisoformat(text)
    return None


def parse_float(value: object | None) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None
