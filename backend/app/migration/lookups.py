from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.migration.helpers import slug_from_5verst_url, slug_from_s95_url, slugify_parkrun_name
from app.migration.legacy_db import legacy_rows
from app.models import Event, Location, Platform


@dataclass(frozen=True)
class LegacyEventMeta:
    slug: str
    event_number: int | None
    is_test_event: bool
    finishers_count: int | None
    volunteers_count: int | None
    source_url: str | None


class FiveVerstLookups:
    def __init__(self) -> None:
        self.slug_by_name: dict[str, str] = {}
        self.name_by_slug: dict[str, str] = {}
        self.event_meta_by_name_date: dict[tuple[str, str], LegacyEventMeta] = {}

    def register_name_slug(self, name: str, slug: str) -> None:
        cleaned_name = name.strip()
        cleaned_slug = slug.strip()
        if not cleaned_name or not cleaned_slug:
            return
        self.slug_by_name.setdefault(cleaned_name, cleaned_slug)
        self.name_by_slug.setdefault(cleaned_slug, cleaned_name)

    def load_from_legacy(self, conn) -> None:
        for row in legacy_rows(
            conn,
            """
            SELECT name_point, link_point
            FROM general_location
            WHERE name_point IS NOT NULL AND link_point IS NOT NULL
            """,
        ):
            name = str(row["name_point"]).strip()
            slug = slug_from_5verst_url(str(row["link_point"]))
            if name and slug:
                self.register_name_slug(name, slug)

        for row in legacy_rows(
            conn,
            """
            SELECT name_point, link_point
            FROM general_link_all_location
            WHERE name_point IS NOT NULL AND link_point IS NOT NULL
            """,
        ):
            name = str(row["name_point"]).strip()
            if name in self.slug_by_name:
                continue
            slug = slug_from_5verst_url(str(row["link_point"]))
            if name and slug:
                self.register_name_slug(name, slug)

        for row in legacy_rows(
            conn,
            """
            SELECT DISTINCT ON (name_point)
                name_point,
                link_event
            FROM list_all_events
            WHERE name_point IS NOT NULL
              AND link_event IS NOT NULL
            ORDER BY name_point, date_event DESC
            """,
        ):
            name = str(row["name_point"]).strip()
            if name in self.slug_by_name:
                continue
            slug = slug_from_5verst_url(str(row["link_event"]))
            if name and slug:
                self.register_name_slug(name, slug)

        for row in legacy_rows(
            conn,
            """
            SELECT
                name_point,
                date_event,
                index_event,
                is_test,
                count_runners,
                count_vol,
                link_event
            FROM list_all_events
            WHERE name_point IS NOT NULL AND date_event IS NOT NULL
            """,
        ):
            name = str(row["name_point"]).strip()
            slug = self.slug_by_name.get(name)
            if not slug and row.get("link_event"):
                slug = slug_from_5verst_url(str(row["link_event"]))
                if slug:
                    self.register_name_slug(name, slug)
            if not slug:
                continue
            event_date = row["date_event"]
            if hasattr(event_date, "date"):
                event_date = event_date.date()
            date_key = event_date.isoformat()
            index_event = row.get("index_event")
            event_number = int(index_event) if index_event is not None else None
            self.event_meta_by_name_date[(name, date_key)] = LegacyEventMeta(
                slug=slug,
                event_number=event_number,
                is_test_event=bool(row.get("is_test")),
                finishers_count=int(row["count_runners"]) if row.get("count_runners") is not None else None,
                volunteers_count=int(row["count_vol"]) if row.get("count_vol") is not None else None,
                source_url=(str(row["link_event"]).strip() if row.get("link_event") else None),
            )

    def resolve_slug(self, name_point: str) -> str | None:
        return self.slug_by_name.get(name_point.strip())


class TargetLookups:
    def __init__(self, db: Session, platform: Platform) -> None:
        self.db = db
        self.platform = platform
        self.locations_by_slug: dict[str, Location] = {}
        self.events_by_slug_date: dict[tuple[str, str], Event] = {}

    def preload_locations(self) -> None:
        rows = (
            self.db.query(Location)
            .filter(Location.platform_id == self.platform.id)
            .all()
        )
        for row in rows:
            self.locations_by_slug[row.external_key] = row

    def preload_events(self) -> None:
        rows = (
            self.db.query(Event, Location.external_key)
            .join(Location, Event.location_id == Location.id)
            .filter(Event.platform_id == self.platform.id)
            .all()
        )
        for event, slug in rows:
            self.events_by_slug_date[(slug, event.event_date.isoformat())] = event

    def remember_location(self, location: Location) -> None:
        self.locations_by_slug[location.external_key] = location

    def remember_event(self, slug: str, event: Event) -> None:
        self.events_by_slug_date[(slug, event.event_date.isoformat())] = event

    def get_location(self, slug: str) -> Location | None:
        return self.locations_by_slug.get(slug)

    def get_event(self, slug: str, event_date) -> Event | None:
        key = (slug, event_date.isoformat() if hasattr(event_date, "isoformat") else str(event_date))
        return self.events_by_slug_date.get(key)


class ParkrunLookups:
    PARKRUN_SUMMARY_SLUG = "parkrun-summary"
    PARKRUN_SUMMARY_NAME = "parkrun (сводка ролей)"

    def __init__(self, catalog_path: Path | None = None) -> None:
        self.slug_by_name: dict[str, str] = {}
        self.display_name_by_user: dict[str, str] = {}
        self._load_catalog(catalog_path)

    def load_users_from_legacy(self, conn) -> None:
        for row in legacy_rows(
            conn,
            """
            SELECT user_id, actual_name_runner, name_runner
            FROM parkrun_users
            WHERE user_id IS NOT NULL
            """,
        ):
            user_id = str(row["user_id"]).strip()
            display = (
                str(row["actual_name_runner"]).strip()
                if row.get("actual_name_runner")
                else (str(row["name_runner"]).strip() if row.get("name_runner") else "")
            )
            if user_id and display:
                self.display_name_by_user[user_id] = display

    def participant_name(self, user_id: str) -> str:
        return self.display_name_by_user.get(user_id, f"parkrunner {user_id}")

    def _load_catalog(self, catalog_path: Path | None) -> None:
        if catalog_path is None:
            repo_root = Path(__file__).resolve().parents[3]
            catalog_path = repo_root / "data" / "location_catalog.json"
        if not catalog_path.is_file():
            return
        entries = json.loads(catalog_path.read_text(encoding="utf-8"))
        for entry in entries:
            canonical = str(entry.get("canonical_name") or "").strip()
            legacy_slug = str(entry.get("legacy_parkrun_slug") or "").strip()
            parkrun_name = str(entry.get("parkrun_location") or "").strip()
            slug = legacy_slug
            for link in entry.get("links") or []:
                if link.get("platform") == "parkrun" and link.get("external_key"):
                    slug = str(link["external_key"]).strip()
                    break
            if not slug:
                continue
            if canonical:
                self.slug_by_name[canonical] = slug
            if parkrun_name:
                self.slug_by_name[parkrun_name] = slug
                self.slug_by_name[parkrun_name.lower()] = slug

    def resolve_slug(self, name_point: str) -> str:
        cleaned = name_point.strip()
        if cleaned in self.slug_by_name:
            return self.slug_by_name[cleaned]
        lowered = cleaned.lower()
        if lowered in self.slug_by_name:
            return self.slug_by_name[lowered]
        return slugify_parkrun_name(cleaned)


class S95Lookups:
    def __init__(self) -> None:
        self.slug_by_name: dict[str, str] = {}

    def load_from_legacy(self, conn) -> None:
        for row in legacy_rows(
            conn,
            """
            SELECT name_point, link_point, full_name_point
            FROM s95_location
            WHERE link_point IS NOT NULL
            """,
        ):
            slug = slug_from_s95_url(str(row["link_point"]))
            if not slug:
                continue
            for name_col in ("name_point", "full_name_point"):
                name = row.get(name_col)
                if name:
                    self.slug_by_name[str(name).strip()] = slug

    def resolve_slug(self, name_point: str) -> str | None:
        return self.slug_by_name.get(name_point.strip())
