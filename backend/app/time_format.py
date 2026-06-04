from __future__ import annotations

import re

FINISH_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$")


def format_finish_time_display(total_seconds: int) -> str:
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def parse_finish_time(value: str | None) -> tuple[int | None, str | None]:
    if not value:
        return None, None
    cleaned = value.strip()
    if not cleaned:
        return None, None

    match = FINISH_TIME_RE.match(cleaned)
    if not match:
        return None, cleaned

    hours = int(match.group(1))
    minutes = int(match.group(2))
    seconds = int(match.group(3) or 0)

    if match.group(3) is None and hours < 60:
        total = hours * 60 + minutes
    else:
        total = hours * 3600 + minutes * 60 + seconds

    return total, format_finish_time_display(total)


def normalize_finish_time_display(
    finish_time_sec: int | None,
    finish_time_display: str | None,
) -> str | None:
    if finish_time_sec is not None:
        return format_finish_time_display(finish_time_sec)
    if not finish_time_display:
        return None
    parsed_sec, parsed_display = parse_finish_time(finish_time_display)
    return parsed_display if parsed_sec is not None else finish_time_display
