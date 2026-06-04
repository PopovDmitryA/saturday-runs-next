from __future__ import annotations

from app.config import get_settings


def is_parkrun_eligible_barcode(barcode_id: str | None) -> bool:
    if not barcode_id:
        return False
    settings = get_settings()
    return len(barcode_id.strip()) <= settings.s95_parkrun_barcode_max_length


_PARKRUN_BARCODE_PREFIXES = ("A", "a", "\u0410", "\u0430")  # Latin A / Cyrillic А


def normalize_parkrun_athlete_id(barcode_id: str) -> str:
    """A7035519, А7035519 or 7035519 -> 7035519 for parkrun profile lookup."""
    cleaned = barcode_id.strip().replace(" ", "")
    if len(cleaned) > 1 and cleaned[0] in _PARKRUN_BARCODE_PREFIXES:
        return cleaned[1:]
    return cleaned
