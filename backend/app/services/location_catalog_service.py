from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy.orm import Session

from app.location_page_url import PLATFORM_ORDER
from app.models import EventSummary, Location, LocationCatalog, LocationCatalogLink, Platform

DISPLAY_OVERRIDE_PLATFORMS = frozenset({"five_verst", "s95"})


def normalize_location_slug(value: str) -> str:
    """Collapse slug variants: readovsky-park, readovskypark → readovskypark."""
    return re.sub(r"[^a-z0-9]", "", value.lower())


def normalize_platform_code(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().lower().replace(" ", "_")
    mapping = {
        "5_верст": "five_verst",
        "5верst": "five_verst",
        "5_verst": "five_verst",
        "с95": "s95",
        "c95": "s95",
        "parkrun": "parkrun",
        "runpark": "runpark",
        "нет_(только_parkrun)": "parkrun",
    }
    return mapping.get(normalized, normalized)


def should_use_catalog_display(catalog: LocationCatalog, platform_code: str) -> bool:
    if catalog.is_closed:
        return False
    active = normalize_platform_code(catalog.active_platform)
    if active in ("parkrun", "runpark"):
        return False
    return active in DISPLAY_OVERRIDE_PLATFORMS


def resolve_location_display_name(
    catalog: LocationCatalog | None,
    *,
    platform_code: str,
    source_name: str,
    active_platform_name: str | None = None,
) -> str:
    """Как площадка называется сегодня — одинаково во всех системах.

    Смысл переопределения через каталог: результат parkrun-времён не должен
    показываться латиницей, если площадка жива и в 5 вёрст/S95 называется
    по-русски. Поэтому имя берём У ЛОКАЦИИ ДЕЙСТВУЮЩЕЙ СИСТЕМЫ
    (active_platform_name) — это её актуальное название. canonical_name из
    каталога остаётся запасным вариантом: у части узлов там лежит легаси —
    транслитерация времён parkrun («Ufa Botanichesky Sad» у площадки, которая
    в 5 вёрст давно «Парк Лесоводов»), и как основной источник имени оно
    подставляло бы ровно то старое название, от которого мы уходим.
    """
    if catalog is None or not should_use_catalog_display(catalog, platform_code):
        return source_name
    return active_platform_name or catalog.canonical_name or source_name


class LocationCatalogIndex:
    def __init__(self, db: Session) -> None:
        self._by_location_id: dict[UUID, LocationCatalog] = {}
        self._by_platform_slug: dict[tuple[str, str], LocationCatalog] = {}
        self._catalogs: dict[UUID, LocationCatalog] = {}
        self._catalog_coords: dict[UUID, tuple[float, float]] = {}
        # Ранг платформы, из которой взяты текущие координаты узла (см. _coord_rank):
        # нужен, чтобы более приоритетная связка перебивала уже записанную.
        self._catalog_coords_rank: dict[UUID, tuple[int, int, str]] = {}
        # Голоса «на паузе» по каталожному узлу: отдельно от платформ с забегами
        # и от платформ без единого события (у них флаг паузы ничего не значит).
        self._catalog_pause_votes: dict[UUID, tuple[int, int]] = {}
        self._catalog_pause_fallback: dict[UUID, tuple[int, int]] = {}
        # Имя локации действующей системы узла — актуальное название площадки
        # (см. resolve_location_display_name). Рядом храним ранг источника,
        # чтобы при нескольких локациях активной системы победа была за той,
        # где реально бегают, а не за случайной.
        self._catalog_active_names: dict[UUID, str] = {}
        self._catalog_active_name_rank: dict[UUID, tuple[int, str]] = {}
        self._load(db)

    def _index_platform_slug(self, platform_code: str, slug: str, catalog: LocationCatalog) -> None:
        if not slug:
            return
        self._by_platform_slug[(platform_code, slug)] = catalog
        normalized = normalize_location_slug(slug)
        if normalized:
            self._by_platform_slug[(platform_code, normalized)] = catalog

    def _set_coords(
        self,
        catalog: LocationCatalog,
        platform_code: str,
        coords: tuple[float, float],
    ) -> None:
        """Координаты узла берём у действующей платформы, а не у первой попавшейся.

        Связки одного узла приходят из БД в произвольном порядке, и раньше побеждала
        любая из них. У «Мытищи Центральный парк» так выигрывал runpark: его точка
        сбора («лавочки у зелёного моста») лежит в ~3 км от старта действующих
        5 вёрст, и страница показывала на карте чужое место.
        """
        rank = self._coord_rank(catalog, platform_code)
        current = self._catalog_coords_rank.get(catalog.id)
        if current is not None and current <= rank:
            return
        self._catalog_coords[catalog.id] = coords
        self._catalog_coords_rank[catalog.id] = rank

    @staticmethod
    def _coord_rank(catalog: LocationCatalog, platform_code: str) -> tuple[int, int, str]:
        active = normalize_platform_code(catalog.active_platform)
        try:
            order = PLATFORM_ORDER.index(platform_code)
        except ValueError:
            order = len(PLATFORM_ORDER)
        # platform_code в хвосте — детерминированный тай-брейк для платформ вне списка.
        return (0 if platform_code == active else 1, order, platform_code)

    def _set_active_name(
        self,
        catalog: LocationCatalog,
        platform_code: str,
        location: Location,
        *,
        has_events: bool,
    ) -> None:
        """Запомнить название площадки по локации её действующей системы."""
        if normalize_platform_code(catalog.active_platform) != normalize_platform_code(platform_code):
            return
        name = (location.name or "").strip()
        if not name:
            return
        # external_key в хвосте — детерминированный тай-брейк: порядок строк из
        # БД произвольный, а имя узла между пересборками индекса меняться не должно.
        rank = (0 if has_events else 1, location.external_key or "")
        current = self._catalog_active_name_rank.get(catalog.id)
        if current is not None and current <= rank:
            return
        self._catalog_active_names[catalog.id] = name
        self._catalog_active_name_rank[catalog.id] = rank

    def _add_pause_vote(self, catalog_id: UUID, *, has_events: bool, is_paused: bool) -> None:
        target = self._catalog_pause_votes if has_events else self._catalog_pause_fallback
        paused, total = target.get(catalog_id, (0, 0))
        target[catalog_id] = (paused + int(is_paused), total + 1)

    def _load(self, db: Session) -> None:
        locations_with_events = {
            location_id
            for (location_id,) in db.query(EventSummary.location_id).distinct().all()
            if location_id is not None
        }
        rows = (
            db.query(LocationCatalogLink, LocationCatalog, Platform, Location)
            .join(LocationCatalog, LocationCatalogLink.catalog_id == LocationCatalog.id)
            .join(Platform, LocationCatalogLink.platform_id == Platform.id)
            .outerjoin(Location, LocationCatalogLink.location_id == Location.id)
            .all()
        )
        for link, catalog, platform, location in rows:
            self._catalogs[catalog.id] = catalog
            self._index_platform_slug(platform.code, link.external_key, catalog)
            if catalog.legacy_parkrun_slug:
                self._index_platform_slug(platform.code, catalog.legacy_parkrun_slug, catalog)
            if link.location_id is not None:
                self._by_location_id[link.location_id] = catalog
            if location is not None:
                self._index_platform_slug(platform.code, location.external_key, catalog)
            if location is not None and location.latitude is not None and location.longitude is not None:
                self._set_coords(catalog, platform.code, (location.latitude, location.longitude))
            if location is not None and not getattr(location, "is_cancelled", False):
                self._add_pause_vote(
                    catalog.id,
                    has_events=location.id in locations_with_events,
                    is_paused=bool(getattr(location, "is_paused", False)),
                )
                self._set_active_name(
                    catalog,
                    platform.code,
                    location,
                    has_events=location.id in locations_with_events,
                )

    def get_for_location(self, location: Location, platform_code: str) -> LocationCatalog | None:
        if location.id in self._by_location_id:
            return self._by_location_id[location.id]
        catalog = self._by_platform_slug.get((platform_code, location.external_key))
        if catalog is not None:
            return catalog
        normalized = normalize_location_slug(location.external_key)
        if normalized:
            return self._by_platform_slug.get((platform_code, normalized))
        return None

    def get_for_identity_key(self, identity_key: str) -> LocationCatalog | None:
        if not identity_key.startswith("catalog:"):
            return None
        catalog_id = UUID(identity_key.split(":", 1)[1])
        return self._catalogs.get(catalog_id)

    def coordinates_for(self, location: Location, platform_code: str) -> tuple[float | None, float | None]:
        catalog = self.get_for_location(location, platform_code)
        if catalog is not None:
            coords = self._catalog_coords.get(catalog.id)
            if coords is not None:
                return coords
        if location.latitude is not None and location.longitude is not None:
            return location.latitude, location.longitude
        return None, None

    def coordinates_for_identity_key(self, identity_key: str) -> tuple[float | None, float | None]:
        catalog = self.get_for_identity_key(identity_key)
        if catalog is None:
            return None, None
        coords = self._catalog_coords.get(catalog.id)
        if coords is None:
            return None, None
        return coords

    def _catalog_is_paused(self, catalog: LocationCatalog) -> bool:
        """Узел на паузе, только если на паузе все платформы, где вообще есть забеги.

        Иначе одна остановленная связка (напр. историческая runpark-строка без
        событий) гасила бы локацию, где по другой платформе забеги идут еженедельно.
        """
        if catalog.is_closed:
            return True
        paused, total = self._catalog_pause_votes.get(catalog.id, (0, 0))
        if total == 0:
            # Ни одной платформы с событиями — считаем по всем связкам разом.
            paused, total = self._catalog_pause_fallback.get(catalog.id, (0, 0))
            return total > 0 and paused > 0
        return paused == total

    def is_paused(self, location: Location, platform_code: str) -> bool:
        if getattr(location, "is_cancelled", False):
            return False
        catalog = self.get_for_location(location, platform_code)
        if catalog is not None:
            return self._catalog_is_paused(catalog)
        return bool(getattr(location, "is_paused", False))

    def is_paused_identity_key(self, identity_key: str) -> bool:
        catalog = self.get_for_identity_key(identity_key)
        if catalog is None:
            return False
        return self._catalog_is_paused(catalog)

    def display_name(self, location: Location, platform_code: str) -> str:
        catalog = self.get_for_location(location, platform_code)
        return resolve_location_display_name(
            catalog,
            platform_code=platform_code,
            source_name=location.name,
            active_platform_name=self.active_platform_name(catalog),
        )

    def active_platform_name(self, catalog: LocationCatalog | None) -> str | None:
        """Название площадки по локации её действующей системы (None, если такой
        локации у узла нет — например, у него осталась одна parkrun-история)."""
        if catalog is None:
            return None
        return self._catalog_active_names.get(catalog.id)

    def canonical_identity_key(self, location: Location, platform_code: str) -> str:
        """Stable key for counting one physical location across platform migrations."""
        catalog = self.get_for_location(location, platform_code)
        if catalog is not None:
            return f"catalog:{catalog.id}"
        return f"location:{location.id}"


# Русские паркраны, которых нет в каталоге локаций (закрылись без преемника в
# 5 вёрст/S95 и потому не попали в location_catalog). Дополнять по мере
# обнаружения: SELECT parkrun-локации без location_catalog_links с русскими
# названиями. parkrun ушёл из России в 2022 — список конечен. Сейчас пуст:
# park-30-letiya-oktyabrya (Боровичи) заведён в каталог связкой с 5 вёрст.
RUSSIAN_PARKRUN_SLUGS_OUTSIDE_CATALOG: frozenset[str] = frozenset()


def is_foreign_location(location: Location, platform_code: str, catalog_index: LocationCatalogIndex) -> bool:
    """Зарубежная ли физическая площадка.

    Для parkrun поле country в БД всегда «United Kingdom» (источник —
    parkrun.org.uk) и не отражает реальную страну — вместо него смотрим, есть
    ли связь с каталогом локаций (площадка когда-то была/стала 5 вёрст/S95)
    либо явное исключение. Для остальных платформ country достоверна (для
    s95.rs/s95.by это реальная зарубежная страна)."""
    if platform_code == "parkrun":
        if location.external_key in RUSSIAN_PARKRUN_SLUGS_OUTSIDE_CATALOG:
            return False
        return catalog_index.get_for_location(location, platform_code) is None
    country = (location.country or "").strip()
    return bool(country) and country != "Россия"


def backfill_city_from_catalog(db: Session, location: Location) -> bool:
    """If location.city is None, look up the catalog for a linked location that has a city.

    Useful for parkrun locations whose source pages don't expose a city field.
    Returns True if the city was updated.
    """
    if location.city is not None:
        return False
    link = (
        db.query(LocationCatalogLink)
        .filter(LocationCatalogLink.location_id == location.id)
        .one_or_none()
    )
    if link is None:
        return False
    city: str | None = (
        db.query(Location.city)
        .join(LocationCatalogLink, LocationCatalogLink.location_id == Location.id)
        .filter(
            LocationCatalogLink.catalog_id == link.catalog_id,
            LocationCatalogLink.location_id != location.id,
            Location.city.isnot(None),
        )
        .order_by(Location.city)
        .limit(1)
        .scalar()
    )
    if city is None:
        return False
    location.city = city
    return True


def backfill_region_from_catalog(db: Session, location: Location) -> bool:
    """If location.region is None, look up the catalog for a linked location that has a region.

    Useful for parkrun locations whose source pages don't expose a region field.
    Returns True if the region was updated.
    """
    if location.region is not None:
        return False
    link = (
        db.query(LocationCatalogLink)
        .filter(LocationCatalogLink.location_id == location.id)
        .one_or_none()
    )
    if link is None:
        return False
    region: str | None = (
        db.query(Location.region)
        .join(LocationCatalogLink, LocationCatalogLink.location_id == Location.id)
        .filter(
            LocationCatalogLink.catalog_id == link.catalog_id,
            LocationCatalogLink.location_id != location.id,
            Location.region.isnot(None),
        )
        .order_by(Location.region)
        .limit(1)
        .scalar()
    )
    if region is None:
        return False
    location.region = region
    db.flush()
    return True
