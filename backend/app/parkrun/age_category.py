"""Возрастная группа parkrun/RunPark — реэкспорт из app.core.age_groups.

Модуль оставлен, чтобы по импорту было видно, чей формат ожидается («SM25-29»,
а не «М30-34»); сами правила с 21.09.2026 (аудит DUP-01) лежат рядом с
остальными в app/core/age_groups.py.
"""

from __future__ import annotations

from app.core.age_groups import PARKRUN_AGE_GROUP_RE, normalize_parkrun_age_group

__all__ = ["PARKRUN_AGE_GROUP_RE", "normalize_parkrun_age_group"]
