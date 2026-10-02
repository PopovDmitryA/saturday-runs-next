#!/usr/bin/env python3
"""Import location catalog from reviewed XLSX into DB and JSON seed."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = SCRIPT_DIR.parent
REPO_ROOT = BACKEND_ROOT.parent

if (REPO_ROOT / "data").is_dir():
    DATA_ROOT = REPO_ROOT
elif Path("/data").is_dir():
    DATA_ROOT = Path("/")
else:
    DATA_ROOT = REPO_ROOT

DEFAULT_XLSX = DATA_ROOT / "data" / "location_mapping_draft.xlsx"
DEFAULT_JSON = DATA_ROOT / "data" / "location_catalog.json"

PARKRUN_SLUG_ALIASES: dict[str, str] = {
    "gorky-park": "gorkypark",
    "park-850-letiya-moskvy": "park850letiyamoskvy",
    "tula-central": "tulacentral",
    "tver-rechnoy-vokzal": "tverrechnoyvokzal",
    "moskovsky-park-pobedy": "moskovskyparkpobedy",
    "lobnya-gorodskoy-park": "lobnyagorodskoypark",
    "serpukhov-gorodskoy-bor": "serpukhovgorodskoybor",
    "gatchina-prioratsky": "gatchinaprioratsky",
    "balashikha-zarechnaya": "balashikhazarechnaya",
    "babushkinsky-na-yauze": "babushkinskynayauze",
    "pokrovskoe-streshnevo": "pokrovskoestreshnevo",
    "severnoe-tushino": "severnoetushino",
    "olimpiyskaya-derevnya": "olimpiyskayaderevnya",
    "lesopark-severny": "lesoparkseverny",
}


PARKRUN_TO_S95_SLUG: dict[str, str] = {
    "izmailovo": "izmailovo",
    "tsaritsyno": "tsaritsyno",
    "olimpiyskaya-derevnya": "olimpiyskaya_derevnya",
    "tver-rechnoy-vokzal": "parkzhrun",
    "gatchina-prioratsky": "gatchina",
}


def five_verst_external_key(parkrun_slug: str, actual_slug: str | None) -> str:
    if actual_slug and actual_slug in PARKRUN_SLUG_ALIASES.values():
        return actual_slug
    if actual_slug and "-" not in actual_slug:
        return actual_slug
    return PARKRUN_SLUG_ALIASES.get(parkrun_slug, norm_slug(parkrun_slug))


def s95_external_key(parkrun_slug: str, actual_slug: str | None) -> str:
    if actual_slug and "_" in str(actual_slug):
        return str(actual_slug)
    return PARKRUN_TO_S95_SLUG.get(parkrun_slug, actual_slug and str(actual_slug) or norm_slug(parkrun_slug))

URL_5VERST = re.compile(r"5verst\.ru/([^/\"'\s]+)", re.I)
URL_S95 = re.compile(r"s95\.ru/events/([^/\"'\s]+)", re.I)


@dataclass
class CatalogEntry:
    parkrun_location: str
    canonical_name: str
    active_platform: str | None
    legacy_parkrun_slug: str
    is_closed: bool = False
    notes: str = ""
    links: list[dict[str, str]] = field(default_factory=list)


def norm_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def parse_url_links(text: str) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    for match in URL_5VERST.finditer(text or ""):
        links.append(("five_verst", match.group(1)))
    for match in URL_S95.finditer(text or ""):
        links.append(("s95", match.group(1)))
    return links


def parse_system(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip().lower().replace("ё", "е")
    if "5" in text and "верст" in text:
        return "five_verst"
    if "с95" in text or "s95" in text:
        return "s95"
    if "runpark" in text:
        return "runpark"
    if "parkrun" in text:
        return "parkrun"
    return None


def is_closed_confirmed(confirmed: str | None, comment: str | None) -> bool:
    blob = f"{confirmed or ''} {comment or ''}".lower()
    return "более не существ" in blob or "не существ" in blob


def is_runpark_only(confirmed: str | None, system: str | None) -> bool:
    blob = (confirmed or "").lower()
    return "runpark" in blob or system == "нет (только parkrun)"


def row_to_entry(row: tuple) -> CatalogEntry | None:
    parkrun_name, current_loc, system, comment, confirmed, pr_slug, actual_slug, _confidence = row[:8]
    if not pr_slug:
        return None

    confirmed_text = (confirmed or "").strip()
    confirmed_lower = confirmed_text.lower()
    closed = is_closed_confirmed(confirmed_text, comment)
    runpark = is_runpark_only(confirmed_text, system)
    active = parse_system(system)

    url_links = parse_url_links(confirmed_text)
    if url_links:
        active = url_links[0][0]

    if closed:
        return CatalogEntry(
            parkrun_location=str(parkrun_name or ""),
            canonical_name=str(current_loc or parkrun_name or pr_slug),
            active_platform=None,
            legacy_parkrun_slug=str(pr_slug),
            is_closed=True,
            notes="; ".join(filter(None, [comment, confirmed_text])),
            links=[{"platform": "parkrun", "external_key": str(pr_slug)}],
        )

    if runpark:
        return CatalogEntry(
            parkrun_location=str(parkrun_name or ""),
            canonical_name=str(current_loc or parkrun_name or pr_slug),
            active_platform="runpark",
            legacy_parkrun_slug=str(pr_slug),
            notes="; ".join(filter(None, [comment, confirmed_text])),
            links=[{"platform": "parkrun", "external_key": str(pr_slug)}],
        )

    if url_links:
        canonical = str(current_loc or parkrun_name or "")
        for _, slug in url_links:
            if active == "five_verst" and not canonical:
                canonical = slug
        links = [{"platform": "parkrun", "external_key": str(pr_slug)}]
        for platform, slug in url_links:
            links.append({"platform": platform, "external_key": slug})
        return CatalogEntry(
            parkrun_location=str(parkrun_name or ""),
            canonical_name=canonical or str(parkrun_name or ""),
            active_platform=active,
            legacy_parkrun_slug=str(pr_slug),
            notes="; ".join(filter(None, [comment, confirmed_text])),
            links=links,
        )

    if confirmed_lower in ("да", "yes") or confirmed_lower.startswith("да"):
        pass
    else:
        return None

    if not current_loc or not active:
        return None

    links: list[dict[str, str]] = [{"platform": "parkrun", "external_key": str(pr_slug)}]
    if active == "five_verst":
        links.append({"platform": "five_verst", "external_key": five_verst_external_key(str(pr_slug), actual_slug and str(actual_slug))})
    elif active == "s95":
        links.append({"platform": "s95", "external_key": s95_external_key(str(pr_slug), actual_slug and str(actual_slug))})

    return CatalogEntry(
        parkrun_location=str(parkrun_name or ""),
        canonical_name=str(current_loc),
        active_platform=active,
        legacy_parkrun_slug=str(pr_slug),
        notes=str(comment or ""),
        links=links,
    )


def load_xlsx(path: Path) -> list[CatalogEntry]:
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb["соответствия"]
    entries: list[CatalogEntry] = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        entry = row_to_entry(row)
        if entry is not None:
            entries.append(entry)
    wb.close()
    return entries


def export_json(entries: list[CatalogEntry], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [asdict(entry) for entry in entries]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_json(path: Path) -> list[CatalogEntry]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [CatalogEntry(**item) for item in raw]


class StaleImportError(RuntimeError):
    """Raised when replace=True would shrink the catalog — likely a stale JSON."""


class ReferencedNodeRemovalError(RuntimeError):
    """Raised when the import would drop catalog nodes that live data still points at."""


def _match_existing_nodes(
    entries: list[CatalogEntry], existing: list, links_by_catalog: dict
) -> list:
    """Для каждой записи JSON — существующий узел каталога или None (новый узел).

    id узла — личность локации, за него держатся гранты организаторов, дом,
    оценки (CATALOG_KEY_REFERENCES). Поэтому сначала ищем узел по
    parkrun-слагу — он уникален и не меняется при переименовании и смене
    основной системы, как у Раменского 26.09.2026; потом по общим связкам
    (система, slug); потом по названию. Связки идут вторыми: связка может
    переехать к другому узлу и не должна увести с собой чужой id.
    Узел достаётся не больше чем одной записи.
    """
    from app.services.location_catalog_service import normalize_platform_code

    node_by_link: dict[tuple[str, str], object] = {}
    for catalog in existing:
        for platform_code, external_key in links_by_catalog.get(catalog.id, ()):
            node_by_link[(platform_code, external_key)] = catalog
    node_by_slug = {c.legacy_parkrun_slug: c for c in existing if c.legacy_parkrun_slug}
    node_by_name: dict[str, object] = {}
    for catalog in existing:
        node_by_name.setdefault(catalog.canonical_name, catalog)

    claimed: set = set()
    matches: list = [None] * len(entries)

    def claim(index: int, candidate) -> bool:
        if candidate is None or candidate.id in claimed:
            return False
        claimed.add(candidate.id)
        matches[index] = candidate
        return True

    # Проходы по убыванию надёжности: запись, совпавшая лишь по связке или
    # названию, не должна отнять узел у записи с тем же parkrun-слагом.
    for index, entry in enumerate(entries):
        if entry.legacy_parkrun_slug:
            claim(index, node_by_slug.get(entry.legacy_parkrun_slug))
    for index, entry in enumerate(entries):
        if matches[index] is not None:
            continue
        votes: dict = {}
        for link in entry.links:
            code = normalize_platform_code(link["platform"]) or link["platform"]
            node = node_by_link.get((code, link["external_key"]))
            if node is not None:
                votes[node.id] = (votes.get(node.id, (0, node))[0] + 1, node)
        for _count, node in sorted(votes.values(), key=lambda item: (-item[0], str(item[1].id))):
            if claim(index, node):
                break
    for index, entry in enumerate(entries):
        if matches[index] is None:
            claim(index, node_by_name.get(entry.canonical_name))
    return matches


def import_to_db(
    entries: list[CatalogEntry],
    *,
    replace: bool = True,
    force: bool = False,
    db=None,
) -> dict[str, int]:
    """Привести каталог в БД к `entries`, сохранив id существующих узлов.

    До 29.09.2026 импорт делал DELETE всего каталога и вставлял узлы заново —
    с новыми id. Ключи «catalog:<id>» в грантах организаторов, домашних
    локациях, оценках и гео-пингах после этого указывали в пустоту: 26.09.2026
    правка одного Раменского сняла все ручные доступы к кабинету организатора.
    Теперь узлы обновляются на месте, а удаление узла, на который кто-то
    ссылается, без --force отказывается.

    replace=False — только добавить/обновить, узлы вне `entries` не трогать.
    """
    backend_path = str(BACKEND_ROOT)
    if backend_path not in sys.path and (BACKEND_ROOT / "app").is_dir():
        sys.path.insert(0, backend_path)

    from app.db.session import get_session_factory
    from app.models import Location, LocationCatalog, LocationCatalogLink, Platform
    from app.services.location_catalog_service import (
        catalog_key_reference_counts,
        normalize_platform_code,
    )

    own_session = db is None
    if own_session:
        db = get_session_factory()()
    stats = {
        "catalog": 0,
        "links": 0,
        "linked_locations": 0,
        "catalog_created": 0,
        "catalog_updated": 0,
        "catalog_removed": 0,
    }
    try:
        platforms = {p.code: p for p in db.query(Platform).all()}
        platform_code_by_id = {p.id: code for code, p in platforms.items()}
        if replace:
            # Импорт устаревшего data/location_catalog.json (старый worktree без
            # свежих связок) выкинул бы кураторские связки без следа в git.
            # 20.07.2026: 16 из 20 worktree сидели на старой копии. Сжатие — только с --force.
            current_links = db.query(LocationCatalogLink).count()
            incoming_links = sum(len(e.links) for e in entries)
            if not force and incoming_links < current_links:
                raise StaleImportError(
                    f"Refusing to import: incoming JSON has {incoming_links} links, "
                    f"DB currently has {current_links}. This --import-db would "
                    "shrink the catalog — almost always a stale data/location_catalog.json "
                    "(git pull origin main first). If this reduction is intentional "
                    "(e.g. removing a bad link), pass --force."
                )

        existing = db.query(LocationCatalog).all()
        existing_links = db.query(LocationCatalogLink).all()
        links_by_catalog: dict = {}
        for link in existing_links:
            code = platform_code_by_id.get(link.platform_id)
            links_by_catalog.setdefault(link.catalog_id, []).append((code, link.external_key))

        matches = _match_existing_nodes(entries, existing, links_by_catalog)
        matched_ids = {node.id for node in matches if node is not None}

        # Узлы, которых нет в JSON, удаляются только при replace и только если
        # на них никто не ссылается: иначе чьи-то гранты и оценки осиротеют.
        removed = [c for c in existing if c.id not in matched_ids] if replace else []
        if removed:
            references = catalog_key_reference_counts(db, [c.id for c in removed])
            if references and not force:
                names = ", ".join(sorted(c.canonical_name for c in removed))
                raise ReferencedNodeRemovalError(
                    f"Refusing to import: nodes missing from JSON are still referenced "
                    f"({references}): {names}. Keep them in the JSON, or pass --force "
                    "after moving the references to the surviving node."
                )
            for catalog in removed:
                db.delete(catalog)
            stats["catalog_removed"] = len(removed)
            db.flush()

        location_by_exact_key: dict[tuple[str, str], Location] = {}
        location_by_norm_key: dict[tuple[str, str], Location] = {}
        for loc, platform in db.query(Location, Platform).join(Platform, Location.platform_id == Platform.id):
            location_by_exact_key[(platform.code, loc.external_key)] = loc
            normalized = norm_slug(loc.external_key)
            if normalized:
                location_by_norm_key.setdefault((platform.code, normalized), loc)

        # Желаемые связки: (platform_id, external_key) → индекс записи.
        desired: dict[tuple, int] = {}
        entry_links: list[list[tuple]] = []
        for index, entry in enumerate(entries):
            keys: list[tuple] = []
            for link in entry.links:
                platform_code = normalize_platform_code(link["platform"]) or link["platform"]
                platform = platforms.get(platform_code)
                if platform is None:
                    continue
                key = (platform.id, link["external_key"])
                if key in desired:
                    continue
                desired[key] = index
                keys.append((key, platform_code))
            entry_links.append(keys)

        # Связка уникальна по (система, slug) — переезд связки к другому узлу
        # идёт через удаление и вставку, и удаления должны уйти в базу первыми.
        removed_ids = {c.id for c in removed}
        kept_links: dict[tuple, object] = {}
        for link in existing_links:
            if link.catalog_id in removed_ids:
                continue
            key = (link.platform_id, link.external_key)
            index = desired.get(key)
            target = matches[index] if index is not None else None
            if target is not None and target.id == link.catalog_id:
                kept_links[key] = link
            elif replace or index is not None:
                db.delete(link)
        db.flush()

        # parkrun-слаг уникален: если узлы обмениваются слагами, сначала снять.
        for index, entry in enumerate(entries):
            node = matches[index]
            if node is not None and node.legacy_parkrun_slug != (entry.legacy_parkrun_slug or None):
                node.legacy_parkrun_slug = None
        db.flush()

        for index, entry in enumerate(entries):
            catalog = matches[index]
            if catalog is None:
                catalog = LocationCatalog()
                db.add(catalog)
                stats["catalog_created"] += 1
            else:
                stats["catalog_updated"] += 1
            catalog.canonical_name = entry.canonical_name
            catalog.legacy_parkrun_slug = entry.legacy_parkrun_slug or None
            catalog.active_platform = entry.active_platform
            catalog.is_closed = entry.is_closed
            catalog.notes = entry.notes or None
            db.flush()
            stats["catalog"] += 1

            for key, platform_code in entry_links[index]:
                platform_id, external_key = key
                location = location_by_exact_key.get((platform_code, external_key)) or location_by_norm_key.get(
                    (platform_code, norm_slug(external_key))
                )
                location_id = location.id if location else None
                link = kept_links.get(key)
                if link is None:
                    db.add(
                        LocationCatalogLink(
                            catalog_id=catalog.id,
                            platform_id=platform_id,
                            external_key=external_key,
                            location_id=location_id,
                        )
                    )
                elif link.location_id != location_id and location_id is not None:
                    link.location_id = location_id
                stats["links"] += 1
                if location:
                    stats["linked_locations"] += 1

        if own_session:
            db.commit()
        else:
            db.flush()
        return stats
    except Exception:
        if own_session:
            db.rollback()
        raise
    finally:
        if own_session:
            db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xlsx", type=Path, default=DEFAULT_XLSX)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--import-db", action="store_true", help="Load into PostgreSQL")
    parser.add_argument("--from-json", action="store_true", help="Import from JSON instead of XLSX")
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Allow --import-db to shrink the catalog and drop referenced nodes "
            "(bypasses the stale-JSON and orphaned-references guards)"
        ),
    )
    args = parser.parse_args()

    if args.from_json:
        entries = load_json(args.json)
    else:
        entries = load_xlsx(args.xlsx)
        export_json(entries, args.json)

    print(f"Entries: {len(entries)}")
    print(f"  closed: {sum(1 for e in entries if e.is_closed)}")
    print(f"  runpark: {sum(1 for e in entries if e.active_platform == 'runpark')}")
    print(f"  five_verst: {sum(1 for e in entries if e.active_platform == 'five_verst')}")
    print(f"  s95: {sum(1 for e in entries if e.active_platform == 's95')}")
    print(f"JSON: {args.json}")

    if args.import_db:
        try:
            stats = import_to_db(entries, force=args.force)
        except (StaleImportError, ReferencedNodeRemovalError) as exc:
            print(f"DB import ABORTED: {exc}", file=sys.stderr)
            return 1
        print(f"DB import: {stats}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
