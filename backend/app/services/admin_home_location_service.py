from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import Location, Platform, PlatformLink, User
from app.services.leaderboard_service import (
    _NO_RUN_DATE,
    PLATFORM_COLUMNS,
    _home_identity,
    _HomePickStats,
    _location_identity_maps,
)
from app.services.location_catalog_service import LocationCatalogIndex

# Домашняя площадка пачкой — для админки.
#
# Своей логики выбора здесь НЕТ: дом считается ровно тем же правилом, что в
# рейтинге дальности (_home_identity — три ступени кабинета: больше пробежек →
# больше волонтёрств → кто раньше начал; ручной выбор из настроек побеждает
# автоматику). Модуль нужен только затем, чтобы посчитать это пачкой — на сотню
# строк списка пользователей или на всех зарегистрированных сразу для среза
# «откуда люди»: кабинет считает по одному человеку, рейтинг — по всей базе.

# Пробежки по площадкам — считаем ДНЯМИ, как автовыбор в кабинете (две гонки в
# один день на одной площадке — один день). Выборка сужена участниками
# зарегистрированных пользователей, поэтому здесь нет parkrun-допуска из
# рейтингов (_PARKRUN_ELIGIBLE_CTE): его условие «есть привязка на сайте»
# у всех наших участников выполнено по определению, а полный проход по
# parkrun-протоколам ради заведомо истинного условия админке дорог.
_HOME_RUN_DAYS_SQL = """
SELECT
    rr.participant_id AS participant_id,
    e.location_id AS location_id,
    MIN(e.event_date) AS first_date,
    COUNT(DISTINCT e.event_date) AS run_days
FROM run_results rr
JOIN events e ON e.id = rr.event_id
LEFT JOIN event_crosslinks ec ON ec.secondary_event_id = e.id
WHERE e.is_test_event = false
  AND rr.participant_id = ANY(:pids)
  AND ec.secondary_event_id IS NULL
GROUP BY rr.participant_id, e.location_id
"""

# Волонтёрства — днями по той же причине. parkrun исключён: его волонтёрства
# приходят сводкой ролей на псевдоплощадку parkrun-summary с датой-заглушкой,
# площадки у них нет (так же поступает рейтинг дальности).
_HOME_VOLUNTEER_DAYS_SQL = """
SELECT
    vr.participant_id AS participant_id,
    e.location_id AS location_id,
    COUNT(DISTINCT e.event_date) AS volunteer_days
FROM volunteer_results vr
JOIN events e ON e.id = vr.event_id
JOIN platforms p ON p.id = e.platform_id
LEFT JOIN event_crosslinks ec ON ec.secondary_event_id = e.id
WHERE e.is_test_event = false
  AND vr.participant_id = ANY(:pids)
  AND p.code <> 'parkrun'
  AND ec.secondary_event_id IS NULL
GROUP BY vr.participant_id, e.location_id
"""


@dataclass(frozen=True)
class AdminHomeLocationCandidate:
    identity_key: str
    name: str
    city: str | None
    slug: str | None
    run_days: int
    volunteer_days: int


@dataclass(frozen=True)
class AdminHomeLocation:
    identity_key: str
    name: str
    city: str | None
    region: str | None
    slug: str | None
    run_days: int
    volunteer_days: int
    # Дом задан руками в настройках, а не вычислен.
    is_manual: bool
    # Все три ступени отбора дали ничью — дом по-настоящему не определён.
    # Рейтинг в этом случае разводит площадки по алфавиту (чтобы выбор не
    # «дышал» между пересчётами), но админке честнее показать весь список.
    is_tie: bool
    # Площадки, поделившие первое место. Заполняется только при ничьей;
    # у большинства участников список пуст.
    tied: list[AdminHomeLocationCandidate]
    # Сколько всего площадок у человека — видно, из чего выбирали.
    locations_total: int


def _display_geo_map(db: Session) -> dict[str, tuple[str | None, str | None]]:
    """identity-ключ площадки -> (город, регион) как их писать людям.

    В рейтингах гео нормализуется в ключи группировки (lowercase, страна вместо
    региона за рубежом) — для таблиц админки нужны исходные подписи. Правило
    выбора источника то же, что у _location_geo_map: данные берём у локации
    самой «старшей» системы (PLATFORM_COLUMNS), город и регион независимо друг
    от друга — у parkrun-строки их часто нет, а у её преемника в 5 вёрстах
    заполнено всё.
    """
    catalog_index = LocationCatalogIndex(db)
    rows = db.query(Location, Platform.code).join(Platform, Location.platform_id == Platform.id).all()
    priority_of = {code: index for index, code in enumerate(PLATFORM_COLUMNS)}
    city_candidates: dict[str, tuple[int, str]] = {}
    region_candidates: dict[str, tuple[int, str]] = {}
    for location, platform_code in rows:
        identity = catalog_index.canonical_identity_key(location, platform_code)
        priority = priority_of.get(platform_code, len(PLATFORM_COLUMNS))
        city = (location.city or "").strip()
        region = (location.region or "").strip()
        if city:
            current = city_candidates.get(identity)
            if current is None or priority < current[0]:
                city_candidates[identity] = (priority, city)
        if region:
            current = region_candidates.get(identity)
            if current is None or priority < current[0]:
                region_candidates[identity] = (priority, region)
    identities = set(city_candidates) | set(region_candidates)
    result: dict[str, tuple[str | None, str | None]] = {}
    for identity in identities:
        city_entry = city_candidates.get(identity)
        region_entry = region_candidates.get(identity)
        result[identity] = (
            city_entry[1] if city_entry is not None else None,
            region_entry[1] if region_entry is not None else None,
        )
    return result


def _participants_by_user(db: Session, user_ids: Sequence[UUID] | None) -> dict[UUID, UUID]:
    """participant_id -> user_id по привязкам платформ.

    Привязка без участника (participant_id IS NULL) в базе бывает: профиль ещё
    не синхронизировался. Пробежек у неё нет, считать дом не из чего.
    """
    query = db.query(PlatformLink.participant_id, PlatformLink.user_id).filter(
        PlatformLink.participant_id.isnot(None)
    )
    if user_ids is not None:
        query = query.filter(PlatformLink.user_id.in_(list(user_ids)))
    return {participant_id: user_id for participant_id, user_id in query.all()}


def _manual_home_keys(db: Session, user_ids: Sequence[UUID] | None) -> dict[UUID, str]:
    query = db.query(User.id, User.home_location_key).filter(User.home_location_key.isnot(None))
    if user_ids is not None:
        query = query.filter(User.id.in_(list(user_ids)))
    return {user_id: str(key) for user_id, key in query.all() if key}


def _pick_rank(entry: _HomePickStats) -> tuple[int, int, date]:
    """Ключ отбора дома — ровно тот же, что в рейтинге дальности, но без
    последней ступени «по алфавиту»: она разводит площадки, между которыми
    правило выбрать не смогло, и для поиска ничьей её надо отбросить."""
    return (-entry.run_days, -entry.volunteer_days, entry.first_run_date or _NO_RUN_DATE)


def _tied_identities(stats: dict[str, _HomePickStats]) -> list[str]:
    """Площадки, поделившие первое место по всем трём ступеням отбора.

    Одна площадка в списке — дом определён однозначно. Несколько — правило
    исчерпано (частый случай: по одной пробежке на каждой из многих площадок,
    и даты первых пробежек неизвестны), и админке показываем весь список.
    """
    if not stats:
        return []
    best = min(_pick_rank(entry) for entry in stats.values())
    return [identity for identity, entry in stats.items() if _pick_rank(entry) == best]


def resolve_admin_home_locations(
    db: Session, user_ids: Sequence[UUID] | None = None
) -> dict[UUID, AdminHomeLocation]:
    """Домашняя площадка каждого пользователя из списка (None — все сразу).

    В ответе только те, у кого в базе есть хоть одна пробежка или волонтёрство:
    у остальных дома нет вовсе, и это отдельное состояние, а не пустая строка.
    """
    participants = _participants_by_user(db, user_ids)
    if not participants:
        return {}

    pids = list(participants.keys())
    run_rows = db.execute(text(_HOME_RUN_DAYS_SQL), {"pids": pids}).all()
    volunteer_rows = db.execute(text(_HOME_VOLUNTEER_DAYS_SQL), {"pids": pids}).all()
    if not run_rows and not volunteer_rows:
        return {}

    identity_by_location, identity_names, identity_slugs = _location_identity_maps(db)
    geo_map = _display_geo_map(db)
    manual_keys = _manual_home_keys(db, user_ids)

    per_user: dict[UUID, dict[str, _HomePickStats]] = {}

    def bucket(participant_id: UUID, location_id: UUID) -> _HomePickStats | None:
        user_id = participants.get(participant_id)
        if user_id is None:
            return None
        identity = identity_by_location.get(location_id, str(location_id))
        return per_user.setdefault(user_id, {}).setdefault(identity, _HomePickStats())

    for participant_id, location_id, first_date, run_days in run_rows:
        entry = bucket(participant_id, location_id)
        if entry is not None:
            entry.add_runs(int(run_days), first_date)
    for participant_id, location_id, volunteer_days in volunteer_rows:
        entry = bucket(participant_id, location_id)
        if entry is not None:
            entry.volunteer_days += int(volunteer_days)

    def candidate(identity: str, entry: _HomePickStats) -> AdminHomeLocationCandidate:
        city, _region = geo_map.get(identity, (None, None))
        return AdminHomeLocationCandidate(
            identity_key=identity,
            name=identity_names.get(identity, identity),
            city=city,
            slug=identity_slugs.get(identity),
            run_days=entry.run_days,
            volunteer_days=entry.volunteer_days,
        )

    result: dict[UUID, AdminHomeLocation] = {}
    for user_id, stats in per_user.items():
        manual_key = manual_keys.get(user_id)
        is_manual = manual_key is not None and manual_key in stats
        home = _home_identity(stats, identity_names, manual_key)
        if home is None:
            continue
        tied = [] if is_manual else _tied_identities(stats)
        city, region = geo_map.get(home, (None, None))
        result[user_id] = AdminHomeLocation(
            identity_key=home,
            name=identity_names.get(home, home),
            city=city,
            region=region,
            slug=identity_slugs.get(home),
            run_days=stats[home].run_days,
            volunteer_days=stats[home].volunteer_days,
            is_manual=is_manual,
            is_tie=len(tied) > 1,
            tied=(
                sorted(
                    (candidate(identity, stats[identity]) for identity in tied),
                    key=lambda item: item.name.casefold(),
                )
                if len(tied) > 1
                else []
            ),
            locations_total=len(stats),
        )
    return result
