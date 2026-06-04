from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup

from app.platform_adapters.canonical import CanonicalLocation

YANDEX_PT_RE = re.compile(r"yandex\.ru/maps/\?pt=\s*([0-9.]+),([0-9.]+)", re.I)
OG_MAP_RE = re.compile(r"center=([0-9.]+)%2C([0-9.]+)|center=([0-9.]+),([0-9.]+)", re.I)
MAP_LINK_TITLE_RE = re.compile(r"Карта и схема проезда|Map and directions|Mapa i uputstva", re.I)


def parse_map_url(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    for link in soup.find_all("a", href=True):
        title = link.get("title") or ""
        text = link.get_text(" ", strip=True)
        href = link["href"].strip()
        if not href.startswith("http"):
            continue
        if MAP_LINK_TITLE_RE.search(title) or MAP_LINK_TITLE_RE.search(text):
            return href
        if any(host in href.lower() for host in ("yandex.", "google.", "2gis.", "openstreetmap", "goo.gl/maps")):
            if "fa-location" in str(link) or "location-dot" in str(link):
                return href
    contacts = soup.find(string=re.compile(r"Наши контакты|Our contacts|Kontakti", re.I))
    if contacts:
        block = contacts.find_parent("div") if contacts.parent else None
        if block:
            for link in block.find_all("a", href=True):
                href = link["href"].strip()
                if href.startswith("http") and any(
                    token in href.lower() for token in ("maps", "yandex", "google", "2gis", "openstreetmap")
                ):
                    return href
    return None


def parse_location_coordinates(html: str) -> tuple[float | None, float | None]:
    for match in YANDEX_PT_RE.finditer(html):
        longitude = float(match.group(1))
        latitude = float(match.group(2))
        return latitude, longitude
    for match in OG_MAP_RE.finditer(html):
        groups = match.groups()
        if groups[0] and groups[1]:
            return float(groups[1]), float(groups[0])
        if groups[2] and groups[3]:
            return float(groups[3]), float(groups[2])
    return None, None


def parse_event_location_page(
    html: str,
    page_url: str,
    *,
    location_external_key: str,
) -> CanonicalLocation:
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    name = h1.get_text(strip=True) if h1 else location_external_key

    city = None
    country = "Россия"
    for node in soup.find_all(string=re.compile(r"Место проведения", re.I)):
        parent = node.parent
        if parent is None:
            continue
        block = parent.find_parent("div") or parent
        text = block.get_text(" ", strip=True) if block else ""
        city_match = re.search(r"Место проведения:\s*([^,]+)", text)
        if city_match:
            city = city_match.group(1).strip()
            break

    latitude, longitude = parse_location_coordinates(html)
    map_url = parse_map_url(html)
    parsed = urlparse(page_url)
    return CanonicalLocation(
        external_key=location_external_key,
        name=name,
        country=country,
        city=city,
        latitude=latitude,
        longitude=longitude,
        source_url=page_url,
        course_source_url=page_url,
        map_url=map_url,
    )
