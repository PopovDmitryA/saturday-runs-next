#!/usr/bin/env python3
"""Собрать сид каталога parkrun для боевой базы — запускается ДОМА, только читает.

Этап 2 (18.10.2026): каталог площадок parkrun переезжает в базу сайта из
SQLite parkrun-monitoring. Забирается всё, что нужно сайту о площадках:

* каталог monitoring (копия data/parkrun.db с VPS): действующие и закрытые
  площадки с id parkrun, датами появления и закрытия, 110 российских из
  архивных снимков, журнал изменений;
* закрытые площадки, которых в monitoring нет: parkrun_world.closed_events
  (точка и официальный слаг) и protocol_events (имена, страна, серия);
* старые имена из снимка app/parkrun/data/events_slugs.json — профиль атлета
  может прислать площадку под прежним именем.

Описания трасс и история забегов остаются в parkrun_world (решение Дмитрия
21.10.2026), скрипт их не трогает.

Скрипт обходится стандартной библиотекой и psql (на домашнем сервере нет venv
с драйвером Postgres). Результат — каталог с JSONL/CSV, его грузит на проде
scripts/seed_parkrun_catalog.py.

    scp viewer@195.58.34.112:parkrun-monitoring/data/parkrun.db /tmp/parkrun.db
    (set -a; . ~/parkrun-monitoring/.env; set +a; \\
     python3 backend/scripts/export_parkrun_catalog_bundle.py --sqlite /tmp/parkrun.db --out /tmp/bundle)
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SLUGS = ROOT / "app" / "parkrun" / "data" / "events_slugs.json"
ARCHIVE = ROOT / "app" / "parkrun" / "data" / "events_archive.json"
PARKRUN_SLUG = re.compile(r"[a-z0-9]+(-juniors)?")
NOISE_NAMES = {"parkrun", "juniors", "parkrunjuniors", "juniorparkrun"}
# Площадка вне живого каталога в той же точке и той же серии, что и живая, —
# её прежний адрес (parkrun переименовал её вместе со слагом: ludlow →
# mortimerforest), а не отдельная закрытая площадка.
SAME_PLACE_KM = 0.05


def norm(value: str | None) -> str:
    """Как app.parkrun.event_slugs.normalize_event_name: без регистра и разделителей."""
    return re.sub(r"[^0-9a-zа-яё]+", "", (value or "").strip().lower())


def bare(name: str | None) -> str:
    value = (name or "").strip()
    value = re.sub(r"^parkrun\s+", "", value, flags=re.IGNORECASE)
    return re.sub(r"\s+parkrun$", "", value, flags=re.IGNORECASE).strip()


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlmb = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def psql_csv(sql: str) -> list[dict[str, str]]:
    dsn = os.environ.get("PM_WORLD_DSN")
    if not dsn:
        raise SystemExit("нет PM_WORLD_DSN (загрузите ~/parkrun-monitoring/.env)")
    out = subprocess.run(
        ["psql", dsn, "-X", "-q", "-v", "ON_ERROR_STOP=1", "-c", "set default_transaction_read_only=on",
         "-c", f"copy ({sql}) to stdout with csv header"],
        check=True,
        capture_output=True,
    ).stdout.decode("utf-8")
    return list(csv.DictReader(io.StringIO(out)))


def iso(value: str | None) -> str | None:
    if not value:
        return None
    return value if "T" in value or "+" in value else value + "T00:00:00+00:00"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sqlite", type=Path, required=True, help="Копия parkrun.db monitoring")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(f"file:{args.sqlite}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    events: dict[str, dict] = {}
    names: dict[tuple[str, str], str] = {}
    by_parkrun_id: dict[int, str] = {}

    def add_name(slug: str, value: str | None, kind: str) -> None:
        key = norm(value)
        # Иероглифы и прочее не-латинское нормализация выбрасывает: у японских
        # местных имён («大蔵海岸公園 parkrun») остаётся одно «parkrun» — такое
        # имя ничего не различает.
        if len(key) < 3 or key in NOISE_NAMES:
            return
        if (key, slug) not in names:
            names[(key, slug)] = kind

    for row in con.execute("select * from events"):
        slug = row["eventname"]
        wayback = (row["catalogue_source"] or "") == "wayback"
        active = bool(row["is_active"])
        events[slug] = {
            "slug": slug,
            "parkrun_id": None if wayback else row["id"],
            "short_name": row["short_name"],
            "long_name": row["long_name"],
            "local_name": row["localised_long_name"],
            "country_code": row["country_code"],
            "series_id": row["series_id"],
            "place": row["location"] or None,
            "latitude": row["latitude"],
            "longitude": row["longitude"],
            "is_active": active,
            "source": "live" if active else ("archive" if wayback else "closed"),
            "first_seen_at": iso(row["first_seen"]),
            "last_seen_at": iso(row["last_seen"]),
            # У архивных строк disappeared_at — время импорта, а не закрытия.
            "closed_at": None if active else iso(row["last_seen"] if wayback else row["disappeared_at"]),
        }
        if not wayback:
            by_parkrun_id[row["id"]] = slug
        add_name(slug, row["short_name"], "short")
        add_name(slug, bare(row["long_name"]), "long")
        add_name(slug, bare(row["localised_long_name"]), "local")
    countries = [dict(row) for row in con.execute("select code, url, name, is_active from countries")]

    changes = []
    for row in con.execute("select * from event_changes order by id"):
        slug = by_parkrun_id.get(row["event_id"]) or row["eventname"]
        if slug not in events:
            continue
        details = json.loads(row["details"]) if row["details"] else {}
        kind = {"added": "added", "removed": "closed", "reappeared": "returned"}.get(row["change_type"], "field")
        out: dict = {}
        if row["change_type"] == "modified":
            if "eventname" in details:
                kind = "renamed"
                old_slug, new_slug = details["eventname"]
                out = {"old_slug": old_slug, "new_slug": new_slug, "old_name": (details.get("short_name") or [None])[0]}
                add_name(slug, out["old_name"], "old")
            elif "latitude" in details or "longitude" in details:
                kind = "moved"
                lat = details.get("latitude") or [events[slug]["latitude"]] * 2
                lon = details.get("longitude") or [events[slug]["longitude"]] * 2
                out = {"old": [lat[0], lon[0]], "new": [lat[1], lon[1]]}
            else:
                out = details
        changes.append({"slug": slug, "kind": kind, "details": out, "detected_at": iso(row["detected_at"])})

    archive = json.loads(ARCHIVE.read_text(encoding="utf-8")).get("events") or {}
    closed_rows = psql_csv(
        "select eventname, runs_slug, country_code, series_id, geo, page_name, last_run from closed_events where status = 'closed'"
    )
    for row in closed_rows:
        slug = row["eventname"]
        if slug in events or not PARKRUN_SLUG.fullmatch(slug or ""):
            continue
        lat = lon = None
        if row["geo"] and ";" in row["geo"]:
            lat, lon = (float(part) for part in row["geo"].split(";", 1))
        events[slug] = {
            "slug": slug, "parkrun_id": None, "short_name": bare(row["page_name"]) or None, "long_name": row["page_name"] or None,
            "local_name": None, "country_code": int(row["country_code"]) if row["country_code"] else None,
            "series_id": int(row["series_id"]) if row["series_id"] else None, "place": None,
            "latitude": lat, "longitude": lon, "is_active": False, "source": "archive",
            "first_seen_at": None, "last_seen_at": None, "closed_at": iso(row["last_run"]),
        }
        add_name(slug, bare(row["page_name"]), "short")
    protocol_rows = psql_csv(
        "select slug, event_name, long_name, local_name, country_code, series_id from protocol_events where not in_catalog"
    )
    for row in protocol_rows:
        slug = row["slug"]
        if not PARKRUN_SLUG.fullmatch(slug or ""):
            continue
        if slug not in events:
            point = archive.get(norm(slug))
            events[slug] = {
                "slug": slug, "parkrun_id": None, "short_name": row["event_name"] or None, "long_name": row["long_name"] or None,
                "local_name": row["local_name"] or None, "country_code": int(row["country_code"]) if row["country_code"] else None,
                "series_id": int(row["series_id"]) if row["series_id"] else None, "place": None,
                "latitude": point[1] if point else None, "longitude": point[2] if point else None,
                "is_active": False, "source": "archive", "first_seen_at": None, "last_seen_at": None, "closed_at": None,
            }
        # long_name у protocol_events — заголовок страницы, бывает мусор
        # («Kungsängen parkrun eventhistorik»); берём только короткое имя.
        add_name(slug, row["event_name"], "short")

    # Прежние адреса живых площадок: имена — живой, сама строка уходит.
    live = [e for e in events.values() if e["is_active"] and e["latitude"] is not None]
    phantoms: dict[str, str] = {}
    for slug, event in list(events.items()):
        if event["is_active"] or event["latitude"] is None or event["country_code"] == 79:
            continue
        twins = [
            item for item in live
            if item["series_id"] == event["series_id"]
            and distance_km(event["latitude"], event["longitude"], item["latitude"], item["longitude"]) <= SAME_PLACE_KM
        ]
        if len(twins) == 1:
            phantoms[slug] = twins[0]["slug"]
    for old_slug, live_slug in phantoms.items():
        del events[old_slug]
        for (name_key, slug), _kind in list(names.items()):
            if slug == old_slug:
                del names[(name_key, slug)]
                names.setdefault((name_key, live_slug), "old")
        add_name(live_slug, old_slug, "old")

    # Старые имена из снимка: «Ludlow» → mortimerforest и т.п.
    snapshot = json.loads(SLUGS.read_text(encoding="utf-8"))
    for name_key, slug in snapshot.items():
        slug = phantoms.get(slug, slug)
        if slug in events and len(name_key) >= 3 and name_key not in NOISE_NAMES:
            names.setdefault((name_key, slug), "old")

    with (args.out / "events.jsonl").open("w", encoding="utf-8") as handle:
        for event in sorted(events.values(), key=lambda item: item["slug"]):
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    with (args.out / "names.jsonl").open("w", encoding="utf-8") as handle:
        for (name_key, slug), kind in sorted(names.items()):
            handle.write(json.dumps({"name_norm": name_key, "slug": slug, "kind": kind}, ensure_ascii=False) + "\n")
    (args.out / "countries.json").write_text(json.dumps(countries, ensure_ascii=False), encoding="utf-8")
    with (args.out / "changes.jsonl").open("w", encoding="utf-8") as handle:
        for change in changes:
            handle.write(json.dumps(change, ensure_ascii=False) + "\n")

    summary = {
        "events": len(events),
        "active": sum(1 for e in events.values() if e["is_active"]),
        "names": len(names),
        "changes": len(changes),
        "phantoms": phantoms,
    }
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
