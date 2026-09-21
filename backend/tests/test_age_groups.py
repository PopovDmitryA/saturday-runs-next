"""Единый модуль возрастных групп: Python-правила и их SQL-двойник не расходятся.

Правила разбора сведены в app/core/age_groups.py 21.09.2026 (аудит DUP-01);
поведение витрин закреплено в test_location_page_service, платформенных
чисток — в test_parkrun_age_category и test_five_verst_age_category. Здесь —
то, чего раньше не проверял никто: что SQL-выражение для отчётов чистит место
в группе так же, как Python-функция 5 вёрст.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.age_groups import (
    age_group_is_plausible,
    age_group_sort_key,
    age_group_sql,
    normalize_age_group,
    normalize_five_verst_age_group,
)

# Сырые значения run_results.age_category, как их кладут синки: 5 вёрст с местом
# в группе и без, parkrun-коды, пустота и мусор. Пробелов по краям здесь нет
# намеренно: SQL-выражение их не срезает (в отличие от Python-функции), а в
# базу они и не попадают — парсеры отдают категорию уже обрезанной. Это
# известное и осознанно не выравненное различие (аудит DUP-01, 21.09.2026):
# менять регулярку отчётов ради случая, которого нет в данных, незачем.
RAW_CATEGORIES: tuple[str | None, ...] = (
    "М40-44 (2)",
    "Ж35-39 (12)",
    "М10 (1)",
    "М75+",
    "SM25-29",
    "VW45-49",
    "",
    None,
    "54.38%",
)


@pytest.mark.parametrize("raw", RAW_CATEGORIES)
def test_age_group_sql_matches_python_twin(db_session: Session, raw: str | None) -> None:
    sql_value = db_session.execute(
        text(f"SELECT {age_group_sql(':raw')}"),
        {"raw": raw},
    ).scalar()
    assert sql_value == normalize_five_verst_age_group(raw)


def test_raw_cleanup_feeds_display_normalization() -> None:
    """Чистка протокола и нормализация витрины — два шага одной цепочки."""
    assert normalize_age_group(normalize_five_verst_age_group("М40-44 (2)")) == "40–44"
    assert normalize_age_group(normalize_five_verst_age_group("Ж10 (3)")) == "<10"


def test_plausibility_uses_the_same_sort_age() -> None:
    assert age_group_is_plausible("75+")
    assert age_group_is_plausible("<10")
    assert not age_group_is_plausible("110–114")
    assert not age_group_is_plausible("<120")
    assert age_group_sort_key("<10") < age_group_sort_key("10–14") < age_group_sort_key("75+")
