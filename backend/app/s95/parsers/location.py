from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

from app.migration.helpers import s95_country_from_url
from app.platform_adapters.canonical import (
    CanonicalDescriptionLink,
    CanonicalDescriptionSection,
    CanonicalLocation,
    CanonicalLocationDescription,
)

YANDEX_PT_RE = re.compile(r"yandex\.ru/maps/\?pt=\s*([0-9.]+),([0-9.]+)", re.I)
OG_MAP_RE = re.compile(r"center=([0-9.]+)%2C([0-9.]+)|center=([0-9.]+),([0-9.]+)", re.I)
MAP_LINK_TITLE_RE = re.compile(r"Карта и схема проезда|Map and directions|Mapa i uputstva", re.I)

# Карточки страницы локации S95: «Общая информация» (текст про трассу) и
# «Наши контакты» (место проведения + ссылки на карту и парковку). Домены
# s95.by/s95.rs отдают те же карточки, но заголовки могут быть переведены.
INFO_CARD_RE = re.compile(r"Общая информация|General information|Opšte informacije", re.I)
CONTACTS_CARD_RE = re.compile(r"Наши контакты|Our contacts|Kontakti", re.I)
# Сербский домен пишет «Mesto događaja» — без этого варианта подпись оставалась
# в тексте и на странице читалась как часть описания («Mesto događaja: The run…»).
VENUE_RE = re.compile(r"^\s*(?:Место проведения|Venue|Mesto (?:doga[đd]aja|održavanja))\s*:?\s*", re.I)
TRAVEL_HEADING_RE = re.compile(r"Как добраться|How to get|Kako (?:doći|stići)", re.I)
# «Поддержка» — одинаковый на всех московских площадках абзац про гранты мэра;
# для страницы локации это не описание места, а служебная сноска.
SKIP_HEADING_RE = re.compile(r"Поддержк|Support|Podrška", re.I)
# «Забеги проходят под названием "S95 ЗИЛ"» — название системы и площадки у нас
# и так в заголовке страницы, в описании это лишняя строка.
BRANDING_LINE_RE = re.compile(r"^Забеги проходят под названием|^Trke se održavaju", re.I)
# «Длина трассы — 5000 м (5 км). Замер произведён с помощью курвиметра
# (специального прибора для измерения расстояний на неровной местности).» —
# одинаковый на всех площадках зачин абзаца про трассу (метод замера меняется —
# курвиметр, колесо и т.п., — но сама пара предложений типовая и для читателя
# не несёт ничего специфичного про конкретный парк). Дальше идёт уже уникальный
# текст про маршрут — его и оставляем.
TRACK_LENGTH_BOILERPLATE_RE = re.compile(
    r"^Длина\s+трассы\s*[—-]\s*[^.]*\.\s*Замер\s+произведён[^.]*\.\s*",
    re.I,
)


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
    # Страну s95 на странице не пишет, зато её однозначно задаёт домен:
    # s95.rs — Сербия, s95.by — Беларусь, s95.ru — Россия.
    country = s95_country_from_url(page_url)
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


def _text(node: Tag) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()


def _is_heading(paragraph: Tag, text: str) -> bool:
    """Абзац-заголовок: весь его текст обёрнут в <strong> («Трасса», «Поддержка»)."""

    strong = paragraph.find("strong")
    return strong is not None and _text(strong) == text


def _find_card(soup: BeautifulSoup, title_re: re.Pattern[str]) -> Tag | None:
    for card in soup.select("div.card"):
        title = card.select_one(".card-title")
        if title is not None and title_re.search(_text(title)):
            return card
    return None


def _split_info_card(card: Tag) -> tuple[list[str], list[CanonicalDescriptionSection]]:
    """Карточка «Общая информация» → абзацы описания + секции «Как добраться»."""

    body = card.select_one(".card-text") or card.select_one(".card-body")
    if body is None:
        return [], []

    course_parts: list[str] = []
    travel_sections: list[CanonicalDescriptionSection] = []
    heading: str | None = None
    for paragraph in body.find_all(["p", "ul", "ol"], recursive=False):
        text = _text(paragraph)
        if not text:
            continue
        if paragraph.name == "p" and _is_heading(paragraph, text):
            heading = text
            continue
        if heading and SKIP_HEADING_RE.search(heading):
            continue
        if heading and TRAVEL_HEADING_RE.search(heading):
            travel_sections.append(CanonicalDescriptionSection(title=heading, text=text))
            continue
        if BRANDING_LINE_RE.search(text):
            continue
        text = TRACK_LENGTH_BOILERPLATE_RE.sub("", text).strip()
        if not text:
            # Абзац целиком состоял из типового зачина — без остатка добавлять нечего.
            continue
        course_parts.append(text)
    return course_parts, travel_sections


def _contacts_card(card: Tag) -> tuple[str | None, list[CanonicalDescriptionLink]]:
    venue_node = card.select_one(".card-text")
    venue = VENUE_RE.sub("", _text(venue_node)) if venue_node is not None else ""
    links: list[CanonicalDescriptionLink] = []
    for link in card.select("a[href]"):
        title = (link.get("title") or "").strip()
        href = link["href"].strip()
        # Оставляем только то, что помогает доехать: карта и парковка.
        # Соцсети и чаты — это уже про сообщество, у них своё место на странице.
        if not title or not href.startswith("http"):
            continue
        if not re.search(r"Карт|Парковк|Map|Parking|Mapa|Parking", title, re.I):
            continue
        links.append(CanonicalDescriptionLink(title=title, url=href))
    return (venue or None), links


def parse_location_description(html: str, source_url: str) -> CanonicalLocationDescription:
    """Разбирает страницу локации S95 (`/events/{slug}`) в блок описания."""

    soup = BeautifulSoup(html, "html.parser")

    course_parts: list[str] = []
    travel_sections: list[CanonicalDescriptionSection] = []
    info_card = _find_card(soup, INFO_CARD_RE)
    if info_card is not None:
        course_parts, travel_sections = _split_info_card(info_card)

    travel_text: str | None = None
    links: list[CanonicalDescriptionLink] = []
    contacts_card = _find_card(soup, CONTACTS_CARD_RE)
    if contacts_card is not None:
        travel_text, links = _contacts_card(contacts_card)

    return CanonicalLocationDescription(
        course_text="\n\n".join(course_parts) or None,
        travel_text=travel_text,
        travel_sections=travel_sections,
        links=links,
        source_url=source_url,
    )
