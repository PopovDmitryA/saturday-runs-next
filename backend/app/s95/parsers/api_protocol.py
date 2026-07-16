from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from app.platform_adapters.canonical import CanonicalRunResult, CanonicalVolunteerResult
from app.s95.parsers.protocol import UNKNOWN_NAMES
from app.s95.parsers.volunteer_roles import (
    canonical_s95_api_role,
    s95_volunteer_role_key,
)
from app.time_format import parse_finish_time

UNKNOWN_NAME = "Unknown"


@dataclass
class ParsedApiProtocol:
    event_date: date
    run_results: list[CanonicalRunResult]
    volunteer_results: list[CanonicalVolunteerResult]
    # external_user_id -> cross-platform codes / meta found in the protocol
    athlete_codes: dict[str, dict] = field(default_factory=dict)


def parse_activity_date(raw: str) -> date:
    """Activity protocol date comes as DD.MM.YYYY (unlike the YYYY-MM-DD list endpoint)."""
    raw = (raw or "").strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unrecognised activity date: {raw!r}")


def _athlete_codes(athlete: dict) -> dict:
    codes: dict[str, object] = {}
    for key in ("parkrun_code", "fiveverst_code", "runpark_code", "parkzhrun_code"):
        value = athlete.get(key)
        if value is not None:
            codes[key] = value
    gender = athlete.get("gender")
    if gender:
        codes["gender"] = gender
    club = athlete.get("club")
    if club:
        codes["club"] = club
    return codes


def parse_s95_activity(
    activity: dict,
    *,
    location_external_key: str,
    location_name: str,
    source_url: str,
) -> ParsedApiProtocol:
    """Convert a /activities/{id}.json payload into canonical run + volunteer results."""
    event_date = parse_activity_date(activity.get("date", ""))

    run_results: list[CanonicalRunResult] = []
    volunteer_results: list[CanonicalVolunteerResult] = []
    athlete_codes: dict[str, dict] = {}

    for index, item in enumerate(activity.get("results", []) or []):
        athlete = item.get("athlete") or {}
        athlete_id = athlete.get("id")
        name = (athlete.get("name") or "").strip() or None
        if athlete_id is None and name is None:
            continue

        position = item.get("position")
        try:
            position = int(position) if position is not None else None
        except (TypeError, ValueError):
            position = None

        is_unknown_runner = athlete_id is None and name in UNKNOWN_NAMES
        if athlete_id is not None:
            external_user_id = str(athlete_id)
        elif is_unknown_runner:
            # «НЕИЗВЕСТНЫЙ» — все безымянные делят одно имя; без уникального id они
            # схлопнутся в одну строку через fallback по (event_id, participant_id).
            marker = position if position is not None else f"i{index}"
            external_user_id = (
                f"unknown:{location_external_key}:{event_date.isoformat()}:{marker}"
            )
        else:
            external_user_id = f"unknown:{name}"

        finish_time_sec, finish_time_display = parse_finish_time(item.get("total_time"))

        external_result_key = (
            f"{location_external_key}:{event_date.isoformat()}:{external_user_id}:{position or 0}"
        )
        run_results.append(
            CanonicalRunResult(
                external_result_key=external_result_key,
                event_date=event_date,
                external_user_id=external_user_id,
                participant_name=name or UNKNOWN_NAME,
                position=position,
                finish_time_sec=finish_time_sec,
                finish_time_display=finish_time_display,
                status="unknown_runner" if is_unknown_runner else None,
                club_name=athlete.get("club"),
                location_external_key=location_external_key,
                location_name=location_name,
            )
        )
        codes = _athlete_codes(athlete)
        if codes:
            athlete_codes[external_user_id] = codes

    for item in activity.get("volunteers", []) or []:
        athlete = item.get("athlete") or {}
        athlete_id = athlete.get("id")
        name = (athlete.get("name") or "").strip() or None
        if athlete_id is None and name is None:
            continue
        external_user_id = str(athlete_id) if athlete_id is not None else None

        role_label = canonical_s95_api_role(item.get("role"))
        role_key = s95_volunteer_role_key(role_label)
        key_subject = external_user_id or name
        external_result_key = (
            f"vol:{location_external_key}:{event_date.isoformat()}:{key_subject}:{role_key}"
        )
        volunteer_results.append(
            CanonicalVolunteerResult(
                external_result_key=external_result_key,
                event_date=event_date,
                external_user_id=external_user_id,
                participant_name=name,
                role=role_label,
                source_url=source_url,
                location_external_key=location_external_key,
                location_name=location_name,
            )
        )
        if external_user_id is not None:
            codes = _athlete_codes(athlete)
            if codes:
                athlete_codes.setdefault(external_user_id, {}).update(codes)

    return ParsedApiProtocol(
        event_date=event_date,
        run_results=run_results,
        volunteer_results=volunteer_results,
        athlete_codes=athlete_codes,
    )
