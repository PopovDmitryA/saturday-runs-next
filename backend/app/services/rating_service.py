from __future__ import annotations

from datetime import date, timedelta
from typing import cast
from uuid import UUID

from sqlalchemy.orm import Session

from app.activity_url import resolve_activity_url
from app.models import (
    Event,
    EventCrosslink,
    Location,
    LocationRating,
    Participant,
    Platform,
    PlatformLink,
    RunResult,
    User,
)
from app.services.location_catalog_service import LocationCatalogIndex
from app.time_format import normalize_finish_time_display

# Право оценивать: минимум столько пробежек в истории (по всем системам).
MIN_RUNS_TO_RATE = 5
# Поставить НОВУЮ оценку можно старту за последние N дней (скользящее окно).
RATING_WINDOW_DAYS = 30
# Исправить/удалить оценку можно, пока старту не больше N дней; дальше — фиксируем.
RATING_EDIT_WINDOW_DAYS = 90
# Рейтинг локации показываем только с этого числа РАЗНЫХ оценивших (v2).
LOCATION_RATING_MIN_VOTERS = 10

# Приоритет платформы при дедупликации кросслинков (одна физическая пробежка).
_PLATFORM_ORDER = {"five_verst": 0, "s95": 1, "parkrun": 2, "runpark": 3}


class RatingError(Exception):
    """Ошибка бизнес-правил оценки (нельзя оценить / нет права)."""


def count_user_total_runs(db: Session, user_id: UUID, *, include_test_events: bool = False) -> int:
    """Число физических пробежек пользователя (кросслинки не дублируются)."""
    secondary = db.query(EventCrosslink.secondary_event_id).subquery()
    query = (
        db.query(RunResult.id)
        .join(Event, RunResult.event_id == Event.id)
        .join(PlatformLink, PlatformLink.participant_id == RunResult.participant_id)
        .filter(PlatformLink.user_id == user_id)
        .filter(Event.id.not_in(db.query(secondary.c.secondary_event_id)))
    )
    if not include_test_events:
        query = query.filter(Event.is_test_event.is_(False))
    return query.count()


def _is_editable(event_date: date, today: date | None = None) -> bool:
    """Оценку можно менять/удалять, пока старту не больше окна редактирования."""
    today = today or date.today()
    return event_date >= today - timedelta(days=RATING_EDIT_WINDOW_DAYS)


def _rating_to_dict(rating: LocationRating, *, today: date | None = None) -> dict[str, object]:
    return {
        "run_result_id": rating.run_result_id,
        "score_overall": rating.score_overall,
        "score_organization": rating.score_organization,
        "score_route": rating.score_route,
        "score_community": rating.score_community,
        "comment": rating.comment,
        "is_public": rating.is_public,
        # можно ли ещё исправить/удалить (в пределах 3 месяцев) или уже зафиксировано
        "editable": _is_editable(rating.event_date, today),
        "created_at": rating.created_at,
        "updated_at": rating.updated_at,
    }


def list_eligible_runs(db: Session, user_id: UUID) -> dict[str, object]:
    """Старты пользователя за окно (дедуп кросслинков) + его текущие оценки."""
    today = date.today()
    since = today - timedelta(days=RATING_WINDOW_DAYS)

    rows = (
        db.query(RunResult, Event, Location, Platform.code, Participant)
        .join(Event, RunResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Event.platform_id == Platform.id)
        .join(Participant, RunResult.participant_id == Participant.id)
        .join(PlatformLink, PlatformLink.participant_id == RunResult.participant_id)
        .filter(
            PlatformLink.user_id == user_id,
            PlatformLink.platform_id == Platform.id,
            Event.is_test_event.is_(False),
            Event.event_date >= since,
            Event.event_date <= today,
        )
        .all()
    )

    catalog_index = LocationCatalogIndex(db)
    # Дедуп по (дата, каноническая площадка) — оставляем приоритетную платформу.
    by_key: dict[tuple[date, str], dict[str, object]] = {}
    for run, event, location, platform_code, participant in rows:
        identity = catalog_index.canonical_identity_key(location, platform_code)
        key = (event.event_date, identity)
        entry = {
            "run_result_id": run.id,
            "event_date": event.event_date,
            "platform_code": platform_code,
            "location_name": catalog_index.display_name(location, platform_code),
            "location_city": location.city,
            "finish_time_display": normalize_finish_time_display(
                run.finish_time_sec, run.finish_time_display
            ),
            "position": run.position,
            "is_pr": bool(run.is_pr),
            "event_url": resolve_activity_url(
                platform_code=platform_code,
                event_date=event.event_date,
                event_number=event.event_number,
                event_source_url=event.source_url,
                location_external_key=location.external_key,
                profile_url=participant.profile_url,
            ),
            "_platform_order": _PLATFORM_ORDER.get(platform_code, 9),
        }
        existing = by_key.get(key)
        if existing is None or cast(int, entry["_platform_order"]) < cast(
            int, existing["_platform_order"]
        ):
            by_key[key] = entry

    entries = sorted(
        by_key.values(), key=lambda e: cast(date, e["event_date"]), reverse=True
    )

    run_ids = [cast(UUID, e["run_result_id"]) for e in entries]
    ratings: dict[UUID, LocationRating] = {}
    if run_ids:
        for rating in (
            db.query(LocationRating)
            .filter(LocationRating.user_id == user_id, LocationRating.run_result_id.in_(run_ids))
            .all()
        ):
            ratings[rating.run_result_id] = rating

    for entry in entries:
        entry.pop("_platform_order", None)
        existing_rating = ratings.get(cast(UUID, entry["run_result_id"]))
        entry["my_rating"] = _rating_to_dict(existing_rating) if existing_rating else None

    total_runs = count_user_total_runs(db, user_id)
    return {
        "can_rate": total_runs >= MIN_RUNS_TO_RATE,
        "total_runs": total_runs,
        "min_runs_required": MIN_RUNS_TO_RATE,
        "window_days": RATING_WINDOW_DAYS,
        "runs": entries,
    }


def _load_user_run(
    db: Session, user_id: UUID, run_result_id: UUID
) -> tuple[RunResult, Event, Location, str]:
    row = (
        db.query(RunResult, Event, Location, Platform.code)
        .join(Event, RunResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Event.platform_id == Platform.id)
        .join(PlatformLink, PlatformLink.participant_id == RunResult.participant_id)
        .filter(
            RunResult.id == run_result_id,
            PlatformLink.user_id == user_id,
            PlatformLink.platform_id == Platform.id,
        )
        .first()
    )
    if row is None:
        raise RatingError("Пробежка не найдена или не ваша")
    return row  # type: ignore[return-value]


def upsert_rating(
    db: Session,
    user: User,
    run_result_id: UUID,
    *,
    score_overall: int,
    score_organization: int | None,
    score_route: int | None,
    score_community: int | None,
    comment: str | None,
    is_public: bool,
) -> dict[str, object]:
    if count_user_total_runs(db, user.id) < MIN_RUNS_TO_RATE:
        raise RatingError(
            f"Оценивать можно после {MIN_RUNS_TO_RATE} пробежек в истории"
        )

    run, event, location, platform_code = _load_user_run(db, user.id, run_result_id)

    today = date.today()
    if event.event_date > today:
        raise RatingError("Нельзя оценить старт из будущего")

    rating = (
        db.query(LocationRating)
        .filter(
            LocationRating.user_id == user.id,
            LocationRating.run_result_id == run_result_id,
        )
        .one_or_none()
    )

    if rating is None:
        # Новая оценка — только на свежий старт (окно создания 30 дней).
        if event.event_date < today - timedelta(days=RATING_WINDOW_DAYS):
            raise RatingError(
                f"Оценить можно только старты за последние {RATING_WINDOW_DAYS} дней"
            )
    else:
        # Правка существующей — пока старту не больше окна редактирования (3 мес).
        if not _is_editable(event.event_date, today):
            raise RatingError(
                "Оценка зафиксирована: старту больше 3 месяцев, изменить нельзя"
            )

    catalog_index = LocationCatalogIndex(db)
    location_key = catalog_index.canonical_identity_key(location, platform_code)

    if rating is None:
        rating = LocationRating(
            user_id=user.id,
            run_result_id=run_result_id,
            location_id=location.id,
            location_key=location_key,
            event_date=event.event_date,
            platform_code=platform_code,
        )
        db.add(rating)

    rating.score_overall = score_overall
    rating.score_organization = score_organization
    rating.score_route = score_route
    rating.score_community = score_community
    rating.comment = comment
    rating.is_public = is_public
    # location_key/event_date/platform могли обновиться (пересинк) — держим свежими
    rating.location_id = location.id
    rating.location_key = location_key
    rating.event_date = event.event_date
    rating.platform_code = platform_code

    db.flush()
    db.refresh(rating)
    return _rating_to_dict(rating)


def delete_rating(db: Session, user_id: UUID, run_result_id: UUID) -> bool:
    rating = (
        db.query(LocationRating)
        .filter(
            LocationRating.user_id == user_id,
            LocationRating.run_result_id == run_result_id,
        )
        .one_or_none()
    )
    if rating is None:
        return False
    if not _is_editable(rating.event_date):
        raise RatingError(
            "Оценка зафиксирована: старту больше 3 месяцев, удалить нельзя"
        )
    db.delete(rating)
    db.flush()
    return True


def list_my_ratings(db: Session, user_id: UUID) -> dict[str, object]:
    """Все оценки пользователя (любой давности) + инфо о старте и флаг editable."""
    rows = (
        db.query(LocationRating, RunResult, Event, Location, Platform.code, Participant)
        .join(RunResult, LocationRating.run_result_id == RunResult.id)
        .join(Event, RunResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Event.platform_id == Platform.id)
        .join(Participant, RunResult.participant_id == Participant.id)
        .filter(LocationRating.user_id == user_id)
        .order_by(Event.event_date.desc())
        .all()
    )
    today = date.today()
    catalog_index = LocationCatalogIndex(db)
    ratings: list[dict[str, object]] = []
    for rating, run, event, location, platform_code, participant in rows:
        entry = _rating_to_dict(rating, today=today)
        entry.update(
            {
                "event_date": event.event_date,
                "platform_code": platform_code,
                "location_name": catalog_index.display_name(location, platform_code),
                "location_city": location.city,
                "finish_time_display": normalize_finish_time_display(
                    run.finish_time_sec, run.finish_time_display
                ),
                "position": run.position,
                "is_pr": bool(run.is_pr),
                "event_url": resolve_activity_url(
                    platform_code=platform_code,
                    event_date=event.event_date,
                    event_number=event.event_number,
                    event_source_url=event.source_url,
                    location_external_key=location.external_key,
                    profile_url=participant.profile_url,
                ),
            }
        )
        ratings.append(entry)

    total_runs = count_user_total_runs(db, user_id)
    return {
        "can_rate": total_runs >= MIN_RUNS_TO_RATE,
        "total_runs": total_runs,
        "min_runs_required": MIN_RUNS_TO_RATE,
        "create_window_days": RATING_WINDOW_DAYS,
        "edit_window_days": RATING_EDIT_WINDOW_DAYS,
        "ratings": ratings,
    }


def _user_display(user: User) -> str:
    if user.display_name:
        return user.display_name
    if user.telegram_username:
        return f"@{user.telegram_username}"
    return f"Участник #{user.serial_id}"


def list_all_ratings(db: Session) -> list[dict[str, object]]:
    """Все оценки сайта (сырьё для админки), от новых к старым."""
    rows = (
        db.query(LocationRating, User, Location)
        .join(User, LocationRating.user_id == User.id)
        .join(Location, LocationRating.location_id == Location.id)
        .order_by(LocationRating.event_date.desc(), LocationRating.created_at.desc())
        .all()
    )
    today = date.today()
    catalog_index = LocationCatalogIndex(db)
    result: list[dict[str, object]] = []
    for rating, user, location in rows:
        result.append(
            {
                "id": rating.id,
                "user_id": rating.user_id,
                "user_display": _user_display(user),
                "user_serial": user.serial_id,
                "event_date": rating.event_date,
                "platform_code": rating.platform_code,
                "location_key": rating.location_key,
                "location_name": catalog_index.display_name(location, rating.platform_code),
                "score_overall": rating.score_overall,
                "score_organization": rating.score_organization,
                "score_route": rating.score_route,
                "score_community": rating.score_community,
                "comment": rating.comment,
                "is_public": rating.is_public,
                "editable": _is_editable(rating.event_date, today),
                "created_at": rating.created_at,
            }
        )
    return result


def _user_home_keys(db: Session, user_ids: set[UUID]) -> dict[UUID, str | None]:
    """Домашняя локация (catalog identity) для каждого пользователя.

    Заданная вручную (`home_location_key`) либо авто по наибольшему числу пробежек —
    для исключения «местных» из рейтинга локации.
    """
    from app.services.home_location_service import resolve_home_location

    home: dict[UUID, str | None] = {}
    for user in db.query(User).filter(User.id.in_(user_ids)).all():
        candidate, _is_auto = resolve_home_location(db, user)
        home[user.id] = candidate.catalog_identity_key if candidate else None
    return home


def _avg(values: list[int]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def location_rating_aggregates(
    db: Session, *, exclude_locals: bool = False
) -> dict[str, object]:
    """Агрегированный рейтинг по локациям (для админ-тестирования)."""
    rows = (
        db.query(LocationRating, Location)
        .join(Location, LocationRating.location_id == Location.id)
        .all()
    )
    catalog_index = LocationCatalogIndex(db)

    home_keys: dict[UUID, str | None] = {}
    if exclude_locals:
        user_ids = {rating.user_id for rating, _loc in rows}
        home_keys = _user_home_keys(db, user_ids)

    buckets: dict[str, dict[str, object]] = {}
    for rating, location in rows:
        if exclude_locals and home_keys.get(rating.user_id) == rating.location_key:
            continue
        bucket = buckets.setdefault(
            rating.location_key,
            {
                "location_key": rating.location_key,
                "location_name": catalog_index.display_name(location, rating.platform_code),
                "voters": set(),
                "overall": [],
                "organization": [],
                "route": [],
                "community": [],
            },
        )
        cast("set[UUID]", bucket["voters"]).add(rating.user_id)
        cast("list[int]", bucket["overall"]).append(rating.score_overall)
        if rating.score_organization is not None:
            cast("list[int]", bucket["organization"]).append(rating.score_organization)
        if rating.score_route is not None:
            cast("list[int]", bucket["route"]).append(rating.score_route)
        if rating.score_community is not None:
            cast("list[int]", bucket["community"]).append(rating.score_community)

    locations: list[dict[str, object]] = []
    for bucket in buckets.values():
        overall = cast("list[int]", bucket["overall"])
        locations.append(
            {
                "location_key": bucket["location_key"],
                "location_name": bucket["location_name"],
                "voters": len(cast("set[UUID]", bucket["voters"])),
                "ratings": len(overall),
                "avg_overall": _avg(overall),
                "avg_organization": _avg(cast("list[int]", bucket["organization"])),
                "avg_route": _avg(cast("list[int]", bucket["route"])),
                "avg_community": _avg(cast("list[int]", bucket["community"])),
                "meets_threshold": len(cast("set[UUID]", bucket["voters"])) >= LOCATION_RATING_MIN_VOTERS,
            }
        )
    # Сначала самые оценённые, затем по среднему.
    locations.sort(key=lambda x: (cast(int, x["ratings"]), cast(float, x["avg_overall"] or 0)), reverse=True)
    return {
        "excluded_locals": exclude_locals,
        "min_voters": LOCATION_RATING_MIN_VOTERS,
        "locations": locations,
    }
