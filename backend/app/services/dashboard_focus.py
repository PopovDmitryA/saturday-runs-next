"""Профили участника: автоопределение фокуса дашборда.

Четыре профиля (решение Дмитрия, вариант А, 27.08.2026): каждый включает
свою группу блоков на дашборде. Мультивыбор; пустой выбор или NULL —
показываются все блоки, как раньше.

Автонабор считается из уже готовой аналитики дашборда — отдельных запросов
в БД не нужно. Пороги первичные, подберём по жалобам: цель — чтобы человек
поправлял готовую догадку, а не заполнял анкету с нуля.
"""

from __future__ import annotations

from typing import Any

FOCUS_TOURIST = "tourist"
FOCUS_RACER = "racer"
FOCUS_VOLUNTEER = "volunteer"
FOCUS_REGULAR = "regular"

# Порядок каноничен: в нём профили показываются в модалке и настройках.
FOCUS_PROFILES: tuple[str, ...] = (
    FOCUS_REGULAR,
    FOCUS_RACER,
    FOCUS_TOURIST,
    FOCUS_VOLUNTEER,
)

# Турист: коллекция площадок уже заметна.
TOURIST_MIN_LOCATIONS = 10
# Волонтёр: столько смен случайно не набирается.
VOLUNTEER_MIN_OCCASIONS = 10
# Скоростник: стабильно высокие места ЛИБО время из 21 минуты.
RACER_MAX_AVG_POSITION = 10
RACER_MAX_BEST_FINISH_SEC = 21 * 60
# Постоянный участник: активен хотя бы в 40% суббот года ЛИБО держит серию.
REGULAR_MIN_CONSISTENCY_PCT = 40.0
REGULAR_MIN_STREAK = 8


def detect_focus_profiles(
    analytics: dict[str, Any],
    *,
    total_volunteering: int,
) -> list[str]:
    """Автонабор профилей по фактической активности.

    Совсем новичок (ни один порог не сработал) получает пустой список —
    модалка тогда предотмечает все профили: пусть снимет лишнее сам,
    угадывать за него не из чего.
    """
    detected: list[str] = []

    consistency = analytics.get("saturday_consistency_pct")
    streak = analytics.get("saturday_streak_current") or 0
    if (consistency is not None and consistency >= REGULAR_MIN_CONSISTENCY_PCT) or (
        streak >= REGULAR_MIN_STREAK
    ):
        detected.append(FOCUS_REGULAR)

    avg_position = analytics.get("avg_position")
    best_finish = analytics.get("best_finish_time_sec")
    location_records = analytics.get("location_records") or {}
    age_group_records = analytics.get("age_group_records") or {}
    if (
        (analytics.get("wins_count") or 0) > 0
        or (location_records.get("current_count") or 0) > 0
        or (age_group_records.get("current_count") or 0) > 0
        or (avg_position is not None and avg_position <= RACER_MAX_AVG_POSITION)
        or (best_finish is not None and best_finish <= RACER_MAX_BEST_FINISH_SEC)
    ):
        detected.append(FOCUS_RACER)

    if (analytics.get("unique_run_locations") or 0) >= TOURIST_MIN_LOCATIONS:
        detected.append(FOCUS_TOURIST)

    if total_volunteering >= VOLUNTEER_MIN_OCCASIONS:
        detected.append(FOCUS_VOLUNTEER)

    return detected


def normalize_focus_selection(raw: object) -> list[str] | None:
    """Валидация выбора из API: только известные ключи, канонический порядок.

    None — «не выбирал»; пустой список храним как есть: это осознанное
    «показывать всё» (модалку больше не предлагать).
    """
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValueError("dashboard_focus: ожидается список профилей")
    selected = {str(item) for item in raw}
    unknown = selected - set(FOCUS_PROFILES)
    if unknown:
        raise ValueError(f"dashboard_focus: неизвестные профили {sorted(unknown)}")
    return [profile for profile in FOCUS_PROFILES if profile in selected]
