#!/usr/bin/env python3
"""Align location_catalog.canonical_name with platform Location.name (5verst/s95)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import get_session_factory
from app.models import Location, LocationCatalog, LocationCatalogLink, Platform
from app.platform_adapters.five_verst import bulk_parser
from app.platform_adapters.five_verst.http import fetch_html
from app.s95.fetch import fetch_page_html
from app.s95.parsers.location import parse_event_location_page as parse_s95_location_page


def _fetch_five_verst_name(slug: str) -> str | None:
    source_url = bulk_parser._location_url(slug)
    html = fetch_html(source_url)
    location = bulk_parser.parse_location_home_html(html, slug, source_url)
    return location.name or None


def _fetch_s95_name(slug: str) -> str | None:
    url = f"https://s95.ru/events/{slug}"
    html = fetch_page_html(url, reason="sync_catalog_name")
    location = parse_s95_location_page(html, url, location_external_key=slug)
    return location.name or None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", help="Only update one external_key (e.g. mytishchicentralpark)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db = get_session_factory()()
    try:
        updated = 0
        query = db.query(LocationCatalogLink, LocationCatalog, Platform, Location).join(
            LocationCatalog, LocationCatalogLink.catalog_id == LocationCatalog.id
        ).join(Platform, LocationCatalogLink.platform_id == Platform.id).outerjoin(
            Location, LocationCatalogLink.location_id == Location.id
        )
        if args.slug:
            query = query.filter(LocationCatalogLink.external_key == args.slug)

        for link, catalog, platform, location in query.all():
            if platform.code not in {"five_verst", "s95"}:
                continue
            if args.slug and link.external_key != args.slug:
                continue

            source_name = location.name if location is not None else None
            if not source_name:
                if platform.code == "five_verst":
                    source_name = _fetch_five_verst_name(link.external_key)
                else:
                    source_name = _fetch_s95_name(link.external_key)
            if not source_name or source_name == catalog.canonical_name:
                continue

            print(
                f"{platform.code}/{link.external_key}: "
                f"{catalog.canonical_name!r} -> {source_name!r}",
                flush=True,
            )
            if not args.dry_run:
                catalog.canonical_name = source_name
                if location is not None and location.name != source_name:
                    location.name = source_name
                updated += 1

        if not args.dry_run:
            db.commit()
        print(f"updated={updated}", flush=True)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
