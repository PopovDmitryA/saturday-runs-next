from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from app.pace import parse_pace_value
from app.platform_adapters.canonical import CanonicalRunResult, CanonicalVolunteerResult
from app.s95.parsers.achievements import ParsedEventStats, parse_event_stats_html, parse_row_achievements
from app.s95.parsers.table_columns import runs_table_column_indexes
from app.s95.parsers.time_utils import parse_finish_time
from app.s95.parsers.volunteer_roles import canonical_s95_volunteer_role, s95_volunteer_role_key

ATHLETE_LINK_RE = re.compile(r"/athletes/(\d+)", re.IGNORECASE)
CLUB_LINK_RE = re.compile(r"/clubs/", re.I)
VOL_HEADER_RE = re.compile(r"Волонт[ёe]р|Volonteri|Volunteer", re.I)
PROTOCOL_HEADER_RE = re.compile(r"Участник|Participant|Učesnik", re.I)
UNKNOWN_NAMES = {"НЕИЗВЕСТНЫЙ", "NEPOZNATO"}


@dataclass(frozen=True)
class ParsedProtocolPage:
    run_results: list[CanonicalRunResult]
    volunteer_results: list[CanonicalVolunteerResult]
    event_stats: ParsedEventStats


def _athlete_id_from_href(href: str | None) -> str | None:
    if not href:
        return None
    match = ATHLETE_LINK_RE.search(href)
    return match.group(1) if match else None


def _find_protocol_table(soup: BeautifulSoup) -> Tag | None:
    for table in soup.find_all("table"):
        header_text = table.get_text(" ", strip=True)
        if PROTOCOL_HEADER_RE.search(header_text) and re.search(
            r"Время|Time|Tempo", header_text, re.I
        ):
            return table
    pane = soup.find("div", class_="tab-pane fade show active") or soup.find(
        "div", class_="tab-pane fade show active"
    )
    if pane:
        table = pane.find("table")
        if table:
            return table
    return None


def _protocol_column_indexes(table: Tag) -> dict[str, int | None] | None:
    header_row = table.find("tr")
    if header_row is None:
        return None
    headers = [cell.get_text(strip=True) for cell in header_row.find_all(["th", "td"])]
    if not headers:
        return None
    blob = " ".join(headers).lower()
    if "участник" in blob or "participant" in blob or "učesnik" in blob:
        return runs_table_column_indexes(headers)
    return None


def _cell_text(cells: list[Tag], index: int | None) -> str:
    if index is None or index >= len(cells):
        return ""
    return cells[index].get_text(" ", strip=True)


def _parse_protocol_row(
    row: Tag,
    *,
    base_url: str,
    location_external_key: str,
    location_name: str,
    event_date: date,
    event_number: int | None,
    column_indexes: dict[str, int | None] | None,
) -> CanonicalRunResult | None:
    cols = row.find_all("td")
    if not cols:
        return None

    if column_indexes is not None:
        position_text = _cell_text(cols, column_indexes.get("event_num"))
        if not position_text.isdigit():
            position_text = _cell_text(cols, 0)
        position = int(position_text) if position_text.isdigit() else None

        participant_idx = column_indexes.get("participant")
        if participant_idx is None:
            participant_idx = 1
        participant_cell = cols[participant_idx] if participant_idx < len(cols) else row
        time_idx = column_indexes.get("time")
        pace_idx = column_indexes.get("pace")
    else:
        position_text = cols[0].get_text(strip=True) if len(cols) > 0 else ""
        position = int(position_text) if position_text.isdigit() else None
        participant_cell = cols[1] if len(cols) > 1 else row
        time_idx = 2 if len(cols) > 2 else None
        pace_idx = 3 if len(cols) > 3 else None

    link_tag = participant_cell.find("a", class_="athlete-link") or participant_cell.find("a")
    athlete_name = link_tag.get_text(strip=True) if link_tag else participant_cell.get_text(strip=True) or None
    athlete_href = None
    if link_tag and link_tag.has_attr("href"):
        athlete_href = urljoin(base_url, link_tag["href"])

    club_name = _extract_club_name(row, cols, column_indexes)

    finish_time_sec = None
    finish_time_display = None
    if time_idx is not None and time_idx < len(cols):
        time_cell = cols[time_idx]
        for icon in time_cell.find_all(["i", "span"]):
            icon.decompose()
        finish_time_sec, finish_time_display = parse_finish_time(time_cell.get_text(strip=True))

    pace_sec_per_km = None
    pace_display = None
    if pace_idx is not None and pace_idx < len(cols):
        pace_sec_per_km, pace_display = parse_pace_value(_cell_text(cols, pace_idx))

    external_user_id = _athlete_id_from_href(athlete_href)
    if external_user_id is None:
        if athlete_name in UNKNOWN_NAMES:
            # «НЕИЗВЕСТНЫЙ» — все безымянные делят одно имя; без уникального id они
            # схлопнутся в одну строку через fallback по (event_id, participant_id).
            external_user_id = (
                f"unknown:{location_external_key}:{event_date.isoformat()}:{position or 0}"
            )
        else:
            external_user_id = f"unknown:{athlete_name or 'runner'}"

    if not athlete_name and external_user_id.startswith("unknown:"):
        return None

    achievements = parse_row_achievements(row)

    external_result_key = f"{location_external_key}:{event_date.isoformat()}:{external_user_id}:{position or 0}"
    return CanonicalRunResult(
        external_result_key=external_result_key,
        event_date=event_date,
        external_user_id=external_user_id,
        participant_name=athlete_name or "Unknown",
        position=position,
        finish_time_sec=finish_time_sec,
        finish_time_display=finish_time_display,
        pace_sec_per_km=pace_sec_per_km,
        pace_display=pace_display,
        location_external_key=location_external_key,
        location_name=location_name,
        event_number=event_number,
        status="unknown_runner" if athlete_name in UNKNOWN_NAMES else None,
        is_pr=achievements.is_pr,
        is_first_run=achievements.is_first_run,
        is_first_run_at_location=achievements.is_first_run_at_location,
        club_name=club_name,
        achievement_labels=achievements.labels,
    )


def _extract_club_name(
    row: Tag,
    cols: list[Tag],
    column_indexes: dict[str, int | None] | None,
) -> str | None:
    club_idx = column_indexes.get("club") if column_indexes else None
    if club_idx is not None and club_idx < len(cols):
        club_cell = cols[club_idx]
    else:
        club_cell = row.find("td", class_=lambda c: c and "hidden-on-phone" in c) or row
    club_link = club_cell.find("a", href=CLUB_LINK_RE) if club_cell else None
    if club_link:
        return club_link.get_text(strip=True) or None
    return None


def parse_protocol_page(
    html: str,
    page_url: str,
    *,
    location_external_key: str,
    location_name: str,
    event_date: date,
    event_number: int | None = None,
) -> ParsedProtocolPage:
    run_results, volunteer_results = _parse_protocol_tables(
        html,
        page_url,
        location_external_key=location_external_key,
        location_name=location_name,
        event_date=event_date,
        event_number=event_number,
    )
    return ParsedProtocolPage(
        run_results=run_results,
        volunteer_results=volunteer_results,
        event_stats=parse_event_stats_html(html),
    )


def parse_protocol_html(
    html: str,
    page_url: str,
    *,
    location_external_key: str,
    location_name: str,
    event_date: date,
    event_number: int | None = None,
) -> tuple[list[CanonicalRunResult], list[CanonicalVolunteerResult]]:
    parsed = parse_protocol_page(
        html,
        page_url,
        location_external_key=location_external_key,
        location_name=location_name,
        event_date=event_date,
        event_number=event_number,
    )
    return parsed.run_results, parsed.volunteer_results


def _parse_protocol_tables(
    html: str,
    page_url: str,
    *,
    location_external_key: str,
    location_name: str,
    event_date: date,
    event_number: int | None = None,
) -> tuple[list[CanonicalRunResult], list[CanonicalVolunteerResult]]:
    parsed = urlparse(page_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    soup = BeautifulSoup(html, "html.parser")

    run_results: list[CanonicalRunResult] = []
    protocol_table = _find_protocol_table(soup)
    column_indexes = _protocol_column_indexes(protocol_table) if protocol_table else None
    rows = protocol_table.find_all("tr") if protocol_table else []
    for row in rows:
        if column_indexes is not None and row.find(["th"]):
            continue
        parsed_row = _parse_protocol_row(
            row,
            base_url=base_url,
            location_external_key=location_external_key,
            location_name=location_name,
            event_date=event_date,
            event_number=event_number,
            column_indexes=column_indexes,
        )
        if parsed_row is not None:
            run_results.append(parsed_row)

    volunteer_results: list[CanonicalVolunteerResult] = []
    header = soup.find(
        lambda tag: tag.name in ["h2", "h3", "h4"] and VOL_HEADER_RE.search(tag.get_text(strip=True))
    )
    volunteers_table = header.find_next("table") if header else None
    if volunteers_table:
        for row in volunteers_table.find_all("tr")[1:]:
            cols = row.find_all("td")
            if len(cols) < 2:
                continue
            athlete_link = cols[0].find("a")
            name = athlete_link.get_text(strip=True) if athlete_link else cols[0].get_text(strip=True) or None
            link = urljoin(base_url, athlete_link["href"]) if athlete_link and athlete_link.has_attr("href") else None
            user_id = _athlete_id_from_href(link)
            role_raw = cols[1].get_text(" ", strip=True)
            role = canonical_s95_volunteer_role(role_raw) or role_raw
            role_key = s95_volunteer_role_key(role)
            external_result_key = (
                f"vol:{location_external_key}:{event_date.isoformat()}:{user_id or name}:{role_key}"
            )
            volunteer_results.append(
                CanonicalVolunteerResult(
                    external_result_key=external_result_key,
                    event_date=event_date,
                    external_user_id=user_id,
                    participant_name=name,
                    role=role,
                    source_url=page_url,
                    location_external_key=location_external_key,
                    location_name=location_name,
                    event_number=event_number,
                )
            )

    return run_results, volunteer_results
