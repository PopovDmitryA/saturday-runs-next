from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import replace
from datetime import date

from bs4 import BeautifulSoup, Tag

from app.platform_adapters.canonical import (
    CanonicalParticipant,
    CanonicalRunResult,
    CanonicalVolunteerResult,
    ProfilePreview,
)
from app.platform_adapters.five_verst.http import NotFoundError, fetch_html
from app.platform_adapters.five_verst.url import ParsedProfileUrl, userstats_url

RESULTS_LINK_RE = re.compile(
    r"5verst\.ru/(?P<slug>[^/]+)/results/(?P<date>\d{2}\.\d{2}\.\d{4})",
    re.IGNORECASE,
)
EVENT_LABEL_RE = re.compile(r"^(?P<name>.+?)\s+#(?P<number>\d+)\s*$")
FINISH_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2})$")
DATE_DISPLAY_RE = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})$")


class ProfileNotFoundError(LookupError):
    pass


class ProfileParseError(ValueError):
    pass


def fetch_userstats_html(url: str) -> str:
    try:
        return fetch_html(url)
    except NotFoundError as exc:
        raise ProfileNotFoundError(str(exc)) from exc


def _extract_display_name(soup: BeautifulSoup) -> str | None:
    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    title_match = re.search(r"Статистика участника\s+(.+?)(?:\s*\||\s*$)", title, re.IGNORECASE)
    if title_match:
        return title_match.group(1).strip()

    h1 = soup.find("h1")
    if h1:
        h1_text = h1.get_text(" ", strip=True)
        h1_match = re.search(r"Статистика участника\s+(.+)", h1_text, re.IGNORECASE)
        if h1_match:
            return h1_match.group(1).strip()
        if h1_text:
            return h1_text

    return None


def _extract_stat_value(soup: BeautifulSoup, label: str) -> int | None:
    text = soup.get_text(" ", strip=True)
    pattern = rf"{re.escape(label)}\s*[^\d]{{0,20}}(\d+)"
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def _extract_club_name(soup: BeautifulSoup) -> str | None:
    for node in soup.find_all(string=re.compile(r"Клуб", re.IGNORECASE)):
        parent = node.parent
        if parent is None:
            continue
        container = parent.parent if parent.parent else parent
        text = container.get_text(" ", strip=True)
        club_match = re.search(r"Клуб[:\s]+(.+)", text, re.IGNORECASE)
        if club_match:
            return club_match.group(1).strip()
    return None


def parse_userstats_html(html: str, parsed_url: ParsedProfileUrl) -> CanonicalParticipant:
    soup = BeautifulSoup(html, "html.parser")
    display_name = _extract_display_name(soup)
    if not display_name:
        raise ProfileParseError("Не удалось определить имя участника на странице профиля")

    total_runs = _extract_stat_value(soup, "Всего финишей")
    total_volunteering = _extract_stat_value(soup, "Всего волонтёрств")
    if total_volunteering is None:
        total_volunteering = _extract_stat_value(soup, "Всего волонтерств")

    return CanonicalParticipant(
        external_user_id=parsed_url.user_id,
        display_name=display_name,
        profile_url=parsed_url.canonical_url,
        total_runs=total_runs,
        total_volunteering=total_volunteering,
        club_name=_extract_club_name(soup),
        source_url=parsed_url.canonical_url,
    )


def fetch_and_parse_profile(parsed_url: ParsedProfileUrl) -> CanonicalParticipant:
    html = fetch_userstats_html(parsed_url.canonical_url)
    return parse_userstats_html(html, parsed_url)


def participant_to_preview(
    participant: CanonicalParticipant,
    *,
    runs: list[CanonicalRunResult] | None = None,
    volunteering: list[CanonicalVolunteerResult] | None = None,
) -> ProfilePreview:
    from app.profile_preview import build_recent_preview_activities

    recent_activities = build_recent_preview_activities(
        runs or [],
        volunteering or [],
    )
    return ProfilePreview(
        external_user_id=participant.external_user_id,
        display_name=participant.display_name,
        profile_url=participant.profile_url,
        total_runs=participant.total_runs,
        total_volunteering=participant.total_volunteering,
        club_name=participant.club_name,
        platform_code="five_verst",
        recent_activities=recent_activities,
    )


def _parse_display_date(value: str) -> date | None:
    match = DATE_DISPLAY_RE.match(value.strip())
    if not match:
        return None
    day, month, year = match.groups()
    return date(int(year), int(month), int(day))


def _parse_finish_time(value: str) -> tuple[int | None, str | None]:
    match = FINISH_TIME_RE.match(value.strip())
    if not match:
        return None, None
    hours, minutes, seconds = (int(part) for part in match.groups())
    display = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return hours * 3600 + minutes * 60 + seconds, display


def _parse_event_label(value: str) -> tuple[str, int | None]:
    match = EVENT_LABEL_RE.match(value.strip())
    if match:
        return match.group("name").strip(), int(match.group("number"))
    return value.strip(), None


def _extract_results_link(row: Tag) -> str | None:
    for link in row.find_all("a", href=True):
        href = link["href"]
        if RESULTS_LINK_RE.search(href):
            return href
    return None


def _parse_results_link(href: str) -> tuple[str, date] | None:
    match = RESULTS_LINK_RE.search(href)
    if not match:
        return None
    slug = match.group("slug")
    event_date = _parse_display_date(match.group("date"))
    if event_date is None:
        return None
    return slug, event_date


def _table_header_texts(table: Tag) -> list[str]:
    header_row = table.find("tr")
    if header_row is None:
        return []
    return [cell.get_text(" ", strip=True).lower() for cell in header_row.find_all(["th", "td"])]


def _find_table(soup: BeautifulSoup, required_headers: set[str]) -> Tag | None:
    for table in soup.find_all("table"):
        headers = set(_table_header_texts(table))
        if required_headers.issubset(headers):
            return table
    return None


def _external_event_key(slug: str, event_date: date, event_number: int | None) -> str:
    if event_number is not None:
        return f"{slug}:{event_number}:{event_date.isoformat()}"
    return f"{slug}:{event_date.isoformat()}"


def parse_userstats_runs_html(
    html: str,
    external_user_id: str,
    participant_name: str,
) -> list[CanonicalRunResult]:
    soup = BeautifulSoup(html, "html.parser")
    table = _find_table(soup, {"дата", "мероприятие", "время"})
    if table is None:
        return []

    results: list[CanonicalRunResult] = []
    for row in table.find_all("tr")[1:]:
        cells = row.find_all(["td", "th"])
        if len(cells) < 3:
            continue

        event_date = _parse_display_date(cells[0].get_text(" ", strip=True))
        if event_date is None:
            continue

        event_label = cells[1].get_text(" ", strip=True)
        location_name, event_number = _parse_event_label(event_label)
        finish_time_sec, finish_time_display = _parse_finish_time(cells[2].get_text(" ", strip=True))

        link = _extract_results_link(row)
        slug = ""
        if link:
            parsed_link = _parse_results_link(link)
            if parsed_link:
                slug, link_date = parsed_link
                event_date = link_date

        location_key = slug or (location_name or "unknown").lower().replace(" ", "_")
        results.append(
            CanonicalRunResult(
                external_result_key=f"{external_user_id}:{event_date.isoformat()}:{location_key}",
                event_date=event_date,
                external_user_id=external_user_id,
                participant_name=participant_name,
                position=None,
                finish_time_sec=finish_time_sec,
                finish_time_display=finish_time_display,
                location_external_key=slug or location_key,
                location_name=location_name,
                event_number=event_number,
            )
        )
    return dedupe_profile_runs(results, external_user_id)


def parse_userstats_volunteering_html(
    html: str,
    external_user_id: str,
    participant_name: str,
) -> list[CanonicalVolunteerResult]:
    soup = BeautifulSoup(html, "html.parser")
    table = _find_table(soup, {"дата", "мероприятие", "роль"})
    if table is None:
        return []

    results: list[CanonicalVolunteerResult] = []
    for row in table.find_all("tr")[1:]:
        cells = row.find_all(["td", "th"])
        if len(cells) < 3:
            continue

        event_date = _parse_display_date(cells[0].get_text(" ", strip=True))
        if event_date is None:
            continue

        event_label = cells[1].get_text(" ", strip=True)
        location_name, event_number = _parse_event_label(event_label)
        role = cells[2].get_text(" ", strip=True)
        if not role:
            continue

        link = _extract_results_link(row)
        slug = ""
        source_url = ""
        if link:
            source_url = link
            parsed_link = _parse_results_link(link)
            if parsed_link:
                slug, link_date = parsed_link
                event_date = link_date

        external_event_key = _external_event_key(slug or location_name, event_date, event_number)
        results.append(
            CanonicalVolunteerResult(
                external_result_key=f"{external_user_id}:{external_event_key}",
                event_date=event_date,
                external_user_id=external_user_id,
                participant_name=participant_name,
                role=role,
                source_url=source_url,
                location_external_key=slug,
                location_name=location_name,
                event_number=event_number,
            )
        )
    return dedupe_profile_volunteering(results, external_user_id)


def _run_location_key(item: CanonicalRunResult) -> str:
    if item.location_external_key:
        return item.location_external_key
    return (item.location_name or "unknown").lower().replace(" ", "_")


def _profile_run_result_key(
    item: CanonicalRunResult,
    external_user_id: str,
    *,
    location_key: str | None = None,
) -> CanonicalRunResult:
    loc = location_key or _run_location_key(item)
    return replace(
        item,
        external_result_key=f"{external_user_id}:{item.event_date.isoformat()}:{loc}",
        location_external_key=item.location_external_key or loc,
    )


def dedupe_profile_runs(
    items: list[CanonicalRunResult],
    external_user_id: str,
) -> list[CanonicalRunResult]:
    """5verst profile: one run per calendar day per location (Jan 1: one per unique location)."""
    by_date: dict[date, list[CanonicalRunResult]] = defaultdict(list)
    for item in items:
        by_date[item.event_date].append(item)

    deduped: list[CanonicalRunResult] = []
    for event_date in sorted(by_date.keys(), reverse=True):
        day_items = by_date[event_date]
        if _is_new_years_day(event_date):
            seen_locations: set[str] = set()
            for item in day_items:
                location_key = _run_location_key(item)
                if location_key in seen_locations:
                    continue
                seen_locations.add(location_key)
                deduped.append(
                    _profile_run_result_key(item, external_user_id, location_key=location_key)
                )
            continue

        by_location: dict[str, CanonicalRunResult] = {}
        for item in day_items:
            location_key = _run_location_key(item)
            existing = by_location.get(location_key)
            if existing is None:
                by_location[location_key] = item
                continue
            if item.finish_time_sec is not None and existing.finish_time_sec is None:
                by_location[location_key] = item
        for location_key, item in by_location.items():
            deduped.append(_profile_run_result_key(item, external_user_id, location_key=location_key))

    return deduped


def _volunteering_location_key(item: CanonicalVolunteerResult) -> str:
    if item.location_external_key and item.location_external_key.strip():
        return item.location_external_key.strip()
    if item.location_name and item.location_name.strip():
        return item.location_name.strip().lower().replace(" ", "_")
    return "unknown"


def _volunteer_role_key(role: str) -> str:
    return re.sub(r"[^\w]+", "_", role.lower(), flags=re.UNICODE).strip("_") or "volunteer"


def _is_new_years_day(event_date: date) -> bool:
    return event_date.month == 1 and event_date.day == 1


def _profile_volunteer_result_key(
    item: CanonicalVolunteerResult,
    external_user_id: str,
    *,
    location_key: str | None = None,
) -> CanonicalVolunteerResult:
    loc = location_key or _volunteering_location_key(item)
    role_key = _volunteer_role_key(item.role or "")
    result_key = f"{external_user_id}:{item.event_date.isoformat()}:{loc}:{role_key}"
    return replace(item, external_result_key=result_key)


def dedupe_profile_volunteering(
    items: list[CanonicalVolunteerResult],
    external_user_id: str,
) -> list[CanonicalVolunteerResult]:
    """Keep distinct volunteering rows per date, location and role."""
    seen: set[tuple[date, str, str]] = set()
    deduped: list[CanonicalVolunteerResult] = []

    for item in sorted(items, key=lambda row: row.event_date, reverse=True):
        location_key = _volunteering_location_key(item)
        role_key = _volunteer_role_key(item.role or "")
        dedupe_key = (item.event_date, location_key, role_key)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        deduped.append(
            _profile_volunteer_result_key(
                item,
                external_user_id,
                location_key=location_key,
            )
        )

    return deduped
