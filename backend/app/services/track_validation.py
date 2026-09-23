"""Отбор треков, годных для паспорта трассы.

Класс записи (A/B/C) говорит о качестве GPS. Здесь решается другое: можно ли
этому треку верить как измерению трассы. Трек бывает безупречно записан, но
непригоден — пробежка по другой местности, сбитая разметка старта, правленые
организатором времена в протоколе.

Пример из жизни: Щёлково 16.05.2026 — запись класса A, но длина 5,22 км и
расхождение с протоколом 83 секунды: трасса в тот день была размечена криво, и
организатор правил времена финишёров руками. Такой трек в паспорт трассы не
идёт.
"""

from __future__ import annotations

from typing import Any

# Парковая пятёрка: всё, что заметно короче или длиннее, меряет не ту трассу.
COURSE_DISTANCE_MIN_M = 4600.0
COURSE_DISTANCE_MAX_M = 5500.0
# Расхождение с протоколом: секунды — норма, минута и больше означает, что
# трек и протокол описывают разные события.
MAX_PROTOCOL_DELTA_SEC = 60
# Средний темп: медленнее 11 мин/км — это прогулка, быстрее 2:30 — не бег.
MIN_AVG_SPEED_MS = 1.5
MAX_AVG_SPEED_MS = 6.7
# Мгновенная скорость выше — это скачок GPS или поездка, а не бег.
MAX_POINT_SPEED_MS = 9.0
# Набор и перепад на парковой пятёрке: выше этого — сбой барометра.
MAX_ELEVATION_GAIN_M = 250.0
MAX_ELEVATION_SPAN_M = 150.0
# Дальше от локации трек начинаться не может — это пробежка в другом месте.
MAX_START_DISTANCE_M = 1500.0
# Разрешающая способность записи, при которой трассе можно верить.
#
# Мерять надо метры, а не секунды: прогулка с записью раз в 5 с даёт 8 м
# между точками, бег с той же записью — 17 м, и это записи разного качества.
#
# Порог проверен 23.09.2026 двумя способами.
#
# 1. Слепое прореживание четырёх треков класса A до заданного шага. Даёт
#    верхнюю оценку потерь: поворот −14…−29% уже при 4 м, −27…−42% при 8 м.
#    Оценка завышенная — выбрасываются в том числе точки на поворотах.
#
# 2. Два трека ОДНОЙ трассы (Дружба): плотный FIT (3,1 м между точками) и
#    умная запись TCX (8,0 м). Реальное расхождение втрое меньше слепого
#    прореживания, но всё равно велико:
#       суммарный поворот  2957° против 2661°  (−10%)
#       длина              5015 м против 4852 м (−3,3%)
#       длинная прямая     582 м против 350 м   (−40%)
#    Для сравнения: два ПЛОТНЫХ трека одной трассы расходятся по длине на
#    1,6%. То есть редкая запись врёт вдвое сильнее собственного разброса
#    метода, а геометрию искажает до неузнаваемости.
#
# Поэтому в паспорт трассы идёт только плотная запись. На личный разбор
# трека это не влияет: там своя пробежка, а не сравнение трасс между собой.
COURSE_SPACING_M = 5.0

REASON_LABELS = {
    "distance_out_of_range": "длина не похожа на парковую пятёрку",
    "protocol_mismatch": "время трека расходится с протоколом",
    "implausible_pace": "средний темп вне разумных границ",
    "speed_spike": "в треке есть скачок скорости",
    "elevation_spike": "аномальный набор высоты",
    "elevation_span_spike": "аномальный перепад высот",
    "far_from_location": "трек начинается далеко от локации",
    "low_quality": "в записи есть пропуски",
    "sparse_recording": "точки записаны слишком редко",
    "no_location": "не привязан к пробежке из протокола",
}


def evaluate_course_fitness(
    *,
    metrics: dict[str, Any],
    quality_class: str | None,
    quality: dict[str, Any] | None = None,
    elevation_gain_m: float | None,
    protocol_delta_sec: int | None,
    has_location: bool,
    start_distance_m: float | None,
) -> tuple[bool, str | None, str | None]:
    """Годится ли трек для паспорта трассы.

    Возвращает (годен, код причины, текст для человека). Причина заполняется
    только когда трек забракован.
    """
    distance = metrics.get("distance_m")
    duration = metrics.get("duration_sec")

    if start_distance_m is not None and start_distance_m > MAX_START_DISTANCE_M:
        return False, "far_from_location", (
            f"Трек начинается в {start_distance_m / 1000:.1f} км от локации".replace(".", ",")
        )

    if not has_location:
        return False, "no_location", "Пробежка в протоколе не найдена"

    if distance is None or not (COURSE_DISTANCE_MIN_M <= distance <= COURSE_DISTANCE_MAX_M):
        shown = "—" if distance is None else f"{distance / 1000:.2f} км".replace(".", ",")
        return False, "distance_out_of_range", f"Длина {shown} выходит за рамки парковой пятёрки"

    if protocol_delta_sec is not None and abs(protocol_delta_sec) > MAX_PROTOCOL_DELTA_SEC:
        return False, "protocol_mismatch", (
            f"Время трека расходится с протоколом на {abs(protocol_delta_sec)} с"
        )

    if distance and duration:
        average_speed = distance / duration
        if not (MIN_AVG_SPEED_MS <= average_speed <= MAX_AVG_SPEED_MS):
            return False, "implausible_pace", f"Средняя скорость {average_speed:.1f} м/с".replace(".", ",")

    max_speed = metrics.get("max_speed_ms")
    if max_speed is not None and max_speed > MAX_POINT_SPEED_MS:
        return False, "speed_spike", f"Скачок скорости до {max_speed:.1f} м/с".replace(".", ",")

    if elevation_gain_m is not None and elevation_gain_m > MAX_ELEVATION_GAIN_M:
        return False, "elevation_spike", f"Набор высоты {elevation_gain_m:.0f} м"

    span = None
    if metrics.get("elevation_max_m") is not None and metrics.get("elevation_min_m") is not None:
        span = metrics["elevation_max_m"] - metrics["elevation_min_m"]
    if span is not None and span > MAX_ELEVATION_SPAN_M:
        return False, "elevation_span_spike", f"Перепад высот {span:.0f} м"

    if quality_class == "C":
        return False, "low_quality", "В записи есть пропуски"

    if quality_class == "B":
        spacing = (quality or {}).get("meters_per_point")
        shown = f" — {spacing:.0f} м".replace(".", ",") if spacing else ""
        return False, "sparse_recording", f"Точки идут слишком далеко друг от друга{shown}"

    return True, None, None
