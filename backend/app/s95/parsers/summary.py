from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.platform_adapters.canonical import CanonicalEventSummary
from app.s95.parsers.time_utils import parse_leader_time

NO_PROTOCOL_MARKERS = (
    "протоколов пока нет",
    "результатов пока нет",
    "мероприятий пока нет",
    "нет протоколов",
    "нет результатов",
    "no results",
    "no events",
)

HEADER_MAP = {
    "#": "index",
    "дата": "date",
    "datum": "date",
    "участники": "runners",
    "sportisti": "runners",
    "волонтёры": "volunteers",
    "volonteri": "volunteers",
    "первый": "first_man",
    "prvi čovek": "first_man",
    "первая": "first_woman",
    "prva žena": "first_woman",
}


@dataclass
class ParsedSummaryRow:
    event_date: date
    event_number: int | None
    finishers_count: int | None
    volunteers_count: int | None
    best_male_time_sec: int | None
    best_male_time_display: str | None
    best_female_time_sec: int | None
    best_female_time_display: str | None
    source_url: str


def _normalize_header(text: str) -> str:
    return HEADER_MAP.get(text.strip().lower(), text.strip().lower())


def _parse_int(value: str | None) -> int | None:
    if not value:
        return None
    cleaned = re.sub(r"\D", "", value)
    return int(cleaned) if cleaned else None


def _parse_event_date(value: str) -> date | None:
    match = re.match(r"^(\d{2})\.(\d{2})\.(\d{4})$", value.strip())
    if not match:
        return None
    day, month, year = match.groups()
    return date(int(year), int(month), int(day))


def parse_location_summary_html(
    html: str,
    page_url: str,
    *,
    location_external_key: str,
    location_name: str,
) -> list[CanonicalEventSummary]:
    parsed = urlparse(page_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    soup = BeautifulSoup(html, "html.parser")
    page_text = soup.get_text(" ", strip=True).lower()

    if any(marker in page_text for marker in NO_PROTOCOL_MARKERS):
        return []

    rows: list = []
    for table in soup.find_all("table"):
        header_cells = [col.get_text(strip=True).lower() for col in table.find("tr").find_all(["th", "td"])] if table.find("tr") else []
        if not header_cells:
            continue
        blob = " ".join(header_cells)
        if ("дата" in blob or "datum" in blob) and ("участники" in blob or "sportisti" in blob or "#" in header_cells):
            rows = table.find_all("tr")
            break

    if not rows:
        table_block = soup.find("div", class_=lambda c: c and "row-cols-1" in c)
        if table_block is None:
            results_header = soup.find(
                lambda tag: tag.name in ["h2", "h3"]
                and re.search(r"Результаты|Results|Rezultati", tag.get_text(strip=True), re.I)
            )
            if results_header:
                table_block = results_header.find_parent("div") or results_header.parent
        if table_block is None:
            return []
        rows = table_block.find_all("tr")
    if len(rows) < 2:
        return []

    header_cells = [col.get_text(strip=True) for col in rows[0].find_all(["td", "th"])]
    normalized = [_normalize_header(cell) for cell in header_cells]

    summaries: list[CanonicalEventSummary] = []
    for row in rows[1:]:
        cells = row.find_all("td")
        if not cells:
            continue

        values: dict[str, str] = {}
        for index, cell in enumerate(cells):
            if index >= len(normalized):
                break
            values[normalized[index]] = cell.get_text(" ", strip=True)

        date_cell = row.find("td", class_="date") or (cells[1] if len(cells) > 1 else None)
        event_link = None
        if date_cell:
            link = date_cell.find("a", href=True)
            if link:
                href = link["href"]
                event_link = href if href.startswith("http") else urljoin(base_url, href)

        event_date = _parse_event_date(values.get("date", ""))
        if event_date is None:
            continue

        event_number = _parse_int(values.get("index"))
        male_sec, male_display = parse_leader_time(values.get("first_man"))
        female_sec, female_display = parse_leader_time(values.get("first_woman"))

        source_url = event_link or page_url
        external_event_key = f"{location_external_key}:{event_date.isoformat()}"
        summary_hash = hashlib.sha256(
            f"{external_event_key}:{values.get('runners')}:{values.get('volunteers')}".encode()
        ).hexdigest()[:32]

        summaries.append(
            CanonicalEventSummary(
                external_event_key=external_event_key,
                event_date=event_date,
                event_number=event_number,
                location_external_key=location_external_key,
                location_name=location_name,
                finishers_count=_parse_int(values.get("runners")),
                volunteers_count=_parse_int(values.get("volunteers")),
                best_male_time_sec=male_sec,
                best_male_time_display=male_display,
                best_female_time_sec=female_sec,
                best_female_time_display=female_display,
                source_url=source_url,
                summary_hash=summary_hash,
            )
        )

    return summaries
