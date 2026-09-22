"""Поиск участников по ФИО во всех системах — для онбординга и привязки профилей.

Ищем по participants.display_name (регистронезависимо, каждое слово запроса —
подстрока имени, порядок слов не важен: «Попов Дмитрий» == «Дмитрий Попов»).
Под LIKE по lower(display_name) есть GIN-индекс pg_trgm (миграция 052).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date
from uuid import UUID

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models import Event, Location, Participant, Platform, PlatformLink, RunResult, User, VolunteerResult
from app.parkrun.volunteer_credits import count_parkrun_volunteering
from app.runpark.mappings import runpark_profile_url
from app.services.co_runners_service import _is_unknown_participant_name
from app.services.location_catalog_service import PARKRUN_PLATFORM_CODE

MIN_QUERY_LENGTH = 3
MAX_QUERY_LENGTH = 100
# Сколько строк-кандидатов достаём из БД до ранжирования и обрезки.
CANDIDATE_LIMIT = 200
RESULT_LIMIT = 30


class ParticipantSearchError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


@dataclass(frozen=True)
class ParticipantSearchActivity:
    kind: str  # "run" | "volunteer"
    event_date: date
    location_name: str
    finish_time_display: str | None
    role: str | None


@dataclass(frozen=True)
class ParticipantSearchResult:
    participant_id: UUID
    platform_code: str
    platform_name: str
    display_name: str
    club_name: str | None
    age_category: str | None
    profile_url: str | None
    total_runs: int
    total_volunteering: int
    last_run_date: date | None
    home_location_name: str | None
    home_location_city: str | None
    already_linked: bool
    linked_to_me: bool
    recent_activities: list[ParticipantSearchActivity]


@dataclass(frozen=True)
class ParticipantSearchPage:
    query: str
    results: list[ParticipantSearchResult]
    truncated: bool
    # Привязанные системы, где по запросу есть совпадения, скрытые из выдачи.
    # Кто именно нашёлся — не раскрываем, только факт и код системы.
    hidden_linked_platform_codes: list[str]


# Код участника: «A7035519» (штрихкод из QR любой системы) или просто цифры
# (номер участника 5 вёрст / parkrun / С95). Минимум 3 цифры, чтобы не путать
# с короткими именами. Кириллическую «А» принимаем наравне с латинской: на
# русской раскладке её набирают, не глядя, а штрихкод от этого не меняется.
_IDENTIFIER_RE = re.compile(r"^[AaАа]?(\d{3,16})$")


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _query_words(raw_query: str) -> list[str]:
    return [word for word in raw_query.split() if word]


def normalize_query_text(raw_query: str) -> str:
    """Запрос без крайних и сдвоенных пробелов — то, что реально ищем и показываем."""
    return " ".join(_query_words(raw_query or ""))


def apply_name_filters(query, words: list[str]):
    """Каждое слово запроса — подстрока имени (регистронезависимо, порядок не важен).

    Условие написано ровно над lower(display_name), чтобы его подхватил
    GIN-индекс pg_trgm ix_participants_display_name_trgm. Общее для онбординга
    и публичного поиска на сайте.
    """
    for word in words:
        query = query.filter(
            func.lower(Participant.display_name).like(f"%{_escape_like(word.lower())}%", escape="\\")
        )
    return query


def _apply_query_filters(query, words: list[str]):
    """Общие условия поиска: код участника (точно) или все слова имени (подстроки)."""
    identifier = _IDENTIFIER_RE.match(words[0]) if len(words) == 1 else None
    if identifier:
        # Ввели код участника — точное совпадение во всех системах:
        # и как штрихкод (A…), и как номер участника (цифры). Штрихкод сверяем
        # в обеих формах: часть кодов С95 лежит в базе без префикса «A»
        # (приехали из легаси голыми цифрами), и без этого свой же QR —
        # A770012057 — в поиске ничего не находил.
        digits = identifier.group(1)
        return query.filter(
            or_(
                func.upper(Participant.barcode_id).in_((f"A{digits}", digits)),
                Participant.external_user_id == digits,
            )
        )
    return apply_name_filters(query, words)


def search_participants(db: Session, user: User, raw_query: str) -> ParticipantSearchPage:
    if not user.consent_accepted:
        raise ParticipantSearchError(
            "Сначала примите условия обработки персональных данных — после этого можно искать и привязывать профили.",
            403,
        )
    query_text = normalize_query_text(raw_query)
    if len(query_text) < MIN_QUERY_LENGTH:
        raise ParticipantSearchError("Введите минимум 3 символа имени или фамилии", 422)
    if len(query_text) > MAX_QUERY_LENGTH:
        raise ParticipantSearchError("Слишком длинный запрос", 422)

    words = _query_words(query_text)
    candidates_query = (
        db.query(Participant, Platform)
        .join(Platform, Participant.platform_id == Platform.id)
        .filter(Platform.is_active.is_(True), Participant.display_name.isnot(None))
    )
    # Системы, где профиль уже привязан, из поиска исключаем: искать там больше
    # нечего, а оставлять работающий поиск людей по чужим именам — нечестно.
    linked_platform_ids = [
        platform_id
        for (platform_id,) in db.query(PlatformLink.platform_id).filter(PlatformLink.user_id == user.id)
    ]
    if linked_platform_ids:
        candidates_query = candidates_query.filter(Participant.platform_id.notin_(linked_platform_ids))
    candidates_query = _apply_query_filters(candidates_query, words)
    candidates = candidates_query.limit(CANDIDATE_LIMIT + 1).all()

    # Совпадения в привязанных системах не показываем, но честно говорим, что
    # они есть: иначе «никого не нашли» выглядит враньём (кейс Дмитрия: ввёл
    # штрихкод, а система этого бегуна у него уже привязана).
    hidden_linked_codes: list[str] = []
    if linked_platform_ids:
        hidden_query = (
            db.query(Platform.code)
            .select_from(Participant)
            .join(Platform, Participant.platform_id == Platform.id)
            .filter(
                Platform.is_active.is_(True),
                Participant.display_name.isnot(None),
                Participant.platform_id.in_(linked_platform_ids),
            )
        )
        hidden_query = _apply_query_filters(hidden_query, words)
        hidden_linked_codes = sorted({code for (code,) in hidden_query.distinct().limit(10).all()})

    truncated_candidates = len(candidates) > CANDIDATE_LIMIT
    candidates = [
        (participant, platform)
        for participant, platform in candidates[:CANDIDATE_LIMIT]
        if not _is_unknown_participant_name(participant.display_name)
    ]
    if not candidates:
        return ParticipantSearchPage(
            query=query_text,
            results=[],
            truncated=truncated_candidates,
            hidden_linked_platform_codes=hidden_linked_codes,
        )

    participant_ids = [participant.id for participant, _ in candidates]
    run_stats = load_run_stats(db, participant_ids)
    volunteering_counts = load_volunteering_counts(db, candidates)
    home_locations = _load_home_locations(db, participant_ids)
    linked_owners = load_linked_owners(db, candidates)

    results: list[ParticipantSearchResult] = []
    for participant, platform in candidates:
        runs_count, last_run_date = run_stats.get(participant.id, (0, None))
        home = home_locations.get(participant.id)
        owner_id = linked_owners.get(participant.id)
        age_category = participant.age_category
        if platform.code == "parkrun":
            from app.parkrun.age_category import normalize_parkrun_age_group

            age_category = normalize_parkrun_age_group(age_category)
        profile_url = participant.profile_url
        if platform.code == "runpark" and not profile_url:
            # Страница кармы открывается только по идентификатору аккаунта;
            # у личности «barcode:A…» аккаунта нет и ссылки быть не должно
            # (как в platformProfileUrl на фронте).
            profile_url = runpark_profile_url(participant.external_user_id)
        results.append(
            ParticipantSearchResult(
                participant_id=participant.id,
                platform_code=platform.code,
                platform_name=platform.name,
                display_name=(participant.display_name or "").strip(),
                club_name=participant.club_name,
                age_category=age_category,
                profile_url=profile_url,
                total_runs=runs_count,
                total_volunteering=volunteering_counts.get(participant.id, 0),
                last_run_date=last_run_date,
                home_location_name=home[0] if home else None,
                home_location_city=home[1] if home else None,
                already_linked=owner_id is not None,
                linked_to_me=owner_id == user.id,
                recent_activities=[],
            )
        )

    results.sort(key=_rank_key)
    truncated = truncated_candidates or len(results) > RESULT_LIMIT
    top = results[:RESULT_LIMIT]
    # Последние события тянем только для показываемой верхушки — не для всех кандидатов.
    recents = _load_recent_activities(db, [item.participant_id for item in top])
    top = [replace(item, recent_activities=recents.get(item.participant_id, [])) for item in top]
    return ParticipantSearchPage(
        query=query_text,
        results=top,
        truncated=truncated,
        hidden_linked_platform_codes=hidden_linked_codes,
    )


def _rank_key(item: ParticipantSearchResult) -> tuple[int, int, int, str]:
    # Свежепробежавшие — выше (решение Дмитрия 25.08.2026): человек скорее
    # всего активен и ищет себя, а «пустые» однофамильцы без пробежек — внизу.
    return (
        0 if item.last_run_date is not None else 1,
        -(item.last_run_date.toordinal() if item.last_run_date is not None else 0),
        -item.total_runs,
        item.display_name.casefold(),
    )


RECENT_ACTIVITIES_LIMIT = 3


def _load_recent_activities(
    db: Session,
    participant_ids: list[UUID],
) -> dict[UUID, list[ParticipantSearchActivity]]:
    """Последние события участника (пробежки и волонтёрства вместе), по 3 на карточку."""
    if not participant_ids:
        return {}

    run_rn = (
        func.row_number()
        .over(partition_by=RunResult.participant_id, order_by=Event.event_date.desc())
        .label("rn")
    )
    runs_sq = (
        db.query(
            RunResult.participant_id.label("participant_id"),
            Event.event_date.label("event_date"),
            Location.name.label("location_name"),
            RunResult.finish_time_display.label("finish_time_display"),
            run_rn,
        )
        .join(Event, RunResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .filter(
            RunResult.participant_id.in_(participant_ids),
            Event.is_test_event.is_(False),
        )
        .subquery()
    )
    run_rows = (
        db.query(runs_sq)
        .filter(runs_sq.c.rn <= RECENT_ACTIVITIES_LIMIT)
        .all()
    )

    vol_rn = (
        func.row_number()
        .over(partition_by=VolunteerResult.participant_id, order_by=Event.event_date.desc())
        .label("rn")
    )
    vol_sq = (
        db.query(
            VolunteerResult.participant_id.label("participant_id"),
            Event.event_date.label("event_date"),
            Location.name.label("location_name"),
            VolunteerResult.role.label("role"),
            vol_rn,
        )
        .join(Event, VolunteerResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .filter(
            VolunteerResult.participant_id.in_(participant_ids),
            Event.is_test_event.is_(False),
            Event.event_date > date(1970, 1, 1),
        )
        .subquery()
    )
    vol_rows = (
        db.query(vol_sq)
        .filter(vol_sq.c.rn <= RECENT_ACTIVITIES_LIMIT)
        .all()
    )

    merged: dict[UUID, list[ParticipantSearchActivity]] = {}
    for row in run_rows:
        merged.setdefault(row.participant_id, []).append(
            ParticipantSearchActivity(
                kind="run",
                event_date=row.event_date,
                location_name=row.location_name,
                finish_time_display=row.finish_time_display,
                role=None,
            )
        )
    for row in vol_rows:
        merged.setdefault(row.participant_id, []).append(
            ParticipantSearchActivity(
                kind="volunteer",
                event_date=row.event_date,
                location_name=row.location_name,
                finish_time_display=None,
                role=row.role,
            )
        )
    return {
        participant_id: sorted(items, key=lambda a: a.event_date, reverse=True)[:RECENT_ACTIVITIES_LIMIT]
        for participant_id, items in merged.items()
    }


def load_run_stats(db: Session, participant_ids: list[UUID]) -> dict[UUID, tuple[int, date | None]]:
    rows = (
        db.query(
            RunResult.participant_id,
            func.count(RunResult.id),
            func.max(Event.event_date),
        )
        .join(Event, RunResult.event_id == Event.id)
        .filter(
            RunResult.participant_id.in_(participant_ids),
            Event.is_test_event.is_(False),
        )
        .group_by(RunResult.participant_id)
        .all()
    )
    return {participant_id: (count, last_date) for participant_id, count, last_date in rows}


def load_volunteering_counts(
    db: Session, candidates: list[tuple[Participant, Platform]]
) -> dict[UUID, int]:
    """Волонтёрств у каждого кандидата поиска.

    У parkrun считать строки нельзя: волонтёрская история приезжает из профиля
    атлета не событиями, а сводкой («Run Director (22×)»), и все её строки лежат
    на дате-заглушке 1970-01-01. Фильтр по дате отсекал их целиком, и карточка
    parkrun показывала «0 волонтёрств» человеку со 112 сменами (Дмитрий
    02.09.2026). Для parkrun берём тот же счётчик, что и кабинет, —
    count_parkrun_volunteering (parkrun называет это Total Credits).
    """
    participant_ids = [participant.id for participant, _ in candidates]
    if not participant_ids:
        return {}

    rows = (
        db.query(VolunteerResult.participant_id, func.count(VolunteerResult.id))
        .join(Event, VolunteerResult.event_id == Event.id)
        .filter(
            VolunteerResult.participant_id.in_(participant_ids),
            Event.is_test_event.is_(False),
            Event.event_date > date(1970, 1, 1),
        )
        .group_by(VolunteerResult.participant_id)
        .all()
    )
    counts: dict[UUID, int] = dict(rows)

    for participant, platform in candidates:
        if platform.code == PARKRUN_PLATFORM_CODE:
            counts[participant.id] = count_parkrun_volunteering(db, participant, platform.id)
    return counts


@dataclass(frozen=True)
class TopLocation:
    name: str
    city: str | None
    count: int
    last_date: date | None


def load_top_locations(
    db: Session,
    participant_ids: list[UUID],
    *,
    source: str = "run",
) -> dict[UUID, TopLocation]:
    """Локация, где у участника больше всего пробежек (source="run") или
    волонтёрств (source="volunteer"); при равенстве — где был последним.

    Волонтёрства parkrun на дате-заглушке 1970-01-01 (сводка ролей из профиля,
    а не события) не считаем: у них нет настоящей площадки, это псевдолокация
    «parkrun (сводка ролей)».
    """
    if not participant_ids:
        return {}
    model = RunResult if source == "run" else VolunteerResult
    query = (
        db.query(
            model.participant_id,
            Location.name,
            Location.city,
            func.count(model.id).label("items_count"),
            func.max(Event.event_date).label("last_date"),
        )
        .join(Event, model.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .filter(
            model.participant_id.in_(participant_ids),
            Event.is_test_event.is_(False),
        )
    )
    if model is VolunteerResult:
        query = query.filter(Event.event_date > date(1970, 1, 1))
    rows = query.group_by(model.participant_id, Location.name, Location.city).all()
    best: dict[UUID, TopLocation] = {}
    for participant_id, name, city, items_count, last_date in rows:
        current = best.get(participant_id)
        candidate = TopLocation(name=name, city=city, count=items_count, last_date=last_date)
        if current is None or (candidate.count, candidate.last_date or date.min) > (
            current.count,
            current.last_date or date.min,
        ):
            best[participant_id] = candidate
    return best


def _load_home_locations(db: Session, participant_ids: list[UUID]) -> dict[UUID, tuple[str, str | None]]:
    """Домашняя локация = где у участника больше всего пробежек (при равенстве — свежее)."""
    return {
        participant_id: (top.name, top.city)
        for participant_id, top in load_top_locations(db, participant_ids).items()
    }


def load_linked_owners(
    db: Session,
    candidates: list[tuple[Participant, Platform]],
) -> dict[UUID, UUID]:
    """participant_id -> user_id владельца привязки (по participant_id и по внешнему ключу)."""
    participant_ids = [participant.id for participant, _ in candidates]
    owners: dict[UUID, UUID] = {}
    rows = (
        db.query(PlatformLink.participant_id, PlatformLink.user_id)
        .filter(PlatformLink.participant_id.in_(participant_ids))
        .all()
    )
    for participant_id, user_id in rows:
        if participant_id is not None:
            owners[participant_id] = user_id

    # Привязка могла быть создана до появления строки участника — тогда
    # participant_id в ней пуст, но (platform_id, external_user_id) совпадает.
    unresolved = [
        (participant.platform_id, participant.external_user_id, participant.id)
        for participant, _ in candidates
        if participant.id not in owners
    ]
    if unresolved:
        external_ids = list({external_id for _, external_id, _ in unresolved})
        link_rows = (
            db.query(PlatformLink.platform_id, PlatformLink.external_user_id, PlatformLink.user_id)
            .filter(PlatformLink.external_user_id.in_(external_ids))
            .all()
        )
        by_identity = {(platform_id, external_id): user_id for platform_id, external_id, user_id in link_rows}
        for platform_id, external_id, participant_id in unresolved:
            owner = by_identity.get((platform_id, external_id))
            if owner is not None:
                owners[participant_id] = owner
    return owners
