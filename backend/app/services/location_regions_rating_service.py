"""Рейтинг регионов по числу локаций — сквозной по действующим системам.

Перенос Grafana-дашборда «Локаций 5 Вёрст по регионам»: там был список регионов
и счётчик площадок 5 вёрст. На сайте регион считается сразу по трём действующим
системам — 5 вёрст, S95 и RunPark (решение Дмитрия 27.08.2026), — а колонки
показывают, из чего сложилось число.

Что считается строкой. Не строка таблицы locations, а физическая площадка:
идентичность каталога (catalog_identity_key). Парк, живущий и в 5 вёрст, и в
RunPark, — одна локация региона, поэтому сумма колонок систем бывает больше
общего счётчика (то же правило, что у рейтинга туризма: «всего» — объединение,
а не сумма).

Зарубежные площадки собраны по СТРАНАМ, а не по регионам — ровно как на карте
(RegionChoropleth: Россия раскрашена по регионам, остальной мир — по странам).
Регион у Гомеля или Нови-Сада в нашей БД есть, но ранжировать белорусскую
область рядом с Московской бессмысленно, поэтому зарубежье идёт отдельной
таблицей.

parkrun в рейтинг не входит. Его российские площадки закрыты с 2022 года (все
как одна «не действует»), а region у parkrun-строк почти везде пуст — считать
по ним «сколько локаций в регионе» нечего. Фильтр «Система» знает только
действующие системы; parkrun, если он придёт из общего фильтра хаба, молча
откатывается на общий зачёт — как в остальных рейтингах.

Площадки со статусом «не действует» из счётчика исключены и вынесены отдельным
числом: рейтинг отвечает на вопрос «где сегодня можно побегать». «Отмена
ближайшего старта» и «скоро откроется» — статусы живой площадки, они в счёте
остаются (здесь рейтинг расходится с хороплетом карты, который прячет и
отменённые).
"""

from __future__ import annotations

import json
from typing import Any, cast

import redis
from sqlalchemy.orm import Session

from app.core.redis_client import get_redis_client
from app.geo.region_names import region_display_name, region_key
from app.services.location_catalog_table_service import build_catalog_locations_table

# Системы, площадки которых считаем. Порядок значим: он же приоритет при
# определении региона площадки, живущей сразу в нескольких системах.
RATING_PLATFORMS: tuple[str, ...] = ("five_verst", "s95", "runpark")

# Значения фильтра «Система». parkrun сюда не входит намеренно — см. модульный
# докстринг.
PLATFORM_FILTER_VALUES: tuple[str, ...] = ("all", *RATING_PLATFORMS)

PLATFORM_LABELS: dict[str, str] = {
    "five_verst": "5 вёрст",
    "s95": "С95",
    "runpark": "RunPark",
}

RUSSIA = "Россия"

# Считается всё из каталога локаций — того же снимка, что рисует карту, — и
# стоит это несколько секунд. Состав каталога меняется синками реестров раз в
# сутки, поэтому снимок живёт в Redis с тем же TTL, что и сам каталог, и гасится
# вместе с ним (flush_location_catalog_caches). Под одним ключом лежат сразу все
# срезы фильтра «Система»: считаются они из одного прохода по каталогу, и
# кэшировать их порознь значило бы перечитать каталог четыре раза.
RATING_CACHE_KEY = "location-regions-rating:v1"
RATING_CACHE_TTL_SECONDS = 3 * 60 * 60


def normalize_platform(value: str | None) -> str:
    """Незнакомая система — общий зачёт. Ссылку из чата рейтинг обязан открыть."""
    if not value or value not in PLATFORM_FILTER_VALUES:
        return "all"
    return value


def _platform_rank(platform_code: str) -> int:
    try:
        return RATING_PLATFORMS.index(platform_code)
    except ValueError:
        return len(RATING_PLATFORMS)


class _Identity:
    """Одна физическая площадка: её строки в системах и место на карте."""

    __slots__ = ("best_rank", "city", "country", "region", "platforms", "paused_rows", "rows")

    def __init__(self) -> None:
        self.best_rank = len(RATING_PLATFORMS)
        self.city: str | None = None
        self.country: str | None = None
        self.region: str | None = None
        self.platforms: set[str] = set()
        self.paused_rows = 0
        self.rows = 0

    def add(self, row: dict[str, Any]) -> None:
        platform_code = str(row["platform_code"])
        self.platforms.add(platform_code)
        self.rows += 1
        if row.get("is_paused"):
            self.paused_rows += 1
        rank = _platform_rank(platform_code)
        # География — со строки самой приоритетной системы: у связки одной
        # площадки регион и город изредка расходятся (разный геокод), и
        # побеждать должна действующая система, а не порядок строк из БД.
        if rank < self.best_rank:
            self.best_rank = rank
            self.city = row.get("city")
            self.country = row.get("country")
            self.region = row.get("region")

    @property
    def is_paused(self) -> bool:
        """На паузе — только если стоят все строки площадки."""
        return self.rows > 0 and self.paused_rows == self.rows


class _Area:
    """Регион России или зарубежная страна — строка рейтинга."""

    __slots__ = ("cities", "identities", "name", "paused", "scope", "by_platform")

    def __init__(self, name: str, scope: str) -> None:
        self.name = name
        self.scope = scope
        self.identities = 0
        self.paused = 0
        self.cities: set[str] = set()
        self.by_platform: dict[str, int] = {code: 0 for code in RATING_PLATFORMS}


def _collect_identities(rows: list[dict[str, Any]], platform: str) -> dict[str, _Identity]:
    identities: dict[str, _Identity] = {}
    for row in rows:
        platform_code = str(row["platform_code"])
        if platform_code not in RATING_PLATFORMS:
            continue
        if platform != "all" and platform_code != platform:
            continue
        identity = identities.get(str(row["catalog_identity_key"]))
        if identity is None:
            identity = _Identity()
            identities[str(row["catalog_identity_key"])] = identity
        identity.add(row)
    return identities


def _area_of(identity: _Identity) -> tuple[str, str, str] | None:
    """Ключ, название и вид области, к которой относится площадка."""
    country = (identity.country or "").strip()
    if country and country != RUSSIA:
        return f"country:{country.lower()}", country, "country"
    key = region_key(identity.region)
    if not key:
        return None
    return f"region:{key}", region_display_name(identity.region), "region"


def _area_row(area: _Area, place: int) -> dict[str, Any]:
    return {
        "place": place,
        "name": area.name,
        "scope": area.scope,
        "locations": area.identities,
        "paused": area.paused,
        "cities": len(area.cities),
        "by_platform": dict(area.by_platform),
    }


def _rank(areas: list[_Area]) -> list[dict[str, Any]]:
    """Места по числу локаций; при равенстве — по алфавиту.

    Равные счётчики получают одно место (в хвосте рейтинга их десятки — все
    регионы с одной площадкой), иначе номер строки выглядел бы преимуществом
    одного региона над другим на ровном месте.
    """
    ordered = sorted(areas, key=lambda area: (-area.identities, area.name.lower()))
    rows: list[dict[str, Any]] = []
    place = 0
    previous: int | None = None
    for index, area in enumerate(ordered, start=1):
        if previous is None or area.identities != previous:
            place = index
            previous = area.identities
        rows.append(_area_row(area, place))
    return rows


def _compute_snapshot(rows: list[dict[str, Any]], platform: str) -> dict[str, Any]:
    identities = _collect_identities(rows, platform)

    areas: dict[str, _Area] = {}
    unknown_region = 0
    paused_total = 0
    for identity in identities.values():
        located = _area_of(identity)
        if located is None:
            # Площадка без региона и без зарубежной страны: geo-бэкфилл до неё
            # ещё не дошёл. Молчать о ней нельзя — иначе сумма по таблице не
            # сойдётся с числом локаций на карте.
            unknown_region += 1
            continue
        key, name, scope = located
        area = areas.get(key)
        if area is None:
            area = _Area(name, scope)
            areas[key] = area
        if identity.is_paused:
            area.paused += 1
            paused_total += 1
            continue
        area.identities += 1
        for platform_code in identity.platforms:
            area.by_platform[platform_code] += 1
        if identity.city:
            area.cities.add(identity.city.strip().lower())

    # Регион, где все площадки на паузе, из рейтинга уходит: строка «0 локаций»
    # ничего не ранжирует. Само число паузы остаётся в сводке.
    live_areas = [area for area in areas.values() if area.identities > 0]
    regions = _rank([area for area in live_areas if area.scope == "region"])
    countries = _rank([area for area in live_areas if area.scope == "country"])

    return {
        "platform": platform,
        "platforms": list(PLATFORM_FILTER_VALUES),
        "regions": regions,
        "countries": countries,
        "totals": {
            "regions": len(regions),
            "region_locations": sum(row["locations"] for row in regions),
            "countries": len(countries),
            "country_locations": sum(row["locations"] for row in countries),
            "paused": paused_total,
            "unknown_region": unknown_region,
        },
    }


def _compute_all(db: Session) -> dict[str, Any]:
    """Все срезы фильтра «Система» из одного прохода по каталогу."""
    rows = cast(list[dict[str, Any]], build_catalog_locations_table(db, None)["rows"])
    return {value: _compute_snapshot(rows, value) for value in PLATFORM_FILTER_VALUES}


def _read_cache() -> dict[str, Any] | None:
    try:
        raw = get_redis_client().get(RATING_CACHE_KEY)
    except redis.RedisError:
        return None
    if not isinstance(raw, str):
        return None
    try:
        return cast(dict[str, Any], json.loads(raw))
    except (TypeError, ValueError):
        return None


def _write_cache(payload: dict[str, Any]) -> None:
    try:
        get_redis_client().setex(RATING_CACHE_KEY, RATING_CACHE_TTL_SECONDS, json.dumps(payload))
    except redis.RedisError:
        pass


def invalidate_regions_rating_cache() -> None:
    """Состав каталога изменился — снимок рейтинга устарел."""
    try:
        get_redis_client().delete(RATING_CACHE_KEY)
    except redis.RedisError:
        pass


def build_regions_rating(
    db: Session, *, platform: str | None = "all", use_cache: bool = True
) -> dict[str, Any]:
    """Регионы и зарубежные страны по числу действующих площадок."""
    selected = normalize_platform(platform)
    if use_cache:
        cached = _read_cache()
        if cached is not None and selected in cached:
            return cast(dict[str, Any], cached[selected])

    snapshots = _compute_all(db)
    if use_cache:
        _write_cache(snapshots)
    return cast(dict[str, Any], snapshots[selected])
