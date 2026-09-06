"""Публичная страница локации: резолв slug, сводные цифры, гистограмма, инфо-карточка.

Идентичность локации сквозь платформы — canonical_identity_key из
LocationCatalogIndex (кросслинки каталога). Публичный slug страницы —
external_key «основной» платформы идентичности (active_platform каталога,
иначе первая по PLATFORM_ORDER). Резолвер принимает external_key любой
платформы, в том числе в нормализованной форме (readovsky-park ≡ readovskypark).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, cast
from uuid import UUID

import redis
from sqlalchemy import and_, bindparam, case, func, or_, text
from sqlalchemy.orm import Session

from app.activity_url import resolve_activity_url
from app.core.redis_client import get_redis_client
from app.location_page_url import PLATFORM_ORDER, location_page_url
from app.models import (
    Event,
    EventCrosslink,
    EventSummary,
    Location,
    LocationCatalog,
    LocationDescription,
    Participant,
    Platform,
    PlatformLink,
    RunResult,
    User,
    VolunteerResult,
)
from app.services.home_distance_service import location_distance_from_home
from app.services.location_catalog_service import (
    LocationCatalogIndex,
    is_foreign_location,
    normalize_location_slug,
    normalize_platform_code,
    resolve_location_display_name,
)
from app.services.newcomer_counts import debutants_sum, location_guests_sum
from app.time_format import format_finish_time_display
from app.volunteer_role_taxonomy import (
    CANONICAL_ROLE_LABELS,
    canonical_volunteer_role,
    preset_role_keys,
)

HISTOGRAM_BIN_SEC = 10
# Индекс локаций — тяжёлая агрегация (~35 тыс. событий + join по протоколам
# на прод-объёме), а меняется только раз в неделю после субботнего синка.
# TTL, а не точечная инвалидация: синк идёт множеством независимых batch-джоб
# по трём платформам — вешать инвалидацию на каждую было бы куда инвазивнее,
# чем оправдывает выигрыш (эти цифры не обязаны быть live).
# v5 — в строку добавлены first_event_date_in_system и first_event_system_code:
# без бампа кэшированный payload отдавал бы их как None до истечения TTL.
LOCATIONS_INDEX_CACHE_KEY = "locations:index:v5"
LOCATIONS_INDEX_CACHE_TTL_SECONDS = 3 * 60 * 60
# Страница/журнал/рейтинги одной локации — тоже тяжёлые (resolve_location_identity
# перечитывает ВСЕ локации + весь каталог на каждый вызов, плюс десяток
# отдельных запросов на подсчёты). Тот же TTL-подход: синк раз в неделю,
# смысла в точечной инвалидации нет. Отдельно от индекса, потому что меняется
# per-slug, а не одним блобом на весь каталог.
LOCATION_PAGE_CACHE_TTL_SECONDS = 3 * 60 * 60
# Версии ключей держим здесь, а не по месту: писатели ушли на v5/v2, а
# invalidate_location_page_cache остался на v3/v1 — ручной сброс молча тёр
# несуществующие ключи, и страница жила до истечения TTL.


# «Как добраться?» с любым хвостом пунктуации — так эту врезку называет S95.
_TRAVEL_HEADING_RE = re.compile(r"\s*как\s+добраться\s*[?:.]*\s*", re.I)


def location_page_cache_key(slug: str) -> str:
    # v14 — в payload добавилась причина отмены ближайшего старта
    # (cancel_reason): без бампа страница до истечения TTL отдавала бы её как
    # None и плашка отмены осталась бы без текста.
    return f"locations:page:v14:{slug.strip().lower()}"


def location_events_cache_key(slug: str) -> str:
    return f"locations:events:v3:{slug.strip().lower()}"


def location_leaders_cache_key(slug: str) -> str:
    return f"locations:leaders:v3:{slug.strip().lower()}"


LOCATION_PARTICIPANTS_CACHE_PREFIX = "locations:participants:v3"


def location_participants_cache_key(slug: str, roles_key: str = "") -> str:
    # v3 — в строках появились platform_first_dates/platform_last_dates. Без
    # бампа страница до истечения TTL отдавала бы старый payload, и в срезе по
    # системе даты остались бы общими.
    #
    # roles_key — срез по волонтёрским ролям. Пресеты дают читаемый суффикс
    # (:ron_site), свой набор — хэш, как в рейтингах (см. normalize_role_filter
    # в leaderboard_service): ключей на пресеты немного, а произвольные наборы
    # редки и сами протухнут по TTL.
    base = f"{LOCATION_PARTICIPANTS_CACHE_PREFIX}:{slug.strip().lower()}"
    return f"{base}:r{roles_key}" if roles_key else base


def normalize_participant_role_filter(
    roles: Sequence[str] | None,
) -> tuple[frozenset[str] | None, str]:
    """Роли из запроса → (канонические ключи, суффикс кэша).

    Повторяет правила рейтингов: неизвестные ключи молча отбрасываются, пустой
    и полный набор — это «все роли», то есть фильтра нет вовсе. Так один и тот
    же список ролей всегда даёт один и тот же ключ кэша.
    """
    if not roles:
        return None, ""
    known = set(CANONICAL_ROLE_LABELS)
    selected = frozenset(role for role in roles if role in known)
    if not selected or selected == known:
        return None, ""
    for preset in ("on_site", "on_site_no_run", "remote"):
        if preset_role_keys(preset) == selected:
            return selected, preset
    digest = hashlib.sha1(",".join(sorted(selected)).encode()).hexdigest()[:10]
    return selected, f"c{digest}"


_ROLE_LABEL_MAP_CACHE_KEY = "locations:participants:role_labels:v1"
_ROLE_LABEL_MAP_TTL_SECONDS = 24 * 3600


def _volunteer_role_labels(db: Session, keys: frozenset[str]) -> list[str]:
    """Сырые ярлыки ролей, которые схлопываются в выбранные канонические ключи.

    Фильтр приходит в канонических ключах таксономии, а в volunteer_results
    лежат исходные названия систем («Сканирование штрих-кодов», «Barcode
    Scanning», «Сканер 25»). Приведение живёт в Python, поэтому в SQL уходит
    список ярлыков, а не ключей.

    Карта «ярлык → ключ» считается полным DISTINCT по volunteer_results и
    кэшируется на сутки: новые названия ролей появляются от силы раз в месяц,
    а проход по ярлыкам стоит заметно дороже самой страницы.
    """
    mapping: dict[str, str] | None = None
    try:
        cached = get_redis_client().get(_ROLE_LABEL_MAP_CACHE_KEY)
        if isinstance(cached, str):
            parsed = json.loads(cached)
            if isinstance(parsed, dict):
                mapping = {str(k): str(v) for k, v in parsed.items()}
    except (redis.RedisError, TypeError, ValueError):
        mapping = None

    if mapping is None:
        mapping = {}
        for (label,) in db.execute(
            text("SELECT DISTINCT role FROM volunteer_results WHERE role IS NOT NULL")
        ).all():
            canonical = canonical_volunteer_role(label)
            if canonical is not None:
                mapping[str(label)] = canonical.key
        _write_json_cache(
            _ROLE_LABEL_MAP_CACHE_KEY,
            cast(dict[str, object], mapping),
            _ROLE_LABEL_MAP_TTL_SECONDS,
        )

    return sorted(label for label, key in mapping.items() if key in keys)


# Порог «активного участника» страницы деталей — три участия, как в легаси-
# дашборде «Рейтинг участников и волонтёров внутри локации» (having count > 2).
# Случайный гость, забежавший раз в отпуске, в постоянный состав площадки не
# входит, а без отсечки список большой локации — это тысячи строк ни о чём.
LOCATION_ACTIVE_MIN_COUNT = 3

# Заглушки вместо имени: безымянного финишёра каждая система печатает по-своему,
# а s95 ещё и складывает всех таких в один аккаунт «unknown:НЕИЗВЕСТНЫЙ» — в
# рейтинге Кузьминок он занимал пятое место со 124 пробежками, хотя за строкой
# нет человека. Сравниваем имя ЦЕЛИКОМ, а не по подстроке: «Андрей НЕИЗВЕСТНЫХ»
# — настоящая фамилия, и такие строки остаются.
UNKNOWN_DISPLAY_NAMES = frozenset(
    {
        "неизвестный",
        "неизвестная",
        "неизвестный бегун",
        "неизвестный участник",
        "неизвестно",
        "неизвестен",
        "nepoznato",
        "unknown",
    }
)


def _identified_name_clause(name_expr: Any) -> Any:
    """Строка про живого человека, а не про заглушку протокола."""
    normalized = func.lower(func.trim(name_expr))
    return and_(
        name_expr.isnot(None),
        normalized != "",
        normalized.notin_(UNKNOWN_DISPLAY_NAMES),
        # «Unknown #67» — так 5 вёрст помечает строку со штрихкодом, но без имени.
        normalized.notlike("unknown #%"),
    )
# Незачётные статусы протоколов не влияют на finish_time (он у них NULL),
# поэтому отдельного фильтра по status нет: гистограмма и рекорды строятся
# только по строкам с известным временем.
# Якорим по всей строке, а не ищем подстроку: иначе из «М110-114» вытаскивалась
# бы «10–11» — группа, которой в протоколе нет. Границы трёхзначные: у
# участника с незаполненной датой рождения 5 вёрст считает абсурдный возраст и
# печатает «М110-114» или «М120». Такие группы показываем как есть — это то,
# что стоит в протоколе (решение Дмитрия 27.07.2026).
_AGE_RANGE_RE = re.compile(r"^[A-Za-zА-Яа-я]{0,3}(\d{1,3})\s*[-–—]\s*(\d{1,3})$")
_AGE_PLUS_RE = re.compile(r"^[A-Za-zА-Яа-я]{0,3}(\d{2,3})\s*\+$")
# Категория одним числом, без верхней границы. Таких у 5 вёрст ровно два вида,
# и оба перечислены в регулярке явно:
#
#   «М10»/«Ж10» («JM10» у parkrun-систем) — строго «младше 10»: десятилетний
#   бежит уже в «10-14». Обе категории идут параллельно (7 тыс. протоколов
#   содержат и ту, и другую), и все 718 участников, побывавших в обеих, сначала
#   бежали в «М10». Поэтому «<10», а не «10»: голое «10» рядом со строкой
#   «10–14» читалось бы как пересекающийся диапазон.
#
#   Трёхзначные «М120» — пометка участника без даты рождения: 5 вёрст считает
#   ему абсурдный возраст. Показываем как есть.
#
# Всё остальное двузначное («М11», «М12») категорией НЕ является: группы идут
# пятилетками, такой ступени не существует. Это обрезки старой регулярки
# парсера («М110-114» → «М11»), их чинит
# scripts/backfill_truncated_age_categories.py, а не витрина.
_AGE_UNDER_RE = re.compile(r"^[A-Za-zА-Яа-я]{1,3}(10|\d{3})$")


def _platform_order_index(code: str) -> int:
    try:
        return PLATFORM_ORDER.index(code)
    except ValueError:
        return len(PLATFORM_ORDER)


# Юниорская лестница RunPark — единственная, которая не ложится на общие
# ступени: JM11-14/JW11-14 и JM15-17/JW15-17 против «10–14» и «15–19» у
# 5 вёрст. В зачёте эти системы идут в одних ступенях (см. подпись под
# возрастными группами), поэтому «11–14» и «15–17» вылезали отдельными
# строками на 1–3 человека и выглядели ошибкой данных (Дмитрий 02.09.2026).
# Своих «11–14»/«15–17» больше нет ни у одной системы — сверено по базе.
_RUNPARK_JUNIOR_GROUPS = {"11–14": "10–14", "15–17": "15–19"}

# Правдоподобный потолок возраста для возрастных зачётов. «М120», «Ж105-109»,
# «М110-114» — обрезки старого парсера 5 вёрст (138 строк на базу), а не
# столетние бегуны; всё старше отсекается и в рейтингах, и в протоколах.
MAX_PLAUSIBLE_AGE = 100


def age_group_is_plausible(age_group: str) -> bool:
    return _age_group_sort_key(age_group)[0] <= MAX_PLAUSIBLE_AGE


def normalize_age_group(age_category: str | None) -> str | None:
    """«М18-24», «SM25-29», «VM35-39» → «18–24»; «М75+» → «75+»; «М10» → «<10»; «М110-114» → «110–114»."""
    if not age_category:
        return None
    cleaned = age_category.strip()
    match = _AGE_RANGE_RE.match(cleaned)
    if match:
        group = f"{int(match.group(1))}–{int(match.group(2))}"
        return _RUNPARK_JUNIOR_GROUPS.get(group, group)
    match = _AGE_PLUS_RE.match(cleaned)
    if match:
        return f"{int(match.group(1))}+"
    match = _AGE_UNDER_RE.match(cleaned)
    if match:
        return f"<{int(match.group(1))}"
    return None


@dataclass
class _PlatformStat:
    first_event_date: date
    last_event_date: date
    events_count: int


@dataclass
class LocationIdentity:
    identity_key: str
    catalog: LocationCatalog | None
    locations: list[tuple[Location, str]]  # (location, platform_code)
    slug: str
    name: str
    # Уже построенный на резолве идентичности — переиспользуем ниже вместо
    # повторного полного скана каталога (LocationCatalogIndex._load() — join
    # по всем location_catalog_links, дорого повторять в одном запросе).
    catalog_index: LocationCatalogIndex


def _identity_display_name(
    catalog: LocationCatalog | None,
    locations: list[tuple[Location, str]],
    catalog_index: LocationCatalogIndex,
) -> str:
    primary_location, primary_code = locations[0]
    return resolve_location_display_name(
        catalog,
        platform_code=primary_code,
        source_name=primary_location.name,
        active_platform_name=catalog_index.active_platform_name(catalog),
    )


def _sort_identity_locations(
    catalog: LocationCatalog | None,
    locations: list[tuple[Location, str]],
) -> list[tuple[Location, str]]:
    """Основная платформа идентичности первой: active_platform, потом PLATFORM_ORDER."""
    active = normalize_platform_code(catalog.active_platform) if catalog is not None else None

    def sort_key(item: tuple[Location, str]) -> tuple[int, int, str]:
        _location, code = item
        return (0 if code == active else 1, _platform_order_index(code), code)

    return sorted(locations, key=sort_key)


def _identity_status(
    catalog_index: LocationCatalogIndex,
    locations: list[tuple[Location, str]],
) -> tuple[bool, bool]:
    """(is_paused, is_cancelled) идентичности.

    Идентичность из одних parkrun-локаций — всегда «на паузе»: parkrun ушёл из
    России в 2022, но флагов паузы/закрытия у таких строк в данных нет (у
    локаций вне каталога — вообще никаких, у «Улица Голубая» каталог ошибочно
    активен), поэтому статус выводим из самого факта отсутствия преемника.
    """
    parkrun_only = all(code == "parkrun" for _location, code in locations)
    is_paused = parkrun_only or any(catalog_index.is_paused(location, code) for location, code in locations)
    is_cancelled = not parkrun_only and all(location.is_cancelled for location, _code in locations)
    return is_paused, is_cancelled


def _identity_cancel_reason(locations: list[tuple[Location, str]]) -> str | None:
    """Причина отмены ближайшего старта — первая непустая среди отменённых строк.

    Пишет её только s95; у 5 вёрст в реестре одна пометка «(отмена)» без текста,
    поэтому у большинства отмен причины не будет вовсе.
    """
    for location, _code in locations:
        if location.is_cancelled and (location.cancel_reason or "").strip():
            return location.cancel_reason.strip()
    return None


def _identity_country(locations: list[tuple[Location, str]]) -> str | None:
    """Страна идентичности. У parkrun-строк в БД country — всегда стаб
    «United Kingdom» (источник parkrun.org.uk); parkrun-only идентичности в
    выдаче по построению русские (см. is_foreign_location)."""
    if all(code == "parkrun" for _location, code in locations):
        return "Россия"
    return cast(str | None, _first_by_platform_order(locations, lambda loc: loc.country))


def resolve_location_identity(db: Session, slug: str) -> LocationIdentity | None:
    requested = (slug or "").strip().lower()
    if not requested:
        return None
    requested_normalized = normalize_location_slug(requested)

    rows = db.query(Location, Platform.code).join(Platform, Location.platform_id == Platform.id).all()
    catalog_index = LocationCatalogIndex(db)

    identity_locations: dict[str, list[tuple[Location, str]]] = {}
    matched_keys: list[str] = []
    for location, platform_code in rows:
        identity_key = catalog_index.canonical_identity_key(location, platform_code)
        identity_locations.setdefault(identity_key, []).append((location, platform_code))
        external = location.external_key.strip().lower()
        if external == requested or (
            requested_normalized and normalize_location_slug(external) == requested_normalized
        ):
            matched_keys.append(identity_key)

    if not matched_keys:
        return None

    # Один slug может встретиться в двух несвязанных идентичностях — берём ту,
    # у которой есть события, дальше по PLATFORM_ORDER лучшей платформы.
    # Фильтр по событиям решает коллизию вида «Шуваловский парк»: анонсированная,
    # но не запущенная 5 вёрст точка (0 событий) и закрытый parkrun с историей
    # делят один слаг, а связки в каталоге нет — без фильтра страница показывала
    # бы пустую идентичность вместо parkrun-истории.
    def identity_rank(key: str) -> int:
        return min(_platform_order_index(code) for _loc, code in identity_locations[key])

    matched = sorted(set(matched_keys), key=identity_rank)
    if len(matched) > 1:
        member_ids = [location.id for key in matched for location, _code in identity_locations[key]]
        locations_with_events = {
            row[0]
            for row in db.query(Event.location_id).filter(Event.location_id.in_(member_ids)).distinct()
        }

        def identity_has_events(key: str) -> bool:
            return any(location.id in locations_with_events for location, _code in identity_locations[key])

        matched.sort(key=lambda key: (not identity_has_events(key), identity_rank(key)))

    identity_key = matched[0]
    catalog = catalog_index.get_for_identity_key(identity_key)
    locations = _sort_identity_locations(catalog, identity_locations[identity_key])
    return LocationIdentity(
        identity_key=identity_key,
        catalog=catalog,
        locations=locations,
        slug=locations[0][0].external_key.strip().lower(),
        name=_identity_display_name(catalog, locations, catalog_index),
        catalog_index=catalog_index,
    )


def _gender_expression(
    platform_code_col: Any,
    participant_extra_col: Any,
    age_category_col: Any,
    participant_age_category_col: Any,
) -> Any:
    """SQL-выражение пола финишёра.

    five_verst/runpark — буква в age_category результата протокола. parkrun
    протокол своей age_category не даёт (в run_results там age-grade %, а не
    категория) — пол берётся из participants.age_category («SM30-34» —
    вторая буква; ~99.9% строк после бэкфилла профилей). s95 — из
    participants.profile_extra (см. gender_position_service).
    """
    return case(
        (
            platform_code_col == "five_verst",
            case(
                (func.substr(age_category_col, 1, 1) == "М", "male"),
                (func.substr(age_category_col, 1, 1) == "Ж", "female"),
                else_=None,
            ),
        ),
        (
            platform_code_col == "runpark",
            case(
                (func.substr(age_category_col, 2, 1) == "M", "male"),
                (func.substr(age_category_col, 2, 1) == "W", "female"),
                else_=None,
            ),
        ),
        (
            platform_code_col == "parkrun",
            case(
                (func.substr(participant_age_category_col, 2, 1) == "M", "male"),
                (func.substr(participant_age_category_col, 2, 1) == "W", "female"),
                else_=None,
            ),
        ),
        (
            platform_code_col == "s95",
            participant_extra_col["platform_codes"]["gender"].astext,
        ),
        else_=None,
    )


def _dedupe_crosslinked_events(db: Session, event_ids: list[UUID]) -> set[UUID]:
    """Убрать вторичные события кросслинков (один физический старт в двух протоколах)."""
    kept = set(event_ids)
    if not event_ids:
        return kept
    links = (
        db.query(EventCrosslink.primary_event_id, EventCrosslink.secondary_event_id)
        .filter(EventCrosslink.secondary_event_id.in_(event_ids))
        .all()
    )
    for primary_id, secondary_id in links:
        if primary_id in kept and secondary_id in kept:
            kept.discard(secondary_id)
    return kept


def _course_record(
    db: Session,
    event_ids: list[UUID],
    gender: str,
) -> dict[str, object] | None:
    gender_expr = _gender_expression(
        Platform.code, Participant.profile_extra, RunResult.age_category, Participant.age_category
    )
    row = (
        db.query(
            RunResult.finish_time_sec,
            Participant.display_name,
            Event.event_date,
            Platform.code,
        )
        .join(Event, RunResult.event_id == Event.id)
        .join(Platform, Event.platform_id == Platform.id)
        .outerjoin(Participant, RunResult.participant_id == Participant.id)
        .filter(
            RunResult.event_id.in_(event_ids),
            RunResult.finish_time_sec.isnot(None),
            RunResult.finish_time_sec > 0,
            gender_expr == gender,
        )
        .order_by(RunResult.finish_time_sec.asc())
        .first()
    )
    if row is None:
        return None
    finish_time_sec, display_name, event_date, platform_code = row
    return {
        "finish_time_sec": finish_time_sec,
        "finish_time_display": format_finish_time_display(finish_time_sec),
        "runner_name": display_name,
        "event_date": event_date,
        "platform_code": platform_code,
    }


def _histogram_rows(db: Session, event_ids: list[UUID]) -> list[dict[str, object]]:
    gender_expr = _gender_expression(
        Platform.code, Participant.profile_extra, RunResult.age_category, Participant.age_category
    )
    bin_expr = (RunResult.finish_time_sec // HISTOGRAM_BIN_SEC) * HISTOGRAM_BIN_SEC
    rows = (
        db.query(
            bin_expr.label("start_sec"),
            gender_expr.label("gender"),
            RunResult.age_category,
            func.count().label("count"),
        )
        .join(Event, RunResult.event_id == Event.id)
        .join(Platform, Event.platform_id == Platform.id)
        .outerjoin(Participant, RunResult.participant_id == Participant.id)
        .filter(
            RunResult.event_id.in_(event_ids),
            RunResult.finish_time_sec.isnot(None),
            RunResult.finish_time_sec > 0,
        )
        # Группируем по САМИМ выражениям, а не по строковым алиасам
        # "start_sec"/"gender": PostgreSQL при group-by по алиасу требует, чтобы
        # все колонки внутри выражения (platforms.code в CASE для gender) тоже
        # были в GROUP BY, и падал с GroupingError. Семантика та же.
        .group_by(bin_expr, gender_expr, RunResult.age_category)
        .all()
    )
    aggregated: dict[tuple[int, str | None, str | None], int] = {}
    for start_sec, gender, age_category, count in rows:
        if gender not in ("male", "female"):
            gender = None
        key = (int(start_sec), gender, normalize_age_group(age_category))
        aggregated[key] = aggregated.get(key, 0) + int(count)
    return [
        {"start_sec": start_sec, "gender": gender, "age_group": age_group, "count": count}
        for (start_sec, gender, age_group), count in sorted(aggregated.items(), key=lambda item: item[0][0])
    ]


def _location_event_ids(db: Session, location_ids: list[UUID]) -> list[UUID]:
    """ID всех нетестовых событий идентичности с дедупом кросслинков."""
    rows = db.query(Event.id).filter(Event.location_id.in_(location_ids), Event.is_test_event.is_(False)).all()
    all_ids = [row[0] for row in rows]
    kept = _dedupe_crosslinked_events(db, all_ids)
    return [event_id for event_id in all_ids if event_id in kept]


def build_location_leaders(
    db: Session, slug: str, *, limit: int = 20, use_cache: bool = True, refresh: bool = False
) -> dict[str, object] | None:
    cache_key = location_leaders_cache_key(slug)
    if use_cache and not refresh:
        cached = _read_json_cache(cache_key)
        if cached is not None:
            return cached

    payload = _compute_location_leaders(db, slug, limit=limit)

    if use_cache and payload is not None:
        _write_json_cache(cache_key, payload, LOCATION_PAGE_CACHE_TTL_SECONDS)
    return payload


def _platform_link_join() -> Any:
    """Тот же принцип, что в admin_users_service._participant_join: надёжный ключ
    между participants и platform_links — (platform_id, external_user_id),
    а не platform_links.participant_id (не всегда проставлен)."""
    return and_(
        PlatformLink.platform_id == Participant.platform_id,
        PlatformLink.external_user_id == Participant.external_user_id,
    )


def _compute_location_leaders(db: Session, slug: str, *, limit: int = 20) -> dict[str, object] | None:
    """Рейтинги внутри локации: топ по пробежкам и топ по волонтёрствам.

    Перенос смысла Grafana-дашборда «Рейтинг участников и волонтёров внутри
    локации». Один человек с единым профилем на сайте (привязал аккаунты
    нескольких систем через platform_links) считается ОДНОЙ строкой суммарно
    по всем платформам — группировка идёт по coalesce(platform_links.user_id,
    participant_id). Без привязки объединить разные participants разных
    систем нельзя (это правда разные внешние аккаунты) — такие остаются
    отдельными строками.
    """
    identity = resolve_location_identity(db, slug)
    if identity is None:
        return None
    event_ids = _location_event_ids(db, [location.id for location, _code in identity.locations])

    runners: list[dict[str, object]] = []
    volunteers: list[dict[str, object]] = []
    if event_ids:
        time_ok = RunResult.finish_time_sec.isnot(None) & (RunResult.finish_time_sec > 0)
        display_name = func.max(func.coalesce(User.display_name, Participant.display_name))
        runner_group_key = func.coalesce(PlatformLink.user_id, RunResult.participant_id)
        runner_rows = (
            db.query(
                display_name.label("name"),
                func.count(func.distinct(RunResult.event_id)).label("runs"),
                func.min(case((time_ok, RunResult.finish_time_sec))).label("best"),
                func.max(User.public_slug).label("slug"),
                func.max(User.serial_id).label("serial_id"),
            )
            .join(Participant, RunResult.participant_id == Participant.id)
            .outerjoin(PlatformLink, _platform_link_join())
            .outerjoin(User, PlatformLink.user_id == User.id)
            .filter(RunResult.event_id.in_(event_ids))
            .group_by(runner_group_key)
            .having(_identified_name_clause(display_name))
            .order_by(func.count(func.distinct(RunResult.event_id)).desc(), display_name.asc())
            .limit(limit)
            .all()
        )
        runners = [
            {
                "name": name,
                "runs_count": int(runs),
                "best_time_sec": int(best) if best is not None else None,
                "best_time_display": format_finish_time_display(int(best)) if best is not None else None,
                "handle": slug or (str(serial_id) if serial_id else None),
            }
            for name, runs, best, slug, serial_id in runner_rows
        ]
        volunteer_group_key = func.coalesce(PlatformLink.user_id, VolunteerResult.participant_id)
        volunteer_rows = (
            db.query(
                display_name.label("name"),
                func.count(func.distinct(VolunteerResult.event_id)).label("events"),
                func.max(User.public_slug).label("slug"),
                func.max(User.serial_id).label("serial_id"),
            )
            .join(Participant, VolunteerResult.participant_id == Participant.id)
            .outerjoin(PlatformLink, _platform_link_join())
            .outerjoin(User, PlatformLink.user_id == User.id)
            .filter(VolunteerResult.event_id.in_(event_ids))
            .group_by(volunteer_group_key)
            .having(_identified_name_clause(display_name))
            .order_by(func.count(func.distinct(VolunteerResult.event_id)).desc(), display_name.asc())
            .limit(limit)
            .all()
        )
        volunteers = [
            {
                "name": name,
                "count": int(events),
                "handle": slug or (str(serial_id) if serial_id else None),
            }
            for name, events, slug, serial_id in volunteer_rows
        ]

    return {
        "slug": identity.slug,
        "name": identity.name,
        "runners": runners,
        "volunteers": volunteers,
    }


LOCATION_ATTENDANCE_PAGE_LIMIT = 50
LOCATION_ATTENDANCE_MAX_LIMIT = 100
LOCATION_ATTENDANCE_KINDS = ("all", "runners", "volunteers")


def location_attendance_cache_key(slug: str, year: int, kind: str, offset: int, limit: int) -> str:
    # v2 — в строках появились month_totals. Без бампа страница до истечения
    # TTL отдавала бы payload без них, и «Всего» в срезе месяца считалось бы
    # по клеткам (у закрытого профиля их нет — вышел бы ноль).
    return (
        f"locations:attendance:v2:{slug.strip().lower()}:{year}:{kind}:o{offset}:l{limit}"
    )


@dataclass
class _AttendancePerson:
    name: str | None = None
    user_name: str | None = None
    handle: str | None = None
    private: bool = False
    run_dates: set[date] = field(default_factory=set)
    # дата -> роли волонтёрства в этот день (канонические ярлыки).
    vol_roles: dict[date, set[str]] = field(default_factory=dict)

    @property
    def display_name(self) -> str | None:
        return self.user_name or self.name

    @property
    def dates(self) -> set[date]:
        return self.run_dates | set(self.vol_roles)


def _attendance_rows_query(db: Session, event_ids: list[UUID], result_model: Any) -> list[Any]:
    """Сырые строки протоколов года: кто, когда, (роль). Группировка по
    человеку — та же, что в рейтингах локации: coalesce(platform_links.user_id,
    participant_id) склеивает привязанные аккаунты в одну строку."""
    group_key = func.coalesce(PlatformLink.user_id, result_model.participant_id)
    columns = [
        group_key.label("gkey"),
        Participant.display_name.label("name"),
        User.display_name.label("user_name"),
        User.public_slug.label("public_slug"),
        User.serial_id.label("serial_id"),
        User.profile_private.label("private"),
        Event.event_date.label("event_date"),
    ]
    if result_model is VolunteerResult:
        columns.append(VolunteerResult.role.label("role"))
    return (
        db.query(*columns)
        .select_from(result_model)
        .join(Event, result_model.event_id == Event.id)
        .join(Participant, result_model.participant_id == Participant.id)
        .outerjoin(PlatformLink, _platform_link_join())
        .outerjoin(User, PlatformLink.user_id == User.id)
        .filter(result_model.event_id.in_(event_ids))
        .all()
    )


def _collect_attendance_people(
    db: Session, event_ids: list[UUID]
) -> dict[object, _AttendancePerson]:
    people: dict[object, _AttendancePerson] = {}

    def person_for(row: Any) -> _AttendancePerson:
        person = people.setdefault(row.gkey, _AttendancePerson())
        if row.name and not person.name:
            person.name = str(row.name)
        if row.user_name and not person.user_name:
            person.user_name = str(row.user_name)
        if row.private:
            person.private = True
        # Хендл — только у открытых профилей: тот же принцип, что у публичных
        # ссылок протокола (public_serial в протоколах страницы локации).
        if not row.private and person.handle is None:
            handle = row.public_slug or (str(row.serial_id) if row.serial_id else None)
            person.handle = handle
        return person

    for row in _attendance_rows_query(db, event_ids, RunResult):
        person_for(row).run_dates.add(row.event_date)
    for row in _attendance_rows_query(db, event_ids, VolunteerResult):
        person = person_for(row)
        roles = person.vol_roles.setdefault(row.event_date, set())
        canonical = canonical_volunteer_role(row.role)
        if canonical is not None:
            roles.add(canonical.label)
    return people


def _attendance_dates(person: _AttendancePerson, kind: str) -> set[date]:
    """Дни человека в выбранном разрезе журнала."""
    if kind == "runners":
        return set(person.run_dates)
    if kind == "volunteers":
        return set(person.vol_roles)
    return person.dates


def _attendance_row_payload(
    person: _AttendancePerson, *, me: bool = False, kind: str = "all"
) -> dict[str, object]:
    """Строка журнала в выбранном разрезе.

    kind — это именно СРЕЗ, а не отбор строк: в «Бегунах» клетки показывают
    только пробежки, и «Всего» считает их же. Никого при этом не выбрасываем —
    человек, который и бежал, и волонтёрил, остаётся в журнале в обоих
    разрезах, просто теряет заливку «чужой» роли (правки Дмитрия 28.08.2026).
    Раньше же фильтр лишь выбрасывал тех, у кого совсем нет пробежек (или
    волонтёрств), а клетки оставались прежними — и переключатель почти ничего
    не менял: у большинства есть и то, и другое.
    """
    dates = _attendance_dates(person, kind)
    items: list[dict[str, object]] = []
    if not person.private or me:
        for day in sorted(dates, reverse=True):
            items.append(
                {
                    "date": day.isoformat(),
                    "run": kind != "volunteers" and day in person.run_dates,
                    "roles": [] if kind == "runners" else sorted(person.vol_roles.get(day, ())),
                }
            )
    month_totals: dict[str, int] = {}
    for day in dates:
        key = day.strftime("%Y-%m")
        month_totals[key] = month_totals.get(key, 0) + 1
    return {
        "name": person.display_name,
        "handle": person.handle,
        "private": person.private,
        "year_total": len(dates),
        # Счёт по месяцам считаем всегда, в том числе закрытому профилю: items
        # ему не отдаём, а колонку «Всего» в срезе месяца заполнить нужно.
        "month_totals": month_totals,
        "runs_total": len(person.run_dates),
        "volunteering_total": len(person.vol_roles),
        "items": items,
    }


def build_location_attendance(
    db: Session,
    slug: str,
    *,
    year: int | None = None,
    kind: str = "all",
    offset: int = 0,
    limit: int = LOCATION_ATTENDANCE_PAGE_LIMIT,
    viewer_user_id: UUID | None = None,
    use_cache: bool = True,
) -> dict[str, object] | None:
    """Журнал посещаемости локации: участники × даты стартов выбранного года.

    Перенос Grafana-дашборда «Журнал посещаемости локации», но в одном журнале
    сразу и бегуны, и волонтёры: клетка даты различает пробежку, волонтёрство
    и «и то и другое». Фильтр kind сужает СТРОКИ (кто попал в таблицу), клетки
    строки всегда показывают обе роли.
    """
    if kind not in LOCATION_ATTENDANCE_KINDS:
        kind = "all"
    limit = max(1, min(limit, LOCATION_ATTENDANCE_MAX_LIMIT))
    offset = max(0, offset)

    # Быстрый путь анонима: ключ на ЗАПРОШЕННЫЙ slug (год 0 = «последний»),
    # чтобы попадание в кэш не тянуло резолв идентичности (он перечитывает все
    # локации и каталог). Залогиненному нужен блок «Вы» — ему полный путь.
    requested_key = location_attendance_cache_key(slug, year or 0, kind, offset, limit)
    if use_cache and viewer_user_id is None:
        cached = _read_json_cache(requested_key)
        if cached is not None:
            return dict(cached, me=None)

    identity = resolve_location_identity(db, slug)
    if identity is None:
        return None
    location_ids = [location.id for location, _code in identity.locations]
    event_rows = (
        db.query(Event.id, Event.event_date, Platform.code)
        .join(Platform, Event.platform_id == Platform.id)
        .filter(Event.location_id.in_(location_ids), Event.is_test_event.is_(False))
        .all()
    )
    kept = _dedupe_crosslinked_events(db, [row[0] for row in event_rows])
    event_rows = [row for row in event_rows if row[0] in kept]

    years = sorted({row[1].year for row in event_rows}, reverse=True)
    if not years:
        return {
            "slug": identity.slug,
            "name": identity.name,
            "year": year or date.today().year,
            "years": [],
            "kind": kind,
            "offset": offset,
            "limit": limit,
            "total_rows": 0,
            "columns": [],
            "date_totals": {},
            "rows": [],
            "me": None,
        }
    if year is None or year not in years:
        year = years[0]

    cache_key = location_attendance_cache_key(identity.slug, year, kind, offset, limit)
    payload: dict[str, object] | None = None
    if use_cache:
        payload = _read_json_cache(cache_key)

    year_events = [row for row in event_rows if row[1].year == year]
    year_event_ids = [row[0] for row in year_events]
    people: dict[object, _AttendancePerson] | None = None

    if payload is None:
        people = _collect_attendance_people(db, year_event_ids)

        columns_by_date: dict[date, set[str]] = {}
        for _event_id, event_date, platform_code in year_events:
            columns_by_date.setdefault(event_date, set()).add(platform_code)
        columns = [
            {"date": day.isoformat(), "platforms": sorted(codes)}
            for day, codes in sorted(columns_by_date.items(), reverse=True)
        ]

        # Итоговая строка «Участников»: посещаемость каждого старта читается
        # прямо из журнала — бегуны и волонтёры считаются по всем людям года,
        # а не по видимой странице.
        # `people` — люди без повторов: кто в один день и бежал, и волонтёрил,
        # попадает и в runners, и в volunteers, поэтому для разреза «Все»
        # runners+volunteers завышали бы число людей — держим отдельный
        # дедуплицированный счётчик.
        date_totals: dict[str, dict[str, int]] = {
            day.isoformat(): {"runners": 0, "volunteers": 0, "people": 0}
            for day in columns_by_date
        }
        for person in people.values():
            for day in person.run_dates:
                totals = date_totals.get(day.isoformat())
                if totals is not None:
                    totals["runners"] += 1
            for day in person.vol_roles:
                totals = date_totals.get(day.isoformat())
                if totals is not None:
                    totals["volunteers"] += 1
            for day in person.dates:
                totals = date_totals.get(day.isoformat())
                if totals is not None:
                    totals["people"] += 1

        # Состав строк от разреза НЕ зависит: в журнале остаются все, кто был
        # здесь в этом году (правка Дмитрия 28.08.2026 — «не должен исключать
        # тех, кто и бежал, и волонтёрил»). Разрез меняет заливку клеток и счёт,
        # а порядок ставит наверх самых активных именно в нём: у кого в разрезе
        # пусто, тот честно уезжает вниз, но из журнала не пропадает.
        selected = sorted(
            people.values(),
            key=lambda person: (
                -len(_attendance_dates(person, kind)),
                -len(person.dates),
                person.display_name or "",
            ),
        )
        page = selected[offset : offset + limit]

        payload = {
            "slug": identity.slug,
            "name": identity.name,
            "year": year,
            "years": years,
            "kind": kind,
            "offset": offset,
            "limit": limit,
            "total_rows": len(selected),
            "columns": columns,
            "date_totals": date_totals,
            "rows": [_attendance_row_payload(person, kind=kind) for person in page],
        }
        if use_cache:
            _write_json_cache(cache_key, payload, LOCATION_PAGE_CACHE_TTL_SECONDS)
            # Продублировать под ключом запроса (год «последний», slug как в
            # адресе) — иначе быстрый путь анонима никогда бы не попадал в кэш.
            if requested_key != cache_key:
                _write_json_cache(requested_key, payload, LOCATION_PAGE_CACHE_TTL_SECONDS)

    result = dict(payload)
    result["me"] = (
        _viewer_location_attendance(db, year_event_ids, viewer_user_id, people, kind)
        if viewer_user_id is not None
        else None
    )
    return result


def _viewer_location_attendance(
    db: Session,
    year_event_ids: list[UUID],
    viewer_user_id: UUID,
    people: dict[object, _AttendancePerson] | None,
    kind: str = "all",
) -> dict[str, object] | None:
    """Строка «Вы» журнала локации — даже когда страница пришла из кэша.

    Если агрегация года уже посчитана в этом запросе, берём готовую; иначе —
    точечные выборки только по участникам зрителя.
    """
    if people is not None:
        person = people.get(viewer_user_id)
        return _attendance_row_payload(person, me=True, kind=kind) if person is not None else None

    participant_ids = [
        row[0]
        for row in db.query(PlatformLink.participant_id)
        .filter(PlatformLink.user_id == viewer_user_id, PlatformLink.participant_id.isnot(None))
        .all()
    ]
    if not participant_ids or not year_event_ids:
        return None
    person = _AttendancePerson()
    run_rows = (
        db.query(Event.event_date)
        .select_from(RunResult)
        .join(Event, RunResult.event_id == Event.id)
        .filter(
            RunResult.event_id.in_(year_event_ids),
            RunResult.participant_id.in_(participant_ids),
        )
        .all()
    )
    for (event_date,) in run_rows:
        person.run_dates.add(event_date)
    vol_rows = (
        db.query(Event.event_date, VolunteerResult.role)
        .select_from(VolunteerResult)
        .join(Event, VolunteerResult.event_id == Event.id)
        .filter(
            VolunteerResult.event_id.in_(year_event_ids),
            VolunteerResult.participant_id.in_(participant_ids),
        )
        .all()
    )
    for event_date, role in vol_rows:
        roles = person.vol_roles.setdefault(event_date, set())
        canonical = canonical_volunteer_role(role)
        if canonical is not None:
            roles.add(canonical.label)
    if not _attendance_dates(person, kind):
        return None
    return _attendance_row_payload(person, me=True, kind=kind)


def build_location_participants(
    db: Session,
    slug: str,
    *,
    use_cache: bool = True,
    refresh: bool = False,
    roles: Sequence[str] | None = None,
) -> dict[str, object] | None:
    role_filter, roles_key = normalize_participant_role_filter(roles)
    cache_key = location_participants_cache_key(slug, roles_key)
    if use_cache and not refresh:
        cached = _read_json_cache(cache_key)
        if cached is not None:
            return cached

    if role_filter is None:
        payload = _compute_location_participants(db, slug)
    else:
        # Бегунов роли не касаются, а считаются они дороже всего (см. докстринг
        # _active_participant_rows). Поэтому в срезе по ролям берём их готовыми
        # из основного payload'а — он к этому моменту почти всегда уже в кэше,
        # его греет locations.warm_cache — и пересчитываем только волонтёров.
        base = build_location_participants(db, slug, use_cache=use_cache, refresh=refresh)
        payload = None if base is None else _volunteers_by_roles(db, slug, base, role_filter)

    if use_cache and payload is not None:
        _write_json_cache(cache_key, payload, LOCATION_PAGE_CACHE_TTL_SECONDS)
    return payload


def _volunteers_by_roles(
    db: Session, slug: str, base: dict[str, object], role_filter: frozenset[str]
) -> dict[str, object] | None:
    """Тот же payload, но волонтёрский зачёт пересчитан по выбранным ролям.

    Считаются только выходы в этих ролях — и в колонке «Волонтёрств», и в
    «Всего», и в знаменателе «N из M». Порог в три участия применяется уже
    после фильтра: в строгом зачёте «только на площадке» человек с двумя
    маршальствами и десятком удалённых ролей постоянным составом не считается.
    """
    identity = resolve_location_identity(db, slug)
    if identity is None:
        return None
    event_ids = _location_event_ids(db, [location.id for location, _code in identity.locations])
    test_event_ids = [row[0] for row in db.query(Event.id).filter(Event.is_test_event.is_(True)).all()]
    volunteers, volunteers_people = _active_participant_rows(
        db,
        VolunteerResult,
        event_ids,
        test_event_ids=test_event_ids,
        role_labels=_volunteer_role_labels(db, role_filter),
    )
    payload = dict(base)
    payload["volunteers"] = volunteers
    payload["volunteers_people_total"] = volunteers_people
    return payload


def _compute_location_participants(db: Session, slug: str) -> dict[str, object] | None:
    """Постоянный состав локации: все, кто был здесь от трёх раз.

    Развёрнутая версия топ-20 со страницы локации (_compute_location_leaders).
    Отличий два: нет лимита (вместо него отсечка LOCATION_ACTIVE_MIN_COUNT) и
    к числу участий здесь добавлены три колонки легаси-дашборда — сколько у
    человека участий во всех локациях вместе (видно, домашняя это площадка
    или заезжий турист) и границы стажа здесь (первый и последний старт).

    Склейка платформ та же, что в рейтингах локации: строка — это профиль
    сайта (coalesce(platform_links.user_id, participant_id)), непривязанные
    аккаунты разных систем остаются разными строками.
    """
    identity = resolve_location_identity(db, slug)
    if identity is None:
        return None
    event_ids = _location_event_ids(db, [location.id for location, _code in identity.locations])

    # Список тестовых событий один на оба зачёта — читаем его здесь, а не
    # дважды внутри.
    test_event_ids = [row[0] for row in db.query(Event.id).filter(Event.is_test_event.is_(True)).all()]
    runners, runners_people = _active_participant_rows(db, RunResult, event_ids, test_event_ids=test_event_ids)
    volunteers, volunteers_people = _active_participant_rows(
        db, VolunteerResult, event_ids, test_event_ids=test_event_ids
    )

    # Системы этой площадки — по ним фильтр. У одиночной системы фильтр
    # показывать незачем, поэтому фронт смотрит на длину списка.
    platform_codes = sorted({code for _location, code in identity.locations}, key=_platform_order_index)

    return {
        "slug": identity.slug,
        "name": identity.name,
        "min_count": LOCATION_ACTIVE_MIN_COUNT,
        "platform_codes": platform_codes,
        "runners": runners,
        "volunteers": volunteers,
        # Знаменатель для подписи «показаны N из M побывавших здесь»: без него
        # не видно, какую долю состава площадки составляют постоянные.
        "runners_people_total": runners_people,
        "volunteers_people_total": volunteers_people,
    }


def _active_participant_rows(
    db: Session,
    model: type[RunResult] | type[VolunteerResult],
    event_ids: list[UUID],
    *,
    test_event_ids: list[UUID],
    min_count: int = LOCATION_ACTIVE_MIN_COUNT,
    role_labels: list[str] | None = None,
) -> tuple[list[dict[str, object]], int]:
    """Строки постоянного состава по одному зачёту (пробежки или волонтёрства).

    Три запроса: группа по локации, участники этих групп (включая привязанные
    аккаунты, которые здесь ни разу не бегали) и «всего участий» по ним.

    Почему «всего» нельзя посчитать тем же group by, что и участия здесь:
    фильтр по событиям локации отсекает как раз то, что нужно сложить.
    Отдельный запрос по всем событиям намеренно сделан голым — только
    run_results/volunteer_results по participant_id (это индекс
    ix_*_participant_event), без join к participants, platform_links и events.
    Первая версия тащила туда всю связку джойнов и считала большую площадку
    минутами: на «всего» приходятся сотни тысяч строк, и каждый join умножал
    их. Склейка аккаунтов в профиль сайта делается после, в Python.

    Следствие голого запроса: кросслинки (один физический старт в протоколах
    двух систем) в «всего» не схлопываются — у человека с аккаунтами в обеих
    системах такой старт даст +2. Случай редкий, а глобальный дедуп стоил бы
    обхода всех событий страны на каждую локацию.
    """
    if not event_ids:
        return [], 0

    # Срез по волонтёрским ролям: в зачёт идут только выходы в выбранных ролях.
    # Пустой список ярлыков (роли есть в таксономии, но в данных не встречались)
    # честно даёт пустой зачёт, а не «все роли». У пробежек ролей нет вовсе,
    # поэтому фильтр к ним не то что не применяется — он и приехать не может.
    role_clause = None
    if role_labels is not None:
        if model is not VolunteerResult:
            raise ValueError("Фильтр ролей применим только к волонтёрскому зачёту")
        role_clause = VolunteerResult.role.in_(role_labels)

    group_key = func.coalesce(PlatformLink.user_id, model.participant_id)
    display_name = func.max(func.coalesce(User.display_name, Participant.display_name))
    here_count = func.count(func.distinct(model.event_id))

    # Без HAVING: те, кто не дотянул до порога, всё равно нужны — по ним
    # считается знаменатель «показаны N из M побывавших здесь». Отдельный
    # count(distinct) обошёлся бы во второй такой же проход по протоколам.
    local_rows = (
        db.query(
            group_key.label("group_key"),
            # max() по uuid в Postgres нет — признак «строка про профиль
            # сайта» берём булевым агрегатом, сам user_id и так в group_key.
            func.bool_or(PlatformLink.user_id.isnot(None)).label("is_user"),
            display_name.label("name"),
            func.max(Participant.display_name).label("protocol_name"),
            func.bool_or(func.coalesce(User.profile_private, False)).label("private"),
            here_count.label("count"),
            func.min(Event.event_date).label("first_date"),
            func.max(Event.event_date).label("last_date"),
            func.max(User.public_slug).label("slug"),
            func.max(User.serial_id).label("serial_id"),
        )
        .select_from(model)
        .join(Event, model.event_id == Event.id)
        .join(Participant, model.participant_id == Participant.id)
        .outerjoin(PlatformLink, _platform_link_join())
        .outerjoin(User, PlatformLink.user_id == User.id)
        .filter(model.event_id.in_(event_ids))
        .filter(*([] if role_clause is None else [role_clause]))
        .group_by(group_key)
        # Заглушки вместо имени отсекаем здесь, а не в Python: они не люди и не
        # должны попадать ни в список, ни в знаменатель «показаны N из M».
        .having(_identified_name_clause(display_name))
        .all()
    )
    people_total = len(local_rows)

    # Участия в разрезе систем: нужны фильтру «Система» на странице состава.
    # Отдельным запросом, а не в основном: там группировка по человеку, и
    # добавление платформы в GROUP BY размножило бы строки.
    platform_counts: dict[Any, dict[str, int]] = {}
    # Границы стажа тоже в разрезе систем: при выбранной системе колонки
    # «Первый/Последний старт» обязаны показывать её даты, а не общие. У
    # Швейцарии, где до 5 вёрст был RunPark, в срезе «5 вёрст» стоял
    # 13.05.2023 — дата из RunPark (Дмитрий 03.09.2026).
    platform_first: dict[Any, dict[str, Any]] = {}
    platform_last: dict[Any, dict[str, Any]] = {}
    for gkey, platform_code, count, first_date, last_date in (
        db.query(
            group_key.label("group_key"),
            Platform.code,
            func.count(func.distinct(model.event_id)),
            func.min(Event.event_date),
            func.max(Event.event_date),
        )
        .select_from(model)
        .join(Event, model.event_id == Event.id)
        .join(Platform, Event.platform_id == Platform.id)
        .join(Participant, model.participant_id == Participant.id)
        .outerjoin(PlatformLink, _platform_link_join())
        .filter(model.event_id.in_(event_ids))
        .filter(*([] if role_clause is None else [role_clause]))
        .group_by(group_key, Platform.code)
        .all()
    ):
        code = str(platform_code)
        platform_counts.setdefault(gkey, {})[code] = int(count)
        if first_date is not None:
            platform_first.setdefault(gkey, {})[code] = first_date
        if last_date is not None:
            platform_last.setdefault(gkey, {})[code] = last_date

    active = [row for row in local_rows if int(row.count) >= min_count]
    active.sort(key=lambda row: (-int(row.count), (row.name or "").lower()))
    if not active:
        return [], people_total

    # Участники группы: у профиля сайта это все привязанные аккаунты, включая
    # те, которыми человек здесь ни разу не отмечался.
    user_ids = [row.group_key for row in active if row.is_user]
    participant_to_group: dict[UUID, UUID] = {
        row.group_key: row.group_key for row in active if not row.is_user
    }
    if user_ids:
        linked = (
            db.query(Participant.id, PlatformLink.user_id)
            .join(PlatformLink, _platform_link_join())
            .filter(PlatformLink.user_id.in_(user_ids))
            .all()
        )
        for participant_id, user_id in linked:
            participant_to_group[participant_id] = user_id

    totals = _totals_by_group(
        db, model, participant_to_group, test_event_ids, role_labels=role_labels
    )

    rows: list[dict[str, object]] = []
    place = 0
    previous_count: int | None = None
    for index, row in enumerate(active):
        count = int(row.count)
        # Место по-спортивному: равное число участий — равное место, следующий
        # за парой третьих получает пятое.
        if count != previous_count:
            place = index + 1
            previous_count = count
        # Закрытый профиль — как в рейтингах и журнале: без ссылки на профиль
        # сайта и с протокольным именем вместо имени аккаунта.
        rows.append(
            {
                "place": place,
                "name": (row.protocol_name or row.name) if row.private else row.name,
                "handle": None
                if row.private
                else row.slug or (str(row.serial_id) if row.serial_id else None),
                "count": count,
                # max() — страховка от кросслинков: «всего» не может быть
                # меньше, чем участий на одной этой площадке.
                "total_count": max(totals.get(row.group_key, count), count),
                "first_date": row.first_date.isoformat() if row.first_date else None,
                "last_date": row.last_date.isoformat() if row.last_date else None,
                # Сколько раз человек был здесь в каждой системе — по этим
                # числам фильтр «Система» пересобирает список и места.
                "platform_counts": platform_counts.get(row.group_key, {}),
                "platform_first_dates": {
                    code: value.isoformat()
                    for code, value in platform_first.get(row.group_key, {}).items()
                },
                "platform_last_dates": {
                    code: value.isoformat()
                    for code, value in platform_last.get(row.group_key, {}).items()
                },
            }
        )
    return rows, people_total


def _totals_by_group(
    db: Session,
    model: type[RunResult] | type[VolunteerResult],
    participant_to_group: dict[UUID, UUID],
    test_event_ids: list[UUID],
    *,
    role_labels: list[str] | None = None,
) -> dict[UUID, int]:
    """Участий во всех локациях — по каждому профилю страницы.

    Считается по participant_id и складывается в Python: см. комментарий в
    _active_participant_rows о том, почему запрос держим без join'ов.

    role_labels сужает и «всего»: если колонка «Волонтёрств» показывает только
    выбранные роли, то и доля «здесь» обязана считаться от них же — иначе у
    маршала, который в других ролях объездил полстраны, доля вышла бы смешной
    и необъяснимой.
    """
    participant_ids = list(participant_to_group)
    if not participant_ids:
        return {}

    # Тестовые события (проверочные протоколы) исключаем списком id, а не
    # join'ом к events: их считанные сотни, а join развернул бы весь этот
    # запрос обратно в тяжёлый.
    counted_event: Any = model.event_id
    if test_event_ids:
        counted_event = case((model.event_id.in_(test_event_ids), None), else_=model.event_id)

    query = db.query(model.participant_id, func.count(func.distinct(counted_event))).filter(
        model.participant_id.in_(participant_ids)
    )
    if role_labels is not None:
        query = query.filter(VolunteerResult.role.in_(role_labels))
    rows = query.group_by(model.participant_id).all()

    totals: dict[UUID, int] = {}
    for participant_id, count in rows:
        group = participant_to_group.get(participant_id)
        if group is None:
            continue
        totals[group] = totals.get(group, 0) + int(count)
    return totals


def _start_point_url(latitude: float | None, longitude: float | None) -> str | None:
    if latitude is None or longitude is None:
        return None
    return f"https://yandex.ru/maps/?pt={longitude},{latitude}&z=16&l=map"


def _first_by_platform_order(
    locations: list[tuple[Location, str]],
    getter: Callable[[Location], object | None],
) -> object | None:
    for location, _code in locations:
        value = getter(location)
        if value is not None:
            return value
    return None


# Клубные пороги 5 вёрст/parkrun (футболки за 10/25/50/100/250 финишей) плюс
# «круглые» юбилеи кратно 25 — те самые, которые локации поздравляют в чатах.
_CLUB_LEVELS = (10, 25, 50, 100, 250)
# Секундомер secondary-событий кросслинков не должен удваивать счёт финишей.
_NOT_SECONDARY_SQL = "id NOT IN (SELECT secondary_event_id FROM event_crosslinks)"


def _last_event_winners(db: Session, event_id: UUID) -> dict[str, str | None]:
    """Имена самого быстрого мужчины и самой быстрой женщины на старте.

    Локации в постах всегда называют победителей поимённо («18:07 — Алексей
    САЙФУЛИН»), поэтому имя нужно рядом со временем.
    """
    gender_expr = _gender_expression(
        Platform.code, Participant.profile_extra, RunResult.age_category, Participant.age_category
    )
    winners: dict[str, str | None] = {"male": None, "female": None}
    for gender in ("male", "female"):
        row = (
            db.query(Participant.display_name)
            .select_from(RunResult)
            .join(Event, RunResult.event_id == Event.id)
            .join(Platform, Event.platform_id == Platform.id)
            .outerjoin(Participant, RunResult.participant_id == Participant.id)
            .filter(
                RunResult.event_id == event_id,
                RunResult.finish_time_sec.isnot(None),
                RunResult.finish_time_sec > 0,
                gender_expr == gender,
            )
            .order_by(RunResult.finish_time_sec.asc())
            .first()
        )
        if row is not None and row[0]:
            winners[gender] = str(row[0])
    return winners


def _last_event_milestones(
    db: Session, event_id: UUID, event_date: date
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Кто на этом старте закрыл клубный порог и кому остался один финиш.

    Счёт ведётся внутри платформы: participants привязаны к платформе, поэтому
    «клуб 25 финишей» у 5 вёрст не смешивается с parkrun. Логика порогов — та
    же, что в админском «Своде по пробежке» (admin_event_report_service).
    """
    rows = db.execute(
        text(
            f"""
            WITH today AS (
                SELECT DISTINCT participant_id FROM run_results
                WHERE event_id = :event_id AND participant_id IS NOT NULL
            ),
            counts AS (
                SELECT r.participant_id, count(DISTINCT r.event_id) AS total
                FROM run_results r
                JOIN events e ON e.id = r.event_id
                WHERE r.participant_id IN (SELECT participant_id FROM today)
                  AND NOT e.is_test_event
                  AND e.event_date <= :event_date
                  AND e.{_NOT_SECONDARY_SQL}
                GROUP BY r.participant_id
            )
            SELECT c.total, p.display_name
            FROM counts c
            JOIN participants p ON p.id = c.participant_id
            WHERE c.total IN :levels
               OR (c.total >= 25 AND c.total % 25 = 0)
               OR (c.total + 1) IN :levels
            ORDER BY c.total DESC
            """
        ).bindparams(bindparam("levels", expanding=True)),
        {"event_id": str(event_id), "event_date": event_date, "levels": list(_CLUB_LEVELS)},
    ).all()

    milestones: list[dict[str, object]] = []
    one_step: list[dict[str, object]] = []
    for total, name in rows:
        total = int(total)
        if not name:
            continue
        if total in _CLUB_LEVELS or (total >= 25 and total % 25 == 0):
            milestones.append({"name": str(name), "count": total})
        elif (total + 1) in _CLUB_LEVELS:
            one_step.append({"name": str(name), "next": total + 1})
    return milestones[:5], one_step[:5]


def _last_event_stats(
    db: Session,
    events: Sequence[Any],
    event_ids: list[UUID],
    event_finishers: Callable[[UUID, int | None], int | None],
    avg_finish_time_sec: int | None,
    median_finish_time_sec: int | None,
) -> tuple[dict[str, object] | None, int | None, int | None]:
    """Метрики последнего старта + дельта, на которую он сдвинул средние/медиану.

    Дельта — «после минус до»: считаем avg/медиану заново без последнего
    старта и сравниваем с уже посчитанными по всем событиям значениями.
    Отрицательная дельта — последний старт был быстрее среднего/типичного.
    """
    if not events:
        return None, None, None

    last_event_id, last_event_date, last_event_number, last_finishers_count, last_platform_code = max(
        events, key=lambda row: row[1]
    )

    gender_expr = _gender_expression(
        Platform.code, Participant.profile_extra, RunResult.age_category, Participant.age_category
    )
    time_ok = RunResult.finish_time_sec.isnot(None) & (RunResult.finish_time_sec > 0)
    row = (
        db.query(
            func.count().label("finishers"),
            func.avg(case((time_ok, RunResult.finish_time_sec))).label("avg_time"),
            func.min(case((time_ok & (gender_expr == "male"), RunResult.finish_time_sec))).label("best_male"),
            func.min(case((time_ok & (gender_expr == "female"), RunResult.finish_time_sec))).label("best_female"),
            # Те же метрики, что в журнале протоколов: дебютанты платформы,
            # гости площадки (впервые здесь, но в системе не впервые), ЛР.
            debutants_sum().label("debutants"),
            location_guests_sum().label("first_here"),
            func.sum(case((RunResult.is_pr.is_(True), 1), else_=0)).label("prs"),
            # Разбивка по полу: локации пишут её в постах «в цифрах»
            # («57 мужчин, 33 девушки, 4 неизвестных»).
            func.sum(case((gender_expr == "male", 1), else_=0)).label("male_count"),
            func.sum(case((gender_expr == "female", 1), else_=0)).label("female_count"),
        )
        .select_from(RunResult)
        .join(Event, RunResult.event_id == Event.id)
        .join(Platform, Event.platform_id == Platform.id)
        .outerjoin(Participant, RunResult.participant_id == Participant.id)
        .filter(RunResult.event_id == last_event_id)
        .first()
    )
    last_avg_time = int(row.avg_time) if row is not None and row.avg_time is not None else None
    last_best_male = int(row.best_male) if row is not None and row.best_male is not None else None
    last_best_female = int(row.best_female) if row is not None and row.best_female is not None else None
    last_debutants = int(row.debutants or 0) if row is not None else None
    last_first_here = int(row.first_here or 0) if row is not None else None
    last_prs = int(row.prs or 0) if row is not None else None

    last_volunteers = (
        db.query(func.count(func.distinct(VolunteerResult.participant_id)))
        .filter(VolunteerResult.event_id == last_event_id, VolunteerResult.participant_id.isnot(None))
        .scalar()
    )

    winners = _last_event_winners(db, last_event_id)
    milestones, one_step = _last_event_milestones(db, last_event_id, last_event_date)
    male_count = int(row.male_count or 0) if row is not None else 0
    female_count = int(row.female_count or 0) if row is not None else 0

    last_event_payload = {
        "event_date": last_event_date,
        "event_number": int(last_event_number) if last_event_number is not None else None,
        "platform_code": last_platform_code,
        "male_finishers": male_count or None,
        "female_finishers": female_count or None,
        "best_male_name": winners.get("male"),
        "best_female_name": winners.get("female"),
        # Юбиляры клубных порогов на этом старте и те, кому до клуба остался
        # один финиш, — самый частый жанр в каналах локаций.
        "milestones": milestones,
        "one_step": one_step,
        "finishers": event_finishers(last_event_id, last_finishers_count),
        "volunteers": int(last_volunteers) if last_volunteers else None,
        "avg_time_sec": last_avg_time,
        "avg_time_display": format_finish_time_display(last_avg_time) if last_avg_time is not None else None,
        "best_male_time_sec": last_best_male,
        "best_male_time_display": format_finish_time_display(last_best_male) if last_best_male is not None else None,
        "best_female_time_sec": last_best_female,
        "best_female_time_display": (
            format_finish_time_display(last_best_female) if last_best_female is not None else None
        ),
        "debutants": last_debutants,
        "first_at_location": last_first_here,
        "prs": last_prs,
    }

    before_event_ids = [event_id for event_id in event_ids if event_id != last_event_id]
    avg_delta: int | None = None
    median_delta: int | None = None
    if before_event_ids:
        before_avg = (
            db.query(func.avg(RunResult.finish_time_sec))
            .filter(RunResult.event_id.in_(before_event_ids), time_ok)
            .scalar()
        )
        if before_avg is not None and avg_finish_time_sec is not None:
            avg_delta = avg_finish_time_sec - int(before_avg)

        before_median = (
            db.query(func.percentile_cont(0.5).within_group(RunResult.finish_time_sec))
            .filter(RunResult.event_id.in_(before_event_ids), time_ok)
            .scalar()
        )
        if before_median is not None and median_finish_time_sec is not None:
            median_delta = median_finish_time_sec - int(before_median)

    return last_event_payload, avg_delta, median_delta


# Не якорим на начало: у «<10» впереди знак, и с якорем группа улетала бы
# в конец таблицы вместо первой строки.
_AGE_GROUP_SORT_RE = re.compile(r"\d+")


def _age_group_sort_key(age_group: str) -> tuple[int, int]:
    """Порядок групп в таблице: по возрасту, а при равном — «<N» перед «N–…».

    «<10» и «10–14» дают одно и то же число, но это разные ступени и в
    протоколе они идут параллельно, так что порядок между ними фиксируем.
    """
    match = _AGE_GROUP_SORT_RE.search(age_group)
    age = int(match.group(0)) if match else 999
    if age_group.startswith("<"):
        return (age, 0)
    if age_group.endswith("+"):
        return (age, 2)
    return (age, 1)


# Возрастные группы считаем только по 5 вёрст — единственной системе, которая
# публикует возрастной диапазон в протоколе («М35-39»). У parkrun в
# run_results.age_category лежит age grade («54.38%»), s95 категорию не отдаёт
# вовсе, а RunPark пишет parkrun-коды («VM40-44»): формально они разбираются,
# но подмешивать вторую систему в топ площадки мы не хотим (решение Дмитрия
# 26.07.2026). Раньше фильтр был «всё, кроме parkrun», и RunPark тихо попадал
# в «Рекорды по возрастным группам» вопреки подсказке «только по 5 вёрст» —
# 29.7 тыс. строк на 49 локациях.
FIVE_VERST_PLATFORM_CODE = "five_verst"
AGE_GROUP_TOP_LIMIT = 5
# Топ группы собираем по СЫРЫМ категориям протоколов, а потом сливаем в
# нормализованную группу: в «10–14» попадают и «М10-14», и «М11-14». Берём с
# запасом — у каждой сырой категории пятёрка своя, и после слияния часть строк
# уходит вниз, иначе итоговая пятёрка могла бы недосчитаться.
_AGE_GROUP_TOP_FETCH = AGE_GROUP_TOP_LIMIT * 2


def age_group_key(gender: str, age_group: str) -> str:
    """Ключ строки возрастной группы: («male», «30–34») → «male-30-34».

    Служит и якорем в разметке: плитка «место в группе» из блока «Вы на этой
    локации» ссылается по нему на нужную строку в «Рекордах по возрастным
    группам», где под спойлером лежит топ-5 этой группы.
    """
    return f"{gender}-{age_group.replace('–', '-').replace('+', 'plus').replace('<', 'under')}"


def _protocol_age_category_gender() -> Any:
    """Пол по возрастной категории протокола 5 вёрст: «М35-39» → male.

    Ровно ветка five_verst из _gender_expression. Остальные там читают
    participants, а здесь недостижимы: выборки возрастных групп ограничены
    5 вёрст. Зато без join к participants выборка по крупной площадке
    укладывается в 0.3с вместо 2.4с — 66 тыс. случайных обращений в таблицу
    на 1.2 млн строк были самой дорогой её частью.
    """
    return case(
        (func.substr(RunResult.age_category, 1, 1) == "М", "male"),
        (func.substr(RunResult.age_category, 1, 1) == "Ж", "female"),
        else_=None,
    )


def _age_group_match_clauses(age_groups: Iterable[str]) -> list[Any]:
    """SQL-условия «сырая категория протокола попадает в эту группу».

    Зеркалят normalize_age_group в обратную сторону: в «30–34» ложится всё, где
    есть подстрока «30-34» с любым из трёх тире («М30-34», «Ж30-34»). Нужны,
    чтобы посчитать место участника одним
    запросом, не вычитывая перед этим все категории локации (такой скан по
    Затюменскому стоит четверть секунды сам по себе).
    """
    clauses: list[Any] = []
    for age_group in age_groups:
        if age_group.endswith("+"):
            clauses.append(RunResult.age_category.like(f"%{age_group}%"))
            continue
        if age_group.startswith("<"):
            # «<10» — это категория, которая числом и заканчивается («М10»);
            # «М10-14» под шаблон не подойдёт, она кончается на «14».
            clauses.append(RunResult.age_category.like(f"%{age_group[1:]}"))
            continue
        low, _, high = age_group.partition("–")
        clauses.extend(RunResult.age_category.like(f"%{low}{dash}{high}%") for dash in ("-", "–", "—"))
    return clauses


def _age_group_runner_bests(
    db: Session,
    event_ids: list[UUID],
    *,
    age_groups: Iterable[str] | None = None,
) -> Any:
    """CTE «лучшее время каждого участника в каждой сырой категории».

    Уникальность — по participants.id, без слияния привязанных аккаунтов по
    platform_links.user_id, как в общих рейтингах локации: выборка и так
    ограничена одной системой, а join к participants и platform_links на все
    протокольные строки крупной площадки стоит секунды. Имена подтягиваются
    потом и только к строкам, которые реально показываем.
    """
    time_ok = RunResult.finish_time_sec.isnot(None) & (RunResult.finish_time_sec > 0)
    gender_expr = _protocol_age_category_gender()
    filters = [
        RunResult.event_id.in_(event_ids),
        RunResult.participant_id.isnot(None),
        time_ok,
        Platform.code == FIVE_VERST_PLATFORM_CODE,
        RunResult.age_category.isnot(None),
        RunResult.age_category != "",
    ]
    if age_groups is not None:
        filters.append(or_(*_age_group_match_clauses(age_groups)))
    return (
        db.query(
            gender_expr.label("gender"),
            RunResult.age_category.label("age_category"),
            RunResult.participant_id.label("runner_id"),
            func.min(RunResult.finish_time_sec).label("best_sec"),
        )
        .join(Event, RunResult.event_id == Event.id)
        .join(Platform, Event.platform_id == Platform.id)
        .filter(*filters)
        .group_by(gender_expr, RunResult.age_category, RunResult.participant_id)
        .cte("age_group_runner_bests")
    )


def _merge_age_group_bests(rows: Iterable[Any]) -> dict[tuple[str, str], list[dict[str, object]]]:
    """Сырые категории → нормализованные группы, по одной строке на участника.

    Возвращает списки, отсортированные по времени, с проставленным спортивным
    местом: равное лучшее время делит одно место.
    """
    merged: dict[tuple[str, str], dict[UUID, int]] = {}
    for row in rows:
        if row.gender not in ("male", "female"):
            continue
        age_group = normalize_age_group(row.age_category)
        if age_group is None:
            continue
        runners = merged.setdefault((row.gender, age_group), {})
        best_sec = int(row.best_sec)
        if row.runner_id not in runners or best_sec < runners[row.runner_id]:
            runners[row.runner_id] = best_sec

    result: dict[tuple[str, str], list[dict[str, object]]] = {}
    for key, runners in merged.items():
        ordered = sorted(runners.items(), key=lambda item: (item[1], str(item[0])))
        place = 0
        previous_sec: int | None = None
        rows_out: list[dict[str, object]] = []
        for index, (runner_id, best_sec) in enumerate(ordered, start=1):
            if best_sec != previous_sec:
                place = index
                previous_sec = best_sec
            rows_out.append({"runner_id": runner_id, "best_time_sec": best_sec, "place": place})
        result[key] = rows_out
    return result


def _participant_display_names(
    db: Session, participant_ids: Iterable[UUID]
) -> dict[UUID, tuple[str | None, str | None]]:
    """participants.id → (имя, хендл профиля на сайте) для показываемых строк.

    Закрытые профили (`users.profile_private`) отдаются как неавторизованным:
    протокольное имя и без хендла — тот же принцип, что в рейтингах
    (fastest_rating_service) и журнале посещаемости.
    """
    ids = list(participant_ids)
    if not ids:
        return {}
    rows = (
        db.query(
            Participant.id,
            func.coalesce(User.display_name, Participant.display_name).label("name"),
            Participant.display_name.label("protocol_name"),
            User.profile_private.label("private"),
            User.public_slug,
            User.serial_id,
        )
        .outerjoin(PlatformLink, _platform_link_join())
        .outerjoin(User, PlatformLink.user_id == User.id)
        .filter(Participant.id.in_(ids))
        .all()
    )
    result: dict[UUID, tuple[str | None, str | None]] = {}
    for row in rows:
        if row.private:
            result[row.id] = (row.protocol_name or row.name, None)
        else:
            result[row.id] = (
                row.name,
                row.public_slug or (str(row.serial_id) if row.serial_id else None),
            )
    return result


def _age_group_tops(
    db: Session, event_ids: list[UUID], *, limit: int = AGE_GROUP_TOP_LIMIT
) -> dict[tuple[str, str], list[dict[str, object]]]:
    """Топ-N в каждой возрастной группе локации: участники и их лучшее время.

    Считается по тем же строкам, что и рекорд группы, поэтому первая строчка
    топа всегда совпадает с рекордом в той же таблице.
    """
    if not event_ids:
        return {}
    bests = _age_group_runner_bests(db, event_ids)
    row_number = func.row_number().over(
        partition_by=(bests.c.gender, bests.c.age_category),
        order_by=(bests.c.best_sec.asc(), bests.c.runner_id.asc()),
    )
    ranked = db.query(bests, row_number.label("row_number")).select_from(bests).subquery()
    rows = db.query(ranked).filter(ranked.c.row_number <= _AGE_GROUP_TOP_FETCH).all()

    merged = {key: runners[:limit] for key, runners in _merge_age_group_bests(rows).items()}
    names = _participant_display_names(
        db, {cast(UUID, runner["runner_id"]) for runners in merged.values() for runner in runners}
    )
    tops: dict[tuple[str, str], list[dict[str, object]]] = {}
    for key, runners in merged.items():
        tops[key] = [
            {
                "place": runner["place"],
                "name": names.get(cast(UUID, runner["runner_id"]), (None, None))[0],
                "handle": names.get(cast(UUID, runner["runner_id"]), (None, None))[1],
                "best_time_sec": runner["best_time_sec"],
                "best_time_display": format_finish_time_display(cast(int, runner["best_time_sec"])),
            }
            for runner in runners
        ]
    return tops


def _age_group_totals(db: Session, event_ids: list[UUID]) -> dict[tuple[str, str], tuple[int, int]]:
    """Размер каждой возрастной группы: (участников, финишей).

    Место без знаменателя не читается: «#35» выглядит слабо, пока не видно, что
    это 35-е из 54. Тот же итог показываем и над топ-5, чтобы плитка и таблица
    сходились.

    Агрегат считает БД, наружу приезжает по строке на сырую категорию — на
    порядки меньше, чем выборка лучших времён каждого участника, которой
    пользуется топ-5. Нормализуем и складываем уже здесь.
    """
    if not event_ids:
        return {}
    gender_expr = _protocol_age_category_gender()
    time_ok = RunResult.finish_time_sec.isnot(None) & (RunResult.finish_time_sec > 0)
    rows = (
        db.query(
            gender_expr.label("gender"),
            RunResult.age_category.label("age_category"),
            func.count(func.distinct(RunResult.participant_id)).label("runners"),
            func.count().label("finishes"),
        )
        .join(Event, RunResult.event_id == Event.id)
        .join(Platform, Event.platform_id == Platform.id)
        .filter(
            RunResult.event_id.in_(event_ids),
            RunResult.participant_id.isnot(None),
            time_ok,
            Platform.code == FIVE_VERST_PLATFORM_CODE,
            RunResult.age_category.isnot(None),
            RunResult.age_category != "",
        )
        .group_by(gender_expr, RunResult.age_category)
        .all()
    )

    totals: dict[tuple[str, str], tuple[int, int]] = {}
    for row in rows:
        if row.gender not in ("male", "female"):
            continue
        age_group = normalize_age_group(row.age_category)
        if age_group is None:
            continue
        key = (row.gender, age_group)
        runners, finishes = totals.get(key, (0, 0))
        totals[key] = (runners + int(row.runners), finishes + int(row.finishes))
    return totals


def _age_group_records(db: Session, event_ids: list[UUID]) -> list[dict[str, object]]:
    """Рекорды локации по возрастным группам: лучшее время в каждой группе М/Ж.

    Вместе с рекордом каждая группа несёт свой топ-5 (_age_group_tops) — он
    раскрывается спойлером прямо в этой таблице, и на него же ссылаются
    личные плитки «место в группе» из блока «Вы на этой локации».

    Считается только по 5 вёрст (FIVE_VERST_PLATFORM_CODE) — см. комментарий
    у константы. Нормализация в Python (normalize_age_group) дополнительно
    отбрасывает всё, что не приводится к группе; из БД берём лучшую строку на каждую СЫРУЮ категорию
    (DISTINCT ON), затем минимум на группу.
    """
    if not event_ids:
        return []
    gender_expr = _gender_expression(
        Platform.code, Participant.profile_extra, RunResult.age_category, Participant.age_category
    )
    time_ok = RunResult.finish_time_sec.isnot(None) & (RunResult.finish_time_sec > 0)
    rows = (
        db.query(
            gender_expr.label("gender"),
            RunResult.age_category,
            RunResult.finish_time_sec,
            func.coalesce(User.display_name, Participant.display_name).label("display_name"),
            Event.event_date,
            Platform.code.label("platform_code"),
            # Профиль на нашем сайте, если участник привязал систему: рекордсмен
            # становится кликабельным (просьба Дмитрия 26.07.2026).
            User.serial_id.label("runner_serial_id"),
            User.public_slug.label("runner_slug"),
        )
        .join(Event, RunResult.event_id == Event.id)
        .join(Platform, Event.platform_id == Platform.id)
        .outerjoin(Participant, RunResult.participant_id == Participant.id)
        .outerjoin(PlatformLink, _platform_link_join())
        .outerjoin(User, PlatformLink.user_id == User.id)
        .filter(
            RunResult.event_id.in_(event_ids),
            time_ok,
            Platform.code == FIVE_VERST_PLATFORM_CODE,
            RunResult.age_category.isnot(None),
            RunResult.age_category != "",
        )
        .distinct(gender_expr, RunResult.age_category)
        .order_by(
            gender_expr,
            RunResult.age_category,
            RunResult.finish_time_sec.asc(),
            Event.event_date.asc(),
        )
        .all()
    )

    best: dict[tuple[str, str], dict[str, object]] = {}
    for (
        gender,
        age_category,
        finish_time_sec,
        runner_name,
        event_date,
        platform_code,
        runner_serial_id,
        runner_slug,
    ) in rows:
        if gender not in ("male", "female"):
            continue
        age_group = normalize_age_group(age_category)
        if age_group is None:
            continue
        key = (gender, age_group)
        current = best.get(key)
        if current is None or int(finish_time_sec) < int(current["finish_time_sec"]):  # type: ignore[arg-type]
            best[key] = {
                "key": age_group_key(gender, age_group),
                "gender": gender,
                "age_group": age_group,
                "finish_time_sec": int(finish_time_sec),
                "finish_time_display": format_finish_time_display(int(finish_time_sec)),
                "runner_name": runner_name,
                "event_date": event_date,
                "platform_code": platform_code,
                "runner_handle": runner_slug or (str(runner_serial_id) if runner_serial_id else None),
            }

    tops = _age_group_tops(db, event_ids)
    totals = _age_group_totals(db, event_ids)
    for key, record in best.items():
        record["top"] = tops.get(key, [])
        runners_total, finishes_total = totals.get(key, (0, 0))
        record["runners_total"] = runners_total
        record["finishes_total"] = finishes_total

    return sorted(
        best.values(),
        key=lambda item: (
            0 if item["gender"] == "male" else 1,
            _age_group_sort_key(str(item["age_group"])),
        ),
    )



def _same_city_locations(
    db: Session, identity_key: str, city: str | None
) -> list[dict[str, object]]:
    """Другие площадки того же города — кластер под запрос «5 вёрст [город]».

    Такой запрос (у «5 верст тюмень» 868 показов/мес) не про одну площадку:
    человеку нужен весь город. Взаимные ссылки собирают страницы города в
    кластер и для поисковика, и для навигации. Данные — из кэшированного
    индекса каталога; список полный (решение Дмитрия 06.08.2026: город должен
    быть виден целиком, даже московские три десятка), по алфавиту — в чипах
    без явного порядка сортировка по стартам читалась как случайная.

    Только действующие площадки: паузы отсеиваются наравне с отменёнными —
    «Severny Rechnoy Vokzal» стоит на паузе с 2019-го, и в списке «куда ещё
    сходить в Москве» ему делать нечего (репорт Дмитрия 06.08.2026).
    """
    city_norm = (city or "").strip().casefold()
    if not city_norm:
        return []
    items = cast("list[dict[str, object]]", build_locations_index(db).get("items") or [])
    same = [
        item
        for item in items
        if str(item.get("city") or "").strip().casefold() == city_norm
        and item.get("identity_key") != identity_key
        and not item.get("is_cancelled")
        and not item.get("is_paused")
    ]
    same.sort(key=lambda item: str(item.get("name") or "").casefold())
    return [
        {
            "slug": item.get("slug"),
            "name": item.get("name"),
            "events_count": item.get("events_count") or 0,
        }
        for item in same
        if item.get("slug")
    ]


def _travel_section_title(raw: object) -> str | None:
    """Заголовок способа добраться — или None, если он повторяет заголовок блока.

    У 5 вёрст секции названы способами («Пешком», «На автомобиле»), а у S95 вся
    врезка называется «Как добраться» — ровно так же, как наш подзаголовок над
    ней. Без этой отсечки на странице выходило «Как добраться» два раза подряд.
    """

    title = str(raw or "").strip()
    if not title or _TRAVEL_HEADING_RE.fullmatch(title):
        return None
    return title


def _description_payload(
    db: Session,
    ordered_platforms: Sequence[tuple[Location, str]],
) -> dict[str, object] | None:
    """Описание площадки первой системы, у которой оно есть.

    Порядок тот же, что у таймлайна платформ: сначала действующая система,
    потом исторические. Два описания одной площадки рядом (5 вёрст и S95 пишут
    об одном парке каждый своё) читателю не помогают, а поиску выглядят как
    дубль текста, поэтому берём одно.
    """

    location_ids = [location.id for location, _code in ordered_platforms]
    if not location_ids:
        return None

    rows = {
        row.location_id: row
        for row in db.query(LocationDescription).filter(LocationDescription.location_id.in_(location_ids)).all()
    }
    for location, platform_code in ordered_platforms:
        row = rows.get(location.id)
        if row is None or not (row.schedule_text or row.course_text or row.travel_text or row.travel_sections):
            continue
        # Действующее время старта — с учётом сезонных окон расписания. Многие
        # живут в парадигме «все стартуют в 9:00», поэтому фронт подсвечивает
        # время, когда оно другое (решение Дмитрия 23.08.2026).
        from app.services.location_schedule_service import current_start_time_label

        start_time_current = current_start_time_label(row.schedule_parsed, date.today())
        return {
            "platform_code": platform_code,
            "schedule_text": row.schedule_text,
            "start_time_current": start_time_current,
            "start_schedule": row.schedule_parsed or [],
            "course_text": row.course_text,
            "travel_text": row.travel_text,
            "travel_sections": [
                {"title": _travel_section_title(section.get("title")), "text": section.get("text")}
                for section in (row.travel_sections or [])
                if section.get("text")
            ],
            "links": [
                {"title": link.get("title"), "url": link.get("url")}
                for link in (row.links or [])
                if link.get("url")
            ],
            "source_url": row.source_url,
            "updated_at": row.content_updated_at.isoformat() if row.content_updated_at else None,
        }
    return None


def build_location_page(
    db: Session, slug: str, *, use_cache: bool = True, refresh: bool = False
) -> dict[str, object] | None:
    cache_key = location_page_cache_key(slug)
    if use_cache and not refresh:
        cached = _read_json_cache(cache_key)
        if cached is not None:
            return cached

    payload = _compute_location_page(db, slug)

    if use_cache and payload is not None:
        _write_json_cache(cache_key, payload, LOCATION_PAGE_CACHE_TTL_SECONDS)
    return payload


def _compute_location_page(db: Session, slug: str) -> dict[str, object] | None:
    identity = resolve_location_identity(db, slug)
    if identity is None:
        return None

    location_ids = [location.id for location, _code in identity.locations]
    event_rows = (
        db.query(
            Event.id,
            Event.event_date,
            Event.event_number,
            Event.finishers_count,
            Platform.code,
        )
        .join(Platform, Event.platform_id == Platform.id)
        .filter(
            Event.location_id.in_(location_ids),
            Event.is_test_event.is_(False),
        )
        .all()
    )
    all_event_ids = [row[0] for row in event_rows]
    kept_event_ids = _dedupe_crosslinked_events(db, all_event_ids)
    events = [row for row in event_rows if row[0] in kept_event_ids]
    event_ids = [row[0] for row in events]

    # Число финишёров события: протокол → events.finishers_count → event_summaries.
    protocol_counts: dict[UUID, int] = {}
    volunteer_counts_total = 0
    unique_participants = 0
    unique_volunteers = 0
    avg_finish_time_sec: int | None = None
    if event_ids:
        protocol_counts = {
            event_id: int(count)
            for event_id, count in db.query(RunResult.event_id, func.count())
            .filter(RunResult.event_id.in_(event_ids))
            .group_by(RunResult.event_id)
            .all()
        }
        volunteer_counts_total = (
            db.query(func.count()).select_from(VolunteerResult).filter(VolunteerResult.event_id.in_(event_ids)).scalar()
            or 0
        )
        unique_participants = (
            db.query(func.count(func.distinct(RunResult.participant_id)))
            .filter(RunResult.event_id.in_(event_ids), RunResult.participant_id.isnot(None))
            .scalar()
            or 0
        )
        unique_volunteers = (
            db.query(func.count(func.distinct(VolunteerResult.participant_id)))
            .filter(VolunteerResult.event_id.in_(event_ids), VolunteerResult.participant_id.isnot(None))
            .scalar()
            or 0
        )
        avg_value = (
            db.query(func.avg(RunResult.finish_time_sec))
            .filter(
                RunResult.event_id.in_(event_ids),
                RunResult.finish_time_sec.isnot(None),
                RunResult.finish_time_sec > 0,
            )
            .scalar()
        )
        avg_finish_time_sec = int(avg_value) if avg_value is not None else None

    summary_counts: dict[UUID, int] = {}
    if location_ids:
        summary_rows = (
            db.query(EventSummary.event_id, EventSummary.finishers_count)
            .filter(
                EventSummary.location_id.in_(location_ids),
                EventSummary.event_id.isnot(None),
                EventSummary.finishers_count.isnot(None),
            )
            .all()
        )
        summary_counts = {event_id: count for event_id, count in summary_rows}

    def event_finishers(event_id: UUID, event_finishers_count: int | None) -> int | None:
        protocol = protocol_counts.get(event_id)
        if protocol:
            return protocol
        if event_finishers_count:
            return event_finishers_count
        return summary_counts.get(event_id)

    finishers_total = 0
    events_with_finishers = 0
    record_finishers = -1
    attendance_record: dict[str, object] | None = None
    for event_id, event_date, event_number, finishers_count, platform_code in events:
        finishers = event_finishers(event_id, finishers_count)
        if finishers is None:
            continue
        finishers_total += finishers
        events_with_finishers += 1
        if finishers > record_finishers:
            record_finishers = finishers
            attendance_record = {
                "finishers": finishers,
                "event_date": event_date,
                "event_number": event_number,
                "platform_code": platform_code,
            }

    course_records = {}
    histogram_rows: list[dict[str, object]] = []
    median_finish_time_sec: int | None = None
    if event_ids:
        course_records = {
            "male": _course_record(db, event_ids, "male"),
            "female": _course_record(db, event_ids, "female"),
        }
        histogram_rows = _histogram_rows(db, event_ids)

        median_value = (
            db.query(func.percentile_cont(0.5).within_group(RunResult.finish_time_sec))
            .filter(
                RunResult.event_id.in_(event_ids),
                RunResult.finish_time_sec.isnot(None),
                RunResult.finish_time_sec > 0,
            )
            .scalar()
        )
        median_finish_time_sec = int(median_value) if median_value is not None else None

    last_event_payload, avg_delta, median_delta = _last_event_stats(
        db,
        events,
        event_ids,
        event_finishers,
        avg_finish_time_sec,
        median_finish_time_sec,
    )

    # Таймлайн платформ (Л1): даты первого/последнего события в каждой системе.
    platform_stats: dict[str, _PlatformStat] = {}
    for _event_id, event_date, _event_number, _finishers_count, platform_code in events:
        stat = platform_stats.get(platform_code)
        if stat is None:
            platform_stats[platform_code] = _PlatformStat(event_date, event_date, 1)
            continue
        stat.events_count += 1
        if event_date < stat.first_event_date:
            stat.first_event_date = event_date
        if event_date > stat.last_event_date:
            stat.last_event_date = event_date

    # Актуальная система: active_platform каталога, иначе система с самым
    # свежим событием (у локаций без каталога active_platform не задан).
    active_platform = (
        normalize_platform_code(identity.catalog.active_platform) if identity.catalog is not None else None
    )
    if active_platform is None and platform_stats:
        active_platform = max(platform_stats.items(), key=lambda item: item[1].last_event_date)[0]

    # Текущая система — всегда последней в таймлайне, остальные — в порядке
    # появления (first_event_date). Иначе более ранняя дата первого старта
    # у активной системы (нередкая при перекрытии платформ на переходе)
    # ставила бы её выше исторических записей.
    ordered_platforms = sorted(
        identity.locations,
        key=lambda item: (
            item[1] == active_platform if active_platform else False,
            platform_stats.get(item[1]) is None,
            platform_stats[item[1]].first_event_date if item[1] in platform_stats else date.max,
            _platform_order_index(item[1]),
        ),
    )
    # Одна запись на платформу: статистика (platform_stats) агрегируется по
    # platform_code, поэтому дубли Location одной системы (напр. две parkrun-строки
    # одной площадки в каталоге) дали бы две одинаковые записи таймлайна.
    platforms_payload: list[dict[str, object]] = []
    seen_platform_codes: set[str] = set()
    for location, platform_code in ordered_platforms:
        if platform_code in seen_platform_codes:
            continue
        seen_platform_codes.add(platform_code)
        stat = platform_stats.get(platform_code)
        platforms_payload.append(
            {
                "platform_code": platform_code,
                "location_name": location.name,
                "external_key": location.external_key,
                "url": location_page_url(platform_code, location.external_key, location.source_url),
                "first_event_date": stat.first_event_date if stat else None,
                "last_event_date": stat.last_event_date if stat else None,
                "events_count": stat.events_count if stat else 0,
                "is_active": platform_code == active_platform if active_platform else None,
            }
        )

    primary_location, primary_code = identity.locations[0]
    catalog_index = identity.catalog_index
    latitude, longitude = catalog_index.coordinates_for(primary_location, primary_code)
    if latitude is None or longitude is None:
        latitude = _first_by_platform_order(identity.locations, lambda loc: loc.latitude)  # type: ignore[assignment]
        longitude = _first_by_platform_order(identity.locations, lambda loc: loc.longitude)  # type: ignore[assignment]

    is_paused, is_cancelled = _identity_status(catalog_index, identity.locations)

    first_event_date = min((row[1] for row in events), default=None)
    last_event_date = max((row[1] for row in events), default=None)

    return {
        "slug": identity.slug,
        "identity_key": identity.identity_key,
        "name": identity.name,
        "city": _first_by_platform_order(identity.locations, lambda loc: loc.city),
        "region": _first_by_platform_order(identity.locations, lambda loc: loc.region),
        "country": _identity_country(identity.locations),
        "is_paused": is_paused,
        "is_cancelled": is_cancelled,
        "cancel_reason": _identity_cancel_reason(identity.locations) if is_cancelled else None,
        "latitude": latitude,
        "longitude": longitude,
        "map_url": _first_by_platform_order(identity.locations, lambda loc: loc.map_url),
        "start_point_url": _start_point_url(latitude, longitude),
        "platforms": platforms_payload,
        "description": _description_payload(db, ordered_platforms),
        "stats": {
            "events_count": len(events),
            "finishers_total": finishers_total,
            "unique_participants": unique_participants,
            "volunteers_total": volunteer_counts_total,
            "unique_volunteers": unique_volunteers,
            "avg_finish_time_sec": avg_finish_time_sec,
            "avg_finish_time_display": (
                format_finish_time_display(avg_finish_time_sec) if avg_finish_time_sec is not None else None
            ),
            "avg_finishers": (round(finishers_total / events_with_finishers) if events_with_finishers else None),
            "attendance_record": attendance_record,
            "course_records": course_records or {"male": None, "female": None},
            "first_event_date": first_event_date,
            "last_event_date": last_event_date,
            "median_finish_time_sec": median_finish_time_sec,
            "median_finish_time_display": (
                format_finish_time_display(median_finish_time_sec) if median_finish_time_sec is not None else None
            ),
            "last_event": last_event_payload,
            "avg_finish_time_delta_sec": avg_delta,
            "median_finish_time_delta_sec": median_delta,
        },
        "histogram": {
            "bin_size_sec": HISTOGRAM_BIN_SEC,
            "rows": histogram_rows,
        },
        "age_group_records": _age_group_records(db, event_ids),
        "city_locations": _same_city_locations(
            db,
            identity.identity_key,
            cast(
                "str | None",
                _first_by_platform_order(identity.locations, lambda loc: loc.city),
            ),
        ),
    }


def build_location_events(
    db: Session, slug: str, *, use_cache: bool = True, refresh: bool = False
) -> dict[str, object] | None:
    cache_key = location_events_cache_key(slug)
    if use_cache and not refresh:
        cached = _read_json_cache(cache_key)
        if cached is not None:
            return cached

    payload = _compute_location_events(db, slug)

    if use_cache and payload is not None:
        _write_json_cache(cache_key, payload, LOCATION_PAGE_CACHE_TTL_SECONDS)
    return payload


def _compute_location_events(db: Session, slug: str) -> dict[str, object] | None:
    """Журнал протоколов локации: все события сквозь все системы, новые сверху."""
    identity = resolve_location_identity(db, slug)
    if identity is None:
        return None

    location_ids = [location.id for location, _code in identity.locations]
    location_by_id = {location.id: location for location, _code in identity.locations}
    event_rows = (
        db.query(Event, Platform.code)
        .join(Platform, Event.platform_id == Platform.id)
        .filter(
            Event.location_id.in_(location_ids),
            Event.is_test_event.is_(False),
        )
        .all()
    )
    kept_event_ids = _dedupe_crosslinked_events(db, [event.id for event, _code in event_rows])
    events = [(event, code) for event, code in event_rows if event.id in kept_event_ids]
    event_ids = [event.id for event, _code in events]

    gender_expr = _gender_expression(
        Platform.code, Participant.profile_extra, RunResult.age_category, Participant.age_category
    )
    time_ok = RunResult.finish_time_sec.isnot(None) & (RunResult.finish_time_sec > 0)
    protocol_stats: dict[UUID, dict[str, object]] = {}
    volunteer_counts: dict[UUID, int] = {}
    best_runner_names: dict[UUID, dict[str, dict[str, Any]]] = {}
    if event_ids:
        rows = (
            db.query(
                RunResult.event_id,
                func.count().label("finishers"),
                func.min(case((time_ok & (gender_expr == "male"), RunResult.finish_time_sec))).label("best_male"),
                func.min(case((time_ok & (gender_expr == "female"), RunResult.finish_time_sec))).label("best_female"),
                func.avg(case((time_ok, RunResult.finish_time_sec))).label("avg_time"),
                debutants_sum().label("debutants"),
                location_guests_sum().label("first_here"),
                func.sum(case((RunResult.is_pr.is_(True), 1), else_=0)).label("prs"),
            )
            .join(Event, RunResult.event_id == Event.id)
            .join(Platform, Event.platform_id == Platform.id)
            .outerjoin(Participant, RunResult.participant_id == Participant.id)
            .filter(RunResult.event_id.in_(event_ids))
            .group_by(RunResult.event_id)
            .all()
        )
        for row in rows:
            protocol_stats[row.event_id] = {
                "finishers": int(row.finishers),
                "best_male": int(row.best_male) if row.best_male is not None else None,
                "best_female": int(row.best_female) if row.best_female is not None else None,
                "avg_time": int(row.avg_time) if row.avg_time is not None else None,
                "debutants": int(row.debutants or 0),
                "first_here": int(row.first_here or 0),
                "prs": int(row.prs or 0),
            }
        volunteer_counts = {
            event_id: int(count)
            for event_id, count in db.query(
                VolunteerResult.event_id, func.count(func.distinct(VolunteerResult.participant_id))
            )
            .filter(VolunteerResult.event_id.in_(event_ids))
            .group_by(VolunteerResult.event_id)
            .all()
        }
        # ФИО обладателя рекорда трассы М/Ж на каждом старте: имя финишёра с
        # минимальным временем в своём поле. DISTINCT ON (event, пол) по
        # возрастанию времени даёт ту же величину, что func.min выше, и её
        # автора одним проходом.
        #
        # serial_id — для ссылки на публичный профиль (/users/{serial_id}), но
        # только если участник связан с юзером сайта И профиль не приватный:
        # приватный отдаёт 403 всем, кроме владельца, — ссылка вела бы в тупик
        # (та же логика, что в leaderboard_service._site_links).
        public_serial = case((User.profile_private.is_(False), User.serial_id), else_=None)
        name_rows = (
            db.query(
                RunResult.event_id,
                gender_expr.label("gender"),
                Participant.display_name,
                public_serial.label("serial_id"),
            )
            .join(Event, RunResult.event_id == Event.id)
            .join(Platform, Event.platform_id == Platform.id)
            .outerjoin(Participant, RunResult.participant_id == Participant.id)
            .outerjoin(PlatformLink, _platform_link_join())
            .outerjoin(User, PlatformLink.user_id == User.id)
            .filter(
                RunResult.event_id.in_(event_ids),
                time_ok,
                gender_expr.in_(("male", "female")),
            )
            .distinct(RunResult.event_id, gender_expr)
            .order_by(RunResult.event_id, gender_expr, RunResult.finish_time_sec.asc())
            .all()
        )
        for event_id, gender, display_name, serial_id in name_rows:
            best_runner_names.setdefault(event_id, {})[gender] = {
                "name": display_name,
                "serial_id": serial_id,
            }

    summaries: dict[UUID, EventSummary] = {}
    if location_ids:
        for summary_row in (
            db.query(EventSummary)
            .filter(EventSummary.location_id.in_(location_ids), EventSummary.event_id.isnot(None))
            .all()
        ):
            if summary_row.event_id is not None:
                summaries[summary_row.event_id] = summary_row

    def fmt(value: int | None) -> str | None:
        return format_finish_time_display(value) if value is not None else None

    items: list[dict[str, object]] = []
    for event, platform_code in events:
        stats = protocol_stats.get(event.id)
        summary = summaries.get(event.id)
        has_protocol = stats is not None
        finishers = (
            stats["finishers"]
            if stats is not None
            else (event.finishers_count or (summary.finishers_count if summary else None))
        )
        volunteers = volunteer_counts.get(event.id) or (summary.volunteers_count if summary else None)
        best_male = stats["best_male"] if stats else (summary.best_male_time_sec if summary else None)
        best_female = stats["best_female"] if stats else (summary.best_female_time_sec if summary else None)
        avg_time = stats["avg_time"] if stats else (summary.avg_time_sec if summary else None)
        runner_names = best_runner_names.get(event.id, {})
        male_runner = runner_names.get("male") or {}
        female_runner = runner_names.get("female") or {}
        items.append(
            {
                "event_date": event.event_date,
                "platform_code": platform_code,
                "event_number": event.event_number,
                "finishers": finishers,
                "volunteers": volunteers,
                "best_male_time_sec": best_male,
                "best_male_time_display": fmt(best_male),  # type: ignore[arg-type]
                "best_male_runner_name": male_runner.get("name"),
                "best_male_runner_serial_id": male_runner.get("serial_id"),
                "best_female_time_sec": best_female,
                "best_female_time_display": fmt(best_female),  # type: ignore[arg-type]
                "best_female_runner_name": female_runner.get("name"),
                "best_female_runner_serial_id": female_runner.get("serial_id"),
                "avg_time_sec": avg_time,
                "avg_time_display": fmt(avg_time),  # type: ignore[arg-type]
                "debutants": stats["debutants"] if stats else None,
                "first_at_location": stats["first_here"] if stats else None,
                "prs": stats["prs"] if stats else None,
                "has_protocol": has_protocol,
                "protocol_url": resolve_activity_url(
                    platform_code=platform_code,
                    event_date=event.event_date,
                    event_number=event.event_number,
                    event_source_url=event.source_url,
                    location_external_key=location_by_id[event.location_id].external_key,
                    summary_source_url=summary.source_url if summary else None,
                ),
            }
        )

    # Прогрессия рекорда посещаемости и рекордов трассы М/Ж: маркер у КАЖДОГО
    # старта, побившего рекорд на свой момент (новый рекорд не снимает маркер
    # со старых). "Глобальный" рекорд — сквозь все платформы идентичности разом
    # (единая хронология); "внутрисистемный" — свой рекорд для каждой
    # платформы отдельно (глобальный всегда является и внутрисистемным на
    # свой момент, т.к. считается по той же убывающей последовательности).
    # Заодно нумеруем старты сквозным номером — какой это по счёту сбор
    # локации за всю историю, вне зависимости от платформы.
    items.sort(key=lambda item: cast(date, item["event_date"]))
    running_max_finishers: int | None = None
    running_best_male: int | None = None
    running_best_female: int | None = None
    running_max_finishers_by_platform: dict[str, int] = {}
    running_best_male_by_platform: dict[str, int] = {}
    running_best_female_by_platform: dict[str, int] = {}
    for overall_number, item in enumerate(items, start=1):
        platform_code = cast(str, item["platform_code"])

        finishers = cast("int | None", item["finishers"])
        is_record = finishers is not None and (running_max_finishers is None or finishers > running_max_finishers)
        item["is_attendance_record"] = is_record
        if is_record:
            running_max_finishers = finishers
        platform_max = running_max_finishers_by_platform.get(platform_code)
        is_platform_record = finishers is not None and (platform_max is None or finishers > platform_max)
        item["is_platform_attendance_record"] = is_platform_record
        if is_platform_record:
            running_max_finishers_by_platform[platform_code] = cast(int, finishers)

        best_male = cast("int | None", item["best_male_time_sec"])
        is_male_record = best_male is not None and (running_best_male is None or best_male < running_best_male)
        item["is_course_record_male"] = is_male_record
        if is_male_record:
            running_best_male = best_male
        platform_best_male = running_best_male_by_platform.get(platform_code)
        is_platform_male_record = best_male is not None and (
            platform_best_male is None or best_male < platform_best_male
        )
        item["is_platform_course_record_male"] = is_platform_male_record
        if is_platform_male_record:
            running_best_male_by_platform[platform_code] = cast(int, best_male)

        best_female = cast("int | None", item["best_female_time_sec"])
        is_female_record = best_female is not None and (
            running_best_female is None or best_female < running_best_female
        )
        item["is_course_record_female"] = is_female_record
        if is_female_record:
            running_best_female = best_female
        platform_best_female = running_best_female_by_platform.get(platform_code)
        is_platform_female_record = best_female is not None and (
            platform_best_female is None or best_female < platform_best_female
        )
        item["is_platform_course_record_female"] = is_platform_female_record
        if is_platform_female_record:
            running_best_female_by_platform[platform_code] = cast(int, best_female)

        item["overall_number"] = overall_number
    items.sort(key=lambda item: cast(date, item["event_date"]), reverse=True)

    return {
        "slug": identity.slug,
        "name": identity.name,
        "total": len(items),
        "items": items,
    }


@dataclass
class _IndexIdentityStat:
    events_count: int = 0
    finishers_total: int = 0
    first_event_date: date | None = None
    # Первый старт отдельно по каждой системе: сквозная дата выше теряет момент,
    # когда площадка начала работать в нынешней системе (просьба Дмитрия
    # 23.08.2026 — этот вопрос закрывал дашборд «дни рождения» в Grafana).
    first_event_date_by_platform: dict[str, date] = field(default_factory=dict)
    last_event_date: date | None = None
    best_male_time_sec: int | None = None
    best_female_time_sec: int | None = None
    attendance_record_finishers: int | None = None
    attendance_record_date: date | None = None
    # Сумма и число финишей — из них считается среднее время площадки.
    # Держим слагаемые, а не готовое среднее: идентичность склеивает
    # несколько локаций, и усреднять уже усреднённое было бы неверно.
    finish_time_sum_sec: int = 0
    finish_time_count: int = 0


def _bulk_identity_stats(
    db: Session,
    location_id_to_identity: dict[UUID, str],
) -> dict[str, _IndexIdentityStat]:
    """Сводные цифры по всем идентичностям сразу — без N+1 запросов на локацию."""
    location_ids = list(location_id_to_identity.keys())
    if not location_ids:
        return {}

    event_rows = (
        db.query(Event.id, Event.location_id, Event.event_date, Event.finishers_count, Platform.code)
        .join(Platform, Event.platform_id == Platform.id)
        .filter(Event.location_id.in_(location_ids), Event.is_test_event.is_(False))
        .all()
    )
    all_event_ids = {row[0] for row in event_rows}

    # Кросслинки всегда связывают события ОДНОЙ идентичности (тот же
    # физический старт, записанный в двух системах) — дедуп безопасен глобально,
    # без группировки по identity. JOIN по location_id (короткий список),
    # а не IN() по event_id (в проде — десятки тысяч id, огромный список
    # параметров убивает время запроса через SSH-туннель на dev).
    excluded_secondary: set[UUID] = set()
    if all_event_ids:
        crosslink_rows = (
            db.query(EventCrosslink.primary_event_id, EventCrosslink.secondary_event_id)
            .join(Event, EventCrosslink.secondary_event_id == Event.id)
            .filter(Event.location_id.in_(location_ids), Event.is_test_event.is_(False))
            .all()
        )
        excluded_secondary = {secondary for primary, secondary in crosslink_rows if primary in all_event_ids}

    # events.finishers_count не заполняется у s95 (только у 5verst) — для
    # верных цифр нужен COUNT по run_results, events.finishers_count только
    # как fallback для событий без протокола (parkrun-эпоха).
    protocol_counts: dict[UUID, int] = {
        event_id: int(count)
        for event_id, count in db.query(RunResult.event_id, func.count())
        .join(Event, RunResult.event_id == Event.id)
        .filter(Event.location_id.in_(location_ids), Event.is_test_event.is_(False))
        .group_by(RunResult.event_id)
        .all()
    }

    stats: dict[str, _IndexIdentityStat] = {}
    for event_id, location_id, event_date, finishers_count, platform_code in event_rows:
        identity_key = location_id_to_identity[location_id]
        stat = stats.setdefault(identity_key, _IndexIdentityStat())
        # Первый старт в системе считаем ДО дедупа кросслинков, в отличие от
        # счётчиков ниже. Вторичная строка связки — тот же физический старт,
        # записанный второй системой: для «сколько стартов» её учитывать нельзя,
        # а для «когда эта система здесь началась» она как раз и есть ответ.
        known = stat.first_event_date_by_platform.get(platform_code)
        if known is None or event_date < known:
            stat.first_event_date_by_platform[platform_code] = event_date
        if event_id in excluded_secondary:
            continue
        stat.events_count += 1
        finishers = protocol_counts.get(event_id) or finishers_count
        if finishers:
            stat.finishers_total += finishers
            if stat.attendance_record_finishers is None or finishers > stat.attendance_record_finishers:
                stat.attendance_record_finishers = finishers
                stat.attendance_record_date = event_date
        if stat.first_event_date is None or event_date < stat.first_event_date:
            stat.first_event_date = event_date
        if stat.last_event_date is None or event_date > stat.last_event_date:
            stat.last_event_date = event_date

    # Рекорды локации (LR) — минимальное время по полу за всю историю
    # идентичности. Кросслинки тут не мешают: вторичное событие — тот же
    # физический старт с теми же временами, на минимум оно не влияет.
    gender_expr = _gender_expression(
        Platform.code, Participant.profile_extra, RunResult.age_category, Participant.age_category
    )
    record_rows = (
        db.query(
            Event.location_id,
            gender_expr.label("gender"),
            func.min(RunResult.finish_time_sec).label("best"),
        )
        .join(Event, RunResult.event_id == Event.id)
        .join(Platform, Event.platform_id == Platform.id)
        .outerjoin(Participant, RunResult.participant_id == Participant.id)
        .filter(
            Event.location_id.in_(location_ids),
            Event.is_test_event.is_(False),
            RunResult.finish_time_sec.isnot(None),
            RunResult.finish_time_sec > 0,
        )
        .group_by(Event.location_id, gender_expr)
        .all()
    )
    for location_id, gender, best in record_rows:
        if gender not in ("male", "female") or best is None:
            continue
        identity_key = location_id_to_identity[location_id]
        stat = stats.setdefault(identity_key, _IndexIdentityStat())
        if gender == "male":
            if stat.best_male_time_sec is None or int(best) < stat.best_male_time_sec:
                stat.best_male_time_sec = int(best)
        elif stat.best_female_time_sec is None or int(best) < stat.best_female_time_sec:
            stat.best_female_time_sec = int(best)

    # Среднее время финишёров площадки: тем же фильтром финишёров, что и
    # рекорды (finish_time_sec IS NOT NULL AND > 0). Вторичные события
    # кросслинков не исключаем сознательно — у зеркала те же самые времена,
    # и на среднее они не влияют, а join к списку исключений стоил бы дорого.
    avg_rows = (
        db.query(
            Event.location_id,
            func.sum(RunResult.finish_time_sec).label("total"),
            func.count(RunResult.id).label("count"),
        )
        .join(Event, RunResult.event_id == Event.id)
        .filter(
            Event.location_id.in_(location_ids),
            Event.is_test_event.is_(False),
            RunResult.finish_time_sec.isnot(None),
            RunResult.finish_time_sec > 0,
        )
        .group_by(Event.location_id)
        .all()
    )
    for location_id, total, count in avg_rows:
        if not count:
            continue
        stat = stats.setdefault(location_id_to_identity[location_id], _IndexIdentityStat())
        stat.finish_time_sum_sec += int(total or 0)
        stat.finish_time_count += int(count)

    return stats


def _read_json_cache(key: str) -> dict[str, object] | None:
    try:
        raw = get_redis_client().get(key)
    except redis.RedisError:
        return None
    if not isinstance(raw, str):
        return None
    try:
        return cast(dict[str, object], json.loads(raw))
    except (TypeError, ValueError):
        return None


def _write_json_cache(key: str, payload: dict[str, object], ttl_seconds: int) -> None:
    try:
        get_redis_client().setex(key, ttl_seconds, json.dumps(payload, default=str))
    except redis.RedisError:
        pass


def _read_locations_index_cache() -> dict[str, object] | None:
    return _read_json_cache(LOCATIONS_INDEX_CACHE_KEY)


def _write_locations_index_cache(payload: dict[str, object]) -> None:
    _write_json_cache(LOCATIONS_INDEX_CACHE_KEY, payload, LOCATIONS_INDEX_CACHE_TTL_SECONDS)


def invalidate_locations_index_cache() -> None:
    try:
        get_redis_client().delete(LOCATIONS_INDEX_CACHE_KEY)
    except redis.RedisError:
        pass


def invalidate_location_page_cache(slug: str) -> None:
    """Ручной сброс кэша страницы/журнала/рейтингов одной локации (TTL сам разрулит остальное)."""
    normalized = slug.strip().lower()
    try:
        client = get_redis_client()
        client.delete(
            location_page_cache_key(normalized),
            location_events_cache_key(normalized),
            location_leaders_cache_key(normalized),
            location_participants_cache_key(normalized),
        )
        # Срезы постоянного состава по ролям лежат отдельными ключами с
        # суффиксом (:ron_site, :rc1a2b3…) — точечный delete их не достанет.
        # Ключей на локацию единицы, поэтому scan по узкой маске дешевле, чем
        # хранить их список. FLUSHDB здесь запрещён — база общая с сессиями.
        role_keys = list(
            client.scan_iter(match=f"{location_participants_cache_key(normalized)}:r*", count=100)
        )
        if role_keys:
            client.delete(*role_keys)
    except redis.RedisError:
        pass


def build_locations_index(
    db: Session, *, use_cache: bool = True, refresh: bool = False
) -> dict[str, object]:
    """Публичный каталог локаций — с TTL-кэшем в Redis (см. LOCATIONS_INDEX_CACHE_TTL_SECONDS).

    Redis недоступен → тихо считаем без кэша (кэш — оптимизация, не зависимость).

    refresh=True — прогрев: кэш не читаем (иначе живой блоб вернулся бы как есть
    и ничего не обновилось), но результат обязательно кладём обратно. Именно
    ради этого флаг и появился: прогрев ходил с use_cache=False, а это значило
    «не читать И не писать» — задача честно считала каталог и все 270 страниц по
    девять минут, а в Redis не попадало ничего, и первый посетитель после
    протухания TTL всё равно ловил таймаут фронтенда на холодном расчёте.
    """
    if use_cache and not refresh:
        cached = _read_locations_index_cache()
        if cached is not None:
            return cached

    payload = _compute_locations_index(db)

    if use_cache:
        _write_locations_index_cache(payload)
    return payload


def _collect_catalog_identities(
    db: Session, catalog_index: LocationCatalogIndex
) -> tuple[dict[str, list[tuple[Location, str]]], dict[UUID, str]]:
    """Идентичности для строк каталога + маппинг location_id → identity_key.

    Первое — какие идентичности показывать и какими локациями они представлены
    (только «витринные» платформы), второе — ВСЕ локации этих идентичностей,
    включая parkrun-эпоху: по нему считаются сквозные цифры истории.
    """
    # Строки таблицы — только «официальные» локации активных систем
    # (five_verst/s95/runpark): у parkrun-локаций почти всегда есть текущий
    # преемник в каталоге, отдельной строки без него не показываем.
    display_rows = (
        db.query(Location, Platform.code)
        .join(Platform, Location.platform_id == Platform.id)
        .filter(Platform.code.in_(("five_verst", "s95", "runpark")), Location.is_official_map.is_(True))
        .all()
    )
    identity_locations: dict[str, list[tuple[Location, str]]] = {}
    for location, platform_code in display_rows:
        identity_key = catalog_index.canonical_identity_key(location, platform_code)
        identity_locations.setdefault(identity_key, []).append((location, platform_code))

    # …плюс русские parkrun-локации без преемника в действующих системах:
    # закрылись с уходом parkrun из России и иначе в список не попали бы вовсе
    # (показываются как неактивные). Зарубежный parkrun-туризм и псевдолокацию
    # «parkrun (сводка ролей)» отсекает is_foreign_location. Совпадение слага с
    # уже показанной локацией (Боровичи ≡ park-30-letiya-oktyabrya) значит, что
    # преемник есть, просто не связан в каталоге — отдельной строки не даём.
    display_normalized_slugs = {
        normalize_location_slug(location.external_key) for location, _code in display_rows
    } - {""}
    parkrun_rows = (
        db.query(Location, Platform.code)
        .join(Platform, Location.platform_id == Platform.id)
        .filter(Platform.code == "parkrun")
        .all()
    )
    for location, platform_code in parkrun_rows:
        if is_foreign_location(location, platform_code, catalog_index):
            continue
        if normalize_location_slug(location.external_key) in display_normalized_slugs:
            continue
        identity_key = catalog_index.canonical_identity_key(location, platform_code)
        if identity_key in identity_locations:
            continue
        identity_locations.setdefault(identity_key, []).append((location, platform_code))

    # …плюс не-official локации действующих систем, у которых реально были
    # старты (is_official_map=False обычно значит «показывается только через
    # каталожную склейку с official-парой», как у моста RunPark → 5 вёрст —
    # но «С95 и друзья»/«S95 & Friends» без города и без official-пары —
    # разъездная серия, а не площадка, и без этой ветки выпадала бы из
    # каталога целиком, хотя финиши по ней есть и считаются на главной).
    locations_with_events = {
        row[0]
        for row in db.query(Event.location_id)
        .join(Location, Location.id == Event.location_id)
        .join(Platform, Platform.id == Location.platform_id)
        .filter(Platform.code.in_(("five_verst", "s95", "runpark")), Location.is_official_map.is_(False))
        .distinct()
    }
    unofficial_rows = (
        db.query(Location, Platform.code)
        .join(Platform, Location.platform_id == Platform.id)
        .filter(
            Platform.code.in_(("five_verst", "s95", "runpark")),
            Location.is_official_map.is_(False),
            Location.id.in_(locations_with_events),
        )
        .all()
    )
    for location, platform_code in unofficial_rows:
        identity_key = catalog_index.canonical_identity_key(location, platform_code)
        if identity_key in identity_locations:
            continue
        if normalize_location_slug(location.external_key) in display_normalized_slugs:
            continue
        identity_locations.setdefault(identity_key, []).append((location, platform_code))

    # Цифры (стартов, финишей, первого старта) — СКВОЗНЫЕ по всей истории
    # идентичности, включая parkrun-эпоху: иначе «Первый старт» теряет годы
    # до перехода на текущую систему, а «Стартов»/«Финишей» занижены.
    all_rows = db.query(Location, Platform.code).join(Platform, Location.platform_id == Platform.id).all()
    location_id_to_identity: dict[UUID, str] = {}
    for location, platform_code in all_rows:
        identity_key = catalog_index.canonical_identity_key(location, platform_code)
        if identity_key in identity_locations:
            location_id_to_identity[location.id] = identity_key

    return identity_locations, location_id_to_identity


def _first_event_in_current_system(
    ordered: list[tuple[Location, str]],
    stat: _IndexIdentityStat | None,
) -> tuple[str | None, date | None]:
    """Система, в которой площадка живёт сейчас, и дата её первого старта именно там.

    `ordered` уже отсортирован «активная платформа первой» (_sort_identity_locations),
    поэтому просто берём первую систему, в которой у площадки вообще есть старты —
    ровно то, о чём просил Дмитрий 23.08.2026: «берём актуальную систему, по
    которой есть пробежки».

    У площадки без прошлого в других системах эта дата совпадёт со сквозной, и
    это не дубль: две колонки расходятся ровно там, где было интересно — на
    парках, переехавших из parkrun-эпохи.
    """
    if stat is None:
        return None, None
    for _location, code in ordered:
        first = stat.first_event_date_by_platform.get(code)
        if first is not None:
            return code, first
    return None, None


def _compute_locations_index(db: Session) -> dict[str, object]:
    """Тяжёлая агрегация каталога локаций: одна строка на каноническую идентичность."""
    catalog_index = LocationCatalogIndex(db)
    identity_locations, location_id_to_identity = _collect_catalog_identities(db, catalog_index)

    identity_stats = _bulk_identity_stats(db, location_id_to_identity)

    items: list[dict[str, object]] = []
    for identity_key, members in identity_locations.items():
        catalog = catalog_index.get_for_identity_key(identity_key)
        ordered = _sort_identity_locations(catalog, members)
        primary_location, primary_code = ordered[0]
        stat = identity_stats.get(identity_key)
        is_paused, is_cancelled = _identity_status(catalog_index, ordered)
        system_code, system_first_date = _first_event_in_current_system(ordered, stat)
        # Среднее время финишёра площадки — по сумме и числу финишей всех её
        # локаций: усреднять уже усреднённое по системам было бы неверно.
        avg_finish_sec = (
            round(stat.finish_time_sum_sec / stat.finish_time_count)
            if stat and stat.finish_time_count
            else None
        )
        items.append(
            {
                "slug": primary_location.external_key.strip().lower(),
                "identity_key": identity_key,
                "name": _identity_display_name(catalog, ordered, catalog_index),
                "city": _first_by_platform_order(ordered, lambda loc: loc.city),
                "region": _first_by_platform_order(ordered, lambda loc: loc.region),
                "country": _identity_country(ordered),
                "platform_codes": sorted({code for _loc, code in ordered}, key=_platform_order_index),
                "is_paused": is_paused,
                "is_cancelled": is_cancelled,
                "events_count": stat.events_count if stat else 0,
                "finishers_total": stat.finishers_total if stat else 0,
                "first_event_date": stat.first_event_date if stat else None,
                "first_event_date_in_system": system_first_date,
                "first_event_system_code": system_code,
                "last_event_date": stat.last_event_date if stat else None,
                "best_male_time_sec": stat.best_male_time_sec if stat else None,
                "best_male_time_display": (
                    format_finish_time_display(stat.best_male_time_sec)
                    if stat and stat.best_male_time_sec is not None
                    else None
                ),
                "attendance_record_finishers": stat.attendance_record_finishers if stat else None,
                "attendance_record_date": stat.attendance_record_date if stat else None,
                "avg_finish_time_sec": avg_finish_sec,
                "avg_finish_time_display": (
                    format_finish_time_display(avg_finish_sec) if avg_finish_sec is not None else None
                ),
                "best_female_time_sec": stat.best_female_time_sec if stat else None,
                "best_female_time_display": (
                    format_finish_time_display(stat.best_female_time_sec)
                    if stat and stat.best_female_time_sec is not None
                    else None
                ),
            }
        )

    items.sort(key=lambda item: str(item["name"]).lower())
    return {"items": items, "total": len(items)}


# Посадочная «Результаты последней субботы»: последний старт каждой локации по
# всем системам разом. Тот же режим обновления, что у каталога: данные меняются
# раз в неделю после субботнего синка, точечная инвалидация не окупается.
# v2 — в строку добавлена система первичного протокола (event_platform_code):
# без бампа кэшированный payload отдавал бы поле как None до истечения TTL.
LAST_RESULTS_CACHE_KEY = "locations:last-results:v2"


def invalidate_last_results_cache() -> None:
    try:
        get_redis_client().delete(LAST_RESULTS_CACHE_KEY)
    except redis.RedisError:
        pass


def build_last_results(db: Session, *, use_cache: bool = True, refresh: bool = False) -> dict[str, object]:
    """Последние результаты всех локаций — с TTL-кэшем в Redis.

    Семантика флагов та же, что у build_locations_index: refresh=True — прогрев
    (не читать кэш, но обязательно записать), use_cache=False — совсем без кэша.
    """
    if use_cache and not refresh:
        cached = _read_json_cache(LAST_RESULTS_CACHE_KEY)
        if cached is not None:
            return cached

    payload = _compute_last_results(db)

    if use_cache:
        _write_json_cache(LAST_RESULTS_CACHE_KEY, payload, LOCATIONS_INDEX_CACHE_TTL_SECONDS)
    return payload


def _compute_last_results(db: Session) -> dict[str, object]:
    """Одна строка на идентичность: её последний старт и цифры этого старта."""
    catalog_index = LocationCatalogIndex(db)
    identity_locations, location_id_to_identity = _collect_catalog_identities(db, catalog_index)

    location_ids = list(location_id_to_identity.keys())
    if not location_ids:
        return {"saturday_date": None, "items": [], "total": 0}

    event_rows = (
        db.query(Event, Platform.code)
        .join(Platform, Event.platform_id == Platform.id)
        .filter(Event.location_id.in_(location_ids), Event.is_test_event.is_(False))
        .all()
    )
    # Дедуп кросслинков — как в _bulk_identity_stats: JOIN по location_id,
    # а не IN() по десяткам тысяч event_id.
    all_event_ids = {event.id for event, _code in event_rows}
    excluded_secondary: set[UUID] = set()
    if all_event_ids:
        crosslink_rows = (
            db.query(EventCrosslink.primary_event_id, EventCrosslink.secondary_event_id)
            .join(Event, EventCrosslink.secondary_event_id == Event.id)
            .filter(Event.location_id.in_(location_ids), Event.is_test_event.is_(False))
            .all()
        )
        excluded_secondary = {secondary for primary, secondary in crosslink_rows if primary in all_event_ids}
    kept = [(event, code) for event, code in event_rows if event.id not in excluded_secondary]

    # «Последняя суббота» — как пульс на главной: максимальная субботняя дата.
    # Fallback на общий максимум нужен разве что теоретически (пустых суббот
    # при живых данных не бывает), но пусть страница не падает и на нём.
    saturday_dates = [event.event_date for event, _code in kept if event.event_date.weekday() == 5]
    saturday_date = max(saturday_dates, default=None) or max(
        (event.event_date for event, _code in kept), default=None
    )

    # Последний день каждой идентичности и события этого дня (обычно одно;
    # два бывает, когда локация в один день отметилась в двух системах без
    # кросслинка — тогда цифры складываем, времена берём лучшие).
    latest_date: dict[str, date] = {}
    for event, _code in kept:
        identity_key = location_id_to_identity[event.location_id]
        if identity_key not in latest_date or event.event_date > latest_date[identity_key]:
            latest_date[identity_key] = event.event_date
    chosen: dict[str, list[tuple[Event, str]]] = {}
    for event, code in kept:
        identity_key = location_id_to_identity[event.location_id]
        if latest_date.get(identity_key) == event.event_date:
            chosen.setdefault(identity_key, []).append((event, code))

    chosen_event_ids = [event.id for pairs in chosen.values() for event, _code in pairs]
    chosen_location_ids = {event.location_id for pairs in chosen.values() for event, _code in pairs}
    location_by_id: dict[UUID, Location] = {
        location.id: location
        for location in db.query(Location).filter(Location.id.in_(chosen_location_ids)).all()
    }

    gender_expr = _gender_expression(
        Platform.code, Participant.profile_extra, RunResult.age_category, Participant.age_category
    )
    time_ok = RunResult.finish_time_sec.isnot(None) & (RunResult.finish_time_sec > 0)
    protocol_stats: dict[UUID, dict[str, int | None]] = {}
    volunteer_counts: dict[UUID, int] = {}
    summaries: dict[UUID, EventSummary] = {}
    if chosen_event_ids:
        rows = (
            db.query(
                RunResult.event_id,
                func.count().label("finishers"),
                func.min(case((time_ok & (gender_expr == "male"), RunResult.finish_time_sec))).label("best_male"),
                func.min(case((time_ok & (gender_expr == "female"), RunResult.finish_time_sec))).label("best_female"),
                func.avg(case((time_ok, RunResult.finish_time_sec))).label("avg_time"),
                func.sum(case((RunResult.is_first_run.is_(True), 1), else_=0)).label("debutants"),
                func.sum(case((RunResult.is_pr.is_(True), 1), else_=0)).label("prs"),
            )
            .join(Event, RunResult.event_id == Event.id)
            .join(Platform, Event.platform_id == Platform.id)
            .outerjoin(Participant, RunResult.participant_id == Participant.id)
            .filter(RunResult.event_id.in_(chosen_event_ids))
            .group_by(RunResult.event_id)
            .all()
        )
        for row in rows:
            protocol_stats[row.event_id] = {
                "finishers": int(row.finishers),
                "best_male": int(row.best_male) if row.best_male is not None else None,
                "best_female": int(row.best_female) if row.best_female is not None else None,
                "avg_time": int(row.avg_time) if row.avg_time is not None else None,
                "debutants": int(row.debutants or 0),
                "prs": int(row.prs or 0),
            }
        volunteer_counts = {
            event_id: int(count)
            for event_id, count in db.query(
                VolunteerResult.event_id, func.count(func.distinct(VolunteerResult.participant_id))
            )
            .filter(VolunteerResult.event_id.in_(chosen_event_ids))
            .group_by(VolunteerResult.event_id)
            .all()
        }
        for summary_row in (
            db.query(EventSummary).filter(EventSummary.event_id.in_(chosen_event_ids)).all()
        ):
            if summary_row.event_id is not None:
                summaries[summary_row.event_id] = summary_row

    def fmt(value: int | None) -> str | None:
        return format_finish_time_display(value) if value is not None else None

    items: list[dict[str, object]] = []
    for identity_key, members in identity_locations.items():
        events_for_key = chosen.get(identity_key)
        if not events_for_key:
            continue  # локация без единого старта — показывать нечего
        catalog = catalog_index.get_for_identity_key(identity_key)
        ordered = _sort_identity_locations(catalog, members)
        primary_location, _primary_code = ordered[0]
        is_paused, is_cancelled = _identity_status(catalog_index, ordered)

        events_for_key.sort(key=lambda pair: _platform_order_index(pair[1]))
        primary_event, primary_event_code = events_for_key[0]
        finishers: int | None = None
        volunteers: int | None = None
        debutants: int | None = None
        prs: int | None = None
        best_male: int | None = None
        best_female: int | None = None
        avg_time: int | None = None
        has_protocol = False
        for event, _code in events_for_key:
            stats = protocol_stats.get(event.id)
            summary = summaries.get(event.id)
            has_protocol = has_protocol or stats is not None
            event_finishers = (
                stats["finishers"]
                if stats is not None
                else (event.finishers_count or (summary.finishers_count if summary else None))
            )
            if event_finishers is not None:
                finishers = (finishers or 0) + event_finishers
            event_volunteers = volunteer_counts.get(event.id) or (summary.volunteers_count if summary else None)
            if event_volunteers is not None:
                volunteers = (volunteers or 0) + event_volunteers
            if stats is not None:
                debutants = (debutants or 0) + (stats["debutants"] or 0)
                prs = (prs or 0) + (stats["prs"] or 0)
            event_best_male = stats["best_male"] if stats else (summary.best_male_time_sec if summary else None)
            if event_best_male is not None and (best_male is None or event_best_male < best_male):
                best_male = event_best_male
            event_best_female = (
                stats["best_female"] if stats else (summary.best_female_time_sec if summary else None)
            )
            if event_best_female is not None and (best_female is None or event_best_female < best_female):
                best_female = event_best_female
            if avg_time is None:
                avg_time = (stats["avg_time"] if stats else None) or (summary.avg_time_sec if summary else None)

        primary_summary = summaries.get(primary_event.id)
        items.append(
            {
                "slug": primary_location.external_key.strip().lower(),
                "identity_key": identity_key,
                "name": _identity_display_name(catalog, ordered, catalog_index),
                "city": _first_by_platform_order(ordered, lambda loc: loc.city),
                "region": _first_by_platform_order(ordered, lambda loc: loc.region),
                "country": _identity_country(ordered),
                "platform_codes": sorted({code for _loc, code in ordered}, key=_platform_order_index),
                "is_paused": is_paused,
                "is_cancelled": is_cancelled,
                "event_date": primary_event.event_date,
                "event_platform_codes": sorted(
                    {code for _event, code in events_for_key}, key=_platform_order_index
                ),
                "event_number": primary_event.event_number,
                # Система первичного протокола — из неё складывается адрес
                # нашей страницы протокола (/locations/{slug}/protocol/...).
                "event_platform_code": primary_event_code,
                "is_last_saturday": saturday_date is not None and primary_event.event_date == saturday_date,
                "finishers": finishers,
                "volunteers": volunteers,
                "debutants": debutants,
                "prs": prs,
                "best_male_time_sec": best_male,
                "best_male_time_display": fmt(best_male),
                "best_female_time_sec": best_female,
                "best_female_time_display": fmt(best_female),
                "avg_time_sec": avg_time,
                "avg_time_display": fmt(avg_time),
                "has_protocol": has_protocol,
                "protocol_url": resolve_activity_url(
                    platform_code=primary_event_code,
                    event_date=primary_event.event_date,
                    event_number=primary_event.event_number,
                    event_source_url=primary_event.source_url,
                    location_external_key=(
                        location_by_id[primary_event.location_id].external_key
                        if primary_event.location_id in location_by_id
                        else primary_location.external_key
                    ),
                    summary_source_url=primary_summary.source_url if primary_summary else None,
                ),
            }
        )

    # Свежие сверху, внутри даты — по явке (имя — стабильный алфавитный
    # тай-брейк); хвост «давно не стартовали» уезжает вниз.
    items.sort(key=lambda item: str(item["name"]).lower())
    items.sort(
        key=lambda item: (
            cast(date, item["event_date"]).toordinal(),
            cast("int | None", item["finishers"]) or 0,
        ),
        reverse=True,
    )
    return {"saturday_date": saturday_date, "items": items, "total": len(items)}


def build_location_age_group_standings(
    db: Session, user_id: UUID, event_ids: list[UUID]
) -> list[dict[str, object]]:
    """Место участника в топе локации по каждой его возрастной группе.

    Групп столько, сколько человек успел пройти на этой площадке: перешёл из
    «30–34» в «35–39» — будут обе, у каждой своё место и своё лучшее время в
    ней. Сравнение всегда внутри группы, поэтому результаты прошлой категории
    не конкурируют с нынешней. Место пересчитывается на лету и меняется, когда
    кто-то в группе пробегает быстрее.

    Группировка та же, что у рекордов по возрастным группам (_age_group_tops),
    чтобы «#16» на плитке и топ-5, на который она ссылается, сходились.
    Только 5 вёрст — как и вся возрастная машинерия страницы.
    """
    if not event_ids:
        return []

    gender_expr = _protocol_age_category_gender()
    time_ok = RunResult.finish_time_sec.isnot(None) & (RunResult.finish_time_sec > 0)

    # Свои строки разбираем в Python: их десятки, зато нормализация категории
    # одна и та же (normalize_age_group), без второй копии правил в SQL.
    my_rows = (
        db.query(
            gender_expr.label("gender"),
            RunResult.age_category.label("age_category"),
            RunResult.event_id.label("event_id"),
            RunResult.finish_time_sec.label("finish_time_sec"),
            Event.event_date.label("event_date"),
        )
        .join(Event, RunResult.event_id == Event.id)
        .join(Platform, Event.platform_id == Platform.id)
        .join(Participant, RunResult.participant_id == Participant.id)
        .join(PlatformLink, _platform_link_join())
        .filter(
            PlatformLink.user_id == user_id,
            RunResult.event_id.in_(event_ids),
            time_ok,
            Platform.code == FIVE_VERST_PLATFORM_CODE,
            RunResult.age_category.isnot(None),
        )
        .all()
    )

    mine: dict[tuple[str, str], dict[str, object]] = {}
    for row in my_rows:
        if row.gender not in ("male", "female"):
            continue
        age_group = normalize_age_group(row.age_category)
        if age_group is None:
            continue
        group = mine.setdefault(
            (row.gender, age_group),
            {"events": set(), "best_sec": None, "best_date": None, "last_date": None},
        )
        cast(set[UUID], group["events"]).add(row.event_id)
        best_sec = cast(int | None, group["best_sec"])
        if best_sec is None or row.finish_time_sec < best_sec:
            group["best_sec"] = int(row.finish_time_sec)
            group["best_date"] = row.event_date
        last_date = cast(date | None, group["last_date"])
        if row.event_date is not None and (last_date is None or row.event_date > last_date):
            group["last_date"] = row.event_date

    if not mine:
        return []

    # Соперники — только в «моих» группах: сужение по категориям срезает
    # основную часть работы запроса на крупной локации.
    bests = _age_group_runner_bests(db, event_ids, age_groups={key[1] for key in mine})
    rivals = _merge_age_group_bests(db.query(bests).all())

    standings: list[dict[str, object]] = []
    for (gender, age_group), group in mine.items():
        best_sec = cast(int, group["best_sec"])
        runners = rivals.get((gender, age_group), [])
        # Своё место ищем по своему же лучшему времени: при ничьей место общее,
        # так что искать конкретную свою строку не нужно.
        place = next(
            (cast(int, runner["place"]) for runner in runners if runner["best_time_sec"] == best_sec),
            None,
        )
        standings.append(
            {
                "key": age_group_key(gender, age_group),
                "gender": gender,
                "age_group": age_group,
                "label": f"{'М' if gender == 'male' else 'Ж'}{age_group}",
                "runs_count": len(cast(set[UUID], group["events"])),
                "best_time_sec": best_sec,
                "best_time_display": format_finish_time_display(best_sec),
                "best_time_date": group["best_date"],
                "last_run_date": group["last_date"],
                "place": place,
                "total": len(runners),
            }
        )

    # Свежая группа первой: обычно это текущая категория бегуна, прошлые ниже.
    standings.sort(
        key=lambda item: (
            cast(date | None, item["last_run_date"]) or date.min,
            _age_group_sort_key(cast(str, item["age_group"])),
        ),
        reverse=True,
    )
    return standings


def _top_volunteer_role_here(
    db: Session, user_id: UUID, event_ids: list[UUID]
) -> dict[str, object] | None:
    """Любимая роль пользователя на этой локации: чаще всего выходил.

    Ярлыки систем схлопываем в канон (см. app.volunteer_role_taxonomy): «Сканер»,
    «Сканирование» и «Barcode Scanning» — одна и та же роль, и без канонизации
    она делилась бы на три с меньшими счётчиками.
    """
    if not event_ids:
        return None
    rows = (
        db.query(VolunteerResult.role, VolunteerResult.event_id)
        .join(Participant, VolunteerResult.participant_id == Participant.id)
        .join(PlatformLink, _platform_link_join())
        .filter(
            PlatformLink.user_id == user_id,
            VolunteerResult.event_id.in_(event_ids),
            VolunteerResult.role.isnot(None),
            VolunteerResult.role != "",
        )
        .all()
    )
    # Считаем по разным стартам: две роли в одну субботу — это один выход,
    # но каждая роль засчитывается себе (как в разборе ролей кабинета).
    events_by_role: dict[str, set[UUID]] = {}
    labels: dict[str, str] = {}
    for role, event_id in rows:
        canonical = canonical_volunteer_role(role)
        if canonical is None:
            continue
        events_by_role.setdefault(canonical.key, set()).add(event_id)
        labels[canonical.key] = canonical.label
    if not events_by_role:
        return None
    key = min(events_by_role, key=lambda item: (-len(events_by_role[item]), labels[item].casefold()))
    return {"role": labels[key], "count": len(events_by_role[key])}


def _location_home_distance(
    db: Session, user: User, identity: LocationIdentity
) -> dict[str, object] | None:
    """Плитка «сколько отсюда до дома» — координаты берём у любой строки
    идентичности: у parkrun-строк своих нет, но связка с действующей системой
    отдаёт точку площадки (см. LocationCatalogIndex.coordinates_for)."""
    coordinates: tuple[float | None, float | None] = (None, None)
    for location, platform_code in identity.locations:
        coordinates = identity.catalog_index.coordinates_for(location, platform_code)
        if coordinates[0] is not None and coordinates[1] is not None:
            break
    return location_distance_from_home(
        db,
        user,
        identity.identity_key,
        coordinates,
        catalog_index=identity.catalog_index,
    )


def build_location_personal_stats(db: Session, user: User, slug: str) -> dict[str, object] | None:
    """Личная статистика пользователя на локации (блок «Вы на этой локации»).

    Без кэша: выборка per-user дешёвая (фильтр по platform_links.user_id),
    а общий Redis-кэш страницы её содержать не может.
    """
    user_id = user.id
    identity = resolve_location_identity(db, slug)
    if identity is None:
        return None
    event_ids = _location_event_ids(db, [location.id for location, _code in identity.locations])

    payload: dict[str, object] = {
        "slug": identity.slug,
        "name": identity.name,
        # Канонический ключ идентичности — нужен роуту для проверки оргдоступа.
        "identity_key": identity.identity_key,
        "runs_count": 0,
        "total_runs": 0,
        "best_time_sec": None,
        "best_time_display": None,
        "best_time_date": None,
        "avg_time_sec": None,
        "avg_time_display": None,
        "first_run_date": None,
        "last_run_date": None,
        "volunteering_count": 0,
        "top_volunteer_role": None,
        "gender": None,
        "rank_by_runs_gender": None,
        "runners_total_gender": None,
        "age_groups": [],
        "home_distance": _location_home_distance(db, user, identity),
    }

    # Все пробежки пользователя (по всем локациям) — для строки «это N% ваших стартов».
    total_runs = (
        db.query(func.count(RunResult.id))
        .join(Participant, RunResult.participant_id == Participant.id)
        .join(PlatformLink, _platform_link_join())
        .filter(PlatformLink.user_id == user_id)
        .scalar()
    )
    payload["total_runs"] = int(total_runs or 0)

    if not event_ids:
        return payload

    rows = (
        db.query(RunResult.event_id, RunResult.finish_time_sec, Event.event_date)
        .join(Event, RunResult.event_id == Event.id)
        .join(Participant, RunResult.participant_id == Participant.id)
        .join(PlatformLink, _platform_link_join())
        .filter(PlatformLink.user_id == user_id, RunResult.event_id.in_(event_ids))
        .all()
    )

    volunteering_count = (
        db.query(func.count(func.distinct(VolunteerResult.event_id)))
        .join(Participant, VolunteerResult.participant_id == Participant.id)
        .join(PlatformLink, _platform_link_join())
        .filter(PlatformLink.user_id == user_id, VolunteerResult.event_id.in_(event_ids))
        .scalar()
    )
    payload["volunteering_count"] = int(volunteering_count or 0)
    payload["top_volunteer_role"] = _top_volunteer_role_here(db, user_id, event_ids)

    if not rows:
        return payload

    runs_count = len({event_id for event_id, _time, _date in rows})
    payload["runs_count"] = runs_count

    timed = [(int(time_sec), event_date) for _eid, time_sec, event_date in rows if time_sec and time_sec > 0]
    if timed:
        best_sec, best_date = min(timed, key=lambda item: (item[0], item[1] or date.max))
        payload["best_time_sec"] = best_sec
        payload["best_time_display"] = format_finish_time_display(best_sec)
        payload["best_time_date"] = best_date
        avg_sec = round(sum(sec for sec, _d in timed) / len(timed))
        payload["avg_time_sec"] = avg_sec
        payload["avg_time_display"] = format_finish_time_display(avg_sec)

    # У parkrun-эпохи бывают события-заглушки с датой 1970 — в первое/последнее не берём.
    real_dates = [event_date for _eid, _t, event_date in rows if event_date and event_date > date(1970, 1, 1)]
    if real_dates:
        payload["first_run_date"] = min(real_dates)
        payload["last_run_date"] = max(real_dates)

    # Место в топе локации по числу пробежек — внутри своего пола. Группировка
    # та же, что в build_location_leaders: привязанные аккаунты сливаются по
    # user_id. Общего места (без разбивки) больше нет: сравнение мужчин и женщин
    # одной строкой мало что говорит, а в знаменатель попадали неопознанные
    # финишёры протокола — у них пол не заполнен, и срез по полу отсекает их сам.
    #
    # Пол берём из participants.gender — он
    # материализован по всем системам (gender_position_service), поэтому срез
    # работает и на parkrun-эпохе, где протокол категории не публикует.
    my_gender = (
        db.query(Participant.gender)
        .join(PlatformLink, _platform_link_join())
        .filter(PlatformLink.user_id == user_id, Participant.gender.isnot(None))
        .limit(1)
        .scalar()
    )
    if my_gender in ("male", "female"):
        gender_runs = (
            db.query(func.count(func.distinct(RunResult.event_id)).label("runs"))
            .join(Participant, RunResult.participant_id == Participant.id)
            .outerjoin(PlatformLink, _platform_link_join())
            .filter(RunResult.event_id.in_(event_ids), Participant.gender == my_gender)
            .group_by(func.coalesce(PlatformLink.user_id, RunResult.participant_id))
            .subquery()
        )
        ahead_gender, total_gender = (
            db.query(
                func.count(case((gender_runs.c.runs > runs_count, 1))),
                func.count(),
            )
            .select_from(gender_runs)
            .one()
        )
        payload["gender"] = my_gender
        payload["rank_by_runs_gender"] = int(ahead_gender) + 1
        payload["runners_total_gender"] = int(total_gender)

    payload["age_groups"] = build_location_age_group_standings(db, user_id, event_ids)

    return payload
