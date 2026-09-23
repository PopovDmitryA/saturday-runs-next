"""Паспорт трассы локации: сборка из треков и отслеживание смены трассы.

Профиль считается медианами по годным трекам: одна пробежка меряет трассу с
точностью около полутора процентов, поэтому чем больше треков, тем ближе цифра
к настоящей.

Трассы иногда меняют — переносят старт, перекладывают дорожки. Тогда прежние
измерения описывают уже не то, что бегут сейчас. Смену определяем по отпечатку
геометрии: трек раскладывается на ячейки сетки 50 метров, и если подряд
несколько пробежек прошли не там, где раньше, заводится новая версия трассы,
а прежняя уходит в историю.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from statistics import median
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import LocationCourseProfile, RunTrack

# Сторона ячейки отпечатка. 50 м — примерно ширина парковой аллеи с запасом:
# тот же маршрут попадает в те же ячейки, соседняя дорожка — уже в другие.
CELL_SIZE_M = 50.0
# Насколько трек должен совпасть с эталоном версии, чтобы считаться «той же
# трассой» (доля общих ячеек, Жаккар).
SAME_COURSE_SIMILARITY = 0.6
# Сколько подряд непохожих треков означают, что трассу поменяли, а не что
# у одного человека сбился GPS.
COURSE_CHANGE_STREAK = 3
# Сколько точек в медианном профиле высот — как у отдельного трека.
PROFILE_POINTS = 200
# Точек в линии трассы для карты: 400 хватает, чтобы повороты читались.
GEOMETRY_POINTS = 400

# Порог, после которого показываем цифры трассы. Пока идёт сбор через админку,
# хватает одного трека; перед выкатом на всех поднимем до пяти РАЗНЫХ людей
# (MIN_UNIQUE_USERS), чтобы паспорт не держался на одном бегуне.
MIN_TRACKS_FOR_PUBLIC = 1
MIN_UNIQUE_USERS_FOR_PUBLIC = 1


def _cells(points: list[Any]) -> set[str]:
    """Отпечаток геометрии: ячейки сетки, через которые прошёл трек."""
    cells: set[str] = set()
    for point in points:
        if not point or len(point) < 2:
            continue
        lat, lon = float(point[0]), float(point[1])
        # Метры на градус: по широте почти постоянны, по долготе зависят от широты.
        lat_cell = int(lat * 111320.0 / CELL_SIZE_M)
        lon_cell = int(lon * 111320.0 * math.cos(math.radians(lat)) / CELL_SIZE_M)
        cells.add(f"{lat_cell}:{lon_cell}")
    return cells


def similarity(first: set[str], second: set[str]) -> float:
    """Доля общих ячеек: 1.0 — та же трасса, около нуля — совсем другая."""
    if not first or not second:
        return 0.0
    return len(first & second) / len(first | second)


def _median_or_none(values: list[Any]) -> float | None:
    clean = [float(value) for value in values if value is not None]
    return round(median(clean), 1) if clean else None


def _median_profile(tracks: list[RunTrack]) -> list[list[float]]:
    """Медианный профиль высот версии, приведённый к низшей точке каждого трека.

    Абсолютная отметка барометра плавает с погодой, а форма профиля от старта
    к старту повторяется — поэтому сравниваем и усредняем именно форму.
    """
    profiles: list[list[list[float]]] = []
    for track in tracks:
        profile = (track.metrics or {}).get("elevation_profile") or []
        if len(profile) >= PROFILE_POINTS // 2:
            lowest = min(point[1] for point in profile)
            profiles.append([[point[0], point[1] - lowest] for point in profile])
    if not profiles:
        return []

    length = min(len(profile) for profile in profiles)
    result: list[list[float]] = []
    for index in range(length):
        distance = median([profile[index][0] for profile in profiles])
        height = median([profile[index][1] for profile in profiles])
        result.append([round(distance), round(height, 1)])
    return result


def eligible_tracks(db: Session, location_id: UUID, course_version: int | None = None) -> list[RunTrack]:
    query = select(RunTrack).where(
        RunTrack.location_id == location_id,
        RunTrack.status == "ok",
        RunTrack.is_course_eligible.is_(True),
    )
    if course_version is not None:
        query = query.where(RunTrack.course_version == course_version)
    return list(db.scalars(query.order_by(RunTrack.started_at.asc())))


def current_profile(db: Session, location_id: UUID) -> LocationCourseProfile | None:
    return db.scalar(
        select(LocationCourseProfile).where(
            LocationCourseProfile.location_id == location_id,
            LocationCourseProfile.is_current.is_(True),
        )
    )


def profile_history(db: Session, location_id: UUID) -> list[LocationCourseProfile]:
    """Все версии трассы, от новой к старой."""
    return list(
        db.scalars(
            select(LocationCourseProfile)
            .where(LocationCourseProfile.location_id == location_id)
            .order_by(LocationCourseProfile.course_version.desc())
        )
    )


def assign_course_version(db: Session, track: RunTrack) -> int:
    """Определяет версию трассы для нового трека.

    Если геометрия расходится с текущей версией — версия пока не меняется:
    одна странная пробежка это ещё не новая трасса. Решение принимается в
    rebuild_location_profile, когда таких треков наберётся подряд достаточно.
    """
    if track.location_id is None:
        return 1
    profile = current_profile(db, track.location_id)
    if profile is None:
        return 1
    return profile.course_version


def rebuild_location_profile(db: Session, location_id: UUID) -> LocationCourseProfile | None:
    """Пересобирает паспорт трассы и, если нужно, заводит новую версию."""
    tracks = eligible_tracks(db, location_id)
    if not tracks:
        return None

    profile = current_profile(db, location_id)
    version = profile.course_version if profile else 1

    if profile and profile.geometry_cells:
        reference = set(profile.geometry_cells)
        # Смотрим последние треки: если подряд несколько прошли не по той
        # геометрии, трассу поменяли.
        recent = tracks[-COURSE_CHANGE_STREAK:]
        if len(recent) == COURSE_CHANGE_STREAK and all(
            similarity(_cells(track.points), reference) < SAME_COURSE_SIMILARITY for track in recent
        ):
            profile.is_current = False
            db.flush()
            version += 1
            for track in recent:
                track.course_version = version
            db.flush()
            profile = None

    for track in tracks:
        if track.course_version is None:
            track.course_version = version
    db.flush()

    version_tracks = [track for track in tracks if track.course_version == version]
    if not version_tracks:
        return profile

    if profile is None:
        profile = LocationCourseProfile(location_id=location_id, course_version=version, is_current=True)
        db.add(profile)
        db.flush()

    _fill_profile(profile, version_tracks)
    db.flush()
    return profile


def _fill_profile(profile: LocationCourseProfile, tracks: list[RunTrack]) -> None:
    metrics = [track.metrics or {} for track in tracks]
    distances = [value for value in (track.distance_m for track in tracks) if value]

    profile.tracks_count = len(tracks)
    profile.unique_user_count = len({track.user_id for track in tracks})
    profile.distance_m = _median_or_none(distances)
    profile.distance_min_m = round(min(distances), 1) if distances else None
    profile.distance_max_m = round(max(distances), 1) if distances else None
    profile.elevation_gain_m = _median_or_none([track.elevation_gain_m for track in tracks])
    profile.elevation_span_m = _median_or_none(
        [
            item["elevation_max_m"] - item["elevation_min_m"]
            for item in metrics
            if item.get("elevation_max_m") is not None and item.get("elevation_min_m") is not None
        ]
    )
    profile.turn_sum_deg = _median_or_none([item.get("turn_sum_deg") for item in metrics])
    profile.u_turn_count = _median_or_none([item.get("u_turn_count") for item in metrics])
    profile.longest_straight_m = _median_or_none([item.get("longest_straight_m") for item in metrics])
    laps = [int(item["lap_count"]) for item in metrics if item.get("lap_count")]
    profile.lap_count = int(median(laps)) if laps else None
    profile.uphill_share = _median_or_none([item.get("uphill_share") for item in metrics])
    profile.downhill_share = _median_or_none([item.get("downhill_share") for item in metrics])
    profile.climb_length_m = _median_or_none([item.get("climb_length_m") for item in metrics])
    profile.climb_rise_m = _median_or_none([item.get("climb_rise_m") for item in metrics])
    profile.climb_grade_percent = _median_or_none([item.get("climb_grade_percent") for item in metrics])
    profile.elevation_profile = _median_profile(tracks)

    # Линия для карты — трек с медианной длиной: он и есть «типичный» проход
    # трассы, в отличие от самого короткого или самого длинного.
    by_distance = sorted((track for track in tracks if track.distance_m), key=lambda item: item.distance_m or 0)
    if by_distance:
        representative = by_distance[len(by_distance) // 2]
        points = representative.points or []
        step = max(1, len(points) // GEOMETRY_POINTS)
        profile.geometry = [[point[0], point[1]] for point in points[::step]]

    # Отпечаток версии — объединение ячеек её треков.
    cells: set[str] = set()
    for track in tracks:
        cells |= _cells(track.points)
    profile.geometry_cells = sorted(cells)

    starts = [track.started_at for track in tracks if track.started_at]
    profile.first_track_at = min(starts) if starts else None
    profile.last_track_at = max(starts) if starts else None
    profile.updated_at = datetime.now(UTC)


def rebuild_for_tracks(db: Session, tracks: list[RunTrack]) -> None:
    """Пересобирает паспорта всех локаций, которых коснулись эти треки."""
    for location_id in {track.location_id for track in tracks if track.location_id}:
        rebuild_location_profile(db, location_id)


def has_enough_data(profile: LocationCourseProfile | None) -> bool:
    """Хватает ли треков, чтобы показывать цифры трассы, а не серую заглушку."""
    if profile is None:
        return False
    return (
        profile.tracks_count >= MIN_TRACKS_FOR_PUBLIC
        and profile.unique_user_count >= MIN_UNIQUE_USERS_FOR_PUBLIC
    )
