from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

EVENT_LINK_RE = re.compile(r"/events/([^/\"?#]+)")
RESERVED_SLUGS = frozenset({"events", "activities", "athletes", "clubs", "badges"})


class S95LocationRegistryStatus(str, enum.Enum):
    active = "active"
    paused = "paused"
    cancelled = "cancelled"


@dataclass(frozen=True)
class S95RegistryEntry:
    slug: str
    name: str
    venue_name: str
    source_url: str
    status: S95LocationRegistryStatus
    status_note: str | None = None


@dataclass(frozen=True)
class ParsedS95EventsPage:
    entries: list[S95RegistryEntry]


def slug_from_href(href: str, base_url: str) -> str | None:
    normalized = urljoin(base_url, href.strip())
    match = EVENT_LINK_RE.search(normalized)
    if not match:
        return None
    slug = match.group(1).lower()
    if slug in RESERVED_SLUGS:
        return None
    return slug


def _card_border_class(card_classes: list[str]) -> str | None:
    for class_name in card_classes:
        if class_name.startswith("border-"):
            return class_name
    return None


def _card_status(card) -> tuple[S95LocationRegistryStatus, str | None]:
    classes = card.get("class") or []
    border = _card_border_class(classes)
    overlay = card.select_one("div.card-img-top.bg-danger, div.bg-danger.card-img-top")
    blob = card.get_text(" ", strip=True).lower()

    if border == "border-danger" or overlay is not None:
        return S95LocationRegistryStatus.cancelled, "отменена"
    if border in {"border-secondary", "border-warning"}:
        return S95LocationRegistryStatus.paused, "на паузе"
    if "отмен" in blob:
        return S95LocationRegistryStatus.cancelled, "отменена"
    if "на паузе" in blob or "pause" in blob:
        return S95LocationRegistryStatus.paused, "на паузе"
    return S95LocationRegistryStatus.active, None


def registry_entry_is_paused(status: S95LocationRegistryStatus) -> bool:
    return status == S95LocationRegistryStatus.paused


def registry_entry_is_cancelled(status: S95LocationRegistryStatus) -> bool:
    return status == S95LocationRegistryStatus.cancelled


def parse_events_registry_html(html: str, page_url: str) -> ParsedS95EventsPage:
    parsed = urlparse(page_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    soup = BeautifulSoup(html, "html.parser")
    entries_by_slug: dict[str, S95RegistryEntry] = {}

    for card in soup.select("div.card-with-border"):
        link = card.find("a", href=EVENT_LINK_RE)
        if link is None:
            continue
        slug = slug_from_href(link["href"], base_url)
        if slug is None:
            continue

        status, status_note = _card_status(card)
        title_node = card.select_one("section.fs-4")
        img = card.find("img", alt=True)
        name = (title_node.get_text(strip=True) if title_node else None) or (
            img.get("alt", "").strip() if img else ""
        )
        venue_name = link.get_text(strip=True) or name
        if not name:
            name = venue_name

        entries_by_slug[slug] = S95RegistryEntry(
            slug=slug,
            name=name,
            venue_name=venue_name,
            source_url=f"{base_url}/events/{slug}",
            status=status,
            status_note=status_note,
        )

    entries = sorted(entries_by_slug.values(), key=lambda item: item.slug)
    return ParsedS95EventsPage(entries=entries)
