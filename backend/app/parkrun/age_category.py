from __future__ import annotations

import re

# Возрастная группа parkrun/RunPark: SM25-29, VM40-44, JW11-14, JM10, SW18-19, VW70-74.
# Отсекает age grade («50.59%»), который старый код записывал в participants.age_category.
PARKRUN_AGE_GROUP_RE = re.compile(r"^[A-Z]{2,3}\d{1,2}(?:-\d{2})?$")


def normalize_parkrun_age_group(value: object) -> str | None:
    """Строка возрастной группы parkrun или None, если значение не похоже на группу."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if PARKRUN_AGE_GROUP_RE.match(cleaned):
        return cleaned
    return None
