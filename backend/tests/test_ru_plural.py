"""Сторож согласования подписей счётчиков с числом (app/ru_plural.py)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.ru_plural import UNIT_FORMS, plural_form, unit_form
from app.services.leaderboard_service import COUNT_BY_META, METRIC_META

_ACHIEVEMENTS = Path(__file__).resolve().parents[1] / "app" / "services" / "achievements_service.py"


@pytest.mark.parametrize(
    ("count", "expected"),
    [(1, "дата"), (2, "даты"), (4, "даты"), (5, "дат"), (11, "дат"), (12, "дат"), (21, "дата"), (101, "дата"),
     (111, "дат"), (192, "даты"), (0, "дат"), (-1, "дата"), (-3, "даты")],
)  # fmt: skip
def test_plural_form(count: int, expected: str) -> None:
    assert plural_form(count, ("дата", "даты", "дат")) == expected


def test_unknown_unit_is_returned_as_is() -> None:
    assert unit_form(3, "сек") == "сек"


def test_many_form_is_the_unit_itself() -> None:
    """Ключ — подпись из кода (родительный множественного), она же третья форма."""
    assert [unit for unit, forms in UNIT_FORMS.items() if forms[2] != unit] == []


def test_every_challenge_and_rating_unit_has_forms() -> None:
    """Новая подпись челленджа или рейтинга без форм — и уведомление снова
    напишет «+1 → 191 дат». Отсюда список того, что надо дописать в UNIT_FORMS."""
    challenge_units = set(re.findall(r'unit="([^"]+)"', _ACHIEVEMENTS.read_text(encoding="utf-8")))
    rating_units = {meta["unit"] for meta in METRIC_META.values()}
    rating_units |= {meta["unit"] for by in COUNT_BY_META.values() for meta in by.values()}
    missing = sorted(u for u in challenge_units | rating_units if u not in UNIT_FORMS and u != "%")
    assert missing == [], f"Нет форм в app/ru_plural.py UNIT_FORMS: {missing}"
