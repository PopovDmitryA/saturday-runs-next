from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import Location, LocationCatalog, LocationCatalogLink, Platform

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
) -> str:
    if catalog is None or not should_use_catalog_display(catalog, platform_code):
        return source_name
    return catalog.canonical_name or source_name


class LocationCatalogIndex:
    def __init__(self, db: Session) -> None:
        self._by_location_id: dict[UUID, LocationCatalog] = {}
        self._by_platform_slug: dict[tuple[str, str], LocationCatalog] = {}
        self._catalogs: dict[UUID, LocationCatalog] = {}
        self._catalog_coords: dict[UUID, tuple[float, float]] = {}
        self._catalog_paused: dict[UUID, bool] = {}
        self._load(db)

    def _index_platform_slug(self, platform_code: str, slug: str, catalog: LocationCatalog) -> None:
        if not slug:
            return
        self._by_platform_slug[(platform_code, slug)] = catalog
        normalized = normalize_location_slug(slug)
        if normalized:
            self._by_platform_slug[(platform_code, normalized)] = catalog

    def _load(self, db: Session) -> None:
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
                self._catalog_coords.setdefault(catalog.id, (location.latitude, location.longitude))
            if location is not None and getattr(location, "is_paused", False):
                self._catalog_paused[catalog.id] = True

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

    def is_paused(self, location: Location, platform_code: str) -> bool:
        if getattr(location, "is_cancelled", False):
            return False
        catalog = self.get_for_location(location, platform_code)
        if catalog is not None:
            if catalog.is_closed or self._catalog_paused.get(catalog.id, False):
                return True
        return bool(getattr(location, "is_paused", False))

    def is_paused_identity_key(self, identity_key: str) -> bool:
        catalog = self.get_for_identity_key(identity_key)
        if catalog is None:
            return False
        if catalog.is_closed:
            return True
        return self._catalog_paused.get(catalog.id, False)

    def display_name(self, location: Location, platform_code: str) -> str:
        catalog = self.get_for_location(location, platform_code)
        return resolve_location_display_name(
            catalog,
            platform_code=platform_code,
            source_name=location.name,
        )

    def canonical_identity_key(self, location: Location, platform_code: str) -> str:
        """Stable key for counting one physical location across platform migrations."""
        catalog = self.get_for_location(location, platform_code)
        if catalog is not None:
            return f"catalog:{catalog.id}"
        return f"location:{location.id}"


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
    db.flush()
    return True
