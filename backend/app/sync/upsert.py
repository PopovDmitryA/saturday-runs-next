from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import defaultdict
from datetime import date, datetime, timezone
from uuid import UUID

from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.activity_url import prefer_event_source_url
from app.geo.country_names import normalize_country_name
from app.models import (
    Event,
    EventSummary,
    Location,
    LocationDescription,
    Participant,
    Platform,
    RunResult,
    SyncStatus,
    VolunteerResult,
)
from app.pace import resolve_run_pace
from app.platform_adapters.canonical import (
    CanonicalEventSummary,
    CanonicalLocation,
    CanonicalLocationDescription,
    CanonicalRunResult,
    CanonicalVolunteerResult,
)
from app.services.gender_position_service import resolve_participant_gender
from app.services.location_catalog_service import backfill_city_from_catalog, backfill_region_from_catalog
from app.services.location_freshness import mark_location_results_changed

PARSER_VERSION = "0.3.2"
logger = logging.getLogger(__name__)

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _resolve_geo(
    location: CanonicalLocation, row: Location | None
) -> tuple[str | None, str | None, str | None]:
    """Страна, регион и город: что дала система — то и берём, пробелы добираем
    обратным геокодом по координатам.

    Раньше геокод доставал только регион, а страну каждый вызывающий должен был
    заполнить сам. s95 её не отдаёт вовсе, и локация, найденная не через реестр,
    а через протокол (см. s95_global_sync_api._ensure_location), оставалась без
    страны — так появилось Кратово. Заполняем здесь, чтобы это работало для всех
    систем и всех путей создания, а не только там, где вызывающий не забыл.

    Один запрос вместо прежнего запроса-на-регион: у Nominatim в ответе и так
    все три поля. Если всё уже известно — в сеть не ходим совсем.

    Страну прогоняем через normalize_country_name: в БД она хранится по-русски,
    одно название на одну страну (см. app/geo/country_names.py).
    """
    country = normalize_country_name(location.country) or (row.country if row else None)
    region = location.region or (row.region if row else None)
    city = location.city or (row.city if row else None)
    if country and region and city:
        return country, region, city
    if location.latitude is None or location.longitude is None:
        return country, region, city
    try:
        from app.geo.reverse_geocode import lookup_address

        address = lookup_address(location.latitude, location.longitude)
    except Exception:
        logger.warning("geocode failed for %s", location.external_key, exc_info=True)
        return country, region, city
    return (
        country or normalize_country_name(address.get("country")),
        region or address.get("region"),
        city or address.get("city"),
    )


def get_platform(db: Session, platform_code: str) -> Platform:
    platform = db.query(Platform).filter(Platform.code == platform_code).one_or_none()
    if platform is None:
        raise ValueError(f"Platform not found: {platform_code}")
    return platform


def upsert_location(
    db: Session,
    platform: Platform,
    location: CanonicalLocation,
    *,
    source_hash: str | None = None,
) -> tuple[Location, bool]:
    row = (
        db.query(Location)
        .filter(
            Location.platform_id == platform.id,
            Location.external_key == location.external_key,
        )
        .one_or_none()
    )
    now = datetime.now(timezone.utc)
    changed = False
    if location.latitude is not None and location.longitude is not None and location.region is None:
        logger.info("geocode region for %s", location.external_key)
    country, region, city = _resolve_geo(location, row)

    if row is None:
        row = Location(
            platform_id=platform.id,
            external_key=location.external_key,
            name=location.name,
            country=country,
            city=city,
            region=region,
            latitude=location.latitude,
            longitude=location.longitude,
            map_url=location.map_url,
            source_url=location.source_url,
            parser_version=PARSER_VERSION,
            source_hash=source_hash,
            fetched_at=now,
            sync_status=SyncStatus.ok,
        )
        db.add(row)
        db.flush()
        return row, True

    if row.source_hash == source_hash and source_hash is not None:
        # Ничего не изменилось, но геопробелы дозаполняем: строка могла родиться
        # до того, как геокод стал общим для всех путей создания.
        if (row.country, row.region, row.city) != (country, region, city):
            row.country, row.region, row.city = country, region, city
            db.flush()
        return row, False

    # Собираем целевое состояние строки. Правило общее: пустое значение из
    # источника НЕ затирает уже известное — разные пути знают о локации разное.
    # Импорт профиля, например, видит только слаг и название, без координат,
    # хеша страницы и карты; синк локаций — наоборот, знает всё.
    new_name = location.name or row.name
    # `or row.country` — s95 страну не отдаёт, и без этого каждый синк стирал бы
    # уже известную. Город и регион так и жили, страна была исключением.
    new_country = country or row.country
    new_city = city or row.city
    new_region = region or row.region
    new_latitude = location.latitude or row.latitude
    new_longitude = location.longitude or row.longitude
    new_map_url = location.map_url or row.map_url
    new_source_url = location.source_url or row.source_url
    # Хеш ставит только тот, кто реально его посчитал (синк по HTML страницы).
    # Раньше вызовы без хеша (импорт профиля) затирали его в NULL, и оптимизация
    # «не обновлять, если не изменилось» переставала работать для ВСЕХ путей:
    # на 05.08.2026 хеша не было у 2837 локаций из 2875.
    new_source_hash = source_hash if source_hash is not None else row.source_hash

    unchanged = (
        row.name == new_name
        and row.country == new_country
        and row.city == new_city
        and row.region == new_region
        and row.latitude == new_latitude
        and row.longitude == new_longitude
        and row.map_url == new_map_url
        and row.source_url == new_source_url
        and row.source_hash == new_source_hash
        and row.parser_version == PARSER_VERSION
        and row.sync_status == SyncStatus.ok
        and row.error_message is None
    )
    if unchanged:
        # Ни одного отличия — не трогаем строку вообще. Раньше здесь всё равно
        # шёл UPDATE ради fetched_at, и на прод-базе такие «пустые» апдейты
        # вставали в очередь за блокировками боевых воркеров (23 с на запрос).
        # fetched_at означает «когда локацию реально пересинкали», поэтому
        # упоминание локации в чужом импорте его двигать не должно.
        return row, False

    row.name = new_name
    row.country = new_country
    row.city = new_city
    row.region = new_region
    row.latitude = new_latitude
    row.longitude = new_longitude
    row.map_url = new_map_url
    row.source_url = new_source_url
    row.parser_version = PARSER_VERSION
    row.source_hash = new_source_hash
    row.fetched_at = now
    row.sync_status = SyncStatus.ok
    row.error_message = None
    changed = True
    logger.info("DB flush location %s", location.external_key)
    db.flush()
    return row, changed


def _description_payload(description: CanonicalLocationDescription) -> dict[str, object]:
    return {
        "schedule_text": description.schedule_text,
        "course_text": description.course_text,
        "travel_text": description.travel_text,
        "travel_sections": [
            {"title": section.title, "text": section.text} for section in description.travel_sections
        ],
        "links": [{"title": link.title, "url": link.url} for link in description.links],
    }


def _description_hash(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def upsert_location_description(
    db: Session,
    location: Location,
    description: CanonicalLocationDescription,
) -> tuple[LocationDescription, bool]:
    """Сохраняет описание площадки. Пустое описание не затирает уже собранное.

    Пустым оно приходит штатно: страница «О трассе» у площадок «на паузе»
    подменяется приглашением стать организатором, и разбор оттуда ничего не
    достаёт. Терять из-за этого нормальный текст, собранный месяц назад, — хуже,
    чем показать слегка устаревший.

    Но отметку fetched_at ставим даже на пустое: обход S95 берёт локации по
    давности загрузки, и локация, с которой текст не снимается никогда, иначе
    навсегда осталась бы первой в очереди и заслоняла все остальные.
    """

    payload = _description_payload(description)
    content_hash = _description_hash(payload)
    now = datetime.now(timezone.utc)
    empty = description.is_empty()

    row = db.query(LocationDescription).filter(LocationDescription.location_id == location.id).one_or_none()
    if row is not None and empty:
        row.fetched_at = now
        db.flush()
        return row, False

    if row is None and empty:
        row = LocationDescription(
            location_id=location.id,
            source_url=description.source_url or None,
            fetched_at=now,
        )
        db.add(row)
        db.flush()
        return row, False

    from app.services.location_schedule_service import parse_schedule_text

    if row is None:
        row = LocationDescription(
            location_id=location.id,
            schedule_text=description.schedule_text,
            schedule_parsed=parse_schedule_text(description.schedule_text),
            course_text=description.course_text,
            travel_text=description.travel_text,
            travel_sections=payload["travel_sections"],
            links=payload["links"],
            source_url=description.source_url or None,
            content_hash=content_hash,
            fetched_at=now,
            content_updated_at=now,
            # Первый сбор — не «изменение»: revision считает, сколько раз текст
            # поменялся ПОСЛЕ того, как мы его впервые увидели.
            revision=0,
        )
        db.add(row)
        db.flush()
        return row, True

    row.fetched_at = now
    if row.content_hash == content_hash:
        db.flush()
        return row, False

    row.schedule_text = description.schedule_text
    row.schedule_parsed = parse_schedule_text(description.schedule_text)
    row.course_text = description.course_text
    row.travel_text = description.travel_text
    row.travel_sections = payload["travel_sections"]
    row.links = payload["links"]
    row.source_url = description.source_url or row.source_url
    row.content_hash = content_hash
    row.content_updated_at = now
    row.revision = (row.revision or 0) + 1
    logger.info("DB flush location description %s", location.external_key)
    db.flush()
    return row, True


def upsert_event_summary(
    db: Session,
    platform: Platform,
    location: Location,
    summary: CanonicalEventSummary,
) -> tuple[EventSummary, bool]:
    row = (
        db.query(EventSummary)
        .filter(
            EventSummary.platform_id == platform.id,
            EventSummary.external_event_key == summary.external_event_key,
        )
        .one_or_none()
    )
    now = datetime.now(timezone.utc)
    unchanged = row is not None and row.summary_hash == summary.summary_hash

    if row is None:
        row = EventSummary(
            platform_id=platform.id,
            location_id=location.id,
            external_event_key=summary.external_event_key,
            event_date=summary.event_date,
            event_number=summary.event_number,
            is_test_event=summary.is_test_event,
            finishers_count=summary.finishers_count,
            volunteers_count=summary.volunteers_count,
            avg_time_sec=summary.avg_time_sec,
            avg_time_display=summary.avg_time_display,
            best_female_time_sec=summary.best_female_time_sec,
            best_female_time_display=summary.best_female_time_display,
            best_male_time_sec=summary.best_male_time_sec,
            best_male_time_display=summary.best_male_time_display,
            summary_hash=summary.summary_hash,
            summary_extra=summary.summary_extra or {},
            source_url=summary.source_url,
            parser_version=PARSER_VERSION,
            source_hash=summary.summary_hash,
            fetched_at=now,
            sync_status=SyncStatus.ok,
        )
        db.add(row)
        _mark_summary_changed(db, platform, location, summary)
        db.flush()
        return row, True

    if unchanged:
        row.sync_status = SyncStatus.unchanged
        db.flush()
        return row, False

    row.location_id = location.id
    row.event_date = summary.event_date
    # None не затирает уже известный номер: часть источников (JSON API s95) его не отдаёт.
    if summary.event_number is not None:
        row.event_number = summary.event_number
    row.is_test_event = summary.is_test_event
    row.finishers_count = summary.finishers_count
    row.volunteers_count = summary.volunteers_count
    row.avg_time_sec = summary.avg_time_sec
    row.avg_time_display = summary.avg_time_display
    row.best_female_time_sec = summary.best_female_time_sec
    row.best_female_time_display = summary.best_female_time_display
    row.best_male_time_sec = summary.best_male_time_sec
    row.best_male_time_display = summary.best_male_time_display
    row.summary_hash = summary.summary_hash
    row.summary_extra = summary.summary_extra or {}
    row.source_url = summary.source_url
    row.parser_version = PARSER_VERSION
    row.source_hash = summary.summary_hash
    row.fetched_at = now
    row.sync_status = SyncStatus.ok
    row.error_message = None
    _mark_summary_changed(db, platform, location, summary)
    db.flush()
    return row, True


def _mark_summary_changed(
    db: Session,
    platform: Platform,
    location: Location,
    summary: CanonicalEventSummary,
) -> None:
    """Сводка старта — уже повод обновить витрины: «Последний старт» на странице
    площадки и «Результаты последней субботы» считаются по ней, полного
    протокола можно ждать ещё часы."""
    mark_location_results_changed(
        db,
        [location.id],
        reason=f"сводка {platform.code}",
        protocols=[(platform.code, summary.event_date)],
    )


def upsert_event_for_summary(
    db: Session,
    platform: Platform,
    location: Location,
    summary: CanonicalEventSummary,
    summary_row: EventSummary,
) -> Event:
    row = _find_existing_event(
        db,
        platform,
        location,
        event_date=summary.event_date,
        external_event_key=summary.external_event_key,
        location_slug=summary.location_external_key,
        location_name=summary.location_name,
    )
    now = datetime.now(timezone.utc)
    if summary.event_number is not None:
        title = f"{summary.location_name} #{summary.event_number}"
    else:
        title = summary.location_name
    if summary.is_test_event:
        title = f"{summary.location_name} (тестовый)"

    if row is None:
        row = Event(
            platform_id=platform.id,
            location_id=location.id,
            external_event_key=summary.external_event_key,
            event_date=summary.event_date,
            event_number=summary.event_number,
            is_test_event=summary.is_test_event,
            title=title,
            finishers_count=summary.finishers_count,
            runners_count=summary.finishers_count,
            source_url=summary.source_url,
            parser_version=PARSER_VERSION,
            source_hash=summary.summary_hash,
            fetched_at=now,
            sync_status=SyncStatus.ok,
        )
        db.add(row)
        try:
            with db.begin_nested():
                db.flush()
        except IntegrityError:
            db.expunge(row)
            row = _find_existing_event(
                db,
                platform,
                location,
                event_date=summary.event_date,
                external_event_key=summary.external_event_key,
                location_slug=summary.location_external_key,
                location_name=summary.location_name,
            )
            if row is None:
                raise

    _assign_external_event_key(db, platform, row, summary.external_event_key)
    row.location_id = location.id
    row.event_date = summary.event_date
    # None не затирает уже известный номер: часть источников (JSON API s95) его не отдаёт.
    if summary.event_number is not None:
        row.event_number = summary.event_number
    if row.event_number is not None and not summary.is_test_event:
        title = f"{summary.location_name} #{row.event_number}"
    row.is_test_event = summary.is_test_event
    row.title = title
    row.finishers_count = summary.finishers_count
    row.runners_count = summary.finishers_count
    row.source_url = summary.source_url
    row.parser_version = PARSER_VERSION
    row.source_hash = summary.summary_hash
    row.fetched_at = now
    row.sync_status = SyncStatus.ok
    row.error_message = None

    try:
        db.flush()
    except IntegrityError:
        db.expire(row)
        row = _find_existing_event(
            db,
            platform,
            location,
            event_date=summary.event_date,
            external_event_key=summary.external_event_key,
            location_slug=summary.location_external_key,
            location_name=summary.location_name,
        )
        if row is None:
            raise
        _assign_external_event_key(db, platform, row, summary.external_event_key)
        db.flush()
    summary_row.event_id = row.id
    db.flush()
    return row


def upsert_participant(
    db: Session,
    platform: Platform,
    *,
    external_user_id: str,
    display_name: str,
    profile_url: str | None = None,
    club_name: str | None = None,
    age_category: str | None = None,
    barcode_id: str | None = None,
) -> Participant:
    # Кеш «платформа+внешний id → строка» на время сессии: профильный импорт
    # ищет одного и того же участника на каждой пробежке, и по SSH-туннелю к
    # проду это были сотни одинаковых SELECT (42% времени импорта долгожителя).
    # Живёт на самой Session, поэтому не течёт между запросами и прогонами.
    cache: dict[tuple[UUID, str], Participant] = getattr(db, "_participant_cache", None)
    if cache is None:
        cache = {}
        db._participant_cache = cache  # noqa: SLF001
    cache_key = (platform.id, external_user_id)
    row = cache.get(cache_key)
    if row is None:
        row = (
            db.query(Participant)
            .filter(
                Participant.platform_id == platform.id,
                Participant.external_user_id == external_user_id,
            )
            .one_or_none()
        )
    now = datetime.now(timezone.utc)
    if external_user_id.startswith("unknown:"):
        default_profile_url = None
    elif platform.code == "runpark":
        default_profile_url = f"https://runpark.ru/Account/Karmas/{external_user_id}"
    elif platform.code == "five_verst":
        default_profile_url = f"https://5verst.ru/userstats/{external_user_id}/"
    else:
        default_profile_url = None
    if row is None:
        row = Participant(
            platform_id=platform.id,
            external_user_id=external_user_id,
            display_name=display_name,
            profile_url=profile_url or default_profile_url,
            club_name=club_name,
            age_category=age_category,
            gender=resolve_participant_gender(platform.code, age_category),
            barcode_id=barcode_id,
            source_url=profile_url,
            parser_version=PARSER_VERSION,
            fetched_at=now,
            sync_status=SyncStatus.ok,
        )
        db.add(row)
    else:
        # Профильный импорт зовёт эту функцию на КАЖДУЮ пробежку одного и того
        # же человека (upsert_run_results вызывается по одной строке), поэтому
        # безусловная запись fetched_at давала сотни UPDATE одинаковыми
        # данными: у долгожителя с 284 забегами — 298 апдейтов по туннелю.
        # Трогаем строку, только если что-то реально меняется.
        changes = (
            row.display_name != display_name
            or (profile_url and row.profile_url != profile_url)
            or (club_name and row.club_name != club_name)
            or (age_category and row.age_category != age_category)
            or (barcode_id and row.barcode_id != barcode_id)
            or row.sync_status != SyncStatus.ok
        )
        if changes:
            row.display_name = display_name
            if profile_url:
                row.profile_url = profile_url
            if club_name:
                row.club_name = club_name
            if age_category:
                row.age_category = age_category
            if barcode_id:
                row.barcode_id = barcode_id
            row.fetched_at = now
            row.sync_status = SyncStatus.ok
    # Пол пересчитываем всегда: у s95 он приезжает в profile_extra отдельным
    # апсертом профиля, у остальных — вместе с обновлённой категорией.
    # Известный пол не затираем на None (протокол без категории — не повод).
    gender = resolve_participant_gender(platform.code, row.age_category, row.profile_extra)
    if gender is not None and row.gender != gender:
        row.gender = gender
    db.flush()
    cache[cache_key] = row
    return row


def upsert_run_results(
    db: Session,
    event: Event,
    platform: Platform,
    results: list[CanonicalRunResult],
    *,
    from_profile: bool = False,
    recalculate_pr: bool = True,
) -> int:
    upserted = 0
    touched_participant_ids: set = set()
    for item in results:
        participant = upsert_participant(
            db,
            platform,
            external_user_id=item.external_user_id,
            display_name=item.participant_name,
            club_name=None if from_profile else item.club_name,
            age_category=None if from_profile else item.age_category,
            barcode_id=item.barcode_id if not from_profile else None,
        )
        touched_participant_ids.add(participant.id)
        row = (
            db.query(RunResult)
            .filter(
                RunResult.event_id == event.id,
                RunResult.external_result_key == item.external_result_key,
            )
            .one_or_none()
        )
        if row is None:
            row = (
                db.query(RunResult)
                .filter(
                    RunResult.event_id == event.id,
                    RunResult.participant_id == participant.id,
                )
                .one_or_none()
            )
        pace_sec_per_km, pace_display = resolve_run_pace(
            item.pace_sec_per_km,
            item.pace_display,
            item.finish_time_sec,
        )
        now = datetime.now(timezone.utc)
        if row is None:
            row = RunResult(
                event_id=event.id,
                participant_id=participant.id,
                external_result_key=item.external_result_key,
                position=item.position,
                finish_time_sec=item.finish_time_sec,
                finish_time_display=item.finish_time_display,
                pace_sec_per_km=pace_sec_per_km,
                pace_display=pace_display,
                age_category=item.age_category,
                status=item.status,
                is_pr=item.is_pr,
                is_first_run=item.is_first_run,
                is_first_run_at_location=item.is_first_run_at_location,
                club_name=item.club_name,
                achievement_labels=item.achievement_labels,
                fetched_at=now,
            )
            db.add(row)
            try:
                with db.begin_nested():
                    db.flush()
            except IntegrityError:
                db.expunge(row)
                row = (
                    db.query(RunResult)
                    .filter(
                        RunResult.event_id == event.id,
                        RunResult.external_result_key == item.external_result_key,
                    )
                    .one_or_none()
                )
                if row is None:
                    row = (
                        db.query(RunResult)
                        .filter(
                            RunResult.event_id == event.id,
                            RunResult.participant_id == participant.id,
                        )
                        .one_or_none()
                    )
                if row is None:
                    raise
            upserted += 1
        else:
            row.participant_id = participant.id
            row.external_result_key = item.external_result_key
            if from_profile:
                if item.finish_time_sec is not None:
                    row.finish_time_sec = item.finish_time_sec
                if item.finish_time_display is not None:
                    row.finish_time_display = item.finish_time_display
                if pace_sec_per_km is not None:
                    row.pace_sec_per_km = pace_sec_per_km
                if pace_display is not None:
                    row.pace_display = pace_display
            else:
                row.position = item.position
                row.finish_time_sec = item.finish_time_sec
                row.finish_time_display = item.finish_time_display
                row.pace_sec_per_km = pace_sec_per_km
                row.pace_display = pace_display
                row.age_category = item.age_category
                row.status = item.status
                row.is_pr = item.is_pr
                row.is_first_run = item.is_first_run
                row.is_first_run_at_location = item.is_first_run_at_location
                row.club_name = item.club_name
                row.achievement_labels = item.achievement_labels
            row.fetched_at = now
            upserted += 1
    _discover_participants_after_protocol_upsert(db, platform, touched_participant_ids)
    if recalculate_pr and touched_participant_ids:
        from app.services.personal_record_service import (
            recalculate_participants_cross_platform_personal_records,
            recalculate_participants_first_run_flags,
            recalculate_participants_personal_records,
        )

        recalculate_participants_first_run_flags(db, platform.code, touched_participant_ids)
        recalculate_participants_personal_records(db, platform.code, touched_participant_ids)
        recalculate_participants_cross_platform_personal_records(db, touched_participant_ids)
    if upserted:
        # Витрины площадки (страница, журнал, каталог, «последняя суббота»)
        # держат снимок на 3 часа — иначе новая пробежка была бы видна в
        # профиле и не видна на локации. Подрезка TTL — после коммита.
        mark_location_results_changed(
            db,
            [event.location_id],
            reason=f"результаты {platform.code}",
            protocols=[(platform.code, event.event_date)],
        )
    db.flush()
    return upserted


def _discover_participants_after_protocol_upsert(
    db: Session,
    platform: Platform,
    participant_ids: set,
) -> None:
    if not participant_ids:
        return
    from app.sync.parkrun_participant_discovery import (
        enqueue_parkrun_discovery_from_barcode,
        enrich_parkrun_protocol_participant,
    )

    rows = db.query(Participant).filter(Participant.id.in_(participant_ids)).all()
    if platform.code == "parkrun":
        # Свой протокол parkrun обрабатывает Mac-воркер (очередь parkrun) — там
        # браузер уместен, тянем инлайн.
        for row in rows:
            enrich_parkrun_protocol_participant(db, row, platform)
        return
    if platform.code == "s95":
        # parkrun-discovery по barcode финишёров развязан от S95: не тянем parkrun
        # инлайн (это вешало worker-s95), а ставим в очередь для Mac-демона.
        seen_barcodes: set[str] = set()
        for row in rows:
            barcode = (row.barcode_id or "").strip()
            if not barcode or barcode in seen_barcodes:
                continue
            seen_barcodes.add(barcode)
            enqueue_parkrun_discovery_from_barcode(db, barcode)


def _volunteer_role_rank(role: str | None) -> int:
    if role is None:
        return 0
    cleaned = role.strip()
    if not cleaned:
        return 0
    if cleaned.lower() in {"volunteer", "волонтёр", "волонтер"}:
        return 1
    return 2


def _prefer_volunteer_role(existing: str | None, incoming: str | None) -> str | None:
    if _volunteer_role_rank(incoming) > _volunteer_role_rank(existing):
        return incoming.strip() if incoming else existing
    if _volunteer_role_rank(existing) >= _volunteer_role_rank(incoming):
        return existing
    return incoming


def _five_verst_profile_volunteer_tail_parts(key: str, external_user_id: str) -> tuple[str, ...] | None:
    prefix = f"{external_user_id}:"
    if not key.startswith(prefix):
        return None
    tail = key[len(prefix) :]
    if not tail:
        return None
    return tuple(tail.split(":"))


def _is_legacy_five_verst_profile_volunteer_key(key: str, external_user_id: str) -> bool:
    """Detect pre-role profile keys that collapsed multiple roles into one row per day."""
    parts = _five_verst_profile_volunteer_tail_parts(key, external_user_id)
    if parts is None:
        return False
    if len(parts) == 1 and _ISO_DATE_RE.match(parts[0]):
        return True
    if len(parts) == 2 and _ISO_DATE_RE.match(parts[0]):
        return True
    if len(parts) >= 2 and _ISO_DATE_RE.match(parts[-1]):
        return True
    return False


def _is_five_verst_profile_volunteer_external_key(key: str, external_user_id: str) -> bool:
    parts = _five_verst_profile_volunteer_tail_parts(key, external_user_id)
    if parts is None:
        return False
    if len(parts) >= 3 and _ISO_DATE_RE.match(parts[0]):
        return True
    return _is_legacy_five_verst_profile_volunteer_key(key, external_user_id)


def _purge_legacy_five_verst_profile_volunteer_keys(
    db: Session,
    participant_id: UUID,
    external_user_id: str,
) -> int:
    removed = 0
    rows = (
        db.query(VolunteerResult)
        .filter(VolunteerResult.participant_id == participant_id)
        .all()
    )
    for row in rows:
        if _is_legacy_five_verst_profile_volunteer_key(row.external_result_key, external_user_id):
            db.delete(row)
            removed += 1
    if removed:
        db.flush()
    return removed


def _is_profile_volunteer_external_key(platform_code: str, external_user_id: str, key: str) -> bool:
    if platform_code == "five_verst":
        return _is_five_verst_profile_volunteer_external_key(key, external_user_id)
    if platform_code == "s95":
        # Legacy profile keys; protocol-style keys are kept (see protocol parser).
        return key.startswith(f"vol:{external_user_id}:")
    if platform_code == "parkrun":
        return key.startswith(f"parkrun:{external_user_id}:")
    return False


def upsert_volunteer_results(
    db: Session,
    event: Event,
    platform: Platform,
    results: list[CanonicalVolunteerResult],
) -> int:
    upserted = 0
    for item in results:
        participant_id: UUID | None = None
        if item.external_user_id:
            participant = upsert_participant(
                db,
                platform,
                external_user_id=item.external_user_id,
                display_name=item.participant_name or f"User {item.external_user_id}",
            )
            participant_id = participant.id

        row = (
            db.query(VolunteerResult)
            .filter(
                VolunteerResult.event_id == event.id,
                VolunteerResult.external_result_key == item.external_result_key,
            )
            .one_or_none()
        )
        now = datetime.now(timezone.utc)
        role = item.role
        if platform.code == "s95":
            from app.s95.parsers.volunteer_roles import canonical_s95_volunteer_role, prefer_s95_volunteer_role

            role = canonical_s95_volunteer_role(role) or role
        # Имя храним только у волонтёра БЕЗ профиля: у остальных оно живёт в
        # participants и отсюда бы только устаревало.
        display_name = None if participant_id is not None else (item.participant_name or None)
        if row is None:
            row = VolunteerResult(
                event_id=event.id,
                participant_id=participant_id,
                external_result_key=item.external_result_key,
                role=role,
                display_name=display_name,
                fetched_at=now,
            )
            db.add(row)
            upserted += 1
        else:
            row.participant_id = participant_id
            row.display_name = display_name
            if platform.code == "s95":
                row.role = prefer_s95_volunteer_role(row.role, role)
            else:
                row.role = _prefer_volunteer_role(row.role, role)
            row.fetched_at = now
            upserted += 1
    if upserted:
        mark_location_results_changed(
            db,
            [event.location_id],
            reason=f"волонтёры {platform.code}",
            protocols=[(platform.code, event.event_date)],
        )
    db.flush()
    return upserted


def replace_event_volunteer_results(
    db: Session,
    event: Event,
    platform: Platform,
    results: list[CanonicalVolunteerResult],
) -> int:
    """Upsert incoming volunteer results for an event and delete any that are no longer present.

    Use for protocol-based syncs where the fetched page is the complete authoritative list.
    Prevents stale records when roles or participants are removed from a protocol.
    """
    incoming_keys = {item.external_result_key for item in results}
    upserted = upsert_volunteer_results(db, event, platform, results)
    deleted = 0
    for row in db.query(VolunteerResult).filter(VolunteerResult.event_id == event.id).all():
        if row.external_result_key not in incoming_keys:
            db.delete(row)
            deleted += 1
    if deleted:
        mark_location_results_changed(
            db,
            [event.location_id],
            reason=f"волонтёры {platform.code} (удаление)",
            protocols=[(platform.code, event.event_date)],
        )
        db.flush()
    return upserted


def replace_event_run_results(
    db: Session,
    event: Event,
    platform: Platform,
    results: list[CanonicalRunResult],
    *,
    from_profile: bool = False,
    recalculate_pr: bool = False,
) -> int:
    """Upsert incoming run results for an event and delete any that are no longer present.

    Use for protocol-based syncs where the fetched page is the complete authoritative list.
    Prevents stale records when results are corrected or participants are removed.
    """
    incoming_keys = {item.external_result_key for item in results}
    upserted = upsert_run_results(
        db,
        event,
        platform,
        results,
        from_profile=from_profile,
        recalculate_pr=recalculate_pr,
    )
    deleted = 0
    for row in db.query(RunResult).filter(RunResult.event_id == event.id).all():
        if row.external_result_key not in incoming_keys:
            db.delete(row)
            deleted += 1
    if deleted:
        mark_location_results_changed(
            db,
            [event.location_id],
            reason=f"результаты {platform.code} (удаление)",
            protocols=[(platform.code, event.event_date)],
        )
        db.flush()
    return upserted


def _profile_event_title(location_name: str, event_number: int | None) -> str:
    if event_number is not None:
        return f"{location_name} #{event_number}"
    return location_name


def _normalize_location_slug(
    location_external_key: str | None,
    location_name: str | None,
) -> str:
    if location_external_key and location_external_key.strip():
        return location_external_key.strip()
    if location_name and location_name.strip():
        return location_name.strip().lower().replace(" ", "_")
    return "unknown"


def _profile_external_event_key(event_date: date, location_slug: str) -> str:
    return f"{event_date.isoformat()}:{location_slug}"


def _find_event_by_location_date(
    db: Session,
    platform: Platform,
    location_id: UUID,
    event_date: date,
) -> Event | None:
    return (
        db.query(Event)
        .filter(
            Event.platform_id == platform.id,
            Event.location_id == location_id,
            Event.event_date == event_date,
        )
        .order_by(Event.created_at.asc())
        .first()
    )


def _find_existing_event(
    db: Session,
    platform: Platform,
    location: Location,
    *,
    event_date: date,
    external_event_key: str,
    location_slug: str,
    location_name: str,
) -> Event | None:
    row = _find_event_by_location_date(db, platform, location.id, event_date)
    if row is not None:
        return row

    row = (
        db.query(Event)
        .filter(
            Event.platform_id == platform.id,
            Event.external_event_key == external_event_key,
        )
        .one_or_none()
    )
    if row is not None:
        return row

    normalized_name = (location_name or "").strip().lower()
    location_filters = [
        Location.external_key == location_slug,
        func.lower(Location.external_key) == location_slug.lower(),
    ]
    if normalized_name:
        location_filters.append(func.lower(Location.name) == normalized_name)

    alt_location_ids = [
        loc_id
        for (loc_id,) in db.query(Location.id)
        .filter(Location.platform_id == platform.id, or_(*location_filters))
        .all()
    ]
    for loc_id in alt_location_ids:
        row = _find_event_by_location_date(db, platform, loc_id, event_date)
        if row is not None:
            return row
    return None


def _assign_external_event_key(
    db: Session,
    platform: Platform,
    row: Event,
    external_event_key: str,
) -> None:
    if row.external_event_key == external_event_key:
        return
    conflict = (
        db.query(Event.id)
        .filter(
            Event.platform_id == platform.id,
            Event.external_event_key == external_event_key,
            Event.id != row.id,
        )
        .one_or_none()
    )
    if conflict is None:
        row.external_event_key = external_event_key


def _apply_event_fields(
    row: Event,
    *,
    platform_code: str,
    location: Location,
    event_date: date,
    event_number: int | None,
    is_test_event: bool,
    title: str,
    source_url: str | None,
    finishers_count: int | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    row.location_id = location.id
    row.event_date = event_date
    # None — это «источник номера не знает», а не «номера нет»: волонтёрская
    # таблица профиля его больше не сообщает, и затирать ей уже известный
    # номер нечем.
    if event_number is not None:
        row.event_number = event_number
    row.is_test_event = is_test_event
    row.title = title
    preferred_source_url = prefer_event_source_url(platform_code, row.source_url, source_url)
    if preferred_source_url:
        row.source_url = preferred_source_url
    if finishers_count is not None:
        row.finishers_count = finishers_count
        row.runners_count = finishers_count
    row.fetched_at = now
    row.sync_status = SyncStatus.ok
    row.error_message = None
    row.parser_version = PARSER_VERSION


def upsert_event_for_profile(
    db: Session,
    platform: Platform,
    location: Location,
    *,
    external_event_key: str,
    event_date: date,
    event_number: int | None,
    location_name: str,
    location_slug: str,
    source_url: str,
    is_test_event: bool = False,
) -> Event:
    row = _find_existing_event(
        db,
        platform,
        location,
        event_date=event_date,
        external_event_key=external_event_key,
        location_slug=location_slug,
        location_name=location_name,
    )
    now = datetime.now(timezone.utc)
    # Заголовок держим на том номере, который в итоге останется у события:
    # иначе волонтёрский импорт, номера не знающий, превратил бы «Дружба #228»
    # обратно в «Дружба».
    effective_number = event_number if event_number is not None else (
        row.event_number if row is not None else None
    )
    title = _profile_event_title(location_name, effective_number)
    if is_test_event:
        title = f"{location_name} (тестовый)"

    if row is None:
        row = Event(
            platform_id=platform.id,
            location_id=location.id,
            external_event_key=external_event_key,
            event_date=event_date,
            event_number=event_number,
            is_test_event=is_test_event,
            title=title,
            source_url=source_url,
            parser_version=PARSER_VERSION,
            fetched_at=now,
            sync_status=SyncStatus.ok,
        )
        db.add(row)
        try:
            with db.begin_nested():
                db.flush()
        except IntegrityError:
            db.expunge(row)
            row = _find_existing_event(
                db,
                platform,
                location,
                event_date=event_date,
                external_event_key=external_event_key,
                location_slug=location_slug,
                location_name=location_name,
            )
            if row is None:
                raise

    _assign_external_event_key(db, platform, row, external_event_key)
    _apply_event_fields(
        row,
        platform_code=platform.code,
        location=location,
        event_date=event_date,
        event_number=event_number,
        is_test_event=is_test_event,
        title=title,
        source_url=source_url,
    )

    try:
        db.flush()
    except IntegrityError:
        db.expire(row)
        row = _find_existing_event(
            db,
            platform,
            location,
            event_date=event_date,
            external_event_key=external_event_key,
            location_slug=location_slug,
            location_name=location_name,
        )
        if row is None:
            raise
        _assign_external_event_key(db, platform, row, external_event_key)
        _apply_event_fields(
            row,
            platform_code=platform.code,
            location=location,
            event_date=event_date,
            event_number=event_number,
            is_test_event=is_test_event,
            title=title,
            source_url=source_url,
        )
        db.flush()
    return row


def import_profile_run_results(
    db: Session,
    platform: Platform,
    results: list[CanonicalRunResult],
) -> int:
    imported = 0
    touched_participant_ids: set[UUID] = set()
    # external_user_id → Participant.id (или None). В профильном импорте ключ
    # всегда один, но код общий для всех платформ — держим словарь.
    participant_id_cache: dict[str, UUID | None] = {}
    for item in results:
        slug = _normalize_location_slug(item.location_external_key, item.location_name)
        display_name = item.location_name or slug
        if platform.code == "s95":
            location_source_url = f"https://s95.ru/events/{slug}" if slug != "unknown" else None
            country = "Россия"
        elif platform.code == "parkrun":
            location_source_url = (
                f"https://www.parkrun.org.uk/{slug}/" if slug != "unknown" else None
            )
            # Профиль не говорит, где площадка: parkrun.org.uk — общий вход в
            # мировой каталог, а не признак Британии. Прежняя заглушка «United
            # Kingdom» помечала ею Якутск и Йошкар-Олу. Пусто честнее — страну
            # добирает бэкфилл по координатам (scripts/backfill_location_country.py).
            country = None
        else:
            location_source_url = f"https://5verst.ru/{slug}/" if slug != "unknown" else None
            country = "Россия"
        location, _ = upsert_location(
            db,
            platform,
            CanonicalLocation(
                external_key=slug,
                name=display_name,
                country=country,
                source_url=location_source_url,
            ),
        )
        if platform.code == "parkrun" and location.city is None:
            backfill_city_from_catalog(db, location)
        if platform.code == "parkrun" and location.region is None:
            backfill_region_from_catalog(db, location)
        external_event_key = _profile_external_event_key(item.event_date, slug)
        if platform.code == "s95":
            source_url = (
                f"https://s95.ru/events/{slug}"
                if slug != "unknown"
                else ""
            )
        elif platform.code == "parkrun":
            source_url = (
                f"https://www.parkrun.org.uk/{slug}/results/{item.event_number or ''}/"
                if slug != "unknown"
                else ""
            )
        else:
            source_url = (
                f"https://5verst.ru/{slug}/results/{item.event_date.strftime('%d.%m.%Y')}/"
                if slug != "unknown"
                else ""
            )
        event = upsert_event_for_profile(
            db,
            platform,
            location,
            external_event_key=external_event_key,
            event_date=item.event_date,
            event_number=item.event_number,
            location_name=display_name,
            location_slug=slug,
            source_url=source_url,
        )
        imported += upsert_run_results(
            db,
            event,
            platform,
            [item],
            from_profile=True,
            recalculate_pr=False,
        )
        if platform.code == "parkrun":
            from app.services.gender_position_service import recalculate_event_gender_positions

            recalculate_event_gender_positions(db, event.id, platform.code)
        # Участник в профильном импорте один и тот же на все пробежки, но раньше
        # его искали заново на каждой строке: у долгожителя с 284 забегами это
        # давало сотни одинаковых SELECT по туннелю (42% всего времени импорта).
        # Кешируем найденное в пределах вызова.
        external_user_id = item.external_user_id
        if external_user_id in participant_id_cache:
            participant_id = participant_id_cache[external_user_id]
        else:
            participant_row = (
                db.query(Participant.id)
                .filter(
                    Participant.platform_id == platform.id,
                    Participant.external_user_id == external_user_id,
                )
                .one_or_none()
            )
            participant_id = participant_row[0] if participant_row is not None else None
            participant_id_cache[external_user_id] = participant_id
        if participant_id is not None:
            touched_participant_ids.add(participant_id)
    if platform.code == "five_verst":
        participant_ids = {item.external_user_id for item in results}
        for external_user_id in participant_ids:
            participant = (
                db.query(Participant)
                .filter(
                    Participant.platform_id == platform.id,
                    Participant.external_user_id == external_user_id,
                )
                .one_or_none()
            )
            if participant is not None:
                dedupe_five_verst_run_results_in_db(db, platform.id, participant.id)
    if touched_participant_ids:
        from app.services.personal_record_service import (
            recalculate_participants_cross_platform_personal_records,
            recalculate_participants_first_run_flags,
            recalculate_participants_personal_records,
        )

        recalculate_participants_first_run_flags(db, platform.code, touched_participant_ids)
        recalculate_participants_personal_records(db, platform.code, touched_participant_ids)
        recalculate_participants_cross_platform_personal_records(db, touched_participant_ids)
    db.flush()
    return imported


def _volunteer_role_dedupe_key(role: str | None, *, platform_code: str | None = None) -> str:
    if not role:
        return "volunteer"
    if platform_code == "s95":
        from app.s95.parsers.volunteer_roles import s95_volunteer_role_key

        return s95_volunteer_role_key(role)
    normalized = re.sub(r"[^\w]+", "_", role.lower(), flags=re.UNICODE).strip("_")
    return normalized or "volunteer"


def _volunteer_result_completeness(vol: VolunteerResult) -> tuple[int, int, int, int]:
    role = vol.role or ""
    cyrillic_chars = sum(1 for char in role if "\u0400" <= char <= "\u04FF")
    key = vol.external_result_key or ""
    is_protocol = 1 if ":vol:" in key else 0
    return (
        cyrillic_chars,
        -is_protocol,
        0 if vol.role else 1,
        0 if vol.fetched_at else 1,
    )


def dedupe_participant_volunteer_results(
    db: Session,
    platform_id: UUID,
    participant_id: UUID,
) -> int:
    """One volunteering row per date, location and role."""
    platform = db.query(Platform).filter(Platform.id == platform_id).one()
    rows = (
        db.query(VolunteerResult, Event)
        .join(Event, VolunteerResult.event_id == Event.id)
        .filter(
            VolunteerResult.participant_id == participant_id,
            Event.platform_id == platform_id,
        )
        .all()
    )
    groups: dict[tuple[date, UUID, str], list[tuple[VolunteerResult, Event]]] = defaultdict(list)
    for vol, event in rows:
        groups[
            (event.event_date, event.location_id, _volunteer_role_dedupe_key(vol.role, platform_code=platform.code))
        ].append((vol, event))

    deleted = 0
    touched_event_ids: set[UUID] = set()
    for group in groups.values():
        if len(group) <= 1:
            continue
        group.sort(key=lambda pair: _volunteer_result_completeness(pair[0]))
        keeper, _keeper_event = group[0]
        canonical_role = keeper.role
        if platform.code == "s95":
            from app.s95.parsers.volunteer_roles import canonical_s95_volunteer_role, prefer_s95_volunteer_role

            for vol, _event in group:
                canonical_role = prefer_s95_volunteer_role(canonical_role, vol.role)
            if canonical_role:
                keeper.role = canonical_s95_volunteer_role(canonical_role) or canonical_role
        for vol, event in group[1:]:
            touched_event_ids.add(event.id)
            db.delete(vol)
            deleted += 1

    for event_id in touched_event_ids:
        has_runs = db.query(RunResult).filter(RunResult.event_id == event_id).count() > 0
        has_vol = db.query(VolunteerResult).filter(VolunteerResult.event_id == event_id).count() > 0
        if not has_runs and not has_vol:
            db.query(Event).filter(Event.id == event_id).delete()

    if deleted:
        db.flush()
    return deleted


def dedupe_five_verst_run_results_in_db(
    db: Session,
    platform_id: UUID,
    participant_id: UUID,
) -> int:
    """One run per participant per event_date per location; remove extras and orphan events."""
    rows = (
        db.query(RunResult, Event)
        .join(Event, RunResult.event_id == Event.id)
        .filter(
            RunResult.participant_id == participant_id,
            Event.platform_id == platform_id,
        )
        .all()
    )
    groups: dict[tuple[date, UUID], list[tuple[RunResult, Event]]] = defaultdict(list)
    for run, event in rows:
        groups[(event.event_date, event.location_id)].append((run, event))

    deleted = 0
    touched_event_ids: set[UUID] = set()
    for group in groups.values():
        if len(group) <= 1:
            continue
        group.sort(
            key=lambda pair: (
                pair[0].position is None,
                pair[0].age_category is None,
                pair[0].finish_time_sec is None,
                pair[0].fetched_at is None,
            ),
        )
        for run, event in group[1:]:
            touched_event_ids.add(event.id)
            db.delete(run)
            deleted += 1

    for event_id in touched_event_ids:
        has_runs = db.query(RunResult).filter(RunResult.event_id == event_id).count() > 0
        has_vol = db.query(VolunteerResult).filter(VolunteerResult.event_id == event_id).count() > 0
        if not has_runs and not has_vol:
            db.query(Event).filter(Event.id == event_id).delete()

    if deleted:
        db.flush()
    return deleted


def import_profile_volunteer_results(
    db: Session,
    platform: Platform,
    participant_id: UUID,
    results: list[CanonicalVolunteerResult],
) -> int:
    if not results:
        return 0

    participant = db.query(Participant).filter(Participant.id == participant_id).one()
    if platform.code == "five_verst":
        _purge_legacy_five_verst_profile_volunteer_keys(
            db,
            participant_id,
            participant.external_user_id,
        )

    incoming_keys: set[str] = set()
    imported = 0
    for item in results:
        slug = _normalize_location_slug(item.location_external_key, item.location_name)
        display_name = item.location_name or slug
        if platform.code == "parkrun":
            # См. import_profile_run_results: домен parkrun.org.uk не означает
            # Британию, поэтому страну отсюда не выдумываем.
            country = None
            default_source = f"https://www.parkrun.org.uk/{slug}/" if slug != "unknown" else ""
        elif platform.code == "s95":
            country = "Россия"
            default_source = f"https://s95.ru/events/{slug}" if slug != "unknown" else ""
        else:
            country = "Россия"
            default_source = f"https://5verst.ru/{slug}/" if slug != "unknown" else ""
        location, _ = upsert_location(
            db,
            platform,
            CanonicalLocation(
                external_key=slug,
                name=display_name,
                country=country,
                source_url=default_source,
            ),
        )
        if platform.code == "parkrun" and location.city is None:
            backfill_city_from_catalog(db, location)
        if platform.code == "parkrun" and location.region is None:
            backfill_region_from_catalog(db, location)
        external_event_key = _profile_external_event_key(item.event_date, slug)
        source_url = item.source_url or (
            f"https://5verst.ru/{slug}/results/{item.event_date.strftime('%d.%m.%Y')}/"
            if platform.code == "five_verst" and slug != "unknown"
            else default_source
        )
        event = upsert_event_for_profile(
            db,
            platform,
            location,
            external_event_key=external_event_key,
            event_date=item.event_date,
            # Номер забега отсюда НЕ берём. Волонтёрская таблица профиля 5 вёрст
            # показывает не тот номер, что страница локации и таблица пробежек:
            # у Дружбы за 15.08.2026 в волонтёрствах «#226», а на самом деле
            # #228 (проверено на 5verst.ru/druzhba/results/all/ и на том же
            # профиле). Мы этот номер записывали поверх правильного, и журнал
            # протоколов шёл вразнобой: 220-219-222-221-224-225-224-227-226-229.
            # Номер приезжает со страницы локации, здесь его трогать нечем.
            event_number=None,
            location_name=display_name,
            location_slug=slug,
            source_url=source_url,
        )
        incoming_keys.add(item.external_result_key)
        imported += upsert_volunteer_results(db, event, platform, [item])

    dedupe_participant_volunteer_results(db, platform.id, participant_id)

    stale_rows = (
        db.query(VolunteerResult)
        .filter(VolunteerResult.participant_id == participant_id)
        .all()
    )
    for row in stale_rows:
        if row.external_result_key in incoming_keys:
            continue
        if _is_profile_volunteer_external_key(
            platform.code,
            participant.external_user_id,
            row.external_result_key,
        ):
            db.delete(row)

    db.flush()
    return imported
