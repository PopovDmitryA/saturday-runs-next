from __future__ import annotations

from datetime import date

from app.platform_adapters.parkrun.url import InvalidProfileUrlError, parse_profile_url

FIVE_VERST_BASE = "https://5verst.ru"


def _s95_protocol_url(url: str) -> bool:
    return "/protocol" in url or "/activities/" in url


def _five_verst_results_url(url: str) -> bool:
    return "/results/" in url


def prefer_event_source_url(
    platform_code: str,
    existing: str | None,
    incoming: str | None,
) -> str | None:
    """Keep protocol/results URLs when profile sync passes a generic location page."""
    current = (existing or "").strip()
    new = (incoming or "").strip()
    if not new:
        return current or None
    if not current:
        return new
    if platform_code == "s95":
        if _s95_protocol_url(current) and not _s95_protocol_url(new):
            return current
    if platform_code == "five_verst":
        if _five_verst_results_url(current) and not _five_verst_results_url(new):
            return current
    return new


def _parkrun_all_results_url(link_url: str | None) -> str | None:
    if not link_url:
        return None
    try:
        return parse_profile_url(link_url).all_results_url
    except InvalidProfileUrlError:
        return None


def resolve_activity_url(
    *,
    platform_code: str,
    event_date: date,
    event_number: int | None,
    event_source_url: str | None,
    location_external_key: str,
    profile_url: str | None = None,
    summary_source_url: str | None = None,
) -> str | None:
    """External URL for a run or volunteering row (platform all-results or event protocol)."""
    if platform_code == "parkrun":
        return _parkrun_all_results_url(profile_url)

    slug = (location_external_key or "").strip()
    if slug == "unknown":
        slug = ""

    stored = (event_source_url or "").strip()
    summary = (summary_source_url or "").strip()

    if platform_code == "s95":
        for candidate in (stored, summary):
            if candidate and _s95_protocol_url(candidate):
                return candidate
        return None

    if stored:
        if platform_code == "five_verst" and _five_verst_results_url(stored):
            return stored
        if platform_code not in ("five_verst", "s95"):
            return stored

    if platform_code == "five_verst" and slug:
        return f"{FIVE_VERST_BASE}/{slug}/results/{event_date.strftime('%d.%m.%Y')}/"

    return None
