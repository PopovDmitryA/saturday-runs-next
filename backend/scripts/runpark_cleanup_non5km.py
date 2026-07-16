#!/usr/bin/env python3
"""RunPark: убрать из БД всё, что не основной 5-км старт.

Зачем: RunPark проводит на одной локации в один день несколько стартов (ночной,
детский, 3 км, эстафета…). Раньше синк тянул их все, а _ensure_event склеивал
события одного дня/локации в одну строку — из-за чего чужой протокол ЗАТИРАЛ
настоящий 5-км. Фильтр по event_type_description = "5 км" это чинит на будущее,
а этот скрипт разгребает уже накопленное.

Порядок запуска на проде — ПОСЛЕ деплоя фильтра "5 км":

    $COMPOSE exec api python scripts/runpark_cleanup_non5km.py --dry-run
    $COMPOSE exec api python scripts/runpark_cleanup_non5km.py --execute
    $COMPOSE exec api python scripts/runpark_initial_import.py          # перезаливка 5км
    $COMPOSE exec api python scripts/runpark_cleanup_non5km.py --restore-ratings

Что переживает перезаливку без нашего участия: локации и их маппинги
(runpark_location_mappings, locations, location_catalog_links), участники и
platform_links — на результаты они не завязаны. PR/first-run — производные флаги,
пересчитываются синком. event_crosslinks пересоздаёт _upsert_crosslinks_for_event.
Единственное невосстановимое — location_ratings (FK на run_results с ON DELETE
CASCADE), поэтому их сохраняем в файл и возвращаем после перезаливки.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

FIVE_KM_TYPE = "5 км"
RATINGS_BACKUP = Path("/data/runpark_ratings_backup.json")
CHUNK = 5000

RATING_FIELDS = (
    "user_id", "participation_type", "location_id", "location_key", "event_date",
    "platform_code", "score_overall", "score_organization", "score_route",
    "score_community", "comment", "is_public", "created_at", "updated_at",
)


def _chunks(seq: list, size: int = CHUNK):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def _source_sets() -> tuple[set[str], set[str], set[str]]:
    """(non5_event_ids, keep5_event_ids, non5_result_ids) — uppercased, from MSSQL."""
    from app.runpark.mssql_client import runpark_query

    ev = runpark_query("SELECT event_id, event_type_description FROM api.vw_events", ())
    non5_events = {str(r["event_id"]).upper() for r in ev if r["event_type_description"] != FIVE_KM_TYPE}
    keep5_events = {str(r["event_id"]).upper() for r in ev if r["event_type_description"] == FIVE_KM_TYPE}

    rr = runpark_query(
        "SELECT result_id FROM api.vw_run_results WHERE event_type_description <> %s "
        "OR event_type_description IS NULL",
        (FIVE_KM_TYPE,),
    )
    non5_results = {str(r["result_id"]).upper() for r in rr}
    return non5_events, keep5_events, non5_results


def _collect(db, pid, non5_events: set[str], non5_results: set[str]) -> dict:
    from sqlalchemy import text

    run_ids: set = set()
    for chunk in _chunks(sorted(non5_results)):
        for (rid,) in db.execute(
            text("SELECT rr.id FROM run_results rr JOIN events e ON e.id = rr.event_id "
                 "WHERE e.platform_id = :pid AND upper(rr.external_result_key) = ANY(:keys)"),
            {"pid": pid, "keys": chunk},
        ).all():
            run_ids.add(rid)

    vol_ids: set = set()
    event_ids: set = set()
    for chunk in _chunks(sorted(non5_events)):
        for (vid,) in db.execute(
            text("SELECT vr.id FROM volunteer_results vr JOIN events e ON e.id = vr.event_id "
                 "WHERE e.platform_id = :pid "
                 "AND upper(substring(vr.external_result_key from 1 for 36)) = ANY(:keys)"),
            {"pid": pid, "keys": chunk},
        ).all():
            vol_ids.add(vid)
        for (eid,) in db.execute(
            text("SELECT e.id FROM events e WHERE e.platform_id = :pid "
                 "AND upper(e.external_event_key) = ANY(:keys)"),
            {"pid": pid, "keys": chunk},
        ).all():
            event_ids.add(eid)
    return {"run_ids": run_ids, "vol_ids": vol_ids, "event_ids": event_ids}


def _save_ratings(db, pid) -> list[dict]:
    """Save runpark location_ratings keyed by external_result_key (survives re-import)."""
    from sqlalchemy import text

    cols = ", ".join(f"lr.{f}" for f in RATING_FIELDS)
    rows = db.execute(
        text(
            f"SELECT {cols}, rr.external_result_key AS run_key, NULL AS vol_key "
            f"FROM location_ratings lr "
            f"JOIN run_results rr ON rr.id = lr.run_result_id "
            f"JOIN events e ON e.id = rr.event_id WHERE e.platform_id = :pid "
            f"UNION ALL "
            f"SELECT {cols}, NULL AS run_key, vr.external_result_key AS vol_key "
            f"FROM location_ratings lr "
            f"JOIN volunteer_results vr ON vr.id = lr.volunteer_result_id "
            f"JOIN events e ON e.id = vr.event_id WHERE e.platform_id = :pid"
        ),
        {"pid": pid},
    ).all()
    saved = []
    for row in rows:
        m = dict(row._mapping)
        saved.append({k: (str(v) if v is not None else None) for k, v in m.items()})
    return saved


def cmd_report(db, pid, execute: bool) -> int:
    from sqlalchemy import text

    non5_events, keep5_events, non5_results = _source_sets()
    logger.info("MSSQL: 5км events=%d, не-5км events=%d, не-5км run results=%d",
                len(keep5_events), len(non5_events), len(non5_results))

    found = _collect(db, pid, non5_events, non5_results)
    logger.info("НАША БД под удаление: run_results=%d, volunteer_results=%d, events=%d",
                len(found["run_ids"]), len(found["vol_ids"]), len(found["event_ids"]))

    # события, которых вообще нет в источнике (ни 5км, ни не-5км)
    known = keep5_events | non5_events
    orphans = [
        (eid, ext) for eid, ext in db.execute(
            text("SELECT id, upper(external_event_key) FROM events WHERE platform_id = :pid"),
            {"pid": pid},
        ).all() if ext not in known
    ]
    logger.info("События, отсутствующие в источнике (НЕ трогаем, только отчёт): %d", len(orphans))
    for eid, ext in orphans[:10]:
        logger.info("   orphan event id=%s ext_key=%s", eid, ext)

    ratings = _save_ratings(db, pid)
    logger.info("RunPark location_ratings к сохранению: %d", len(ratings))

    totals = db.execute(
        text("SELECT "
             "(SELECT count(*) FROM run_results rr JOIN events e ON e.id=rr.event_id WHERE e.platform_id=:pid),"
             "(SELECT count(*) FROM volunteer_results vr JOIN events e ON e.id=vr.event_id WHERE e.platform_id=:pid),"
             "(SELECT count(*) FROM events e WHERE e.platform_id=:pid)"),
        {"pid": pid},
    ).one()
    logger.info("Сейчас в БД (runpark): run_results=%d volunteer_results=%d events=%d",
                totals[0], totals[1], totals[2])

    if not execute:
        logger.info("DRY-RUN — ничего не изменено. Для применения: --execute")
        return 0

    RATINGS_BACKUP.parent.mkdir(parents=True, exist_ok=True)
    RATINGS_BACKUP.write_text(json.dumps(ratings, ensure_ascii=False, indent=2))
    logger.info("Оценки сохранены в %s", RATINGS_BACKUP)

    # Порядок важен: дети (run/volunteer) до events — FK без ON DELETE CASCADE.
    deleted_runs = deleted_vols = deleted_events = 0
    for chunk in _chunks(sorted(found["run_ids"], key=str)):
        deleted_runs += db.execute(
            text("DELETE FROM run_results WHERE id = ANY(:ids)"), {"ids": chunk}
        ).rowcount
    for chunk in _chunks(sorted(found["vol_ids"], key=str)):
        deleted_vols += db.execute(
            text("DELETE FROM volunteer_results WHERE id = ANY(:ids)"), {"ids": chunk}
        ).rowcount
    # у не-5км событий могли остаться прочие результаты — снимаем их перед удалением события
    for chunk in _chunks(sorted(found["event_ids"], key=str)):
        db.execute(text("DELETE FROM run_results WHERE event_id = ANY(:ids)"), {"ids": chunk})
        db.execute(text("DELETE FROM volunteer_results WHERE event_id = ANY(:ids)"), {"ids": chunk})
        deleted_events += db.execute(
            text("DELETE FROM events WHERE id = ANY(:ids)"), {"ids": chunk}
        ).rowcount
    db.commit()
    logger.info("УДАЛЕНО: run_results=%d volunteer_results=%d events=%d",
                deleted_runs, deleted_vols, deleted_events)
    logger.info("Дальше: scripts/runpark_initial_import.py (перезаливка 5км), "
                "затем --restore-ratings")
    return 0


def cmd_restore_ratings(db, pid) -> int:
    from sqlalchemy import text

    if not RATINGS_BACKUP.exists():
        logger.error("Нет файла с оценками: %s", RATINGS_BACKUP)
        return 1
    saved = json.loads(RATINGS_BACKUP.read_text())
    logger.info("Загружено оценок из бэкапа: %d", len(saved))

    restored = skipped = already = 0
    for item in saved:
        run_key, vol_key = item.get("run_key"), item.get("vol_key")
        if run_key:
            new_id = db.execute(
                text("SELECT rr.id FROM run_results rr JOIN events e ON e.id=rr.event_id "
                     "WHERE e.platform_id=:pid AND rr.external_result_key=:k"),
                {"pid": pid, "k": run_key},
            ).scalar()
            id_col, id_val = "run_result_id", new_id
        else:
            new_id = db.execute(
                text("SELECT vr.id FROM volunteer_results vr JOIN events e ON e.id=vr.event_id "
                     "WHERE e.platform_id=:pid AND vr.external_result_key=:k"),
                {"pid": pid, "k": vol_key},
            ).scalar()
            id_col, id_val = "volunteer_result_id", new_id

        if id_val is None:
            logger.warning("Оценка не восстановлена — результат не найден после перезаливки "
                           "(run_key=%s vol_key=%s)", run_key, vol_key)
            skipped += 1
            continue
        exists = db.execute(
            text(f"SELECT 1 FROM location_ratings WHERE user_id=:u AND {id_col}=:r"),
            {"u": item["user_id"], "r": id_val},
        ).scalar()
        if exists:
            already += 1
            continue
        fields = list(RATING_FIELDS) + [id_col]
        values = {f: item[f] for f in RATING_FIELDS}
        values[id_col] = id_val
        db.execute(
            text(f"INSERT INTO location_ratings ({', '.join(fields)}) "
                 f"VALUES ({', '.join(':' + f for f in fields)})"),
            values,
        )
        restored += 1
    db.commit()
    logger.info("Оценки: восстановлено=%d, уже были=%d, не найдено=%d", restored, already, skipped)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", default=True, help="только отчёт (по умолчанию)")
    group.add_argument("--execute", action="store_true", help="сохранить оценки и удалить не-5км данные")
    group.add_argument("--restore-ratings", action="store_true", help="вернуть оценки после перезаливки")
    args = parser.parse_args()

    from sqlalchemy import text

    from app.db.session import get_session_factory

    with get_session_factory()() as db:
        pid = db.execute(text("SELECT id FROM platforms WHERE code='runpark'")).scalar_one()
        if args.restore_ratings:
            return cmd_restore_ratings(db, pid)
        return cmd_report(db, pid, execute=args.execute)


if __name__ == "__main__":
    raise SystemExit(main())
