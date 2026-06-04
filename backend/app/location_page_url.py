from __future__ import annotations

PLATFORM_ORDER = ("five_verst", "s95", "runpark", "parkrun")


def location_page_url(
    platform_code: str,
    external_key: str,
    source_url: str | None = None,
) -> str | None:
    slug = (external_key or "").strip()
    canonical: str | None = None
    if slug and slug != "unknown":
        if platform_code == "five_verst":
            canonical = f"https://5verst.ru/{slug}/"
        elif platform_code == "s95":
            canonical = f"https://s95.ru/events/{slug}"
        elif platform_code == "parkrun":
            canonical = f"https://www.parkrun.org.uk/{slug}/"
        elif platform_code == "runpark":
            canonical = f"https://runpark.ru/Places/{slug}"

    stored = (source_url or "").strip()
    if stored:
        lowered = stored.lower()
        if any(marker in lowered for marker in ("/results/", "/activities/", "/protocol")):
            return canonical or stored
        return stored
    return canonical


def pick_primary_location_url(
    platform_codes: list[str],
    *,
    active_platform: str | None = None,
    urls_by_platform: dict[str, str | None] | None = None,
) -> str | None:
    urls = urls_by_platform or {}
    if active_platform:
        url = urls.get(active_platform)
        if url:
            return url
    for code in PLATFORM_ORDER:
        url = urls.get(code)
        if url:
            return url
    for code in platform_codes:
        url = urls.get(code)
        if url:
            return url
    return None
