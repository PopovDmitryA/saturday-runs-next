from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import unquote, urljoin, urlparse

from bs4 import BeautifulSoup

from app.platform_adapters.canonical import CanonicalEventSummary
from app.s95.parsers.time_utils import parse_leader_time

DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
ACTIVITY_LINK_RE = re.compile(r"/activities/(\d+)")
EVENT_LINK_RE = re.compile(r"/events/([^/\"?#]+)")


@dataclass
class ParsedActivityRow:
    activity_id: str
    event_date: date
    location_external_key: str
    location_name: str
    city: str | None
    finishers_count: int | None
    volunteers_count: int | None
    source_url: str
    summary: CanonicalEventSummary


def _parse_date(text: str) -> date | None:
    match = DATE_RE.search(text)
    if not match:
        return None
    day, month, year = match.groups()
    return date(int(year), int(month), int(day))


def _parse_int(value: str | None) -> int | None:
    if not value:
        return None
    cleaned = re.sub(r"\D", "", value)
    return int(cleaned) if cleaned else None


def parse_recent_activities_html(html: str, page_url: str) -> list[ParsedActivityRow]:
    parsed = urlparse(page_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    soup = BeautifulSoup(html, "html.parser")
    rows: list[ParsedActivityRow] = []

    for table in soup.find_all("table"):
        header_cells = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        if not header_cells:
            continue
        blob = " ".join(header_cells)
        if "дата" not in blob and "datum" not in blob:
            continue
        if "мероприятие" not in blob and "dogadj" not in blob:
            continue

        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 4:
                continue

            activity_id = None
            activity_url = None
            date_cell = cells[0]
            date_link = date_cell.find("a", href=True)
            if date_link:
                activity_url = urljoin(base_url, date_link["href"])
                match = ACTIVITY_LINK_RE.search(date_link["href"])
                if match:
                    activity_id = match.group(1)

            event_date = _parse_date(date_cell.get_text(" ", strip=True))
            if event_date is None or activity_id is None:
                continue

            location_external_key = ""
            location_name = ""
            city = None
            for cell in cells:
                event_link = cell.find("a", href=EVENT_LINK_RE)
                if event_link and event_link.has_attr("href"):
                    href = event_link["href"]
                    loc_match = EVENT_LINK_RE.search(href)
                    if loc_match:
                        location_external_key = unquote(loc_match.group(1))
                        location_name = event_link.get_text(strip=True)
                text = cell.get_text(strip=True)
                if not city and text and text not in (location_name, "") and not DATE_RE.search(text):
                    if _parse_int(text) is None and "(" not in text:
                        city = text

            finishers = _parse_int(cells[3].get_text(strip=True)) if len(cells) > 3 else None
            volunteers = _parse_int(cells[4].get_text(strip=True)) if len(cells) > 4 else None
            first_man = cells[5].get_text(" ", strip=True) if len(cells) > 5 else None
            first_woman = cells[6].get_text(" ", strip=True) if len(cells) > 6 else None
            male_sec, male_display = parse_leader_time(first_man)
            female_sec, female_display = parse_leader_time(first_woman)

            external_event_key = f"{location_external_key}:{event_date.isoformat()}"
            summary_hash = hashlib.sha256(
                f"activity:{activity_id}:{finishers}:{volunteers}".encode()
            ).hexdigest()[:32]

            summary = CanonicalEventSummary(
                external_event_key=external_event_key,
                event_date=event_date,
                event_number=int(activity_id),
                location_external_key=location_external_key,
                location_name=location_name,
                finishers_count=finishers,
                volunteers_count=volunteers,
                best_male_time_sec=male_sec,
                best_male_time_display=male_display,
                best_female_time_sec=female_sec,
                best_female_time_display=female_display,
                source_url=activity_url or f"{base_url}/activities/{activity_id}",
                summary_hash=summary_hash,
            )
            rows.append(
                ParsedActivityRow(
                    activity_id=activity_id,
                    event_date=event_date,
                    location_external_key=location_external_key,
                    location_name=location_name,
                    city=city,
                    finishers_count=finishers,
                    volunteers_count=volunteers,
                    source_url=summary.source_url,
                    summary=summary,
                )
            )

    return rows
