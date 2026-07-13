from __future__ import annotations

from datetime import date
from functools import partial
from uuid import UUID

from sqlalchemy.orm import Session

from app.activity_url import resolve_activity_url
from app.models import (
    Event,
    Location,
    Participant,
    Platform,
    PlatformLink,
    RunResult,
    VolunteerResult,
)
from app.services.history_milestone_settings_service import get_disabled_milestone_kinds
from app.services.location_catalog_service import LocationCatalogIndex
from app.services.user_location_stats import _canonical_region, _normalize_geo_value
from app.time_format import normalize_finish_time_display
from app.volunteering_occasions import volunteer_occasion_dates

# Клубы пробежек — сквозные (кросс-платформенно) и по каждой отдельной системе
# используют один и тот же набор номеров.
RUN_CLUB_NUMBERS = (10, 25, 50, 100, 250, 500, 1000)
# Клубы пробежек на одной локации (физическая площадка сквозь все системы).
LOCATION_CLUBS = (10, 25, 50, 100, 250, 500)
# Клубы волонтёрств — сквозные и по каждой отдельной системе.
VOLUNTEER_CLUB_NUMBERS = (10, 25, 50, 100, 250)


def is_geo_milestone_number(number: int) -> bool:
    """Юбилейный порядковый номер региона/города: 3, 5, 10, 15, 20, 25, 30…"""
    return number in (3, 5) or (number >= 10 and number % 5 == 0)


_PLATFORM_ORDER = {"five_verst": 0, "s95": 1, "parkrun": 2, "runpark": 3}

# Русские паркраны, которых нет в каталоге локаций (закрылись без преемника в
# 5 вёрст/S95 и потому не попали в location_catalog). Дополнять по мере
# обнаружения: SELECT parkrun-локации без location_catalog_links с русскими
# названиями. parkrun ушёл из России в 2022 — список конечен.
_RUSSIAN_PARKRUN_SLUGS_OUTSIDE_CATALOG = frozenset({
    "park-30-letiya-oktyabrya",  # Омск, парк 30-летия Октября
})

# Порядок вех внутри одного дня — от «крупной» к «мелкой».
_KIND_ORDER = {
    "first_run": 0,
    "first_run_platform": 1,
    "run_club": 2,
    "run_club_platform": 3,
    "location_club": 4,
    "volunteer_location_club": 5,
    "global_pr": 6,
    "pr": 7,
    "first_foreign_parkrun": 8,
    "first_foreign_run": 9,
    "new_country": 10,
    "new_region": 11,
    "new_city": 12,
    "new_location": 13,
    "first_volunteer": 14,
    "volunteer_club": 15,
    "volunteer_club_platform": 16,
}


def _city_geo_value(city: str | None) -> str | None:
    """Город для гео-вех: отсекаем слоган 5 вёрст, попавший в поле city."""
    value = _normalize_geo_value(city)
    if value is None or "5 км" in value:
        return None
    return value


def _milestone_event_url(
    *,
    platform_code: str,
    event: Event,
    location: Location,
    profile_url: str | None,
) -> str | None:
    """Ссылка на источник вехи. Для parkrun не выводим — там нет полезного
    места, куда вести пользователя (только общий список результатов бегуна)."""
    if platform_code == "parkrun":
        return None
    return resolve_activity_url(
        platform_code=platform_code,
        event_date=event.event_date,
        event_number=event.event_number,
        event_source_url=event.source_url,
        location_external_key=location.external_key,
        profile_url=profile_url,
    )


def _base_entry(
    *,
    kind: str,
    event_date: date,
    platform_code: str,
    location_name: str,
    location_city: str | None,
    number: int | None = None,
    finish_time_display: str | None = None,
    finish_time_sec: int | None = None,
    position: int | None = None,
    gender_position: int | None = None,
    pace_display: str | None = None,
    delta_sec: int | None = None,
    is_global_pr: bool = False,
    region: str | None = None,
    country: str | None = None,
    role: str | None = None,
    event_url: str | None = None,
) -> dict[str, object]:
    return {
        "kind": kind,
        "number": number,
        "event_date": event_date,
        "platform_code": platform_code,
        "location_name": location_name,
        "location_city": location_city,
        "finish_time_display": finish_time_display,
        "finish_time_sec": finish_time_sec,
        "position": position,
        "gender_position": gender_position,
        "pace_display": pace_display,
        "delta_sec": delta_sec,
        "is_global_pr": is_global_pr,
        "region": region,
        "country": country,
        "role": role,
        "event_url": event_url,
    }


def _run_milestone(
    *,
    kind: str,
    catalog_index: LocationCatalogIndex,
    run: RunResult,
    event: Event,
    location: Location,
    platform_code: str,
    participant: Participant,
    number: int | None = None,
    delta_sec: int | None = None,
    is_global_pr: bool = False,
) -> dict[str, object]:
    return _base_entry(
        kind=kind,
        number=number,
        event_date=event.event_date,
        platform_code=platform_code,
        location_name=catalog_index.display_name(location, platform_code),
        location_city=_city_geo_value(location.city),
        finish_time_display=normalize_finish_time_display(
            run.finish_time_sec, run.finish_time_display
        ),
        finish_time_sec=run.finish_time_sec,
        position=run.position,
        gender_position=run.gender_position,
        pace_display=run.pace_display,
        delta_sec=delta_sec,
        is_global_pr=is_global_pr,
        region=location.region,
        country=location.country,
        event_url=_milestone_event_url(
            platform_code=platform_code,
            event=event,
            location=location,
            profile_url=participant.profile_url,
        ),
    )


def _collect_run_milestones(
    db: Session,
    user_id: UUID,
    *,
    include_test_events: bool,
) -> list[dict[str, object]]:
    from app.services.personal_record_service import user_secondary_crosslinked_run_ids

    query = (
        db.query(RunResult, Event, Location, Platform.code, Participant)
        .join(Event, RunResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Event.platform_id == Platform.id)
        .join(Participant, RunResult.participant_id == Participant.id)
        .join(PlatformLink, PlatformLink.participant_id == RunResult.participant_id)
        .filter(
            PlatformLink.user_id == user_id,
            PlatformLink.platform_id == Platform.id,
            Event.event_date > date(1970, 1, 1),
        )
    )
    if not include_test_events:
        query = query.filter(Event.is_test_event.is_(False))

    # «Не в зачёте» дубли (secondary crosslink) не участвуют ни в нумерации
    # пробежек, ни в PR — как в total_runs дашборда и в PR-логике.
    excluded_ids = user_secondary_crosslinked_run_ids(
        db, user_id, include_test_events=include_test_events
    )
    rows = [row for row in query.all() if row[0].id not in excluded_ids]
    rows.sort(
        key=lambda row: (
            row[1].event_date,
            row[1].event_number or 0,
            _PLATFORM_ORDER.get(row[3], 9),
            str(row[0].id),
        )
    )

    catalog_index = LocationCatalogIndex(db)
    milestones: list[dict[str, object]] = []

    # Лучшее время по каждой платформе и глобально — для PR-вех считаем
    # хронологически сами (первый результат платформы — базовая точка, не веха).
    best_by_platform: dict[str, int] = {}
    global_best: int | None = None
    # «Домашняя» страна — страна первой пробежки с заполненной географией;
    # старт в любой другой стране = зарубежный. Новую страну/регион/город
    # (независимо от того, пробежка это или волонтёрство) считает отдельная
    # объединённая функция ниже — _collect_location_geo_milestones.
    home_country: str | None = None
    foreign_run_seen = False
    # Счётчик пробежек по физической локации (canonical identity — кросслинки
    # и миграции платформ считаются одной площадкой) — для клубов локации.
    runs_by_location: dict[str, int] = {}
    # Счётчик пробежек по каждой платформе — для дебюта и клуба в системе.
    runs_by_platform: dict[str, int] = {}
    foreign_parkrun_seen = False

    for run_number, (run, event, location, platform_code, participant) in enumerate(rows, start=1):
        make = partial(
            _run_milestone,
            catalog_index=catalog_index,
            run=run,
            event=event,
            location=location,
            platform_code=platform_code,
            participant=participant,
        )

        platform_run_number = runs_by_platform.get(platform_code, 0) + 1
        runs_by_platform[platform_code] = platform_run_number

        # Первая пробежка вообще / первая пробежка на конкретной платформе.
        # Если это одна и та же пробежка (дебютная платформа) — веха одна.
        if run_number == 1:
            milestones.append(make(kind="first_run", number=1))
        elif platform_run_number == 1:
            milestones.append(make(kind="first_run_platform", number=1))

        # Клубы пробежек: сквозной (по номеру пробежки во всей истории) и по
        # конкретной платформе (по номеру пробежки на ней) — независимые вехи,
        # у активного бегуна на нескольких системах могут совпасть по дате.
        if run_number in RUN_CLUB_NUMBERS:
            milestones.append(make(kind="run_club", number=run_number))
        if platform_run_number in RUN_CLUB_NUMBERS:
            milestones.append(make(kind="run_club_platform", number=platform_run_number))

        # Клуб пробежек на конкретной локации: 10-я/25-я/… пробежка на одной
        # физической площадке. Первая пробежка на локации вехой не считается.
        identity = catalog_index.canonical_identity_key(location, platform_code)
        location_run_number = runs_by_location.get(identity, 0) + 1
        runs_by_location[identity] = location_run_number
        if location_run_number in LOCATION_CLUBS:
            milestones.append(make(kind="location_club", number=location_run_number))

        # Первый зарубежный паркран. Русские паркраны есть в каталоге локаций
        # (location_catalog связывает их с 5в/S95) либо в явном списке
        # исключений; все остальные parkrun-локации — зарубежные.
        if (
            platform_code == "parkrun"
            and not foreign_parkrun_seen
            and location.external_key not in _RUSSIAN_PARKRUN_SLUGS_OUTSIDE_CATALOG
            and catalog_index.get_for_location(location, platform_code) is None
        ):
            foreign_parkrun_seen = True
            milestones.append(make(kind="first_foreign_parkrun"))

        # Первый зарубежный старт (пробежка, не волонтёрство — «старт» не про
        # это). «Домашняя» страна — страна первой пробежки с заполненной
        # географией; выезд в любую другую = зарубежный старт. parkrun
        # пропускаем: страна там всегда «United Kingdom» (источник
        # parkrun.org.uk) — дала бы ложную веху (первый зарубежный паркран —
        # отдельная веха выше).
        if platform_code != "parkrun":
            country = _normalize_geo_value(location.country)
            if country:
                if home_country is None:
                    home_country = country
                elif country != home_country and not foreign_run_seen:
                    milestones.append(make(kind="first_foreign_run"))
                    foreign_run_seen = True

        # PR: улучшение своего лучшего времени на этой платформе. Первый
        # результат на платформе — базовая точка (дебют вехой PR не считаем).
        # Глобальный рекорд всегда одновременно является и платформенным (min
        # по всем платформам не может быть меньше min по одной из них) — поэтому
        # это ВЗАИМОИСКЛЮЧАЮЩИЕ вехи одного события: либо pr, либо global_pr,
        # никогда обе сразу (одна карточка на пробежку).
        finish_time = run.finish_time_sec
        if finish_time is not None and finish_time > 0:
            platform_best = best_by_platform.get(platform_code)
            if platform_best is not None and finish_time < platform_best:
                is_global = global_best is not None and finish_time < global_best
                kind = "global_pr" if is_global else "pr"
                milestones.append(
                    make(kind=kind, delta_sec=platform_best - finish_time, is_global_pr=is_global)
                )
            if platform_best is None or finish_time < platform_best:
                best_by_platform[platform_code] = finish_time
            if global_best is None or finish_time < global_best:
                global_best = finish_time

    return milestones


def _collect_volunteer_milestones(
    db: Session,
    user_id: UUID,
    *,
    include_test_events: bool,
) -> list[dict[str, object]]:
    query = (
        db.query(VolunteerResult, Event, Location, Platform.code, Participant)
        .join(Event, VolunteerResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Event.platform_id == Platform.id)
        .join(Participant, VolunteerResult.participant_id == Participant.id)
        .join(PlatformLink, PlatformLink.participant_id == VolunteerResult.participant_id)
        .filter(
            PlatformLink.user_id == user_id,
            PlatformLink.platform_id == Platform.id,
            # parkrun-волонтёрства хранятся сводкой на 1970-01-01 — без дат их
            # не разместить на таймлайне, пропускаем (как в календаре активности).
            Event.event_date > date(1970, 1, 1),
        )
    )
    if not include_test_events:
        query = query.filter(Event.is_test_event.is_(False))

    catalog_index = LocationCatalogIndex(db)

    # Нумерация волонтёрств — по «occasions» каждой платформы (несколько ролей в
    # один день = одно волонтёрство), как в счётчиках дашборда.
    rows_by_platform: dict[str, list[tuple[VolunteerResult, Event, Location, Participant]]] = {}
    for volunteer, event, location, platform_code, participant in query.all():
        rows_by_platform.setdefault(platform_code, []).append(
            (volunteer, event, location, participant)
        )

    occasions: list[dict[str, object]] = []
    for platform_code, rows in rows_by_platform.items():
        occasion_dates = volunteer_occasion_dates(
            platform_code,
            [(event.event_date, location.external_key or "unknown") for _v, event, location, _p in rows],
        )
        # Представитель дня — первая строка по этой дате (роль/локация для вехи).
        by_date: dict[date, tuple[VolunteerResult, Event, Location, Participant]] = {}
        for row in sorted(rows, key=lambda item: str(item[0].id)):
            event_date = row[1].event_date
            if event_date in occasion_dates and event_date not in by_date:
                by_date[event_date] = row
        for event_date, (volunteer, event, location, participant) in by_date.items():
            occasions.append(
                {
                    "event_date": event_date,
                    "platform_code": platform_code,
                    "location_name": catalog_index.display_name(location, platform_code),
                    "location_city": _city_geo_value(location.city),
                    "location_key": catalog_index.canonical_identity_key(location, platform_code),
                    "role": (volunteer.role or "").strip() or None,
                    "event_url": _milestone_event_url(
                        platform_code=platform_code,
                        event=event,
                        location=location,
                        profile_url=participant.profile_url,
                    ),
                }
            )

    # Сортировка по дате (при совпадении — приоритет платформы) — общий порядок
    # для сквозной нумерации; фильтрация по платформе ниже сохраняет этот
    # порядок для нумерации внутри каждой отдельной системы.
    occasions.sort(
        key=lambda item: (item["event_date"], _PLATFORM_ORDER.get(str(item["platform_code"]), 9))
    )

    def _volunteer_milestone(occasion: dict[str, object], *, kind: str, number: int) -> dict[str, object]:
        return _base_entry(
            kind=kind,
            number=number,
            event_date=occasion["event_date"],  # type: ignore[arg-type]
            platform_code=str(occasion["platform_code"]),
            location_name=str(occasion["location_name"]),
            location_city=occasion["location_city"],  # type: ignore[arg-type]
            role=occasion["role"],  # type: ignore[arg-type]
            event_url=occasion["event_url"],  # type: ignore[arg-type]
        )

    milestones: list[dict[str, object]] = []

    # Сквозная нумерация (по всем системам вместе): первое волонтёрство и клуб.
    for occasion_number, occasion in enumerate(occasions, start=1):
        if occasion_number == 1:
            milestones.append(_volunteer_milestone(occasion, kind="first_volunteer", number=1))
        elif occasion_number in VOLUNTEER_CLUB_NUMBERS:
            milestones.append(
                _volunteer_milestone(occasion, kind="volunteer_club", number=occasion_number)
            )

    # Нумерация по каждой отдельной платформе — свой клуб волонтёрств в системе.
    occasions_by_platform: dict[str, list[dict[str, object]]] = {}
    for occasion in occasions:
        occasions_by_platform.setdefault(str(occasion["platform_code"]), []).append(occasion)
    for platform_occasions in occasions_by_platform.values():
        for platform_occasion_number, occasion in enumerate(platform_occasions, start=1):
            if platform_occasion_number in VOLUNTEER_CLUB_NUMBERS:
                milestones.append(
                    _volunteer_milestone(
                        occasion, kind="volunteer_club_platform", number=platform_occasion_number
                    )
                )

    # Клуб волонтёрств на конкретной локации: сквозь платформы (canonical
    # identity), как и клуб пробежек на локации.
    occasions_by_location: dict[str, int] = {}
    for occasion in occasions:
        location_key = str(occasion["location_key"])
        location_occasion_number = occasions_by_location.get(location_key, 0) + 1
        occasions_by_location[location_key] = location_occasion_number
        if location_occasion_number in LOCATION_CLUBS:
            milestones.append(
                _volunteer_milestone(
                    occasion, kind="volunteer_location_club", number=location_occasion_number
                )
            )

    return milestones


def _collect_location_geo_milestones(
    db: Session,
    user_id: UUID,
    *,
    include_test_events: bool,
) -> list[dict[str, object]]:
    """Гео-вехи (новая страна/регион/город) и «новая локация» — по ОБЪЕДИНЁННОЙ
    хронологии пробежек и волонтёрств: если человек сначала волонтёрил в новом
    городе, а потом там же пробежал — город уже не новый ни для той, ни для
    другой активности (и наоборот). Страна/регион/город пропускают parkrun —
    его данные о географии не заслуживают доверия (см. _collect_run_milestones);
    «новая локация» — самый мелкий уровень иерархии страна→регион→город→локация,
    parkrun не пропускает: конкретная площадка — валидная локация сама по себе,
    просто без надёжного региона."""
    from app.services.personal_record_service import user_secondary_crosslinked_run_ids

    catalog_index = LocationCatalogIndex(db)

    run_query = (
        db.query(RunResult, Event, Location, Platform.code, Participant)
        .join(Event, RunResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Event.platform_id == Platform.id)
        .join(Participant, RunResult.participant_id == Participant.id)
        .join(PlatformLink, PlatformLink.participant_id == RunResult.participant_id)
        .filter(
            PlatformLink.user_id == user_id,
            PlatformLink.platform_id == Platform.id,
            Event.event_date > date(1970, 1, 1),
        )
    )
    if not include_test_events:
        run_query = run_query.filter(Event.is_test_event.is_(False))
    excluded_ids = user_secondary_crosslinked_run_ids(
        db, user_id, include_test_events=include_test_events
    )

    vol_query = (
        db.query(VolunteerResult, Event, Location, Platform.code, Participant)
        .join(Event, VolunteerResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Event.platform_id == Platform.id)
        .join(Participant, VolunteerResult.participant_id == Participant.id)
        .join(PlatformLink, PlatformLink.participant_id == VolunteerResult.participant_id)
        .filter(
            PlatformLink.user_id == user_id,
            PlatformLink.platform_id == Platform.id,
            Event.event_date > date(1970, 1, 1),
        )
    )
    if not include_test_events:
        vol_query = vol_query.filter(Event.is_test_event.is_(False))

    # Единая хронология визитов: (дата, платформа, локация, участник, событие,
    # пробежка|None, роль волонтёра|None). run=None означает волонтёрство.
    Visit = tuple[date, str, Location, Participant, Event, RunResult | None, str | None]
    visits: list[Visit] = []
    for run, event, location, platform_code, participant in run_query.all():
        if run.id in excluded_ids:
            continue
        visits.append((event.event_date, platform_code, location, participant, event, run, None))
    for volunteer, event, location, platform_code, participant in vol_query.all():
        role = (volunteer.role or "").strip() or None
        visits.append((event.event_date, platform_code, location, participant, event, None, role))

    # При совпадении даты пробежки идут раньше волонтёрств (условность, но
    # стабильная и предсказуемая) — сортировка последним ключом на run/None.
    visits.sort(key=lambda v: (v[0], _PLATFORM_ORDER.get(v[1], 9), v[5] is None))

    def make_entry(
        visit: Visit,
        *,
        kind: str,
        number: int | None = None,
        region: str | None = None,
        country: str | None = None,
    ) -> dict[str, object]:
        event_date, platform_code, location, participant, event, run, role = visit
        if run is not None:
            finish_time_display = normalize_finish_time_display(run.finish_time_sec, run.finish_time_display)
            finish_time_sec = run.finish_time_sec
            position = run.position
            gender_position = run.gender_position
            pace_display = run.pace_display
        else:
            finish_time_display = None
            finish_time_sec = None
            position = None
            gender_position = None
            pace_display = None
        return _base_entry(
            kind=kind,
            number=number,
            event_date=event_date,
            platform_code=platform_code,
            location_name=catalog_index.display_name(location, platform_code),
            location_city=_city_geo_value(location.city),
            finish_time_display=finish_time_display,
            finish_time_sec=finish_time_sec,
            position=position,
            gender_position=gender_position,
            pace_display=pace_display,
            region=region,
            country=country,
            role=role,
            event_url=_milestone_event_url(
                platform_code=platform_code,
                event=event,
                location=location,
                profile_url=participant.profile_url,
            ),
        )

    milestones: list[dict[str, object]] = []
    seen_regions: set[str] = set()
    seen_countries: set[str] = set()
    seen_cities: set[str] = set()
    seen_locations: set[str] = set()

    for visit in visits:
        _event_date, platform_code, location, _participant, _event, _run, _role = visit
        identity = catalog_index.canonical_identity_key(location, platform_code)
        geo_milestone_made = False

        if platform_code != "parkrun":
            region_raw = _normalize_geo_value(location.region)
            region = _canonical_region(region_raw) if region_raw else None
            country = _normalize_geo_value(location.country)
            city = _city_geo_value(location.city)
            if country:
                if country not in seen_countries and seen_countries:
                    milestones.append(
                        make_entry(visit, kind="new_country", number=len(seen_countries) + 1, country=country)
                    )
                    geo_milestone_made = True
                seen_countries.add(country)
            if region:
                region_number = len(seen_regions) + 1
                if region not in seen_regions and seen_regions:
                    # Юбилейный номер (3/5/10/…) пробивает подавление — иначе
                    # «10-й регион», совпавший с новой страной, потерялся бы.
                    if not geo_milestone_made or is_geo_milestone_number(region_number):
                        milestones.append(
                            make_entry(visit, kind="new_region", number=region_number, region=region)
                        )
                        geo_milestone_made = True
                seen_regions.add(region)
            if city:
                city_number = len(seen_cities) + 1
                if city not in seen_cities and seen_cities:
                    if not geo_milestone_made or is_geo_milestone_number(city_number):
                        milestones.append(make_entry(visit, kind="new_city", number=city_number))
                        geo_milestone_made = True
                seen_cities.add(city)

        # Новая локация — самый мелкий уровень: любая физическая площадка,
        # на которой человек ещё не был (пробежка или волонтёрство). Юбилейный
        # номер (3/5/10/…) считает по общему числу посещённых локаций.
        if identity not in seen_locations:
            location_number = len(seen_locations) + 1
            if seen_locations and (not geo_milestone_made or is_geo_milestone_number(location_number)):
                milestones.append(make_entry(visit, kind="new_location", number=location_number))
            seen_locations.add(identity)

    return milestones


def get_my_history(
    db: Session,
    user_id: UUID,
    *,
    include_test_events: bool = False,
) -> dict[str, object]:
    """«Моя история»: хронология ключевых вех бегуна.

    Вехи: первая пробежка (сквозная и по каждой системе), клубы пробежек
    (сквозные и по каждой системе, 10/25/50/100/250/500/1000), клубы на
    локации (пробежки и волонтёрства — сквозь платформы), личный и глобальный
    рекорды (взаимоисключающие — одна карточка на пробежку), новый регион/
    страна/город/локация (считаются по ОБЕИМ активностям — пробежка или
    волонтёрство), первый зарубежный старт (и отдельно первый зарубежный
    паркран), первое волонтёрство и клубы волонтёрств (сквозные и по каждой
    системе).
    """
    milestones = (
        _collect_run_milestones(db, user_id, include_test_events=include_test_events)
        + _collect_volunteer_milestones(db, user_id, include_test_events=include_test_events)
        + _collect_location_geo_milestones(db, user_id, include_test_events=include_test_events)
    )

    disabled_kinds = get_disabled_milestone_kinds(db)
    if disabled_kinds:
        milestones = [item for item in milestones if str(item["kind"]) not in disabled_kinds]

    # От новых к старым — при открытии страницы видна самая свежая веха, а не
    # начало пути. Сортировка в два стабильных прохода: сперва kind_order по
    # возрастанию (крупная веха раньше мелкой в рамках одного дня), затем дата
    # по убыванию — стабильность sort() сохраняет уже расставленный порядок
    # внутри одной даты.
    milestones.sort(key=lambda item: _KIND_ORDER.get(str(item["kind"]), 99))
    milestones.sort(key=lambda item: item["event_date"], reverse=True)
    return {"milestones": milestones, "total": len(milestones)}
