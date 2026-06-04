from __future__ import annotations

from app.time_format import parse_finish_time

__all__ = ["parse_finish_time", "parse_leader_time"]


def parse_leader_time(value: str | None) -> tuple[int | None, str | None]:
    """Parse 'Name (18:59)' fragments from summary rows."""
    if not value or "(" not in value:
        return None, None
    _, time_part = value.rsplit("(", 1)
    return parse_finish_time(time_part.replace(")", "").strip())
