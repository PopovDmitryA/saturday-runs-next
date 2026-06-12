from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from bs4 import BeautifulSoup

from app.parkrun.errors import ParkrunProfileNotFound, ParkrunProfileParseError
from app.parkrun.fetch.coordinator import fetch_page_html
from app.parkrun.volunteer_credits import is_total_credits_label
from app.platform_adapters.canonical import (
    CanonicalParticipant,
    CanonicalRunResult,
    CanonicalVolunteerResult,
    ProfilePreview,
)
from app.platform_adapters.parkrun.url import ParsedParkrunProfile
from app.time_format import format_finish_time_display, parse_finish_time

NAME_BARCODE_RE = re.compile(r"^(.+?)\s*\(A(\d+)\)\s*$", re.IGNORECASE)
AGE_CATEGORY_RE = re.compile(
    r"most recent age category was\s+([A-Za-z0-9\-]+)",
    re.IGNORECASE,
)
TOTAL_RUNS_RE = re.compile(r"(\d+)\s+parkruns?\s+total", re.IGNORECASE)
DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")


def _slugify_event(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower().strip())
    return slug.strip("-") or "unknown"


def _parse_run_date(value: str) -> date:
    return datetime.strptime(value.strip(), "%d/%m/%Y").date()


def _parse_time_seconds(value: str) -> int | None:
    cleaned = value.strip()
    if not cleaned or cleaned in {"-", "—"}:
        return None
    parts = cleaned.split(":")
    try:
        if len(parts) == 2:
            minutes, seconds = int(parts[0]), int(parts[1])
            return minutes * 60 + seconds
        if len(parts) == 3:
            hours, minutes, seconds = int(parts[0]), int(parts[1]), int(parts[2])
            return hours * 3600 + minutes * 60 + seconds
    except ValueError:
        return None
    return None


def _normalize_header(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\n", " ").strip().lower())


def _find_table_by_heading(soup: BeautifulSoup, heading_substring: str) -> BeautifulSoup | None:
    needle = re.sub(r"\s+", " ", heading_substring.lower())
    for heading in soup.find_all(["h2", "h3", "h4", "caption"]):
        heading_text = re.sub(r"\s+", " ", heading.get_text(" ", strip=True).lower())
        if needle not in heading_text:
            continue
        if heading.name == "caption":
            parent = heading.parent
            if parent is not None and parent.name == "table":
                return parent
        table = heading.find_next("table")
        if table is not None:
            return table
        parent_table = heading.find_parent("table")
        if parent_table is not None:
            return parent_table
    return None


def _table_headers(table: BeautifulSoup) -> list[str]:
    headers: list[str] = []
    for row in table.find_all("tr"):
        cells = row.find_all("th")
        if cells:
            headers = [_normalize_header(cell.get_text()) for cell in cells]
            break
    return headers


def _row_cells(row) -> list[str]:
    return [cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"])]


def _cell_by_header(headers: list[str], cells: list[str], *names: str) -> str | None:
    for name in names:
        key = _normalize_header(name)
        if key in headers:
            idx = headers.index(key)
            if idx < len(cells):
                return cells[idx]
    return None


def _parse_name_barcode(soup: BeautifulSoup) -> tuple[str, str]:
    for heading in soup.find_all("h2"):
        text = heading.get_text(" ", strip=True)
        match = NAME_BARCODE_RE.match(text)
        if match:
            return match.group(1).strip(), match.group(2)
    raise ParkrunProfileParseError("Не удалось найти имя и штрихкод на странице parkrun")


def _parse_profile_meta(soup: BeautifulSoup, athlete_id: str) -> tuple[int | None, str | None, int]:
    page_text = soup.get_text(" ", strip=True)
    total_runs = None
    runs_match = TOTAL_RUNS_RE.search(page_text)
    if runs_match:
        total_runs = int(runs_match.group(1))

    age_match = AGE_CATEGORY_RE.search(page_text)
    age_category = age_match.group(1) if age_match else None

    volunteer_table = _find_table_by_heading(soup, "volunteer summary")
    volunteer_total = 0
    if volunteer_table is not None:
        _summary, volunteer_total = _parse_volunteer_summary_table(volunteer_table)

    return total_runs, age_category, volunteer_total


def _parse_volunteer_summary_table(
    volunteer_table: BeautifulSoup,
) -> tuple[list[dict[str, object]], int]:
    summary: list[dict[str, object]] = []
    occasions_sum = 0
    total_credits: int | None = None
    headers = _table_headers(volunteer_table)

    body = volunteer_table.find("tbody")
    body_rows = body.find_all("tr") if body is not None else volunteer_table.find_all("tr")
    for row in body_rows:
        if row.find_parent("tfoot") is not None:
            continue
        cells = _row_cells(row)
        if not cells or cells == headers:
            continue
        if row.find("th") and not row.find("td"):
            continue
        role_raw = _cell_by_header(headers, cells, "role")
        occasions_raw = _cell_by_header(headers, cells, "occasions")
        role = (role_raw or "").strip()
        if not role or role.lower() == "role" or is_total_credits_label(role):
            continue
        occasions = int(occasions_raw) if occasions_raw and occasions_raw.isdigit() else 0
        occasions_sum += occasions
        summary.append({"role": role, "occasions": occasions})

    tfoot = volunteer_table.find("tfoot")
    if tfoot is not None:
        for row in tfoot.find_all("tr"):
            cells = _row_cells(row)
            if len(cells) < 2:
                continue
            if is_total_credits_label(cells[0]):
                digits = re.search(r"\d+", cells[1])
                if digits:
                    total_credits = int(digits.group())

    return summary, total_credits if total_credits is not None else occasions_sum


def _parse_volunteer_summary(
    soup: BeautifulSoup,
    *,
    athlete_id: str,
    display_name: str,
) -> tuple[list[dict[str, object]], int]:
    volunteer_table = _find_table_by_heading(soup, "volunteer summary")
    if volunteer_table is None:
        return [], 0
    del display_name, athlete_id
    return _parse_volunteer_summary_table(volunteer_table)


def parse_all_results_html(
    html: str,
    *,
    parsed: ParsedParkrunProfile,
    display_name: str = "",
    barcode_id: str = "",
) -> tuple[CanonicalParticipant, list[CanonicalRunResult], dict[str, object]]:
    soup = BeautifulSoup(html, "html.parser")
    if "403 forbidden" in soup.get_text(" ", strip=True).lower()[:200]:
        raise ParkrunProfileNotFound("Профиль parkrun не найден или недоступен")

    name, athlete_from_page = _parse_name_barcode(soup)
    if athlete_from_page != parsed.athlete_id:
        raise ParkrunProfileParseError("ID в ссылке не совпадает с ID на странице")

    total_runs, age_category, volunteer_total = _parse_profile_meta(soup, parsed.athlete_id)
    volunteer_summary, volunteer_occasions = _parse_volunteer_summary(
        soup,
        athlete_id=parsed.athlete_id,
        display_name=display_name or name,
    )
    if volunteer_occasions == 0:
        volunteer_occasions = volunteer_total

    participant = CanonicalParticipant(
        external_user_id=parsed.athlete_id,
        display_name=display_name or name,
        profile_url=parsed.profile_url,
        total_runs=total_runs,
        total_volunteering=volunteer_occasions or None,
        barcode_id=barcode_id or f"A{parsed.athlete_id}",
        source_url=parsed.all_results_url,
    )

    runs: list[CanonicalRunResult] = []
    results_table = _find_table_by_heading(soup, "all results")
    if results_table is not None:
        headers = _table_headers(results_table)
        for row in results_table.find_all("tr"):
            cells = _row_cells(row)
            if not cells or cells == headers:
                continue

            event_name = _cell_by_header(headers, cells, "event")
            run_date_raw = _cell_by_header(headers, cells, "run date")
            run_number_raw = _cell_by_header(headers, cells, "run number")
            position_raw = _cell_by_header(headers, cells, "pos", "position")
            time_raw = _cell_by_header(headers, cells, "time")
            age_grade = _cell_by_header(headers, cells, "age grade", "age\ngrade")

            if not event_name or not run_date_raw or not DATE_RE.match(run_date_raw):
                continue

            event_date = _parse_run_date(run_date_raw)
            event_slug = _slugify_event(event_name)
            run_number = int(run_number_raw) if run_number_raw and run_number_raw.isdigit() else None
            position = int(position_raw) if position_raw and position_raw.isdigit() else None
            finish_time_sec = _parse_time_seconds(time_raw or "")
            if finish_time_sec is not None:
                finish_time_display = format_finish_time_display(finish_time_sec)
            else:
                _parsed_sec, finish_time_display = parse_finish_time(time_raw)
                finish_time_sec = _parsed_sec
            # Personal records for parkrun are computed in app.services.parkrun_pr_service.
            is_pr = False

            external_result_key = (
                f"parkrun:{parsed.athlete_id}:{event_slug}:{event_date.isoformat()}:{run_number or 0}"
            )
            runs.append(
                CanonicalRunResult(
                    external_result_key=external_result_key,
                    event_date=event_date,
                    external_user_id=parsed.athlete_id,
                    participant_name=participant.display_name,
                    position=position,
                    finish_time_sec=finish_time_sec,
                    finish_time_display=finish_time_display,
                    age_category=age_grade,
                    is_pr=is_pr,
                    location_external_key=event_slug,
                    location_name=event_name,
                    event_number=run_number,
                )
            )

    profile_extra = {
        "age_category": age_category,
        "volunteer_summary": volunteer_summary,
        "volunteer_occasions_total": volunteer_occasions,
    }
    return participant, runs, profile_extra


def parse_profile_summary_html(html: str, *, parsed: ParsedParkrunProfile) -> tuple[CanonicalParticipant, dict[str, object]]:
    soup = BeautifulSoup(html, "html.parser")
    if "403 forbidden" in soup.get_text(" ", strip=True).lower()[:200]:
        raise ParkrunProfileNotFound("Профиль parkrun не найден или недоступен")

    name, athlete_from_page = _parse_name_barcode(soup)
    if athlete_from_page != parsed.athlete_id:
        raise ParkrunProfileParseError("ID в ссылке не совпадает с ID на странице")

    total_runs, age_category, volunteer_total = _parse_profile_meta(soup, parsed.athlete_id)
    volunteer_summary, volunteer_occasions = _parse_volunteer_summary(
        soup,
        athlete_id=parsed.athlete_id,
        display_name=name,
    )
    if volunteer_occasions == 0:
        volunteer_occasions = volunteer_total

    participant = CanonicalParticipant(
        external_user_id=parsed.athlete_id,
        display_name=name,
        profile_url=parsed.profile_url,
        total_runs=total_runs,
        total_volunteering=volunteer_occasions or None,
        barcode_id=f"A{parsed.athlete_id}",
        source_url=parsed.profile_url,
    )
    profile_extra = {
        "age_category": age_category,
        "volunteer_summary": volunteer_summary,
        "volunteer_occasions_total": volunteer_occasions,
    }
    return participant, profile_extra


def volunteer_summary_to_canonical(
    summary: list[dict[str, object]],
    *,
    athlete_id: str,
    display_name: str,
) -> list[CanonicalVolunteerResult]:
    results: list[CanonicalVolunteerResult] = []
    for item in summary:
        role = str(item.get("role") or "").strip()
        occasions = int(item.get("occasions") or 0)
        if not role or occasions <= 0:
            continue
        results.append(
            CanonicalVolunteerResult(
                external_result_key=f"parkrun:{athlete_id}:vol-summary:{_slugify_event(role)}",
                event_date=date(1970, 1, 1),
                external_user_id=athlete_id,
                participant_name=display_name,
                role=f"{role} ({occasions}×)",
                location_external_key="parkrun-summary",
                location_name="parkrun (сводка ролей)",
            )
        )
    return results


def fetch_athlete_activity(parsed: ParsedParkrunProfile) -> tuple[
    CanonicalParticipant,
    list[CanonicalRunResult],
    list[CanonicalVolunteerResult],
    dict[str, object],
]:
    import logging
    import time

    from app.config import get_settings

    logger = logging.getLogger(__name__)
    settings = get_settings()

    logger.info("parkrun import: step 1/2 summary %s", parsed.profile_url)
    base_html = fetch_page_html(parsed.profile_url, reason="profile-summary")
    base_participant, base_extra = parse_profile_summary_html(base_html, parsed=parsed)

    delay = settings.parkrun_fetch_between_pages_delay_seconds
    if delay > 0:
        logger.info("parkrun import: waiting %.1fs before /all", delay)
        time.sleep(delay)

    logger.info("parkrun import: step 2/2 all-results %s", parsed.all_results_url)
    all_html = fetch_page_html(parsed.all_results_url, reason="all-results")
    participant, runs, profile_extra = parse_all_results_html(all_html, parsed=parsed)
    logger.info("parkrun import: all-results ok, runs=%d", len(runs))

    if base_extra.get("volunteer_summary"):
        profile_extra["volunteer_summary"] = base_extra["volunteer_summary"]
        profile_extra["volunteer_occasions_total"] = base_extra.get("volunteer_occasions_total", 0)
        participant = CanonicalParticipant(
            external_user_id=participant.external_user_id,
            display_name=participant.display_name or base_participant.display_name,
            profile_url=participant.profile_url,
            total_runs=participant.total_runs,
            total_volunteering=base_extra.get("volunteer_occasions_total") or participant.total_volunteering,
            barcode_id=participant.barcode_id or base_participant.barcode_id,
            source_url=participant.source_url,
        )

    # parkrun publishes only role totals without event dates; keep count in profile_extra.
    volunteering: list[CanonicalVolunteerResult] = []
    return participant, runs, volunteering, profile_extra


@dataclass(frozen=True)
class ParkrunProfilePreviewFetch:
    preview: ProfilePreview
    runs: list[CanonicalRunResult]
    profile_extra: dict


def fetch_profile_preview_activity(parsed: ParsedParkrunProfile) -> ParkrunProfilePreviewFetch:
    import logging
    import time

    from app.config import get_settings
    from app.parkrun.errors import ParkrunBanDetected
    from app.parkrun.fetch.diagnostics import inspect_html_response
    from app.profile_preview import build_recent_preview_activities

    logger = logging.getLogger(__name__)
    settings = get_settings()

    logger.info("parkrun preview: step 1/2 summary %s", parsed.profile_url)
    summary_html = fetch_page_html(parsed.profile_url, reason="preview-summary")
    summary_check = inspect_html_response(summary_html, url=parsed.profile_url)
    if summary_check.is_protection:
        raise ParkrunBanDetected(
            f"Ban/protection on parkrun summary for {parsed.profile_url} ({summary_check.summary})"
        )
    logger.info("parkrun preview: summary ok (%s)", summary_check.summary)
    participant, profile_extra = parse_profile_summary_html(summary_html, parsed=parsed)

    runs: list[CanonicalRunResult] = []
    delay = settings.parkrun_fetch_between_pages_delay_seconds
    if delay > 0:
        logger.info("parkrun preview: waiting %.1fs before /all", delay)
        time.sleep(delay)

    logger.info("parkrun preview: step 2/2 all-results %s", parsed.all_results_url)
    try:
        all_html = fetch_page_html(parsed.all_results_url, reason="preview-all")
        all_check = inspect_html_response(all_html, url=parsed.all_results_url)
        logger.info("parkrun preview: all-results fetched (%s)", all_check.summary)
        _all_participant, runs, all_extra = parse_all_results_html(all_html, parsed=parsed)
        if all_extra.get("age_category") and not profile_extra.get("age_category"):
            profile_extra["age_category"] = all_extra.get("age_category")
        logger.info("parkrun preview: complete with %d recent runs", len(runs))
    except ParkrunBanDetected as exc:
        # Summary is enough for linking; /all can be synced later when cooldown ends.
        logger.warning(
            "parkrun preview: summary ok, /all blocked for athlete %s — %s",
            parsed.athlete_id,
            exc,
        )

    preview = ProfilePreview(
        platform_code="parkrun",
        external_user_id=participant.external_user_id,
        display_name=participant.display_name,
        profile_url=participant.profile_url,
        total_runs=participant.total_runs,
        total_volunteering=participant.total_volunteering,
        barcode_id=participant.barcode_id,
        age_category=profile_extra.get("age_category"),
        parkrun_eligible=False,
        recent_activities=build_recent_preview_activities(runs, []),
    )
    return ParkrunProfilePreviewFetch(preview=preview, runs=runs, profile_extra=profile_extra)


def fetch_profile_preview(parsed: ParsedParkrunProfile) -> ProfilePreview:
    return fetch_profile_preview_activity(parsed).preview
