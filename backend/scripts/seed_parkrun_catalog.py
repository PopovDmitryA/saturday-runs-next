#!/usr/bin/env python3
"""Засеять каталог parkrun в боевой базе из сида scripts/export_parkrun_catalog_bundle.py.

Этап 2 (18.10.2026). Грузит площадки, имена, страны и журнал изменений
monitoring. Повторный запуск ничего не дублирует: площадки обновляются по
слагу, имена вставляются с пропуском уже лежащих, журнал прежнего сида
заменяется. Описания трасс и история забегов остаются в parkrun_world.

После сида первая сверка (`scripts/parkrun_catalog_run.py`) сравнит живой
каталог с этим состоянием и полечит parkrun-локации сайта.

    docker compose exec api python scripts/seed_parkrun_catalog.py --bundle /data/parkrun_stage2 --dry-run
    docker compose exec api python scripts/seed_parkrun_catalog.py --bundle /data/parkrun_stage2
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from app.db.session import get_session_factory  # noqa: E402
from app.models import ParkrunCatalogRun  # noqa: E402

EVENT_COLUMNS = (
    "slug", "parkrun_id", "short_name", "long_name", "local_name", "country_code", "series_id", "place",
    "latitude", "longitude", "is_active", "source", "first_seen_at", "last_seen_at", "closed_at",
)


def _lines(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--prune-names",
        action="store_true",
        help="Убрать у площадок сида имена, которых нет в сиде (пересев до первой живой сверки: "
        "21.10.2026 так убраны заголовки архивных страниц)",
    )
    args = parser.parse_args()
    bundle = args.bundle
    now = datetime.now(timezone.utc)
    stats: dict[str, int] = {}

    db = get_session_factory()()
    try:
        db.execute(text("set local statement_timeout = 0"))
        events = list(_lines(bundle / "events.jsonl"))
        update_set = ", ".join(f"{col} = excluded.{col}" for col in EVENT_COLUMNS if col != "slug")
        sql = text(
            f"insert into parkrun_events ({', '.join(EVENT_COLUMNS)}, updated_at) "
            f"values ({', '.join(':' + col for col in EVENT_COLUMNS)}, now()) "
            f"on conflict (slug) do update set {update_set}, updated_at = now()"
        )
        for event in events:
            db.execute(sql, {col: event.get(col) for col in EVENT_COLUMNS})
        stats["events"] = len(events)
        ids = {slug: event_id for event_id, slug in db.execute(text("select id, slug from parkrun_events")).all()}

        for country in json.loads((bundle / "countries.json").read_text(encoding="utf-8")):
            code = int(country["code"])
            if code == 0:
                continue
            domain = (country.get("url") or "").strip() or None
            active = bool(country.get("is_active", 1)) and code not in (31, 79)
            db.execute(
                text(
                    """
                    insert into parkrun_countries (code, domain, name, is_active) values (:code, :domain, :name, :active)
                    on conflict (code) do update set
                        domain = case when excluded.is_active then excluded.domain else parkrun_countries.domain end,
                        name = coalesce(parkrun_countries.name, excluded.name),
                        is_active = excluded.is_active,
                        updated_at = now()
                    """
                ),
                {"code": code, "domain": domain if active else None, "name": country.get("name"), "active": active},
            )

        names = 0
        wanted: set[tuple[str, int]] = set()
        for item in _lines(bundle / "names.jsonl"):
            event_id = ids.get(item["slug"])
            if event_id is None:
                continue
            wanted.add((item["name_norm"], event_id))
            db.execute(
                text(
                    "insert into parkrun_event_names (name_norm, event_id, kind) values (:name, :event, :kind) "
                    "on conflict (name_norm, event_id) do nothing"
                ),
                {"name": item["name_norm"], "event": event_id, "kind": item["kind"]},
            )
            names += 1
        stats["names"] = names
        if args.prune_names:
            live_runs = db.execute(
                text("select count(*) from parkrun_catalog_runs where status in ('ok', 'unchanged')")
            ).scalar_one()
            if live_runs:
                # После живой сверки в таблице есть имена из events.json — их сид
                # не знает и удалил бы.
                raise SystemExit(f"--prune-names только до первой живой сверки, а их уже {live_runs}")
            seeded = {ids[event["slug"]] for event in events if event["slug"] in ids}
            existing = db.execute(
                text("select name_norm, event_id from parkrun_event_names where event_id = any(:ids)"),
                {"ids": sorted(seeded)},
            ).all()
            stale = [{"name": name, "event": event_id} for name, event_id in existing if (name, event_id) not in wanted]
            if stale:
                db.execute(text("delete from parkrun_event_names where name_norm = :name and event_id = :event"), stale)
            stats["names_pruned"] = len(stale)

        db.execute(text("delete from parkrun_event_changes where run_id in (select id from parkrun_catalog_runs where status = 'seed')"))
        db.execute(text("delete from parkrun_catalog_runs where status = 'seed'"))
        summary = json.loads((bundle / "summary.json").read_text(encoding="utf-8"))
        run = ParkrunCatalogRun(
            status="seed",
            started_at=now,
            finished_at=now,
            active_total=sum(1 for event in events if event.get("is_active")),
            summary={"seed": summary},
        )
        db.add(run)
        db.flush()
        changes = 0
        for change in _lines(bundle / "changes.jsonl"):
            event_id = ids.get(change["slug"])
            if event_id is None:
                continue
            db.execute(
                text(
                    "insert into parkrun_event_changes (run_id, event_id, kind, details, detected_at) "
                    "values (:run, :event, :kind, cast(:details as jsonb), :detected)"
                ),
                {
                    "run": run.id,
                    "event": event_id,
                    "kind": change["kind"],
                    "details": json.dumps(change.get("details") or {}, ensure_ascii=False),
                    "detected": change.get("detected_at") or now,
                },
            )
            changes += 1
        stats["changes"] = changes

        stats["catalog_total"] = db.execute(text("select count(*) from parkrun_events")).scalar_one()
        stats["active_total"] = db.execute(text("select count(*) from parkrun_events where is_active")).scalar_one()
        if args.dry_run:
            db.rollback()
        else:
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    print(json.dumps({"dry_run": args.dry_run, **stats}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
