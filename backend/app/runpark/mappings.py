from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

DUP_MATCH_RE = re.compile(
    r"^(?P<platform>five_verst|s95|parkrun):\s*(?P<slug>[^/]+)\s*/",
    re.IGNORECASE,
)
RUNPARK_SLUG_RE = re.compile(r"/Places/([^/?#]+)", re.IGNORECASE)
RUNPARK_PLACES_BASE = "https://runpark.ru/Places"
KEEP_NAME_RE = re.compile(r'Оставляем\s+с\s+названием\s*["«](.+?)["»]?\s*$', re.IGNORECASE)
MATCH_PARKRUN_NAME_RE = re.compile(r'фиксируем\s*["«](.+?)["»]', re.IGNORECASE)


class RunparkMappingDecision(StrEnum):
    REMOVE_FROM_RUNPARK = "remove_from_runpark"
    KEEP_RUNPARK = "keep_runpark"
    MATCH_PARKRUN = "match_parkrun"
    TRANSITIONAL = "transitional"


MANUAL_MATCHES: dict[str, tuple[str, str]] = {
    "gorkypark": ("five_verst", "gorkypark"),
    "mytishchicentralpark": ("five_verst", "mytishchicentralpark"),
    "zhukovsky": ("five_verst", "zhukovsky"),
}

LEGACY_PARKRUN_SLUGS: dict[str, str] = {
    "mytishchicentralpark": "mytishchicentralpark",
    "zhukovsky": "zhukovsky",
    "PushKino": "lesopark-severny",
}


def parse_bool_flag(value: object | None) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "да"}


@dataclass
class RunparkMappingEntry:
    runpark_location_id: str
    runpark_slug: str | None
    runpark_name: str
    display_name: str | None
    decision: str
    show_on_map: bool
    is_transitional: bool
    latitude: float | None
    longitude: float | None
    city: str | None
    region: str | None
    country: str | None
    matched_platform: str | None
    matched_external_key: str | None
    legacy_parkrun_slug: str | None
    public_url: str | None
    duplicate_match_text: str | None
    decision_raw: str | None
    source_batch: str
    notes: str | None = None
    is_paused: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_runpark_slug(public_url: str | None) -> str | None:
    if not public_url:
        return None
    match = RUNPARK_SLUG_RE.search(str(public_url))
    return match.group(1) if match else None


def runpark_protocol_base(public_url: str | None, runpark_slug: str | None) -> str | None:
    """Base runpark.ru URL of a location's protocols, i.e. ``.../Places/{slug}``.

    Prefers the mapping's stored ``public_url`` — the location's own ``external_key``
    is NOT a safe slug source: it may carry a ``runpark-`` prefix or diverge entirely
    (``runpark-prityazhenie`` → real slug ``magnitprityagenie``). Falls back to the
    parsed ``runpark_slug`` when ``public_url`` is missing.
    """
    base = (public_url or "").strip().rstrip("/")
    if base:
        return base
    slug = (runpark_slug or "").strip().strip("/")
    if slug:
        return f"{RUNPARK_PLACES_BASE}/{slug}"
    return None


def runpark_protocol_url(
    public_url: str | None,
    runpark_slug: str | None,
    event_number: int | None,
) -> str | None:
    """URL of a RunPark event protocol page: ``.../Places/{slug}/event/{n}``."""
    if not event_number:
        return None
    base = runpark_protocol_base(public_url, runpark_slug)
    return f"{base}/event/{event_number}" if base else None


def parse_duplicate_match(text: str | None) -> tuple[str | None, str | None]:
    if not text:
        return None, None
    match = DUP_MATCH_RE.match(str(text).strip())
    if match is None:
        return None, None
    platform = match.group("platform").lower()
    if platform == "five_verst":
        platform = "five_verst"
    return platform, match.group("slug").strip()


def classify_decision(decision_raw: str | None) -> tuple[str, bool, bool, str | None]:
    text = (decision_raw or "").strip()
    lowered = text.lower()

    if "под вопросом" in lowered or "parkrun" in lowered and "5 верст" in lowered:
        return RunparkMappingDecision.TRANSITIONAL, False, True, None

    if "фиксируем" in lowered and "parkrun" in lowered:
        name_match = MATCH_PARKRUN_NAME_RE.search(text)
        display_name = name_match.group(1).strip() if name_match else None
        return RunparkMappingDecision.MATCH_PARKRUN, True, False, display_name

    if lowered.startswith("оставляем"):
        name_match = KEEP_NAME_RE.search(text)
        display_name = name_match.group(1).strip() if name_match else None
        return RunparkMappingDecision.KEEP_RUNPARK, True, False, display_name

    if "удаляем" in lowered:
        return RunparkMappingDecision.REMOVE_FROM_RUNPARK, False, False, None

    raise ValueError(f"Unknown runpark mapping decision: {text!r}")


def parse_xlsx_row(row: tuple[Any, ...], *, source_batch: str) -> RunparkMappingEntry:
    (
        location_id,
        name,
        duplicate_match_text,
        decision_raw,
        city,
        region,
        country,
        latitude,
        longitude,
        is_active,
        is_paused,
        public_url,
        *_rest,
    ) = row[:12]

    runpark_slug = parse_runpark_slug(str(public_url) if public_url else None)
    decision, show_on_map, is_transitional, display_name = classify_decision(
        str(decision_raw) if decision_raw else None
    )

    matched_platform, matched_external_key = parse_duplicate_match(
        str(duplicate_match_text) if duplicate_match_text else None
    )
    if runpark_slug and runpark_slug in MANUAL_MATCHES and matched_platform is None:
        matched_platform, matched_external_key = MANUAL_MATCHES[runpark_slug]

    legacy_parkrun_slug = LEGACY_PARKRUN_SLUGS.get(runpark_slug or "")

    lat = float(latitude) if latitude not in (None, "") else None
    lon = float(longitude) if longitude not in (None, "") else None

    notes = None
    if is_transitional:
        notes = "parkrun → runpark → 5verst; runpark stats merge TBD"

    return RunparkMappingEntry(
        runpark_location_id=str(location_id).strip().upper(),
        runpark_slug=runpark_slug,
        runpark_name=str(name or "").strip(),
        display_name=display_name,
        decision=decision,
        show_on_map=show_on_map,
        is_transitional=is_transitional,
        is_paused=parse_bool_flag(is_paused),
        latitude=lat,
        longitude=lon,
        city=str(city).strip() if city else None,
        region=str(region).strip() if region else None,
        country=str(country).strip() if country else None,
        matched_platform=matched_platform,
        matched_external_key=matched_external_key,
        legacy_parkrun_slug=legacy_parkrun_slug,
        public_url=str(public_url).strip() if public_url else None,
        duplicate_match_text=str(duplicate_match_text).strip() if duplicate_match_text else None,
        decision_raw=str(decision_raw).strip() if decision_raw else None,
        source_batch=source_batch,
        notes=notes,
    )


def fill_missing_geo(entry: RunparkMappingEntry, geo: dict[str, str | None]) -> RunparkMappingEntry:
    city = entry.city or geo.get("city")
    region = entry.region or geo.get("region")
    country = entry.country or geo.get("country")
    return RunparkMappingEntry(
        **{
            **entry.to_dict(),
            "city": city,
            "region": region,
            "country": country,
        }
    )
