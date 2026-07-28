from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import cast
from uuid import UUID

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.activity_url import resolve_activity_url
from app.models import (
    Event,
    EventCrosslink,
    Location,
    LocationRating,
    LocationRatingPhoto,
    Participant,
    Platform,
    PlatformLink,
    RunResult,
    User,
    VolunteerResult,
)
from app.services.location_catalog_service import LocationCatalogIndex
from app.services.photo_service import PhotoPayload, delete_rating_photo, list_rating_photos
from app.time_format import normalize_finish_time_display

# Право оценивать: минимум столько пробежек в истории (по всем системам).
MIN_RUNS_TO_RATE = 5
# Поставить НОВУЮ оценку можно любому старту за последние N дней (скользящее окно).
RATING_WINDOW_DAYS = 30
# Исправить/удалить оценку можно, пока старту (или самой оценке) не больше N
# дней; дальше — фиксируем. Оценка из добора считается от даты оценки: старту
# может быть и два года, но исправить его владельцу оценки мы даём.
RATING_EDIT_WINDOW_DAYS = 90
# Добор истории: за пределами окна создания даём оценить по ОДНОМУ старту на
# локацию — самый свежий из «старых», и только там, где пользователь ещё ни разу
# не оценивал. Привязка к локации, а не к дате: иначе слот возрождался бы каждый
# раз, когда очередная пробежка выпадает из окна, и «один раз» перестало бы быть
# правдой.
# parkrun в добор не входит — его историю не оцениваем.
LEGACY_EXCLUDED_PLATFORMS = frozenset({"parkrun"})
# Рейтинг локации показываем только с этого числа РАЗНЫХ оценивших (v2).
LOCATION_RATING_MIN_VOTERS = 10

# Приоритет платформы при дедупликации кросслинков (одна физическая пробежка).
_PLATFORM_ORDER = {"five_verst": 0, "s95": 1, "parkrun": 2, "runpark": 3}

# Тип участия на старте.
PARTICIPATION_RUN = "run"
PARTICIPATION_VOLUNTEER = "volunteer"


class RatingError(Exception):
    """Ошибка бизнес-правил оценки (нельзя оценить / нет права)."""


def _entry_id(participation_type: str, source_id: UUID) -> str:
    """Опаковый идентификатор старта для оценки: 'run:<uuid>' / 'vol:<uuid>'."""
    prefix = "vol" if participation_type == PARTICIPATION_VOLUNTEER else "run"
    return f"{prefix}:{source_id}"


def _parse_entry_id(entry_id: str) -> tuple[str, UUID]:
    """('run'|'volunteer', uuid) из опакового entry_id. Кидает RatingError."""
    prefix, _, raw = entry_id.partition(":")
    if prefix == "run":
        participation = PARTICIPATION_RUN
    elif prefix == "vol":
        participation = PARTICIPATION_VOLUNTEER
    else:
        raise RatingError("Некорректный идентификатор старта")
    try:
        return participation, UUID(raw)
    except ValueError as exc:
        raise RatingError("Некорректный идентификатор старта") from exc


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


def _is_editable(
    event_date: date,
    today: date | None = None,
    created_at: datetime | None = None,
) -> bool:
    """Оценку можно менять/удалять, пока старту не больше окна редактирования —
    либо пока не истекло то же окно с момента самой оценки. Второе — про добор
    истории: старт двухлетней давности иначе фиксировался бы в ту же секунду,
    что и поставлен, и опечатку было бы не исправить."""
    today = today or date.today()
    cutoff = today - timedelta(days=RATING_EDIT_WINDOW_DAYS)
    if event_date >= cutoff:
        return True
    if created_at is not None:
        return created_at.astimezone(timezone.utc).date() >= cutoff
    return False


def _rating_to_dict(
    rating: LocationRating,
    *,
    today: date | None = None,
    photos: list[PhotoPayload] | None = None,
) -> dict[str, object]:
    source_id = rating.run_result_id or rating.volunteer_result_id
    return {
        "id": rating.id,
        "photos": [
            {"id": photo.id, "url": photo.url, "width": photo.width, "height": photo.height}
            for photo in (photos or [])
        ],
        "entry_id": _entry_id(rating.participation_type, cast(UUID, source_id)),
        "participation_type": rating.participation_type,
        "run_result_id": rating.run_result_id,
        "score_overall": rating.score_overall,
        "score_organization": rating.score_organization,
        "score_route": rating.score_route,
        "score_community": rating.score_community,
        "comment": rating.comment,
        "is_public": rating.is_public,
        # можно ли ещё исправить/удалить (в пределах 3 месяцев) или уже зафиксировано
        "editable": _is_editable(rating.event_date, today, rating.created_at),
        "created_at": rating.created_at,
        "updated_at": rating.updated_at,
    }


def _rated_location_keys(db: Session, user_id: UUID) -> set[str]:
    """Локации, где пользователь уже хоть раз оценивал (канонические ключи)."""
    rows = (
        db.query(LocationRating.location_key)
        .filter(LocationRating.user_id == user_id)
        .distinct()
        .all()
    )
    return {row[0] for row in rows}


def _select_legacy_entries(
    older_entries: list[dict[str, object]],
    rated_location_keys: set[str],
) -> list[dict[str, object]]:
    """Добор истории: по одному старту на локацию — самый свежий из «старых» и
    только там, где пользователь ещё ни разу не оценивал. parkrun пропускаем."""
    best: dict[str, dict[str, object]] = {}
    for entry in older_entries:
        if cast(str, entry["platform_code"]) in LEGACY_EXCLUDED_PLATFORMS:
            continue
        identity = cast(str, entry["_identity"])
        if identity in rated_location_keys:
            continue
        current = best.get(identity)
        if current is None or cast(date, entry["event_date"]) > cast(date, current["event_date"]):
            best[identity] = entry
    return list(best.values())


def list_eligible_runs(db: Session, user_id: UUID) -> dict[str, object]:
    """Старты, доступные к оценке: всё за окно создания (30 дней) + добор истории
    по одному старту на неоценённую локацию (пробежки + волонтёрства, дедуп
    кросслинков)."""
    today = date.today()
    since = today - timedelta(days=RATING_WINDOW_DAYS)

    catalog_index = LocationCatalogIndex(db)
    # Дедуп по (дата, каноническая площадка) — оставляем приоритетную платформу.
    # Пробежка приоритетнее волонтёрства: если в этот день на площадке была
    # пробежка, оцениваем как бегун.
    by_key: dict[tuple[date, str], dict[str, object]] = {}

    run_rows = (
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
            Event.event_date <= today,
        )
        .all()
    )
    for run, event, location, platform_code, participant in run_rows:
        identity = catalog_index.canonical_identity_key(location, platform_code)
        key = (event.event_date, identity)
        entry = {
            "entry_id": _entry_id(PARTICIPATION_RUN, run.id),
            "participation_type": PARTICIPATION_RUN,
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
            "_identity": identity,
        }
        existing = by_key.get(key)
        if existing is None or cast(int, entry["_platform_order"]) < cast(
            int, existing["_platform_order"]
        ):
            by_key[key] = entry

    vol_rows = (
        db.query(VolunteerResult, Event, Location, Platform.code, Participant)
        .join(Event, VolunteerResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Event.platform_id == Platform.id)
        .join(Participant, VolunteerResult.participant_id == Participant.id)
        .join(PlatformLink, PlatformLink.participant_id == VolunteerResult.participant_id)
        .filter(
            PlatformLink.user_id == user_id,
            PlatformLink.platform_id == Platform.id,
            Event.is_test_event.is_(False),
            Event.event_date <= today,
        )
        .all()
    )
    # На одном событии участник мог волонтёрить в нескольких ролях (несколько
    # VolunteerResult). Группируем по (дата, площадка): canonical vol_id —
    # наименьший (стабильно между рендерами), роли собираем все в подпись.
    vol_groups: dict[
        tuple[date, str],
        list[tuple[VolunteerResult, Event, Location, str, Participant]],
    ] = {}
    for vol, event, location, platform_code, participant in vol_rows:
        identity = catalog_index.canonical_identity_key(location, platform_code)
        key = (event.event_date, identity)
        if key in by_key:
            # В этот день на этой площадке уже есть пробежка — оцениваем как бегун.
            continue
        vol_groups.setdefault(key, []).append(
            (vol, event, location, platform_code, participant)
        )
    for key, group in vol_groups.items():
        group.sort(key=lambda g: str(g[0].id))
        vol, event, location, platform_code, participant = group[0]
        roles: list[str] = []
        for g in group:
            role = (g[0].role or "").strip()
            if role and role not in roles:
                roles.append(role)
        by_key[key] = {
            "entry_id": _entry_id(PARTICIPATION_VOLUNTEER, vol.id),
            "participation_type": PARTICIPATION_VOLUNTEER,
            "run_result_id": None,
            "event_date": event.event_date,
            "platform_code": platform_code,
            "location_name": catalog_index.display_name(location, platform_code),
            "location_city": location.city,
            "finish_time_display": None,
            "position": None,
            "is_pr": False,
            "volunteer_role": ", ".join(roles) if roles else None,
            "event_url": resolve_activity_url(
                platform_code=platform_code,
                event_date=event.event_date,
                event_number=event.event_number,
                event_source_url=event.source_url,
                location_external_key=location.external_key,
                profile_url=participant.profile_url,
            ),
            "_platform_order": _PLATFORM_ORDER.get(platform_code, 9),
            "_identity": key[1],
        }

    # Свежие старты (окно создания) доступны все; из остальных добираем по одному
    # на локацию, где пользователь ещё не оценивал.
    recent = [e for e in by_key.values() if cast(date, e["event_date"]) >= since]
    older = [e for e in by_key.values() if cast(date, e["event_date"]) < since]
    legacy = _select_legacy_entries(older, _rated_location_keys(db, user_id))
    for entry in recent:
        entry["is_legacy"] = False
    for entry in legacy:
        entry["is_legacy"] = True

    entries = sorted(
        recent + legacy, key=lambda e: cast(date, e["event_date"]), reverse=True
    )

    run_ids = [
        cast(UUID, e["run_result_id"])
        for e in entries
        if e["participation_type"] == PARTICIPATION_RUN
    ]
    vol_ids = [
        _parse_entry_id(cast(str, e["entry_id"]))[1]
        for e in entries
        if e["participation_type"] == PARTICIPATION_VOLUNTEER
    ]
    ratings_by_entry: dict[str, LocationRating] = {}
    if run_ids or vol_ids:
        query = db.query(LocationRating).filter(LocationRating.user_id == user_id)
        conditions = []
        if run_ids:
            conditions.append(LocationRating.run_result_id.in_(run_ids))
        if vol_ids:
            conditions.append(LocationRating.volunteer_result_id.in_(vol_ids))
        for rating in query.filter(or_(*conditions)).all():
            source_id = rating.run_result_id or rating.volunteer_result_id
            ratings_by_entry[_entry_id(rating.participation_type, cast(UUID, source_id))] = rating

    photos_by_rating = list_rating_photos(db, [r.id for r in ratings_by_entry.values()])

    for entry in entries:
        entry.pop("_platform_order", None)
        # Канонический ключ площадки отдаём наружу: по нему страница локации
        # понимает, что этот старт — про неё (slug у разных платформ свой).
        entry["location_identity_key"] = entry.pop("_identity", None)
        existing_rating = ratings_by_entry.get(cast(str, entry["entry_id"]))
        entry["my_rating"] = (
            _rating_to_dict(existing_rating, photos=photos_by_rating.get(existing_rating.id))
            if existing_rating
            else None
        )

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


def _load_user_volunteer(
    db: Session, user_id: UUID, volunteer_result_id: UUID
) -> tuple[VolunteerResult, Event, Location, str]:
    row = (
        db.query(VolunteerResult, Event, Location, Platform.code)
        .join(Event, VolunteerResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Event.platform_id == Platform.id)
        .join(PlatformLink, PlatformLink.participant_id == VolunteerResult.participant_id)
        .filter(
            VolunteerResult.id == volunteer_result_id,
            PlatformLink.user_id == user_id,
            PlatformLink.platform_id == Platform.id,
        )
        .first()
    )
    if row is None:
        raise RatingError("Волонтёрство не найдено или не ваше")
    return row  # type: ignore[return-value]


def upsert_rating(
    db: Session,
    user: User,
    entry_id: str,
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

    participation, source_id = _parse_entry_id(entry_id)
    if participation == PARTICIPATION_VOLUNTEER:
        _vol, event, location, platform_code = _load_user_volunteer(db, user.id, source_id)
    else:
        _run, event, location, platform_code = _load_user_run(db, user.id, source_id)

    today = date.today()
    if event.event_date > today:
        raise RatingError("Нельзя оценить старт из будущего")

    if participation == PARTICIPATION_VOLUNTEER:
        rating = (
            db.query(LocationRating)
            .filter(
                LocationRating.user_id == user.id,
                LocationRating.volunteer_result_id == source_id,
            )
            .one_or_none()
        )
    else:
        rating = (
            db.query(LocationRating)
            .filter(
                LocationRating.user_id == user.id,
                LocationRating.run_result_id == source_id,
            )
            .one_or_none()
        )

    if rating is None:
        # Новая оценка — только на старт, который сейчас доступен к оценке.
        # Спрашиваем ровно тот же список, что видит пользователь, чтобы правило
        # (окно 30 дней + добор по одному на локацию) жило в одном месте.
        eligible_ids = {
            cast(str, entry["entry_id"])
            for entry in cast(list[dict[str, object]], list_eligible_runs(db, user.id)["runs"])
        }
        if entry_id not in eligible_ids:
            if event.event_date >= today - timedelta(days=RATING_WINDOW_DAYS):
                raise RatingError("Этот старт нельзя оценить")
            raise RatingError(
                f"Старты старше {RATING_WINDOW_DAYS} дней можно оценить только по "
                "одному на локацию — самый свежий там, где вы ещё не оценивали. "
                "parkrun в добор не входит."
            )
    else:
        # Правка существующей — пока старт (или сама оценка) в окне редактирования.
        if not _is_editable(event.event_date, today, rating.created_at):
            raise RatingError(
                "Оценка зафиксирована: прошло больше 3 месяцев, изменить нельзя"
            )

    catalog_index = LocationCatalogIndex(db)
    location_key = catalog_index.canonical_identity_key(location, platform_code)

    if rating is None:
        rating = LocationRating(
            user_id=user.id,
            run_result_id=source_id if participation == PARTICIPATION_RUN else None,
            volunteer_result_id=source_id if participation == PARTICIPATION_VOLUNTEER else None,
            participation_type=participation,
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
    return _rating_to_dict(rating, photos=list_rating_photos(db, [rating.id]).get(rating.id))


def load_editable_rating(db: Session, user_id: UUID, entry_id: str) -> LocationRating:
    """Своя оценка, которую ещё можно менять — общая проверка для фото-роутов."""
    participation, source_id = _parse_entry_id(entry_id)
    filter_col = (
        LocationRating.volunteer_result_id
        if participation == PARTICIPATION_VOLUNTEER
        else LocationRating.run_result_id
    )
    rating = (
        db.query(LocationRating)
        .filter(LocationRating.user_id == user_id, filter_col == source_id)
        .one_or_none()
    )
    if rating is None:
        raise RatingError("Сначала сохраните оценку, потом добавляйте фото")
    if not _is_editable(rating.event_date, None, rating.created_at):
        raise RatingError("Оценка зафиксирована: прошло больше 3 месяцев, изменить нельзя")
    return rating


def delete_rating(db: Session, user_id: UUID, entry_id: str) -> bool:
    participation, source_id = _parse_entry_id(entry_id)
    filter_col = (
        LocationRating.volunteer_result_id
        if participation == PARTICIPATION_VOLUNTEER
        else LocationRating.run_result_id
    )
    rating = (
        db.query(LocationRating)
        .filter(LocationRating.user_id == user_id, filter_col == source_id)
        .one_or_none()
    )
    if rating is None:
        return False
    if not _is_editable(rating.event_date, None, rating.created_at):
        raise RatingError(
            "Оценка зафиксирована: прошло больше 3 месяцев, удалить нельзя"
        )
    # Строки фото унесёт FK ON DELETE CASCADE, а файлы в хранилище — нет:
    # снимаем их явно, иначе бакет копит сирот после каждого удалённого отзыва.
    for photo in db.query(LocationRatingPhoto).filter(LocationRatingPhoto.rating_id == rating.id).all():
        delete_rating_photo(db, photo)
    db.delete(rating)
    db.flush()
    return True


def _my_rating_entry(
    rating: LocationRating,
    event: Event,
    location: Location,
    platform_code: str,
    participant: Participant,
    catalog_index: LocationCatalogIndex,
    *,
    finish_time_display: str | None,
    position: int | None,
    is_pr: bool,
    today: date,
    photos: list[PhotoPayload] | None = None,
) -> dict[str, object]:
    entry = _rating_to_dict(rating, today=today, photos=photos)
    entry.update(
        {
            "event_date": event.event_date,
            "platform_code": platform_code,
            "location_name": catalog_index.display_name(location, platform_code),
            "location_city": location.city,
            "finish_time_display": finish_time_display,
            "position": position,
            "is_pr": is_pr,
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
    return entry


def list_my_ratings(db: Session, user_id: UUID) -> dict[str, object]:
    """Все оценки пользователя (любой давности) + инфо о старте и флаг editable."""
    today = date.today()
    catalog_index = LocationCatalogIndex(db)
    ratings: list[dict[str, object]] = []

    run_rows = (
        db.query(LocationRating, RunResult, Event, Location, Platform.code, Participant)
        .join(RunResult, LocationRating.run_result_id == RunResult.id)
        .join(Event, RunResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Event.platform_id == Platform.id)
        .join(Participant, RunResult.participant_id == Participant.id)
        .filter(LocationRating.user_id == user_id)
        .all()
    )
    run_photos = list_rating_photos(db, [row[0].id for row in run_rows])
    for rating, run, event, location, platform_code, participant in run_rows:
        ratings.append(
            _my_rating_entry(
                rating,
                event,
                location,
                platform_code,
                participant,
                catalog_index,
                finish_time_display=normalize_finish_time_display(
                    run.finish_time_sec, run.finish_time_display
                ),
                position=run.position,
                is_pr=bool(run.is_pr),
                today=today,
                photos=run_photos.get(rating.id),
            )
        )

    vol_rows = (
        db.query(LocationRating, Event, Location, Platform.code, Participant)
        .join(VolunteerResult, LocationRating.volunteer_result_id == VolunteerResult.id)
        .join(Event, VolunteerResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Event.platform_id == Platform.id)
        .join(Participant, VolunteerResult.participant_id == Participant.id)
        .filter(LocationRating.user_id == user_id)
        .all()
    )
    vol_photos = list_rating_photos(db, [row[0].id for row in vol_rows])
    for rating, event, location, platform_code, participant in vol_rows:
        ratings.append(
            _my_rating_entry(
                rating,
                event,
                location,
                platform_code,
                participant,
                catalog_index,
                finish_time_display=None,
                position=None,
                is_pr=False,
                today=today,
                photos=vol_photos.get(rating.id),
            )
        )

    ratings.sort(key=lambda e: cast(date, e["event_date"]), reverse=True)

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
                "participation_type": rating.participation_type,
                "event_date": rating.event_date,
                "platform_code": rating.platform_code,
                "location_key": rating.location_key,
                "location_name": catalog_index.display_name(location, rating.platform_code),
                "location_city": location.city,
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


def ratings_stats(db: Session) -> dict[str, dict[str, int]]:
    """Счётчики оценок для админки: за 1/7/30 дней и всего.

    Две группы: по дате оценки (`created_at`) и по дате пробежки (`event_date`).
    """
    now = datetime.now(timezone.utc)
    today = date.today()

    def _count_by_created(days: int | None) -> int:
        query = db.query(func.count(LocationRating.id))
        if days is not None:
            query = query.filter(LocationRating.created_at >= now - timedelta(days=days))
        return int(query.scalar() or 0)

    def _count_by_event(days: int | None) -> int:
        query = db.query(func.count(LocationRating.id))
        if days is not None:
            query = query.filter(LocationRating.event_date >= today - timedelta(days=days))
        return int(query.scalar() or 0)

    return {
        "by_rating_date": {
            "last_1d": _count_by_created(1),
            "last_7d": _count_by_created(7),
            "last_30d": _count_by_created(30),
            "total": _count_by_created(None),
        },
        "by_event_date": {
            "last_1d": _count_by_event(1),
            "last_7d": _count_by_event(7),
            "last_30d": _count_by_event(30),
            "total": _count_by_event(None),
        },
    }


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
