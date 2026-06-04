from __future__ import annotations

import re

RUN_DISTANCE_KM = 5

PACE_PER_KM_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
MAX_PACE_MINUTES_PER_KM = 15


def format_pace_display(pace_sec_per_km: int) -> str:
    minutes = pace_sec_per_km // 60
    seconds = pace_sec_per_km % 60
    return f"{minutes}:{seconds:02d}"


def parse_pace_value(value: str | None) -> tuple[int | None, str | None]:
    if not value:
        return None, None
    cleaned = value.strip()
    if not cleaned:
        return None, None
    match = PACE_PER_KM_RE.match(cleaned)
    if not match:
        return None, None
    minutes = int(match.group(1))
    seconds = int(match.group(2))
    if minutes >= MAX_PACE_MINUTES_PER_KM:
        return None, None
    total = minutes * 60 + seconds
    return total, format_pace_display(total)


def pace_from_finish_time_sec(finish_time_sec: int | None) -> tuple[int | None, str | None]:
    if finish_time_sec is None or finish_time_sec <= 0:
        return None, None
    pace_sec = int(round(finish_time_sec / RUN_DISTANCE_KM))
    return pace_sec, format_pace_display(pace_sec)


def resolve_run_pace(
    pace_sec_per_km: int | None,
    pace_display: str | None,
    finish_time_sec: int | None,
) -> tuple[int | None, str | None]:
    if pace_sec_per_km is not None:
        display = pace_display or format_pace_display(pace_sec_per_km)
        return pace_sec_per_km, display
    parsed_sec, parsed_display = parse_pace_value(pace_display)
    if parsed_sec is not None:
        return parsed_sec, parsed_display
    return pace_from_finish_time_sec(finish_time_sec)
