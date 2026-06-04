from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Tag

from app.s95.parsers.pr_detection import row_is_pr

PR_TITLE_RE = re.compile(r"личн\w*\s*рекорд|personal\s*record|lični\s*rekord", re.I)
FIRST_RUN_TITLE_RE = re.compile(r"перв\w*\s*забег|first\s*run|prvi\s*trk", re.I)
EVENT_STAT_COUNT_RE = re.compile(r"(\d+)")


@dataclass
class ParsedRowAchievements:
    is_pr: bool = False
    is_first_run: bool = False
    is_first_run_at_location: bool = False
    labels: list[str] = field(default_factory=list)


@dataclass
class ParsedEventStats:
    finishers_count: int | None = None
    personal_records_count: int | None = None
    first_at_location_count: int | None = None
    first_at_s95_count: int | None = None
    volunteers_count: int | None = None

    def as_dict(self) -> dict[str, int]:
        payload: dict[str, int] = {}
        if self.finishers_count is not None:
            payload["finishers_count"] = self.finishers_count
        if self.personal_records_count is not None:
            payload["personal_records_count"] = self.personal_records_count
        if self.first_at_location_count is not None:
            payload["first_at_location_count"] = self.first_at_location_count
        if self.first_at_s95_count is not None:
            payload["first_at_s95_count"] = self.first_at_s95_count
        if self.volunteers_count is not None:
            payload["volunteers_count"] = self.volunteers_count
        return payload


def _icon_classes(icon: Tag) -> str:
    return " ".join(icon.get("class") or []).lower()


def parse_row_achievements(row: Tag) -> ParsedRowAchievements:
    parsed = ParsedRowAchievements(is_pr=row_is_pr(row))
    if parsed.is_pr and "Личный рекорд" not in parsed.labels:
        parsed.labels.append("Личный рекорд")

    for icon in row.find_all(["i", "img", "span", "svg"]):
        title = (icon.get("title") or icon.get("alt") or "").strip()
        classes = _icon_classes(icon)
        if "fa-bolt" in classes and title and PR_TITLE_RE.search(title):
            parsed.is_pr = True
            if "Личный рекорд" not in parsed.labels:
                parsed.labels.append("Личный рекорд")
            continue
        if "fa-heart" not in classes or not title or not FIRST_RUN_TITLE_RE.search(title):
            continue
        if "text-success" in classes:
            parsed.is_first_run = True
            if "Первый забег S95" not in parsed.labels:
                parsed.labels.append("Первый забег S95")
        elif "text-primary" in classes:
            parsed.is_first_run_at_location = True
            if "Первый забег на локации" not in parsed.labels:
                parsed.labels.append("Первый забег на локации")
        else:
            parsed.is_first_run = True
            if "Первый забег" not in parsed.labels:
                parsed.labels.append("Первый забег")

    return parsed


def _stat_count(text: str) -> int | None:
    match = EVENT_STAT_COUNT_RE.search(text)
    return int(match.group(1)) if match else None


def parse_event_stats_html(html: str) -> ParsedEventStats:
    soup = BeautifulSoup(html, "html.parser")
    stats = ParsedEventStats()
    for item in soup.select("li.list-group-item"):
        text = item.get_text(" ", strip=True)
        lowered = text.lower()
        count = _stat_count(text)
        if count is None:
            continue
        if "личн" in lowered and "рекорд" in lowered:
            stats.personal_records_count = count
        elif "впервые на забеге s95" in lowered or "впервые на забеге с95" in lowered:
            stats.first_at_s95_count = count
        elif "впервые на забеге" in lowered:
            stats.first_at_location_count = count
        elif "участник" in lowered and "забег" in lowered:
            stats.finishers_count = count
        elif "волонт" in lowered and "команд" in lowered:
            stats.volunteers_count = count
    return stats
