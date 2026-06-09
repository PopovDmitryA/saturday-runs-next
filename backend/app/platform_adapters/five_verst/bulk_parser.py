from __future__ import annotations

import enum
import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from app.platform_adapters.canonical import (
    CanonicalEvent,
    CanonicalEventSummary,
    CanonicalLocation,
    CanonicalRunResult,
    CanonicalVolunteerResult,
)
from app.platform_adapters.five_verst.http import BASE_URL, NotFoundError, fetch_html, source_hash  # noqa: F401

DATE_IN_URL_RE = re.compile(r"/results/(\d{2}\.\d{2}\.\d{4})/")
USERSTATS_ID_RE = re.compile(r"/userstats/(\d+)")
PARK_HOME_RE = re.compile(r"^https://5verst\.ru/([a-z0-9]+)/$", re.I)
FIVE_VERST_SLUG_RE = re.compile(r"^https://5verst\.ru/([a-z0-9]+)/?$", re.I)
EVENTS_PAGE_URL = f"{BASE_URL}/events/"
LATEST_RESULTS_URL = f"{BASE_URL}/results/latest/"
TIME_RE = re.compile(r"\b(\d{1,2}):(\d{2}):(\d{2})\b")
AGE_CATEGORY_RE = re.compile(r"([MVЖМ]\d{1,2}(?:-\d{1,2})?)(?:\s*\(\d+\))?")
CLUB_HREF_RE = re.compile(r"/clubs/([^/?#]+)")
YANDEX_PT_RE = re.compile(r"yandex\.ru/maps/\?pt=\s*([0-9.]+),([0-9.]+)", re.I)
LOCATION_RESULTS_ALL_RE = re.compile(r"/([a-z0-9]+)/results/all/?", re.I)
EVENT_NUMBER_IN_TEXT_RE = re.compile(r"#(\d+)\s*$")
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


class LocationRegistryStatus(str, enum.Enum):
    active = "active"
    paused = "paused"
    cancelled = "cancelled"
    preparing = "preparing"


@dataclass(frozen=True)
class ParsedLocationRef:
    slug: str
    name: str
    city: str | None
    country: str | None
    source_url: str


@dataclass(frozen=True)
class ParsedRegistryEntry:
    slug: str
    name: str
    source_url: str
    city_group: str | None
    status: LocationRegistryStatus
    status_note: str | None = None


@dataclass(frozen=True)
class ParsedEventsPage:
    entries: list[ParsedRegistryEntry]
    saturday_cancellations: list[ParsedRegistryEntry]


@dataclass(frozen=True)
class ParsedEventRow:
    event_number: int | None
    event_date: date
    finishers_count: int | None
    volunteers_count: int | None
    source_url: str


def _parse_ru_date(value: str) -> date:
    return datetime.strptime(value.strip(), "%d.%m.%Y").date()


def _time_to_seconds(value: str | None) -> tuple[int | None, str | None]:
    if not value:
        return None, None
    match = TIME_RE.search(value)
    if not match:
        return None, value.strip() or None
    hours, minutes, seconds = (int(match.group(i)) for i in range(1, 4))
    total = hours * 3600 + minutes * 60 + seconds
    return total, match.group(0)


def _location_url(slug: str) -> str:
    return f"{BASE_URL}/{slug}/"


def slug_from_href(href: str) -> str | None:
    normalized = urljoin(BASE_URL, href.strip())
    match = FIVE_VERST_SLUG_RE.match(normalized)
    if not match:
        return None
    slug = match.group(1).lower()
    if slug in RESERVED_SLUGS:
        return None
    return slug


def _registry_status_from_li_text(text: str) -> tuple[LocationRegistryStatus, str | None]:
    lowered = text.lower()
    if "(отмена)" in lowered:
        return LocationRegistryStatus.cancelled, "отмена"
    if "(на паузе)" in lowered:
        return LocationRegistryStatus.paused, "на паузе"
    if "(скоро)" in lowered:
        return LocationRegistryStatus.preparing, "скоро"
    return LocationRegistryStatus.active, None


def _clean_registry_name(raw_name: str) -> str:
    name = raw_name.strip()
    for marker in ("(отмена)", "(на паузе)", "(скоро)"):
        name = name.replace(marker, "")
    return name.strip()


def parse_events_page_html(html: str) -> ParsedEventsPage:
    soup = BeautifulSoup(html, "html.parser")
    entries_by_slug: dict[str, ParsedRegistryEntry] = {}
    saturday_cancellations: list[ParsedRegistryEntry] = []

    for block in soup.select("div.events-columns div.event-block"):
        city_group: str | None = None
        for heading in block.find_all(["h4", "h3", "h2"]):
            city_group = heading.get_text(strip=True) or None
            break
        for item in block.find_all("li"):
            link = item.find("a", href=True)
            if link is None:
                continue
            slug = slug_from_href(link["href"])
            if slug is None:
                continue
            status, status_note = _registry_status_from_li_text(item.get_text(" ", strip=True))
            name = _clean_registry_name(link.get_text(strip=True))
            source_url = _location_url(slug)
            entries_by_slug[slug] = ParsedRegistryEntry(
                slug=slug,
                name=name,
                source_url=source_url,
                city_group=city_group,
                status=status,
                status_note=status_note,
            )

    cancel_list = soup.select_one("div.cancel-list")
    if cancel_list is not None:
        for link in cancel_list.find_all("a", href=True):
            slug = slug_from_href(link["href"])
            if slug is None:
                continue
            name = link.get_text(strip=True)
            saturday_cancellations.append(
                ParsedRegistryEntry(
                    slug=slug,
                    name=name,
                    source_url=_location_url(slug),
                    city_group=None,
                    status=LocationRegistryStatus.cancelled,
                    status_note="отмена на ближайшую субботу",
                )
            )

    entries = sorted(entries_by_slug.values(), key=lambda item: item.slug)
    return ParsedEventsPage(entries=entries, saturday_cancellations=saturday_cancellations)


def fetch_events_page() -> tuple[ParsedEventsPage, str]:
    html = fetch_html(EVENTS_PAGE_URL)
    return parse_events_page_html(html), html


def registry_entry_is_cancelled(status: LocationRegistryStatus) -> bool:
    return status == LocationRegistryStatus.cancelled


def registry_entry_is_paused(status: LocationRegistryStatus) -> bool:
    return status in {
        LocationRegistryStatus.paused,
        LocationRegistryStatus.preparing,
    }


def list_location_slugs(limit: int | None = None) -> list[str]:
    page, _ = fetch_events_page()
    slugs = [entry.slug for entry in page.entries]
    if limit is not None:
        return slugs[:limit]
    return slugs


def _results_all_url(slug: str) -> str:
    return f"{BASE_URL}/{slug}/results/all/"


def _results_date_url(slug: str, event_date: date) -> str:
    return f"{BASE_URL}/{slug}/results/{event_date.strftime('%d.%m.%Y')}/"


def _course_url(slug: str) -> str:
    return f"{BASE_URL}/{slug}/course/"


def _external_event_key(slug: str, event_date: date, event_number: int | None) -> str:
    if event_number is not None:
        return f"{slug}:{event_number}:{event_date.isoformat()}"
    return f"{slug}:{event_date.isoformat()}"


def compute_summary_hash(
    *,
    event_number: int | None,
    event_date: date,
    finishers_count: int | None,
    volunteers_count: int | None,
    avg_time_sec: int | None,
    best_female_time_sec: int | None,
    best_male_time_sec: int | None,
) -> str:
    payload = "|".join(
        [
            str(event_number),
            event_date.isoformat(),
            str(finishers_count),
            str(volunteers_count),
            str(avg_time_sec),
            str(best_female_time_sec),
            str(best_male_time_sec),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_course_coordinates(html: str) -> tuple[float | None, float | None]:
    seen: set[tuple[float, float]] = set()
    for match in YANDEX_PT_RE.finditer(html):
        longitude = float(match.group(1))
        latitude = float(match.group(2))
        key = (longitude, latitude)
        if key in seen:
            continue
        seen.add(key)
        return latitude, longitude
    return None, None


def parse_location_home_html(
    html: str,
    slug: str,
    source_url: str,
    *,
    course_html: str | None = None,
    course_source_url: str | None = None,
) -> CanonicalLocation:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.string.strip() if soup.title and soup.title.string else slug
    title_parts = [part.strip() for part in title.split("|")]
    name = title_parts[1] if len(title_parts) > 1 else slug
    city = title_parts[2] if len(title_parts) > 2 else None
    country = "Россия"

    latitude: float | None = None
    longitude: float | None = None
    if course_html:
        latitude, longitude = parse_course_coordinates(course_html)

    return CanonicalLocation(
        external_key=slug,
        name=name,
        country=country,
        city=city,
        latitude=latitude,
        longitude=longitude,
        source_url=source_url,
        course_source_url=course_source_url or _course_url(slug),
    )


def fetch_location(slug: str) -> tuple[CanonicalLocation, str]:
    source_url = _location_url(slug)
    course_source_url = _course_url(slug)
    home_html = fetch_html(source_url)
    course_html = ""
    try:
        course_html = fetch_html(course_source_url)
    except NotFoundError:
        pass
    location = parse_location_home_html(
        home_html,
        slug,
        source_url,
        course_html=course_html or None,
        course_source_url=course_source_url,
    )
    combined_html = home_html + course_html
    return location, combined_html


def _row_is_test_event(row: Tag) -> bool:
    for img in row.find_all("img"):
        alt = (img.get("alt") or "").lower()
        src = (img.get("src") or "").lower()
        if "тестов" in alt or "test.svg" in src:
            return True
    return False


def _parse_event_date_from_row(row: Tag, raw_cells: list[str]) -> date:
    for link in row.find_all("a", href=True):
        match = DATE_IN_URL_RE.search(link["href"])
        if match:
            return _parse_ru_date(match.group(1))
    return _parse_ru_date(raw_cells[1])


def parse_event_summaries_html(
    html: str,
    slug: str,
    location_name: str,
    limit: int | None = None,
) -> list[CanonicalEventSummary]:
    soup = BeautifulSoup(html, "html.parser")
    summaries: list[CanonicalEventSummary] = []

    for row in soup.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) < 3:
            continue
        header_text = row.get_text(" ", strip=True).lower()
        if "дата" in header_text and "финиш" in header_text:
            continue

        raw_cells = [cell.get_text(" ", strip=True) for cell in cells]
        is_test_event = _row_is_test_event(row)
        if not raw_cells[0].isdigit() and not is_test_event:
            continue

        try:
            event_number = 0 if is_test_event else int(raw_cells[0])
            event_date = _parse_event_date_from_row(row, raw_cells)
        except (ValueError, IndexError):
            continue

        finishers_count = int(raw_cells[2]) if len(raw_cells) > 2 and raw_cells[2].isdigit() else None
        volunteers_count = int(raw_cells[3]) if len(raw_cells) > 3 and raw_cells[3].isdigit() else None

        avg_time_sec, avg_time_display = _time_to_seconds(raw_cells[4] if len(raw_cells) > 4 else None)
        best_female_time_sec, best_female_time_display = _time_to_seconds(raw_cells[5] if len(raw_cells) > 5 else None)
        best_male_time_sec, best_male_time_display = _time_to_seconds(raw_cells[6] if len(raw_cells) > 6 else None)

        source_url = _results_date_url(slug, event_date)
        summary_hash = compute_summary_hash(
            event_number=event_number,
            event_date=event_date,
            finishers_count=finishers_count,
            volunteers_count=volunteers_count,
            avg_time_sec=avg_time_sec,
            best_female_time_sec=best_female_time_sec,
            best_male_time_sec=best_male_time_sec,
        )

        summaries.append(
            CanonicalEventSummary(
                external_event_key=_external_event_key(slug, event_date, event_number),
                event_date=event_date,
                event_number=event_number,
                is_test_event=is_test_event,
                location_external_key=slug,
                location_name=location_name,
                finishers_count=finishers_count,
                volunteers_count=volunteers_count,
                avg_time_sec=avg_time_sec,
                avg_time_display=avg_time_display,
                best_female_time_sec=best_female_time_sec,
                best_female_time_display=best_female_time_display,
                best_male_time_sec=best_male_time_sec,
                best_male_time_display=best_male_time_display,
                source_url=source_url,
                summary_hash=summary_hash,
            )
        )
        if limit is not None and len(summaries) >= limit:
            break

    return summaries


def fetch_event_summaries(
    slug: str,
    location_name: str,
    limit: int | None = None,
) -> tuple[list[CanonicalEventSummary], str]:
    source_url = _results_all_url(slug)
    html = fetch_html(source_url)
    return parse_event_summaries_html(html, slug, location_name, limit=limit), html


def parse_events_all_html(html: str, slug: str, location_name: str, limit: int | None = None) -> list[CanonicalEvent]:
    summaries = parse_event_summaries_html(html, slug, location_name, limit=limit)
    return [
        CanonicalEvent(
            external_event_key=summary.external_event_key,
            event_date=summary.event_date,
            location_external_key=summary.location_external_key,
            location_name=summary.location_name,
            title=f"{summary.location_name} #{summary.event_number}",
            event_number=summary.event_number,
            source_url=summary.source_url,
        )
        for summary in summaries
    ]


def fetch_events(slug: str, location_name: str, limit: int | None = None) -> tuple[list[CanonicalEvent], str]:
    summaries, html = fetch_event_summaries(slug, location_name, limit=limit)
    events = [
        CanonicalEvent(
            external_event_key=summary.external_event_key,
            event_date=summary.event_date,
            location_external_key=summary.location_external_key,
            location_name=summary.location_name,
            title=f"{summary.location_name} #{summary.event_number}",
            event_number=summary.event_number,
            source_url=summary.source_url,
        )
        for summary in summaries
    ]
    return events, html


def _extract_user_id_from_row(row: Tag) -> str | None:
    for link in row.find_all("a", href=True):
        match = USERSTATS_ID_RE.search(link["href"])
        if match:
            return match.group(1)
    return None


def _extract_participant_name_from_row(row: Tag) -> str | None:
    unknown_cell = row.find(class_=re.compile(r"\bunknown\b", re.I))
    if unknown_cell:
        text = unknown_cell.get_text(" ", strip=True)
        if text:
            return text
    for link in row.find_all("a", href=True):
        if USERSTATS_ID_RE.search(link["href"]):
            text = link.get_text(" ", strip=True)
            if text:
                return text
    name_cell = row.find(class_=re.compile(r"row_name|cell-label_name", re.I))
    if name_cell:
        link = name_cell.find("a", href=True)
        if link:
            text = link.get_text(" ", strip=True)
            if text:
                return text
    return None


@dataclass
class ParsedAchievements:
    is_pr: bool = False
    is_first_run: bool = False
    is_first_run_at_location: bool = False
    labels: list[str] = field(default_factory=list)


def _find_stats_cell(row: Tag) -> Tag | None:
    stats_cell = row.find("td", class_=lambda c: c and "row_stats" in c)
    if stats_cell is not None:
        return stats_cell
    cells = row.find_all("td")
    return cells[2] if len(cells) >= 3 else None


def _parse_age_category_from_row(row: Tag) -> str | None:
    stats_cell = _find_stats_cell(row)
    if stats_cell is None:
        return None
    text = stats_cell.get_text(" ", strip=True)
    text = text.split("age grade", 1)[0].strip()
    match = AGE_CATEGORY_RE.search(text)
    return match.group(0) if match else None


def _extract_club_from_row(row: Tag) -> str | None:
    name_cell = row.find("td", class_=lambda c: c and "row_name" in c)
    if name_cell is None:
        cells = row.find_all("td")
        name_cell = cells[1] if len(cells) >= 2 else None
    if name_cell is None:
        return None
    for link in name_cell.find_all("a", href=True):
        if "/clubs/" not in link["href"]:
            continue
        match = CLUB_HREF_RE.search(link["href"])
        if not match or not match.group(1):
            continue
        text = link.get_text(strip=True)
        if text:
            return text
    return None


def _parse_achievements_from_row(row: Tag) -> ParsedAchievements:
    parsed = ParsedAchievements()
    achievements = row.select_one(".table-achievments")
    if achievements is None:
        return parsed
    for img in achievements.find_all("img"):
        alt = (img.get("alt") or "").strip()
        parent = img.find_parent(["span", "a"])
        title = (parent.get("title") or "").strip() if parent else ""
        label = title or alt
        if not label or label in parsed.labels:
            continue
        parsed.labels.append(label)
        icon_src = (img.get("src") or "").lower()
        lower = label.lower()
        if "личный рекорд" in lower or "beaker" in icon_src:
            parsed.is_pr = True
        elif "первый финиш на 5" in lower or "crown" in icon_src:
            parsed.is_first_run = True
        elif "первый финиш" in lower or "thumbs_up" in icon_src:
            parsed.is_first_run_at_location = True
    return parsed


def _extract_finish_time_from_row(row: Tag) -> tuple[str | None, int | None]:
    time_cell = row.find(class_=re.compile(r"cell-label_time", re.I))
    if time_cell is None:
        cells = row.find_all("td")
        time_cell = cells[-1] if cells else None
    if time_cell is None:
        return None, None
    fragments: list[str] = []
    for node in time_cell.find_all(string=True):
        parent = node.parent
        if parent and parent.find_parent(class_=re.compile(r"table-achievments", re.I)):
            continue
        text = str(node).strip()
        if text:
            fragments.append(text)
    text = " ".join(fragments)
    match = TIME_RE.search(text)
    if not match:
        return None, None
    hours, minutes, seconds = (int(part) for part in match.groups())
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}", hours * 3600 + minutes * 60 + seconds


def _row_status(row: Tag, *, has_finish_time: bool, is_unknown: bool) -> str | None:
    if is_unknown:
        return "unknown"
    if has_finish_time:
        return "finished"
    return None


def parse_run_protocol_html(
    html: str,
    *,
    slug: str,
    event_date: date,
    event_number: int | None,
    limit: int | None = None,
) -> list[CanonicalRunResult]:
    soup = BeautifulSoup(html, "html.parser")
    results: list[CanonicalRunResult] = []

    for row in soup.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) < 2:
            continue
        header = row.get_text(" ", strip=True).lower()
        if header.startswith("##") or "участник" in header:
            continue

        position_text = cells[0].get_text(" ", strip=True)
        if not position_text.isdigit():
            continue
        position = int(position_text)

        external_user_id = _extract_user_id_from_row(row)
        participant_name = _extract_participant_name_from_row(row)
        is_unknown = external_user_id is None and participant_name is not None and "неизвест" in participant_name.lower()
        if external_user_id is None and not is_unknown:
            continue
        if is_unknown:
            external_user_id = f"unknown:{slug}:{event_date.isoformat()}:{position}"
            participant_name = participant_name or "НЕИЗВЕСТНЫЙ"
        elif participant_name is None:
            participant_name = f"Unknown #{position}"

        finish_time_display, finish_time_sec = _extract_finish_time_from_row(row)
        age_category = _parse_age_category_from_row(row)
        club_name = _extract_club_from_row(row)
        achievements = _parse_achievements_from_row(row)

        results.append(
            CanonicalRunResult(
                external_result_key=f"{slug}:{event_date.isoformat()}:{external_user_id}",
                event_date=event_date,
                external_user_id=external_user_id,
                participant_name=participant_name,
                position=position,
                finish_time_sec=finish_time_sec,
                finish_time_display=finish_time_display,
                age_category=age_category,
                status=_row_status(row, has_finish_time=finish_time_sec is not None, is_unknown=is_unknown),
                is_pr=achievements.is_pr,
                is_first_run=achievements.is_first_run,
                is_first_run_at_location=achievements.is_first_run_at_location,
                club_name=club_name,
                achievement_labels=achievements.labels,
                location_external_key=slug,
                event_number=event_number,
            )
        )
        if limit is not None and len(results) >= limit:
            break

    return results


def fetch_event_protocol(
    slug: str,
    event_date: date,
    event_number: int | None,
    *,
    run_limit: int | None = None,
    volunteer_limit: int | None = None,
) -> tuple[list[CanonicalRunResult], list[CanonicalVolunteerResult], str]:
    """One HTTP request per event page — runs and volunteers share the same HTML."""
    source_url = _results_date_url(slug, event_date)
    html = fetch_html(source_url, reason="protocol")
    run_results = parse_run_protocol_html(
        html,
        slug=slug,
        event_date=event_date,
        event_number=event_number,
        limit=run_limit,
    )
    volunteers = parse_volunteers_from_event_html(
        html,
        slug=slug,
        event_date=event_date,
        event_number=event_number,
        limit=volunteer_limit,
    )
    return run_results, volunteers, html


def fetch_run_protocol(
    slug: str,
    event_date: date,
    event_number: int | None,
    *,
    limit: int | None = None,
) -> tuple[list[CanonicalRunResult], str]:
    source_url = _results_date_url(slug, event_date)
    html = fetch_html(source_url)
    return parse_run_protocol_html(
        html,
        slug=slug,
        event_date=event_date,
        event_number=event_number,
        limit=limit,
    ), html


def _normalize_role_key(role: str) -> str:
    return re.sub(r"[^\w]+", "_", role.lower(), flags=re.UNICODE).strip("_") or "volunteer"


def parse_volunteers_from_event_html(
    html: str,
    *,
    slug: str,
    event_date: date,
    event_number: int | None = None,
    limit: int | None = None,
) -> list[CanonicalVolunteerResult]:
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find(string=re.compile(r"Команда организаторов", re.I))
    if heading is None:
        return []

    heading_el = heading.find_parent(["h2", "h3", "h4"])
    if heading_el is None:
        return []

    table = heading_el.find_next("table")
    if table is None:
        return []

    source_url = _results_date_url(slug, event_date)
    results: list[CanonicalVolunteerResult] = []

    for row in table.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) < 2:
            continue

        header = row.get_text(" ", strip=True).lower()
        if "волонт" in header and "роль" in header:
            continue

        external_user_id = _extract_user_id_from_row(row)
        participant_name = _extract_participant_name_from_row(row)
        if external_user_id is None:
            continue

        role = cells[-1].get_text(" ", strip=True) or "volunteer"
        role_key = _normalize_role_key(role)

        results.append(
            CanonicalVolunteerResult(
                external_result_key=f"{slug}:{event_date.isoformat()}:vol:{external_user_id}:{role_key}",
                event_date=event_date,
                external_user_id=external_user_id,
                participant_name=participant_name or f"User {external_user_id}",
                role=role,
                source_url=source_url,
                location_external_key=slug,
                event_number=event_number,
            )
        )
        if limit is not None and len(results) >= limit:
            break

    return results


def fetch_volunteers(
    slug: str,
    event_date: date,
    event_number: int | None = None,
    *,
    limit: int | None = None,
) -> tuple[list[CanonicalVolunteerResult], str]:
    source_url = _results_date_url(slug, event_date)
    html = fetch_html(source_url)
    volunteers = parse_volunteers_from_event_html(
        html,
        slug=slug,
        event_date=event_date,
        event_number=event_number,
        limit=limit,
    )
    return volunteers, html


def _parse_latest_results_row(row: Tag) -> CanonicalEventSummary | None:
    cells = row.find_all("td")
    if len(cells) < 3:
        return None

    link = cells[0].find("a", href=True)
    if link is None:
        return None
    slug_match = LOCATION_RESULTS_ALL_RE.search(link["href"])
    if slug_match is None:
        return None
    slug = slug_match.group(1).lower()
    if slug in RESERVED_SLUGS:
        return None

    link_text = link.get_text(" ", strip=True)
    number_match = EVENT_NUMBER_IN_TEXT_RE.search(link_text)
    is_test_event = _row_is_test_event(row)
    if number_match is None and not is_test_event:
        return None
    event_number = 0 if is_test_event else int(number_match.group(1)) if number_match else None
    location_name = link_text[: number_match.start()].strip() if number_match else link_text

    raw_cells = [cell.get_text(" ", strip=True) for cell in cells]
    try:
        event_date = _parse_ru_date(raw_cells[1])
    except (ValueError, IndexError):
        return None

    finishers_count = int(raw_cells[2]) if len(raw_cells) > 2 and raw_cells[2].isdigit() else None
    volunteers_count = int(raw_cells[3]) if len(raw_cells) > 3 and raw_cells[3].isdigit() else None
    avg_time_sec, avg_time_display = _time_to_seconds(raw_cells[4] if len(raw_cells) > 4 else None)
    best_female_time_sec, best_female_time_display = _time_to_seconds(raw_cells[5] if len(raw_cells) > 5 else None)
    best_male_time_sec, best_male_time_display = _time_to_seconds(raw_cells[6] if len(raw_cells) > 6 else None)

    summary_hash = compute_summary_hash(
        event_number=event_number,
        event_date=event_date,
        finishers_count=finishers_count,
        volunteers_count=volunteers_count,
        avg_time_sec=avg_time_sec,
        best_female_time_sec=best_female_time_sec,
        best_male_time_sec=best_male_time_sec,
    )
    return CanonicalEventSummary(
        external_event_key=_external_event_key(slug, event_date, event_number),
        event_date=event_date,
        event_number=event_number,
        is_test_event=is_test_event,
        location_external_key=slug,
        location_name=location_name,
        finishers_count=finishers_count,
        volunteers_count=volunteers_count,
        avg_time_sec=avg_time_sec,
        avg_time_display=avg_time_display,
        best_female_time_sec=best_female_time_sec,
        best_female_time_display=best_female_time_display,
        best_male_time_sec=best_male_time_sec,
        best_male_time_display=best_male_time_display,
        source_url=_results_date_url(slug, event_date),
        summary_hash=summary_hash,
    )


def parse_latest_results_html(html: str, *, limit: int | None = None) -> list[CanonicalEventSummary]:
    soup = BeautifulSoup(html, "html.parser")
    summaries: list[CanonicalEventSummary] = []
    for row in soup.select("table.results-table tr"):
        if row.find("th"):
            continue
        summary = _parse_latest_results_row(row)
        if summary is None:
            continue
        summaries.append(summary)
        if limit is not None and len(summaries) >= limit:
            break
    return summaries


def fetch_latest_results(*, limit: int | None = None) -> tuple[list[CanonicalEventSummary], str]:
    html = fetch_html(LATEST_RESULTS_URL)
    return parse_latest_results_html(html, limit=limit), html


def list_park_slugs(limit: int | None = None) -> list[str]:
    """Deprecated alias: registry slugs from /events/ (not /parks/)."""
    return list_location_slugs(limit=limit)
