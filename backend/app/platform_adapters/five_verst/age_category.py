"""Возрастная группа 5 вёрст — реэкспорт из app.core.age_groups.

Модуль оставлен, чтобы по импорту было видно, чей формат ожидается («М40-44 (2)»
с местом в группе); сами правила с 21.09.2026 (аудит DUP-01) лежат рядом с
остальными в app/core/age_groups.py, там же — SQL-двойник age_group_sql.
"""

from __future__ import annotations

from app.core.age_groups import FIVE_VERST_PLACE_SUFFIX_RE, normalize_five_verst_age_group

__all__ = ["FIVE_VERST_PLACE_SUFFIX_RE", "normalize_five_verst_age_group"]
