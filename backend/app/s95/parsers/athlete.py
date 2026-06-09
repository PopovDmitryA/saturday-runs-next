from __future__ import annotations

import re
from datetime import date
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from app.pace import parse_pace_value
from app.platform_adapters.canonical import (
    CanonicalParticipant,
    CanonicalRunResult,
    CanonicalVolunteerResult,
    ProfilePreview,
)
from app.s95.parsers.pr_detection import row_is_pr
from app.s95.parsers.table_columns import runs_table_column_indexes
from app.s95.parsers.time_utils import parse_finish_time

ATHLETE_ID_RE = re.compile(r"/athletes/(\d+)", re.IGNORECASE)
DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
EVENT_LINK_RE = re.compile(r"/(?:events|protocols?)/", re.IGNORECASE)
VOL_HEADER_RE = re.compile(r"Волонт[ёe]р|Volonteri|Volunteer", re.IGNORECASE)
VOL_SECTION_TITLE_RE = re.compile(r"^Волонт[её]р(ства|ы)$", re.I)
ACTIVITY_LINK_RE = re.compile(r"/activities/\d+", re.I)
EVENT_LINK_RE = re.compile(r"/events/([^/?#]+)", re.I)
FRIENDS_HEADER_RE = re.compile(r"^\s*\d*\s*Друзья\s*$", re.I)
IN_FRIENDS_HEADER_RE = re.compile(r"^\s*\d*\s*В друзьях\s*$", re.I)
AWARDS_HEADER_RE = re.compile(r"наград|trophies|bedž", re.I)


class AthleteParseError(ValueError):
    pass


class AthletePageNotFoundError(AthleteParseError):
    pass


class AthletePageUnavailableError(AthleteParseError):
    """Login/registration stub — profile is not accessible anonymously."""


NOT_FOUND_MARKERS = (
    "404",
    "не существует",
    "not found",
    "страница не найдена",
)
LOGIN_PAGE_TITLES = frozenset({"регистрация", "registration", "вход", "login"})


def classify_athlete_page(html: str, *, display_name: str | None = None) -> str:
    """Return athlete page kind: ok | not_found | unavailable."""
    lowered = html[:8000].lower()
    title_match = re.search(r"<title[^>]*>([^<]+)</title>", html, re.I)
    title = title_match.group(1).strip().lower() if title_match else ""
    name = (display_name or "").strip().lower()

    for marker in NOT_FOUND_MARKERS:
        if marker in title or marker in name:
            return "not_found"
        if marker in lowered[:3000]:
            return "not_found"

    if title.split("|")[0].strip() in LOGIN_PAGE_TITLES or name in LOGIN_PAGE_TITLES:
        return "unavailable"

    return "ok"


def parse_barcode_and_planning(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    barcode_element = soup.find("h5", id="barcodeModalLabel")
    barcode = barcode_element.get_text(strip=True) if barcode_element else None

    planning = None
    badge = soup.find("div", class_=lambda c: c and "badge" in c and "bg-success" in c)
    if badge:
        text_value = badge.get_text(strip=True)
        if text_value.startswith("Собирается в "):
            planning = text_value.replace("Собирается в ", "", 1).strip()
        elif text_value:
            planning = text_value

    return barcode, planning


def _parse_display_name(soup: BeautifulSoup) -> str | None:
    h1 = soup.find("h1")
    if h1:
        text = h1.get_text(" ", strip=True)
        if text:
            return text
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
        match = re.search(r"^(.+?)\s*\|", title)
        if match:
            return match.group(1).strip()
        return title
    return None


def _parse_stat_value(soup: BeautifulSoup, label: str) -> int | None:
    text = soup.get_text(" ", strip=True)
    pattern = rf"{re.escape(label)}\s*[^\d]{{0,20}}(\d+)"
    match = re.search(pattern, text, re.IGNORECASE)
    return int(match.group(1)) if match else None


RUNS_TOTAL_PATTERNS = (
    re.compile(r"(\d+)\s+всего\s+пробежек", re.I),
    re.compile(r"Всего\s+финишей\s*[^\d]{0,20}(\d+)", re.I),
    re.compile(r"(\d+)\s+Всего\s+финишей", re.I),
    re.compile(r"Финишей\s*[^\d]{0,20}(\d+)", re.I),
)

VOLS_TOTAL_PATTERNS = (
    re.compile(r"(\d+)\s+всего\s+волонт[её]рств", re.I),
    re.compile(r"Всего\s+волонт[её]рств\s*[^\d]{0,20}(\d+)", re.I),
    re.compile(r"(\d+)\s+Всего\s+волонт[её]рств", re.I),
)


def _parse_total_from_patterns(text: str, patterns: tuple[re.Pattern[str], ...]) -> int | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return int(match.group(1))
    return None


def _find_volunteering_section_header(soup: BeautifulSoup) -> Tag | None:
    """Main «Волонтёрства» block — not the stats accordion «всего волонтёрств»."""
    for tag in soup.find_all(["h2", "h3"]):
        if "accordion-header" in (tag.get("class") or []):
            continue
        text = tag.get_text(" ", strip=True)
        if VOL_SECTION_TITLE_RE.match(text):
            return tag
    return None


def _s95_volunteer_external_result_key(
    *,
    location_slug: str,
    event_date: date,
    external_user_id: str,
    role: str,
) -> str:
    """Same shape as protocol parser — one row per event/role in DB."""
    return f"vol:{location_slug}:{event_date.isoformat()}:{external_user_id}:{role}"


def _location_from_event_href(href: str, base_url: str) -> tuple[str, str]:
    parsed = urlparse(urljoin(base_url, href))
    match = EVENT_LINK_RE.search(parsed.path)
    if match:
        slug = match.group(1)
        return slug, slug
    return "", ""


def _parse_volunteering_role_from_button(button: Tag) -> str:
    clone = BeautifulSoup(str(button), "html.parser").find("button")
    if clone is None:
        return button.get_text(" ", strip=True)
    for badge in clone.find_all("span", class_=lambda c: c and "badge" in c):
        badge.decompose()
    return clone.get_text(" ", strip=True)


def _parse_volunteering_accordion(
    header: Tag,
    *,
    external_user_id: str,
    display_name: str,
    base_url: str,
) -> list[CanonicalVolunteerResult]:
    accordion = header.find_next("div", id="accordionRoles")
    if accordion is None:
        return []

    results: list[CanonicalVolunteerResult] = []
    seen: set[tuple[date, str, str]] = set()

    for item in accordion.find_all("div", class_="accordion-item"):
        button = item.find("button", class_=lambda c: c and "accordion-button" in c)
        if button is None:
            continue
        role = _parse_volunteering_role_from_button(button)
        if not role:
            continue

        for row in item.find_all("li", class_="list-group-item"):
            activity_link = row.find("a", href=ACTIVITY_LINK_RE)
            event_link = row.find("a", href=EVENT_LINK_RE)
            if activity_link is None:
                continue
            event_date = _parse_date_cell(activity_link.get_text(" ", strip=True))
            if event_date is None:
                continue

            location_key = ""
            location_name = ""
            if event_link is not None:
                location_name = event_link.get_text(strip=True)
                location_key, slug_from_href = _location_from_event_href(event_link["href"], base_url)
                if slug_from_href:
                    location_key = slug_from_href
            location_slug = location_key or (location_name or "unknown").lower().replace(" ", "_")

            dedupe_key = (event_date, location_slug, role)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            results.append(
                CanonicalVolunteerResult(
                    external_result_key=_s95_volunteer_external_result_key(
                        location_slug=location_slug,
                        event_date=event_date,
                        external_user_id=external_user_id,
                        role=role,
                    ),
                    event_date=event_date,
                    external_user_id=external_user_id,
                    participant_name=display_name,
                    role=role,
                    location_external_key=location_key or location_slug,
                    location_name=location_name or location_slug,
                )
            )

    return results


def _parse_volunteering_tables(
    header: Tag,
    *,
    external_user_id: str,
    display_name: str,
) -> list[CanonicalVolunteerResult]:
    tables: list[Tag] = []
    table = header.find_next("table")
    if table:
        tables.append(table)
    if not tables:
        return []

    results: list[CanonicalVolunteerResult] = []
    seen: set[tuple[date, str, str]] = set()
    for table in tables:
        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            event_date = _parse_date_cell(cells[0].get_text(" ", strip=True))
            if event_date is None:
                continue
            role = cells[-1].get_text(" ", strip=True)
            location_name = cells[1].get_text(" ", strip=True) if len(cells) > 2 else ""
            location_slug = (location_name or "unknown").lower().replace(" ", "_")
            dedupe_key = (event_date, location_slug, role)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            results.append(
                CanonicalVolunteerResult(
                    external_result_key=_s95_volunteer_external_result_key(
                        location_slug=location_slug,
                        event_date=event_date,
                        external_user_id=external_user_id,
                        role=role,
                    ),
                    event_date=event_date,
                    external_user_id=external_user_id,
                    participant_name=display_name,
                    role=role,
                    location_name=location_name,
                    location_external_key=location_slug,
                )
            )
    return results


def _volunteering_total_from_section(soup: BeautifulSoup, vol_rows: list[CanonicalVolunteerResult]) -> int | None:
    header = _find_volunteering_section_header(soup)
    if header is not None:
        block = header.find_parent("section") or header.find_parent("div") or header
        text = block.get_text(" ", strip=True) if block else ""
        if re.search(r"пока не было", text, re.I):
            return 0
    if vol_rows:
        return len(vol_rows)
    return None


def enrich_participant_activity_totals(
    html: str,
    participant: CanonicalParticipant,
) -> CanonicalParticipant:
    """
    S95 shows run/vol totals in lazy turbo-frames; use visible tables and section text as fallback.
    """
    soup = BeautifulSoup(html, "html.parser")
    page_text = soup.get_text(" ", strip=True)

    runs = parse_athlete_runs_html(
        html,
        external_user_id=participant.external_user_id,
        display_name=participant.display_name,
        profile_url=participant.profile_url,
    )
    volunteering = parse_athlete_volunteering_html(
        html,
        external_user_id=participant.external_user_id,
        display_name=participant.display_name,
        profile_url=participant.profile_url or "",
    )

    total_runs = participant.total_runs
    if total_runs is None:
        total_runs = _parse_total_from_patterns(page_text, RUNS_TOTAL_PATTERNS)
    if total_runs is None and runs:
        total_runs = len(runs)

    total_volunteering = participant.total_volunteering
    if total_volunteering is None:
        total_volunteering = _parse_total_from_patterns(page_text, VOLS_TOTAL_PATTERNS)
    if total_volunteering is None:
        total_volunteering = _volunteering_total_from_section(soup, volunteering)
    if volunteering and (total_volunteering is None or len(volunteering) > total_volunteering):
        total_volunteering = len(volunteering)

    return CanonicalParticipant(
        external_user_id=participant.external_user_id,
        display_name=participant.display_name,
        profile_url=participant.profile_url,
        total_runs=total_runs,
        total_volunteering=total_volunteering,
        club_name=participant.club_name,
        barcode_id=participant.barcode_id,
        planning_location=participant.planning_location,
        source_url=participant.source_url,
    )


def _parse_date_cell(text: str) -> date | None:
    match = DATE_RE.search(text)
    if not match:
        return None
    day, month, year = match.groups()
    return date(int(year), int(month), int(day))


def _cell_text(cells: list[Tag], index: int | None) -> str:
    if index is None or index >= len(cells):
        return ""
    return cells[index].get_text(" ", strip=True)


def _extract_location_from_row(row: Tag, base_url: str) -> tuple[str, str]:
    location_name = ""
    location_key = ""
    for link in row.find_all("a", href=True):
        href = link["href"]
        if "/events/" in href:
            location_name = link.get_text(strip=True)
            parsed = urlparse(urljoin(base_url, href))
            parts = [p for p in parsed.path.split("/") if p]
            if len(parts) >= 2 and parts[0] == "events":
                location_key = parts[1]
                break
        if "/locations/" in href or "/location/" in href:
            location_name = link.get_text(strip=True)
            parsed = urlparse(urljoin(base_url, href))
            location_key = parsed.path.rstrip("/").split("/")[-1]
            break
    return location_key, location_name


def parse_athlete_runs_html(
    html: str,
    *,
    external_user_id: str,
    display_name: str,
    profile_url: str,
) -> list[CanonicalRunResult]:
    soup = BeautifulSoup(html, "html.parser")
    parsed = urlparse(profile_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    results: list[CanonicalRunResult] = []

    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True) for th in table.find_all("th")]
        if not headers:
            continue
        header_blob = " ".join(h.lower() for h in headers)
        if "дата" not in header_blob and "datum" not in header_blob:
            continue
        if "время" not in header_blob and "time" not in header_blob and "результат" not in header_blob:
            continue

        cols = runs_table_column_indexes(headers)

        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue

            event_number = None
            event_num_text = _cell_text(cells, cols["event_num"])
            if event_num_text.isdigit():
                event_number = int(event_num_text)

            position = None
            place_text = _cell_text(cells, cols["place"])
            if place_text.isdigit():
                position = int(place_text)

            event_date = _parse_date_cell(_cell_text(cells, cols["date"]))
            if event_date is None:
                for cell in cells:
                    event_date = _parse_date_cell(cell.get_text(" ", strip=True))
                    if event_date:
                        break
            if event_date is None:
                continue

            finish_time_sec = None
            finish_time_display = None
            time_text = _cell_text(cells, cols["time"])
            if time_text:
                finish_time_sec, finish_time_display = parse_finish_time(time_text)
            if finish_time_sec is None:
                for cell in cells:
                    time_parsed = parse_finish_time(cell.get_text(" ", strip=True))
                    if time_parsed[0] is not None:
                        finish_time_sec, finish_time_display = time_parsed
                        break

            pace_sec_per_km = None
            pace_display = None
            pace_text = _cell_text(cells, cols["pace"])
            if pace_text:
                pace_sec_per_km, pace_display = parse_pace_value(pace_text)

            location_key, location_name = _extract_location_from_row(row, base_url)
            if not location_name and cols["event"] is not None:
                location_name = _cell_text(cells, cols["event"])

            if event_number is None:
                for link in row.find_all("a", href=True):
                    if EVENT_LINK_RE.search(link["href"]):
                        num_match = re.search(r"#(\d+)", link.get_text(strip=True))
                        if num_match:
                            event_number = int(num_match.group(1))
                            break

            location_slug = location_key or (location_name or "unknown").lower().replace(" ", "_")
            # One finisher per location per day — key must not include # or place (they can be corrected later).
            external_result_key = f"profile:{external_user_id}:{event_date.isoformat()}:{location_slug}"
            results.append(
                CanonicalRunResult(
                    external_result_key=external_result_key,
                    event_date=event_date,
                    external_user_id=external_user_id,
                    participant_name=display_name,
                    position=position,
                    finish_time_sec=finish_time_sec,
                    finish_time_display=finish_time_display,
                    pace_sec_per_km=pace_sec_per_km,
                    pace_display=pace_display,
                    location_external_key=location_key,
                    location_name=location_name,
                    event_number=event_number,
                    is_pr=row_is_pr(row),
                )
            )

    return results


def parse_athlete_volunteering_html(
    html: str,
    *,
    external_user_id: str,
    display_name: str,
    profile_url: str = "",
) -> list[CanonicalVolunteerResult]:
    soup = BeautifulSoup(html, "html.parser")
    header = _find_volunteering_section_header(soup)
    if header is None:
        return []

    parsed = urlparse(profile_url or "https://s95.ru/")
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    accordion_rows = _parse_volunteering_accordion(
        header,
        external_user_id=external_user_id,
        display_name=display_name,
        base_url=base_url,
    )
    if accordion_rows:
        return accordion_rows

    return _parse_volunteering_tables(
        header,
        external_user_id=external_user_id,
        display_name=display_name,
    )


def parse_athlete_html(html: str, profile_url: str, external_user_id: str) -> CanonicalParticipant:
    soup = BeautifulSoup(html, "html.parser")
    display_name = _parse_display_name(soup)
    page_kind = classify_athlete_page(html, display_name=display_name)
    if page_kind == "not_found":
        raise AthletePageNotFoundError(f"Athlete page not found: {external_user_id}")
    if page_kind == "unavailable":
        raise AthletePageUnavailableError(f"Athlete page unavailable (login/registration): {external_user_id}")
    if not display_name:
        raise AthleteParseError("Не удалось определить имя участника")

    barcode, planning = parse_barcode_and_planning(soup)
    page_text = soup.get_text(" ", strip=True)
    total_runs = (
        _parse_stat_value(soup, "Всего финишей")
        or _parse_stat_value(soup, "Финишей")
        or _parse_total_from_patterns(page_text, RUNS_TOTAL_PATTERNS)
    )
    total_volunteering = (
        _parse_total_from_patterns(page_text, VOLS_TOTAL_PATTERNS)
        or _parse_stat_value(soup, "Всего волонтёрств")
        or _parse_stat_value(soup, "Всего волонтерств")
    )

    return CanonicalParticipant(
        external_user_id=external_user_id,
        display_name=display_name,
        profile_url=profile_url,
        total_runs=total_runs,
        total_volunteering=total_volunteering,
        barcode_id=barcode,
        planning_location=planning,
        source_url=profile_url,
    )


def _friend_links_after_header(soup: BeautifulSoup, header_pattern: re.Pattern[str]) -> list[dict[str, str]]:
    header = soup.find(lambda tag: tag.name in ["h2", "h3", "h4"] and header_pattern.search(tag.get_text(strip=True)))
    if header is None:
        return []
    container = header.find_parent("div") or header.parent
    if container is None:
        return []
    friends: list[dict[str, str]] = []
    for link in container.find_all("a", href=ATHLETE_ID_RE):
        href = link.get("href", "")
        match = ATHLETE_ID_RE.search(href)
        if not match:
            continue
        friends.append(
            {
                "external_user_id": match.group(1),
                "display_name": link.get_text(strip=True),
                "profile_url": urljoin("https://s95.ru", href),
            }
        )
    return friends


def parse_athlete_friends_html(html: str) -> dict[str, list[dict[str, str]]]:
    soup = BeautifulSoup(html, "html.parser")
    return {
        "friends_added": _friend_links_after_header(soup, FRIENDS_HEADER_RE),
        "friends_of": _friend_links_after_header(soup, IN_FRIENDS_HEADER_RE),
    }


def parse_athlete_awards_html(html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    awards: list[dict[str, str]] = []
    header = soup.find(lambda tag: tag.name in ["h2", "h3", "h4"] and AWARDS_HEADER_RE.search(tag.get_text(strip=True)))
    if header is None:
        return awards
    container = header.find_parent("div") or header.parent
    if container is None:
        return awards
    for item in container.find_all(["li", "div", "span", "a"]):
        text = item.get_text(" ", strip=True)
        if not text or text.lower() == "загрузка...":
            continue
        if len(text) > 200:
            continue
        if AWARDS_HEADER_RE.search(text):
            continue
        if any(marker in text.lower() for marker in ("всего", "загрузка", "посещ", "рекорд", "лучшее", "trophies", "new ")):
            continue
        if re.fullmatch(r"\d+", text):
            continue
        if text not in {a["title"] for a in awards}:
            awards.append({"title": text})
    return awards[:50]


def participant_to_preview(
    participant: CanonicalParticipant,
    *,
    parkrun_eligible: bool,
    runs: list[CanonicalRunResult] | None = None,
    volunteering: list[CanonicalVolunteerResult] | None = None,
) -> ProfilePreview:
    from app.profile_preview import build_recent_preview_activities

    return ProfilePreview(
        external_user_id=participant.external_user_id,
        display_name=participant.display_name,
        profile_url=participant.profile_url,
        total_runs=participant.total_runs,
        total_volunteering=participant.total_volunteering,
        club_name=participant.club_name,
        barcode_id=participant.barcode_id,
        planning_location=participant.planning_location,
        parkrun_eligible=parkrun_eligible,
        platform_code="s95",
        recent_activities=build_recent_preview_activities(runs or [], volunteering or []),
    )
