from __future__ import annotations

from datetime import date

UNDATED_ACTIVITY_PLACEHOLDER = date(1970, 1, 1)


def has_real_activity_date(value: date | None) -> bool:
    return value is not None and value > UNDATED_ACTIVITY_PLACEHOLDER
