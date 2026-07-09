#!/usr/bin/env python3
"""Manual club catalog operations: cross-platform merge and slug-change handling.

Examples:
    # Say that 5verst club X and s95 club Y are the same club:
    python scripts/link_club_catalog.py --five-verst myruno --s95 izmylong --name "MyRUNo"

    # Handle a 5verst slug change (old inactive club → new club):
    python scripts/link_club_catalog.py --rename-slug old-slug new-slug

    # Show catalog state (multi-platform entries and orphans):
    python scripts/link_club_catalog.py --list
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.script_runtime import add_bootstrap_args, apply_bootstrap_args, bootstrap_from_env
from app.db.session import get_session_factory


def _get_platform(db, code: str):
    from app.sync import upsert

    return upsert.get_platform(db, code)


def _get_link(db, platform, external_key: str):
    from app.models import ClubCatalogLink

    return (
        db.query(ClubCatalogLink)
        .filter(ClubCatalogLink.platform_id == platform.id, ClubCatalogLink.external_key == external_key)
        .one_or_none()
    )


def _get_club(db, platform, external_key: str):
    from app.models import Club

    return (
        db.query(Club)
        .filter(Club.platform_id == platform.id, Club.external_key == external_key)
        .one_or_none()
    )


def _delete_catalog_if_orphan(db, catalog_id) -> bool:
    from app.models import ClubCatalog, ClubCatalogLink

    remaining = db.query(ClubCatalogLink).filter(ClubCatalogLink.catalog_id == catalog_id).count()
    if remaining == 0:
        catalog = db.get(ClubCatalog, catalog_id)
        if catalog is not None:
            db.delete(catalog)
            return True
    return False


def cmd_merge(db, five_verst_slug: str, s95_slug: str, name: str | None) -> dict[str, object]:
    """Point both platform slugs to one catalog entry with link_source='manual'."""
    from app.models import ClubCatalog, ClubCatalogLink
    from app.sync.five_verst_clubs import normalize_club_name

    fv_platform = _get_platform(db, "five_verst")
    s95_platform = _get_platform(db, "s95")

    fv_link = _get_link(db, fv_platform, five_verst_slug)
    fv_club = _get_club(db, fv_platform, five_verst_slug)
    if fv_link is None and fv_club is None:
        raise SystemExit(f"5verst club not found: {five_verst_slug}")

    # Target catalog: prefer the 5verst one, else create.
    catalog = fv_link.catalog if fv_link is not None else None
    if catalog is None:
        canonical = name or (fv_club.name if fv_club is not None else five_verst_slug)
        catalog = ClubCatalog(canonical_name=canonical, normalized_name=normalize_club_name(canonical))
        db.add(catalog)
        db.flush()
    if name:
        catalog.canonical_name = name
        catalog.normalized_name = normalize_club_name(name)

    actions: list[str] = []
    if fv_link is None:
        db.add(
            ClubCatalogLink(
                catalog_id=catalog.id,
                platform_id=fv_platform.id,
                external_key=five_verst_slug,
                club_id=fv_club.id if fv_club is not None else None,
                link_source="manual",
            )
        )
        actions.append(f"created manual link five_verst:{five_verst_slug}")
    else:
        fv_link.link_source = "manual"
        actions.append(f"kept link five_verst:{five_verst_slug} (marked manual)")

    s95_link = _get_link(db, s95_platform, s95_slug)
    s95_club = _get_club(db, s95_platform, s95_slug)
    if s95_link is not None:
        old_catalog_id = s95_link.catalog_id
        s95_link.catalog_id = catalog.id
        s95_link.link_source = "manual"
        actions.append(f"moved link s95:{s95_slug} to catalog {catalog.canonical_name!r}")
        if old_catalog_id != catalog.id and _delete_catalog_if_orphan(db, old_catalog_id):
            actions.append("deleted orphan catalog entry")
    else:
        db.add(
            ClubCatalogLink(
                catalog_id=catalog.id,
                platform_id=s95_platform.id,
                external_key=s95_slug,
                club_id=s95_club.id if s95_club is not None else None,
                link_source="manual",
            )
        )
        actions.append(f"created manual link s95:{s95_slug}")

    db.flush()
    return {"catalog": catalog.canonical_name, "actions": actions}


def cmd_rename_slug(db, old_slug: str, new_slug: str) -> dict[str, object]:
    """Merge an old (inactive) 5verst club record into the new-slug record after a slug change."""
    from app.models import ClubMember

    platform = _get_platform(db, "five_verst")
    old_club = _get_club(db, platform, old_slug)
    new_club = _get_club(db, platform, new_slug)
    if old_club is None:
        raise SystemExit(f"old club not found: {old_slug}")
    if new_club is None:
        raise SystemExit(f"new club not found: {new_slug}")

    actions: list[str] = []

    # Preserve membership history: keep the earliest first_seen_at.
    old_members = db.query(ClubMember).filter(ClubMember.club_id == old_club.id).all()
    new_members = {row.participant_id: row for row in db.query(ClubMember).filter(ClubMember.club_id == new_club.id)}
    moved = 0
    merged = 0
    for row in old_members:
        target = new_members.get(row.participant_id)
        if target is None:
            row.club_id = new_club.id
            moved += 1
        else:
            if row.first_seen_at < target.first_seen_at:
                target.first_seen_at = row.first_seen_at
            db.delete(row)
            merged += 1
    actions.append(f"memberships: moved {moved}, merged {merged}")

    # Catalog: drop the old link (and its catalog if orphaned); the new slug keeps/creates its own.
    old_link = _get_link(db, platform, old_slug)
    if old_link is not None:
        old_catalog_id = old_link.catalog_id
        db.delete(old_link)
        db.flush()
        if _delete_catalog_if_orphan(db, old_catalog_id):
            actions.append("deleted orphan catalog entry of old slug")

    new_link = _get_link(db, platform, new_slug)
    if new_link is not None:
        new_link.link_source = "manual"

    if new_club.created_at > old_club.created_at:
        new_club.created_at = old_club.created_at
    db.delete(old_club)
    db.flush()
    actions.append(f"deleted old club record {old_slug}")
    return {"old": old_slug, "new": new_slug, "actions": actions}


def cmd_list(db) -> dict[str, object]:
    from app.models import Club, ClubCatalog, ClubCatalogLink, Platform

    catalogs = db.query(ClubCatalog).order_by(ClubCatalog.canonical_name.asc()).all()
    platforms = {p.id: p.code for p in db.query(Platform).all()}
    multi = []
    for catalog in catalogs:
        if len(catalog.links) > 1:
            multi.append(
                {
                    "catalog": catalog.canonical_name,
                    "links": [
                        {"platform": platforms.get(link.platform_id), "slug": link.external_key, "source": link.link_source}
                        for link in catalog.links
                    ],
                }
            )
    unlinked_clubs = (
        db.query(Club)
        .outerjoin(
            ClubCatalogLink,
            (ClubCatalogLink.platform_id == Club.platform_id) & (ClubCatalogLink.external_key == Club.external_key),
        )
        .filter(ClubCatalogLink.id.is_(None))
        .all()
    )
    return {
        "catalog_entries": len(catalogs),
        "multi_platform_entries": multi,
        "clubs_without_catalog_link": [f"{platforms.get(c.platform_id)}:{c.external_key}" for c in unlinked_clubs],
    }


def main() -> int:
    bootstrap_from_env()
    parser = argparse.ArgumentParser(description="Manual club catalog operations (merge, slug rename, list)")
    add_bootstrap_args(parser)
    parser.add_argument("--five-verst", metavar="SLUG", help="5verst club slug to merge")
    parser.add_argument("--s95", metavar="SLUG", help="s95 club slug to merge with --five-verst")
    parser.add_argument("--name", help="Canonical catalog name for the merged club")
    parser.add_argument("--rename-slug", nargs=2, metavar=("OLD", "NEW"), help="Merge old 5verst slug into new one")
    parser.add_argument("--list", action="store_true", help="Show catalog state")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    apply_bootstrap_args(args)

    db = get_session_factory()()
    try:
        if args.list:
            payload = cmd_list(db)
        elif args.rename_slug:
            payload = cmd_rename_slug(db, args.rename_slug[0], args.rename_slug[1])
            db.commit()
        elif args.five_verst and args.s95:
            payload = cmd_merge(db, args.five_verst, args.s95, args.name)
            db.commit()
        else:
            parser.error("pass --list, --rename-slug OLD NEW, or --five-verst SLUG --s95 SLUG")
            return 1
    except Exception as exc:
        db.rollback()
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    finally:
        db.close()

    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
