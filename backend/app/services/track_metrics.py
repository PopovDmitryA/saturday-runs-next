"""Метрики трека: замер трассы, сплиты, круги, качество записи.

Считается по одному треку, но алгоритм один на всех: длина трассы не должна
зависеть от того, чей трек взяли первым. Поэтому сглаживание и пороги здесь
зафиксированы константами и применяются одинаково ко всем источникам.

Проверено на треке Лихославля 09.09.2026: сырое суммирование расстояний между
секундными точками даёт 5022 м, сглаженное — 4955 м, часы показывают 4992 м.
Разброс 1.3% при идеальной записи — отсюда и требование единого алгоритма.
"""

from __future__ import annotations

import math
from statistics import median
from typing import Any

EARTH_RADIUS_M = 6371000.0
# Окно сглаживания координат: 5 секундных точек убирают дрожание GPS, но не
# срезают реальные повороты.
SMOOTH_WINDOW = 5
# Шаг ресемпла для геометрии: на 10 м повороты читаются, шум уже не мешает.
GEOMETRY_STEP_M = 10.0
# Окно расчёта скорости, секунды.
SPEED_WINDOW_SEC = 9
# Поворот меньше этого угла на шаг ресемпла — это шум, а не поворот.
TURN_NOISE_DEG = 3.0
# Накопленный поворот, с которого считаем, что это именно поворот трассы.
TURN_MIN_DEG = 45.0
# От этого угла поворот считается разворотом «почти на месте».
U_TURN_DEG = 150.0
# Прямой участок держим, пока накопленный поворот не превысит порог.
STRAIGHT_TOLERANCE_DEG = 25.0
# Круги ищем автокорреляцией: у многокруговой трассы профиль «расстояние от
# старта» повторяется с периодом в длину круга.
LAP_MIN_LENGTH_M = 400.0
# Ниже этой корреляции считаем, что повторяющегося круга нет. Порог высокий
# намеренно: на 0.55 две трассы из выгрузки Дмитрия ложно определялись как
# восьмикруговые — корреляция упиралась в потолок перебора, а не в реальный круг.
LAP_MIN_CORRELATION = 0.75
# Больше этого числа кругов на парковой пятёрке не бывает.
MAX_LAPS = 8
# Насколько гипотеза может уступать лучшей, чтобы считаться равной ей.
LAP_SCORE_TOLERANCE = 0.02
# Финиш ищем по короткому окну скорости: широкое окно размазывает обрыв и
# сдвигает момент пересечения линии на секунды.
FINISH_SPEED_WINDOW_SEC = 3
# Ниже этой доли от бегового темпа человек уже не бежит, а идёт по коридору.
FINISH_RUNNING_RATIO = 0.7
# Сколько точек в профиле высоты: 200 хватает, чтобы график читался и на
# телефоне, и в постере, а весит он копейки.
ELEVATION_PROFILE_POINTS = 200
# Окно сглаживания высот: барометр дрожит на десятые доли метра.
ELEVATION_SMOOTH_WINDOW = 9
# Подъём или спуск меньше этого — шум прибора, в набор не идёт.
ELEVATION_NOISE_M = 1.0
# С какого уклона участок считается подъёмом, а не «плоско», в процентах.
GRADE_FLAT_PERCENT = 1.0
# Отдельный подъём трассы: короче этого не показываем как «главный».
MIN_CLIMB_M = 50.0


def haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    delta_lat = lat2 - lat1
    delta_lon = math.radians(b[1] - a[1])
    h = math.sin(delta_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def _smooth(points: list[tuple[float, float, float]]) -> list[tuple[float, float, float]]:
    half = SMOOTH_WINDOW // 2
    out: list[tuple[float, float, float]] = []
    for index in range(len(points)):
        start = max(0, index - half)
        stop = min(len(points), index + half + 1)
        window = points[start:stop]
        out.append(
            (
                sum(p[0] for p in window) / len(window),
                sum(p[1] for p in window) / len(window),
                points[index][2],
            )
        )
    return out


def _bearing(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    delta_lon = math.radians(b[1] - a[1])
    y = math.sin(delta_lon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(delta_lon)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _angle_delta(first: float, second: float) -> float:
    return (second - first + 540) % 360 - 180


def compute_metrics(
    points: list[tuple[float, float, float]],
    elevations: list[float | None] | None = None,
) -> dict[str, Any]:
    """points: [(lat, lon, секунды от начала записи)] — уже по возрастанию времени.

    elevations — высоты тех же точек, если источник их дал: только тогда
    считается профиль трассы «километраж → высота».
    """
    if len(points) < 10:
        return {}

    smoothed = _smooth(points)
    segments = [haversine(smoothed[i][:2], smoothed[i + 1][:2]) for i in range(len(smoothed) - 1)]
    cumulative = [0.0]
    for segment in segments:
        cumulative.append(cumulative[-1] + segment)
    total_m = cumulative[-1]
    raw_total_m = sum(haversine(points[i][:2], points[i + 1][:2]) for i in range(len(points) - 1))
    duration_sec = smoothed[-1][2] - smoothed[0][2]
    if total_m < 100 or duration_sec <= 0:
        return {}

    metrics: dict[str, Any] = {
        "distance_m": round(total_m, 1),
        "raw_distance_m": round(raw_total_m, 1),
        "duration_sec": round(duration_sec, 1),
        "start_end_gap_m": round(haversine(points[0][:2], points[-1][:2]), 1),
    }
    metrics["splits"] = _splits(smoothed, cumulative, total_m)
    speeds = _speeds(smoothed, cumulative)
    metrics.update(_geometry(smoothed, cumulative))
    metrics.update(_laps(smoothed, cumulative, total_m))
    metrics.update(_speed_marks(smoothed, cumulative, speeds, total_m))
    if elevations:
        metrics.update(_elevation(cumulative, elevations, total_m))
    return metrics


def _splits(
    smoothed: list[tuple[float, float, float]],
    cumulative: list[float],
    total_m: float,
) -> list[dict[str, Any]]:
    """Время на каждом полном километре плюс остаток."""
    splits: list[dict[str, Any]] = []
    previous_time = smoothed[0][2]
    km = 1
    for index in range(len(smoothed)):
        if cumulative[index] >= km * 1000:
            splits.append({"km": km, "seconds": round(smoothed[index][2] - previous_time, 1)})
            previous_time = smoothed[index][2]
            km += 1
    tail_m = total_m - (km - 1) * 1000
    if tail_m > 50:
        splits.append(
            {
                "km": None,
                "meters": round(tail_m),
                "seconds": round(smoothed[-1][2] - previous_time, 1),
            }
        )
    return splits


def _speeds(
    smoothed: list[tuple[float, float, float]],
    cumulative: list[float],
    window_sec: int = SPEED_WINDOW_SEC,
) -> list[float]:
    half = window_sec // 2
    speeds: list[float] = []
    for index in range(len(smoothed)):
        start = max(0, index - half)
        stop = min(len(smoothed) - 1, index + half)
        elapsed = smoothed[stop][2] - smoothed[start][2]
        speeds.append((cumulative[stop] - cumulative[start]) / elapsed if elapsed > 0 else 0.0)
    return speeds


def _resample(
    smoothed: list[tuple[float, float, float]],
    cumulative: list[float],
) -> list[tuple[float, float, float]]:
    """Точки через равные расстояния: только так углы поворотов сопоставимы."""
    resampled: list[tuple[float, float, float]] = []
    target = 0.0
    for index in range(len(smoothed)):
        if cumulative[index] >= target:
            resampled.append((smoothed[index][0], smoothed[index][1], cumulative[index]))
            target += GEOMETRY_STEP_M
    return resampled


def _geometry(smoothed: list[tuple[float, float, float]], cumulative: list[float]) -> dict[str, Any]:
    resampled = _resample(smoothed, cumulative)
    if len(resampled) < 5:
        return {}
    bearings = [_bearing(resampled[i][:2], resampled[i + 1][:2]) for i in range(len(resampled) - 1)]
    turns = [_angle_delta(bearings[i], bearings[i + 1]) for i in range(len(bearings) - 1)]

    turn_groups: list[float] = []
    accumulated = 0.0
    open_group = False
    for turn in turns:
        if abs(turn) > TURN_NOISE_DEG:
            if not open_group:
                open_group = True
                accumulated = 0.0
            accumulated += turn
        elif open_group:
            if abs(accumulated) > TURN_MIN_DEG:
                turn_groups.append(accumulated)
            open_group = False
    if open_group and abs(accumulated) > TURN_MIN_DEG:
        turn_groups.append(accumulated)

    longest: dict[str, float] = {"meters": 0.0, "from_m": 0.0}
    index = 0
    while index < len(turns):
        accumulated = 0.0
        end = index
        while end < len(turns) and abs(accumulated + turns[end]) < STRAIGHT_TOLERANCE_DEG:
            accumulated += turns[end]
            end += 1
        stop = min(end, len(resampled) - 1)
        length = resampled[stop][2] - resampled[index][2]
        if length > longest["meters"]:
            longest = {"meters": round(length), "from_m": round(resampled[index][2])}
        index = max(end, index + 1)

    return {
        "turn_sum_deg": round(sum(abs(t) for t in turns)),
        "turn_count": len(turn_groups),
        "u_turn_count": sum(1 for t in turn_groups if abs(t) > U_TURN_DEG),
        "longest_straight_m": longest["meters"],
        "longest_straight_from_m": longest["from_m"],
        **_footprint(resampled),
    }


# Шаг перебора угла при поиске самого тесного прямоугольника, градусы.
# Прямоугольник симметричен через 90°, поэтому дальше идти незачем; на градусе
# площадь уже не «дышит» — проверено на трассах от 150 м до 2,5 км в поперечнике.
FOOTPRINT_ANGLE_STEP_DEG = 1


def _footprint(resampled: list[tuple[float, float, float]]) -> dict[str, Any]:
    """Самый тесный прямоугольник, в который влезает трасса.

    Не по сторонам света: прямоугольник поворачиваем вместе с трассой и берём
    угол с наименьшей площадью. Иначе диагональная аллея давала бы огромный
    «квадрат» только оттого, что лежит наискосок к меридиану.

    Пять километров, уложенные в 150 × 300 м, — это про то, как густо
    намотана трасса; сама по себе цифра нагляднее, чем число кругов.
    """
    if len(resampled) < 5:
        return {}

    lat0 = sum(point[0] for point in resampled) / len(resampled)
    # Локальная плоскость: на масштабе трассы (километры) искажение меньше
    # метра, а считать в метрах и проще, и честнее.
    scale_lon = math.cos(math.radians(lat0)) * math.pi * EARTH_RADIUS_M / 180
    scale_lat = math.pi * EARTH_RADIUS_M / 180
    lon0 = sum(point[1] for point in resampled) / len(resampled)
    plane = [((point[1] - lon0) * scale_lon, (point[0] - lat0) * scale_lat) for point in resampled]

    best: tuple[float, float, float] | None = None
    for step in range(0, 90, FOOTPRINT_ANGLE_STEP_DEG):
        angle = math.radians(step)
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        xs = [x * cos_a + y * sin_a for x, y in plane]
        ys = [-x * sin_a + y * cos_a for x, y in plane]
        width = max(xs) - min(xs)
        height = max(ys) - min(ys)
        area = width * height
        if best is None or area < best[0]:
            best = (area, width, height)

    area, width, height = best  # type: ignore[misc]
    short, long = sorted((width, height))
    return {
        "box_short_m": round(short),
        "box_long_m": round(long),
        "box_area_m2": round(area),
    }


def _laps(smoothed: list[tuple[float, float, float]], cumulative: list[float], total_m: float) -> dict[str, Any]:
    """Сколько кругов в трассе и какой они длины.

    Проверяем гипотезы «дистанция поделена на N кругов» и берём ту, при которой
    ряд «расстояние от точки старта» лучше всего совпадает сам с собой со
    сдвигом на круг. Подсчёт возвратов в ту же точку не годится: на участках
    «туда и обратно» бегун проходит рядом с самим собой, и период выходит
    случайным (на Мещерском такой счёт давал три круга по 1.9 км при дистанции
    в 5 км — цифры, не сходящиеся между собой).
    """
    start_point = (smoothed[0][0], smoothed[0][1])
    step = GEOMETRY_STEP_M
    series: list[float] = []
    cursor = 0
    target = 0.0
    while target <= total_m:
        while cursor + 1 < len(cumulative) and cumulative[cursor + 1] < target:
            cursor += 1
        series.append(haversine(smoothed[cursor][:2], start_point))
        target += step
    single = {"lap_count": 1, "lap_length_m": round(total_m)}
    if len(series) < 40:
        return single

    mean = sum(series) / len(series)
    centred = [value - mean for value in series]

    scored: list[tuple[int, float]] = []
    for laps in range(2, MAX_LAPS + 1):
        lap_length = total_m / laps
        if lap_length < LAP_MIN_LENGTH_M:
            break
        lag = int(round(lap_length / step))
        overlap = len(series) - lag
        if lag <= 0 or overlap < 20:
            continue
        left = centred[:overlap]
        right = centred[lag : lag + overlap]
        left_norm = sum(value * value for value in left) ** 0.5
        right_norm = sum(value * value for value in right) ** 0.5
        if left_norm <= 0 or right_norm <= 0:
            continue
        scored.append((laps, sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)))

    if not scored:
        return single
    best_score = max(score for _laps, score in scored)
    if best_score < LAP_MIN_CORRELATION:
        return single
    # Круг, повторённый дважды, коррелирует не хуже одинарного: из одинаково
    # хороших гипотез берём самый короткий круг, то есть наибольшее их число.
    best_laps = max(laps for laps, score in scored if score >= best_score - LAP_SCORE_TOLERANCE)
    return {
        "lap_count": best_laps,
        # Длину круга выводим из числа кругов, чтобы «N кругов по X» сходилось
        # с дистанцией.
        "lap_length_m": round(total_m / best_laps),
        "lap_confidence": round(best_score, 2),
    }


def _speed_marks(
    smoothed: list[tuple[float, float, float]],
    cumulative: list[float],
    speeds: list[float],
    total_m: float,
) -> dict[str, Any]:
    """Самое медленное и быстрое место дистанции и момент пересечения финиша."""
    middle = [
        (cumulative[i], speeds[i])
        for i in range(len(speeds))
        if 200 < cumulative[i] < total_m - 200 and speeds[i] > 0
    ]
    marks: dict[str, Any] = {}
    if speeds:
        # Максимальная скорость нужна для отбраковки: скачок GPS или поездка
        # видны именно по ней.
        marks["max_speed_ms"] = round(max(speeds), 2)
    if middle:
        slowest = min(middle, key=lambda item: item[1])
        fastest = max(middle, key=lambda item: item[1])
        marks["slowest_at_m"] = round(slowest[0])
        marks["slowest_speed_ms"] = round(slowest[1], 2)
        marks["fastest_at_m"] = round(fastest[0])
        marks["fastest_speed_ms"] = round(fastest[1], 2)

    # Финиш: последняя точка, где человек ещё бежал. Дальше он идёт по коридору
    # до карточки, поэтому время на часах длиннее протокола (проверено на треке
    # Мещерского: отсечка секундомера пришлась на падение скорости, а часы
    # остановились на 10 с позже).
    short = _speeds(smoothed, cumulative, window_sec=FINISH_SPEED_WINDOW_SEC)
    running = [value for value in short if value > 0]
    if not running:
        return marks
    threshold = median(running) * FINISH_RUNNING_RATIO
    for index in range(len(short) - 1, 0, -1):
        if short[index] >= threshold:
            marks["finish_at_sec"] = round(smoothed[index][2], 1)
            marks["finish_at_m"] = round(cumulative[index])
            marks["after_finish_sec"] = round(smoothed[-1][2] - smoothed[index][2], 1)
            break
    return marks


def _elevation(cumulative: list[float], elevations: list[float | None], total_m: float) -> dict[str, Any]:
    """Профиль трассы: высота по километражу плюс подъёмы, спуски и главная горка.

    Считается по высотам прибора: на равнинных трассах барометр точнее любой
    высотной модели (замер 09.09.2026 — SRTM насчитал 39 м набора там, где
    реальный перепад 6 м).
    """
    pairs = [
        (cumulative[index], value)
        for index, value in enumerate(elevations)
        if index < len(cumulative) and value is not None
    ]
    if len(pairs) < 10:
        return {}

    # Равномерная сетка по дистанции: график должен быть привязан к километрам,
    # а не к точкам записи (на подъёме они чаще, на спуске реже).
    step = total_m / (ELEVATION_PROFILE_POINTS - 1)
    grid: list[tuple[float, float]] = []
    cursor = 0
    for index in range(ELEVATION_PROFILE_POINTS):
        target = index * step
        while cursor + 1 < len(pairs) and pairs[cursor + 1][0] < target:
            cursor += 1
        left = pairs[cursor]
        right = pairs[min(cursor + 1, len(pairs) - 1)]
        span = right[0] - left[0]
        share = 0.0 if span <= 0 else (target - left[0]) / span
        grid.append((target, left[1] + (right[1] - left[1]) * max(0.0, min(1.0, share))))

    half = ELEVATION_SMOOTH_WINDOW // 2
    profile: list[tuple[float, float]] = []
    for index, (distance, _value) in enumerate(grid):
        window = grid[max(0, index - half) : min(len(grid), index + half + 1)]
        profile.append((distance, sum(item[1] for item in window) / len(window)))

    heights = [value for _distance, value in profile]
    gain = loss = 0.0
    reference = heights[0]
    for value in heights[1:]:
        delta = value - reference
        if abs(delta) < ELEVATION_NOISE_M:
            continue
        if delta > 0:
            gain += delta
        else:
            loss -= delta
        reference = value

    uphill_m = downhill_m = 0.0
    for index in range(len(profile) - 1):
        run = profile[index + 1][0] - profile[index][0]
        if run <= 0:
            continue
        grade = (profile[index + 1][1] - profile[index][1]) / run * 100
        if grade > GRADE_FLAT_PERCENT:
            uphill_m += run
        elif grade < -GRADE_FLAT_PERCENT:
            downhill_m += run

    return {
        # [[метры от старта, высота], ...] — готово к отрисовке как есть.
        "elevation_profile": [[round(distance), round(value, 1)] for distance, value in profile],
        "elevation_gain_profile_m": round(gain),
        "elevation_loss_profile_m": round(loss),
        "elevation_min_m": round(min(heights), 1),
        "elevation_max_m": round(max(heights), 1),
        "uphill_share": round(uphill_m / total_m, 3),
        "downhill_share": round(downhill_m / total_m, 3),
        **_main_climb(profile),
    }


def _main_climb(profile: list[tuple[float, float]]) -> dict[str, Any]:
    """Самый заметный подъём трассы: где он, какой длины и насколько крутой.

    Подъём ведём от подножия до вершины и закрываем, когда высота откатилась
    от достигнутого максимума заметнее шума — иначе пологий спуск за вершиной
    «съедает» набор и горка выглядит вдвое меньше, чем она есть.
    """
    best: dict[str, Any] = {}
    start = 0
    peak = 0

    def consider(from_index: int, to_index: int) -> None:
        nonlocal best
        length = profile[to_index][0] - profile[from_index][0]
        rise = profile[to_index][1] - profile[from_index][1]
        if length >= MIN_CLIMB_M and rise > best.get("climb_rise_m", 0):
            best = {
                "climb_from_m": round(profile[from_index][0]),
                "climb_length_m": round(length),
                "climb_rise_m": round(rise, 1),
                "climb_grade_percent": round(rise / length * 100, 1),
            }

    for index in range(1, len(profile)):
        if profile[index][1] > profile[peak][1]:
            peak = index
        elif profile[peak][1] - profile[index][1] > ELEVATION_NOISE_M:
            consider(start, peak)
            start = peak = index
    consider(start, peak)
    return best


def assess_quality(points: list[tuple[float, float, float]]) -> tuple[str, dict[str, Any]]:
    """Класс записи: A — годится для паспорта трассы, B — личный разбор, C — сырьё.

    Смотрим на то, что видно в самом треке: частоту записи, пропуски и дрожание
    на прямых. Модель прибора — только приор, решает фактическое качество.
    """
    if len(points) < 10:
        return "C", {"reason": "точек слишком мало"}

    intervals = [points[i + 1][2] - points[i][2] for i in range(len(points) - 1)]
    intervals = [value for value in intervals if value > 0]
    if not intervals:
        return "C", {"reason": "у точек нет времени"}
    sample_interval = median(intervals)
    gaps = sum(1 for value in intervals if value > 5)
    gap_share = gaps / len(intervals)

    smoothed = _smooth(points)
    offsets = [haversine(points[i][:2], smoothed[i][:2]) for i in range(len(points))]
    noise_m = median(offsets) if offsets else 0.0

    quality: dict[str, Any] = {
        "sample_interval_sec": round(sample_interval, 2),
        "gap_share": round(gap_share, 3),
        "noise_m": round(noise_m, 2),
        "point_count": len(points),
    }

    if sample_interval <= 2 and gap_share < 0.02 and noise_m <= 3.0:
        return "A", quality
    if sample_interval <= 5 and gap_share < 0.1:
        return "B", quality
    return "C", quality
