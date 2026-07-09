"""Parsers for the 5verst clubs listing (/clubs/) and club detail pages (/clubs/{slug}/)."""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup, Tag

from app.platform_adapters.five_verst.http import BASE_URL, fetch_html

logger = logging.getLogger(__name__)

CLUBS_PAGE_URL = f"{BASE_URL}/clubs/"
CLUB_HREF_RE = re.compile(r"/clubs/([^/?#]+)")
USERSTATS_ID_RE = re.compile(r"/userstats/(\d+)")
TIME_RE = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})")


@dataclass(frozen=True)
class ParsedClubsListEntry:
    slug: str
    name: str
    source_url: str
    members_count: int | None
    finishes_count: int | None
    volunteerings_count: int | None
    avg_time_seconds: int | None
    best_female_time_seconds: int | None
    best_male_time_seconds: int | None
    external_links: list[dict[str, str]]
    row_hash: str


@dataclass(frozen=True)
class ParsedClubMember:
    userstats_id: str
    display_name: str
    profile_url: str


@dataclass(frozen=True)
class ParsedClubDetail:
    slug: str
    title: str | None
    description: str | None
    logo_url: str | None
    external_links: list[dict[str, str]]
    members: list[ParsedClubMember]
    members_skipped: int


def club_url(slug: str) -> str:
    return f"{BASE_URL}/clubs/{slug}/"


def _int_or_none(value: str | None) -> int | None:
    if not value:
        return None
    cleaned = value.strip().replace("\xa0", "").replace(" ", "")
    if not cleaned.isdigit():
        return None
    return int(cleaned)


def _time_to_seconds(value: str | None) -> int | None:
    if not value:
        return None
    match = TIME_RE.search(value)
    if not match:
        return None
    hours, minutes, seconds = (int(match.group(i)) for i in range(1, 4))
    return hours * 3600 + minutes * 60 + seconds


def _link_kind(title: str | None, href: str) -> str:
    lowered = (title or "").lower()
    href_lowered = href.lower()
    if "вконтакте" in lowered or "vk.com" in href_lowered:
        return "vk"
    if "telegram" in lowered or "t.me" in href_lowered:
        return "telegram"
    if "сайт" in lowered:
        return "site"
    return "link"


def _extract_links(container: Tag) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    for anchor in container.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href or href.startswith("#"):
            continue
        title = anchor.get("title") or None
        links.append({"kind": _link_kind(title, href), "url": href})
    return links


def _cell_text(cell: Tag) -> str:
    return cell.get_text(" ", strip=True)


def _row_hash(entry_fields: list[str | None]) -> str:
    payload = "|".join("" if item is None else item for item in entry_fields)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_clubs_list_html(html: str) -> list[ParsedClubsListEntry]:
    soup = BeautifulSoup(html, "html.parser")
    entries: list[ParsedClubsListEntry] = []

    for row in soup.select("table tr"):
        cells = row.find_all("td")
        if len(cells) < 7:
            continue
        link = cells[0].find("a", href=True)
        if link is None:
            continue
        match = CLUB_HREF_RE.search(link["href"])
        if not match:
            continue
        slug = match.group(1).lower()
        name = link.get_text(strip=True)
        if not name:
            continue

        members_raw = _cell_text(cells[1])
        finishes_raw = _cell_text(cells[2])
        volunteerings_raw = _cell_text(cells[3])
        avg_time_raw = _cell_text(cells[4])
        best_female_raw = _cell_text(cells[5])
        best_male_raw = _cell_text(cells[6])
        external_links = _extract_links(cells[7]) if len(cells) > 7 else []

        entries.append(
            ParsedClubsListEntry(
                slug=slug,
                name=name,
                source_url=club_url(slug),
                members_count=_int_or_none(members_raw),
                finishes_count=_int_or_none(finishes_raw),
                volunteerings_count=_int_or_none(volunteerings_raw),
                avg_time_seconds=_time_to_seconds(avg_time_raw),
                best_female_time_seconds=_time_to_seconds(best_female_raw),
                best_male_time_seconds=_time_to_seconds(best_male_raw),
                external_links=external_links,
                row_hash=_row_hash(
                    [
                        name,
                        members_raw,
                        finishes_raw,
                        volunteerings_raw,
                        avg_time_raw,
                        best_female_raw,
                        best_male_raw,
                        ";".join(sorted(item["url"] for item in external_links)),
                    ]
                ),
            )
        )

    return entries


def parse_club_detail_html(html: str, slug: str) -> ParsedClubDetail:
    soup = BeautifulSoup(html, "html.parser")

    heading = soup.find("h1")
    title = heading.get_text(strip=True) if heading is not None else None

    description_block = soup.select_one("div.club-description")
    description = description_block.get_text(" ", strip=True) if description_block is not None else None

    logo_img = soup.select_one("img.club-logo__img")
    logo_url = logo_img.get("src") if logo_img is not None else None

    links_block = soup.select_one("div.club-links-section") or soup.select_one("div.club-links")
    external_links = _extract_links(links_block) if links_block is not None else []

    members: list[ParsedClubMember] = []
    members_skipped = 0
    seen_ids: set[str] = set()
    for row in soup.select("table tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        anchor = None
        for candidate in row.find_all("a", href=True):
            if USERSTATS_ID_RE.search(candidate["href"]):
                anchor = candidate
                break
        if anchor is None:
            members_skipped += 1
            logger.warning("Club %s: member row without userstats link: %s", slug, _cell_text(cells[1])[:80])
            continue
        match = USERSTATS_ID_RE.search(anchor["href"])
        assert match is not None
        userstats_id = match.group(1)
        if userstats_id in seen_ids:
            continue
        display_name = anchor.get_text(" ", strip=True)
        if not display_name:
            members_skipped += 1
            continue
        seen_ids.add(userstats_id)
        members.append(
            ParsedClubMember(
                userstats_id=userstats_id,
                display_name=display_name,
                profile_url=f"{BASE_URL}/userstats/{userstats_id}/",
            )
        )

    return ParsedClubDetail(
        slug=slug,
        title=title,
        description=description,
        logo_url=logo_url,
        external_links=external_links,
        members=members,
        members_skipped=members_skipped,
    )


def fetch_clubs_page() -> tuple[list[ParsedClubsListEntry], str]:
    html = fetch_html(CLUBS_PAGE_URL, reason="clubs registry")
    return parse_clubs_list_html(html), html


def fetch_club_detail(slug: str) -> tuple[ParsedClubDetail, str]:
    html = fetch_html(club_url(slug), reason=f"club {slug}")
    return parse_club_detail_html(html, slug), html
