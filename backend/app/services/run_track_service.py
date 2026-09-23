"""Треки участника: приём файла или ссылки, привязка к пробежке, сверка с протоколом.

Что здесь происходит с треком:
1. разбор источника (track_parsing) и расчёт метрик (track_metrics);
2. поиск своей пробежки в протоколе по дате и координатам старта;
3. обрезка точек окрестностью локации — трек до дома мы не храним;
4. сверка времени по треку с временем в протоколе.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Event, Location, Platform, PlatformLink, RunResult, RunTrack, User
from app.services.track_metrics import assess_quality, compute_metrics, haversine
from app.services.track_parsing import ParsedTrack, TrackParseError, TrackPoint
from app.services.track_validation import evaluate_course_fitness

# Радиус, за пределами которого точки не храним: старт с запасом на парк, но
# без дороги от дома (согласовано в Ч33 — «точки вне парка не хранить»).
PRIVACY_RADIUS_M = 2000.0
# Насколько далеко от локации может начаться трек, чтобы привязка считалась
# уверенной.
LOCATION_MATCH_RADIUS_M = 1500.0
# Сколько точек максимум храним: посекундная запись часовой пробежки проходит
# целиком, более длинные прореживаем.
MAX_STORED_POINTS = 3600


def build_track(db: Session, user: User, parsed: ParsedTrack) -> RunTrack:
    """Собирает запись трека из разобранного источника. Не сохраняет в БД."""
    if not parsed.points:
        raise TrackParseError("В треке нет точек.")

    base_time = parsed.points[0].at
    series: list[tuple[float, float, float]] = []
    for point in parsed.points:
        if point.at and base_time:
            offset = (point.at - base_time).total_seconds()
        else:
            offset = float(len(series))
        series.append((point.lat, point.lon, offset))

    elevations = [point.elevation_m for point in parsed.points]
    metrics = compute_metrics(series, elevations if any(v is not None for v in elevations) else None)
    quality_class, quality = assess_quality(series)

    run_result, event, location, start_distance_m = _match_run(db, user.id, parsed)
    kept = _trim_to_location(parsed.points, series, location)
    points_payload = [
        [round(lat, 6), round(lon, 6), round(offset, 1), elevation]
        for lat, lon, offset, elevation in kept
    ]

    track = RunTrack(
        user_id=user.id,
        run_result_id=run_result.id if run_result else None,
        event_id=event.id if event else None,
        location_id=location.id if location else None,
        source=parsed.source,
        source_ref=parsed.source_ref,
        source_url=parsed.source_url,
        started_at=parsed.started_at,
        duration_sec=parsed.duration_sec,
        device_distance_m=parsed.device_distance_m,
        distance_m=metrics.get("distance_m"),
        elevation_gain_m=parsed.elevation_gain_m,
        elevation_loss_m=parsed.elevation_loss_m,
        min_elevation_m=parsed.min_elevation_m,
        max_elevation_m=parsed.max_elevation_m,
        device_name=parsed.device_name,
        device_firmware=parsed.device_firmware,
        has_barometer=parsed.has_barometer,
        point_count=len(series),
        sample_interval_sec=quality.get("sample_interval_sec"),
        quality_class=quality_class,
        quality=quality,
        metrics=metrics,
        points=points_payload,
        protocol_delta_sec=_protocol_delta(metrics, run_result),
        start_distance_m=round(start_distance_m, 1) if start_distance_m is not None else None,
        status="ok",
    )
    eligible, reason, note = evaluate_course_fitness(
        metrics=metrics,
        quality_class=quality_class,
        quality=quality,
        elevation_gain_m=parsed.elevation_gain_m,
        protocol_delta_sec=track.protocol_delta_sec,
        has_location=location is not None,
        start_distance_m=start_distance_m,
    )
    track.is_course_eligible = eligible
    track.exclusion_reason = reason
    track.exclusion_note = note
    return track


def _match_run(
    db: Session,
    user_id: UUID,
    parsed: ParsedTrack,
) -> tuple[RunResult | None, Event | None, Location | None, float | None]:
    """Ищет пробежку участника, к которой относится трек: дата плюс координаты.

    Четвёртым значением — расстояние от начала трека до локации. Если ни одна
    пробежка не подошла, возвращается расстояние до ближайшей: по нему видно,
    что трек записан в другой местности.
    """
    if not parsed.started_at:
        return None, None, None, None

    started = parsed.started_at.astimezone(UTC)
    # Часовые пояса локаций сдвигают дату старта: смотрим соседние сутки.
    dates = {(started + timedelta(days=shift)).date() for shift in (-1, 0, 1)}
    rows = db.execute(
        select(RunResult, Event, Location)
        .join(Event, RunResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Event.platform_id == Platform.id)
        .join(PlatformLink, PlatformLink.participant_id == RunResult.participant_id)
        .where(
            PlatformLink.user_id == user_id,
            PlatformLink.platform_id == Platform.id,
            Event.event_date.in_(dates),
            Event.is_test_event.is_(False),
        )
    ).all()
    if not rows:
        return None, None, None, None

    start_point = (parsed.points[0].lat, parsed.points[0].lon)
    best: tuple[float, RunResult, Event, Location] | None = None
    nearest_distance: float | None = None
    for run_result, event, location in rows:
        if location.latitude is None or location.longitude is None:
            # Координат нет — принимаем только если это единственный кандидат.
            distance = LOCATION_MATCH_RADIUS_M if len(rows) == 1 else float("inf")
        else:
            distance = haversine(start_point, (location.latitude, location.longitude))
            if nearest_distance is None or distance < nearest_distance:
                nearest_distance = distance
        if distance <= LOCATION_MATCH_RADIUS_M and (best is None or distance < best[0]):
            best = (distance, run_result, event, location)

    if best is None:
        return None, None, None, nearest_distance
    return best[1], best[2], best[3], best[0]


def _trim_to_location(
    points: list[TrackPoint],
    series: list[tuple[float, float, float]],
    location: Location | None,
) -> list[tuple[float, float, float, float | None]]:
    """Отрезает дорогу от дома и обратно, прореживая слишком длинные треки.

    Режем ИМЕННО хвосты, а не все далёкие точки: трасса не обязана целиком
    помещаться в круг радиусом 2 км. На «туда и обратно» вдоль набережной
    разворот стоит в 2,5 км от старта, и отбор по расстоянию выкусывал из
    середины кусок самой трассы — линия на карте обрывалась, а у 8 треков
    из 133 пропадало до 1,1 км (поймано 23.09.2026). Приватность от этого
    не страдает: круг тот же, просто всё, что внутри пробежки, остаётся.
    """
    if location is not None and location.latitude is not None and location.longitude is not None:
        center = (location.latitude, location.longitude)
    else:
        center = (series[0][0], series[0][1])

    near = [
        index
        for index, (lat, lon, _offset) in enumerate(series)
        if haversine((lat, lon), center) <= PRIVACY_RADIUS_M
    ]
    if near:
        first, last = near[0], near[-1]
    else:
        first, last = 0, len(series) - 1

    kept: list[tuple[float, float, float, float | None]] = [
        (lat, lon, offset, point.elevation_m)
        for point, (lat, lon, offset) in zip(points[first : last + 1], series[first : last + 1], strict=False)
    ]

    if len(kept) > MAX_STORED_POINTS:
        step = len(kept) // MAX_STORED_POINTS + 1
        kept = kept[::step]
    return kept


def _protocol_delta(metrics: dict[str, Any], run_result: RunResult | None) -> int | None:
    """Насколько время по треку длиннее протокольного.

    Часы почти всегда показывают больше: после линии человек ещё идёт по
    коридору, а иногда и забывает остановить запись.
    """
    if run_result is None or run_result.finish_time_sec is None:
        return None
    finish_at = metrics.get("finish_at_sec")
    if finish_at is None:
        return None
    return int(round(finish_at - run_result.finish_time_sec))


def find_existing(db: Session, user_id: UUID, source: str, source_ref: str) -> RunTrack | None:
    """Сохранённый трек того же источника. Черновики предпросмотра не в счёт.

    Иначе брошенный предпросмотр («отменить» не нажали, закрыли вкладку)
    навсегда блокировал бы повторную загрузку того же файла.
    """
    return db.scalar(
        select(RunTrack).where(
            RunTrack.user_id == user_id,
            RunTrack.source == source,
            RunTrack.source_ref == source_ref,
            RunTrack.status == "ok",
        )
    )


def drop_own_previews(db: Session, user_id: UUID) -> None:
    """Убирает брошенные черновики участника перед новой загрузкой.

    Черновики админского импорта не трогаем: у них заполнен import_batch_id,
    и распоряжается ими админка, а не кабинет.
    """
    for track in db.scalars(
        select(RunTrack).where(
            RunTrack.user_id == user_id,
            RunTrack.status == "preview",
            RunTrack.import_batch_id.is_(None),
        )
    ):
        db.delete(track)
    db.flush()


def list_tracks(db: Session, user_id: UUID, *, limit: int = 100) -> list[RunTrack]:
    """Треки кабинета: загрузки админа со статусом preview сюда не попадают."""
    return list(
        db.scalars(
            select(RunTrack)
            .where(RunTrack.user_id == user_id, RunTrack.status == "ok")
            .order_by(RunTrack.started_at.desc().nullslast(), RunTrack.created_at.desc())
            .limit(limit)
        )
    )


def get_track(
    db: Session,
    user_id: UUID,
    track_id: UUID,
    *,
    include_preview: bool = False,
) -> RunTrack | None:
    """Трек участника. По умолчанию — только сохранённый.

    include_preview нужен там, где человек работает со своим черновиком:
    смотрит разбор перед сохранением, сохраняет его или отменяет.
    """
    statuses = ["ok", "preview"] if include_preview else ["ok"]
    return db.scalar(
        select(RunTrack).where(
            RunTrack.id == track_id,
            RunTrack.user_id == user_id,
            RunTrack.status.in_(statuses),
        )
    )


def delete_track(db: Session, user_id: UUID, track_id: UUID) -> bool:
    track = get_track(db, user_id, track_id, include_preview=True)
    if track is None:
        return False
    db.delete(track)
    db.flush()
    return True


def ensure_consent(db: Session, user: User) -> None:
    """Первая загрузка фиксирует согласие на обработку трека."""
    if user.track_consent_at is None:
        user.track_consent_at = datetime.now(UTC)
        db.flush()
