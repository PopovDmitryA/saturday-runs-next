#!/usr/bin/env python3
"""Привести ключи parkrun-локаций к слагам самого parkrun: слить двойников,
переименовать транслитерации, поправить протоколы и результаты.

Зачем (16.10.2026, Дмитрий): слаг площадки должен записываться у нас ровно
так, как его пишет parkrun, — в локации, в протоколах и в результатах. На проде
было иначе:

* 56 пар локаций-двойников (55 российских `angarskie-prudy` / `angarskieprudy`
  и green-point / greenpoint). Половина без дефиса связана с каталогом
  локаций, а импорт профиля с транслитерацией перетаскивал протоколы на
  дефисную;
* 37 локаций на транслитерации или со старым слагом при известном
  официальном: 19 российских (`gorky-park` → `gorkypark`), Okura kaigan koen,
  закрытые площадки и четыре переименованные (`ludlow` → `mortimerforest`);
* у 63 тыс. протоколов ключ и ссылка на старом слаге (`bushy-park` у `bushy`).

Официальный слаг берётся из снимка каталога в образе
(`app/parkrun/data/events_slugs.json`: живой каталог, закрытые площадки и
российские из архивных снимков). Порядок поиска: ключ уже официальный →
официальный слаг с тем же ключом без разделителей → слаг по названию площадки,
но только если точка старта совпадает с каталогом до километра (так находятся
переименованные площадки и не находятся чужие тёзки). Чего нет в снимке
(≈190 закрытых до 2019-го), остаётся как есть.

Что делается с группой локаций одного официального слага:
* одна локация — переименовать, если ключ не официальный;
* две и больше — основной остаётся та, у кого ключ официальный (иначе
  связанная с каталогом локаций, иначе старшая), остальные сливаются в неё
  (app/parkrun/location_keys.merge_parkrun_locations);
* у всех parkrun-локаций — ключи и ссылки протоколов и ключи результатов
  приводятся к ключу локации.

Каждая группа — своя короткая транзакция с lock_timeout: импорт профиля в это
время может идти, конфликт блокировки просто откладывает группу (повторный
прогон её доделает). Запускать ПОСЛЕ выката кода со снимком: старый код при
первом же импорте завёл бы двойников обратно.

По умолчанию — холостой прогон (всё делается и откатывается): для стенда и
копии прода. На проде холостой прогон держит те же блокировки и раздувает
таблицы, поэтому там — только план, потом запись:

    docker compose exec api python scripts/unify_parkrun_location_keys.py --plan-only
    docker compose exec api python scripts/unify_parkrun_location_keys.py --apply \
        --state /data/unify_parkrun_state.jsonl --report /data/unify_parkrun_report.json

Перед записью: прод и домашний сервер на новом коде (`.deployed_sha` прода =
`~/saturday-runs-next/.deployed_sha`, в снимке есть `gorkypark`), `make parkrun`
на Маке не запущен. Слияния и переименования идемпотентны: оборвалось —
запустить ещё раз. Пересчёт после них (флаги первого старта, рекорды локации,
кэши) повторяется сам или отдельно: `--finish-only --state <тот же журнал>`.
После записи — `VACUUM (ANALYZE) events, run_results, start_weather`.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from app.db.session import get_session_factory  # noqa: E402
from app.models import Location, Platform  # noqa: E402
from app.parkrun import key_plan  # noqa: E402
from app.parkrun.event_slugs import _catalog, catalog_event, official_event_slug  # noqa: E402
from app.parkrun.location_keys import (  # noqa: E402
    merge_parkrun_locations,
    rename_parkrun_location,
    rewrite_event_keys,
)

# Площадки, которые parkrun переименовал вместе со слагом: новое имя — как в каталоге.
RENAMED_NAMES = {
    "colbywoodlandgarden": "Colby Woodland Garden",
    "mortimerforest": "Mortimer Forest",
    "invereskprecinct": "Inveresk Precinct",
    "markshall": "Markshall",
}


def build_plan(db, platform: Platform) -> dict:
    """План по снимку каталога в репо (имена берутся в момент вызова — их подменяют тесты)."""
    view = key_plan.snapshot_view(_catalog, official_event_slug, catalog_event, RENAMED_NAMES)
    return key_plan.build_plan(db, platform, view)


def run_group(session_factory, apply: bool, label: str, action) -> dict:
    return key_plan.run_group(session_factory, apply, action)


def _plan_summary(plan: dict) -> dict:
    return {
        "merges": [
            {"keep": keep.external_key, "drop": drop.external_key, "slug": slug} for keep, drop, slug in plan["merges"]
        ],
        "renames": [{"old": loc.external_key, "new": slug} for loc, slug in plan["renames"]],
        "unknown": plan["unknown"],
        "name_only": plan["name_only"],
    }


def _record(state: Path | None, entry: dict) -> None:
    """Журнал закоммиченных групп — по нему --finish-only доделает пересчёт после обрыва."""
    if state is None:
        return
    with state.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


def _read_state(state: Path) -> tuple[set, set, set]:
    participants: set = set()
    touched: set = set()
    reshaped: set = set()
    if not state.exists():
        return participants, touched, reshaped
    for line in state.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        location_id = UUID(entry["location_id"])
        touched.add(location_id)
        if entry["kind"] in ("merge", "rename"):
            reshaped.add(location_id)
        participants.update(UUID(value) for value in entry.get("participants") or [])
    return participants, touched, reshaped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Записать (без флага — холостой прогон)")
    parser.add_argument("--plan-only", action="store_true", help="Только план: ничего не трогать (так — на проде)")
    parser.add_argument("--finish-only", action="store_true", help="Только пересчёт флагов и кэшей по журналу --state")
    parser.add_argument("--state", type=Path, default=None, help="Журнал закоммиченных групп (JSONL), дописывается")
    parser.add_argument("--max-merges", type=int, default=60, help="Стоп, если пар больше (защита от неверного снимка)")
    parser.add_argument("--max-renames", type=int, default=45, help="Стоп, если переименований больше")
    parser.add_argument("--skip-rewrite", action="store_true", help="Не переписывать ключи протоколов у остальных")
    parser.add_argument("--report", type=Path, default=None, help="Куда положить подробный JSON-отчёт")
    args = parser.parse_args()
    session_factory = get_session_factory()

    if args.finish_only:
        if args.state is None:
            print("--finish-only читает журнал: укажите --state", file=sys.stderr)
            return 2
        report: dict = {"finish_only": True}
        finish(session_factory, *_read_state(args.state), report)
        print(json.dumps(report, ensure_ascii=False, default=str))
        return 0 if not report.get("finish_failed") else 1

    db = session_factory()
    platform = db.query(Platform).filter(Platform.code == "parkrun").one()
    plan = build_plan(db, platform)
    summary = _plan_summary(plan)
    merges = [(keep.id, keep.external_key, drop.id, drop.external_key, slug) for keep, drop, slug in plan["merges"]]
    renames = [(loc.id, loc.external_key, slug) for loc, slug in plan["renames"]]
    all_ids = [loc.id for loc in plan["locations"]]
    print(
        f"parkrun-локаций: {len(plan['locations'])} | слияний: {len(merges)} | переименований: {len(renames)} | "
        f"без официального слага: {len(plan['unknown'])} | имя есть, точка не сошлась: {len(plan['name_only'])}"
    )
    db.close()
    if args.plan_only:
        for item in summary["merges"]:
            print(f"  слить {item['drop']} → {item['keep']}")
        for item in summary["renames"]:
            print(f"  переименовать {item['old']} → {item['new']}")
        if args.report:
            args.report.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        return 0
    if len(merges) > args.max_merges or len(renames) > args.max_renames:
        print("Стоп: слияний или переименований больше порога — проверьте снимок каталога.", file=sys.stderr)
        return 2

    report = {"apply": args.apply, "merges": [], "renames": [], "rewrite": {"events": 0, "results": 0, "failed": []}}
    participants: set = set()
    touched: set = set()  # локации, у которых что-то поменялось: сбросить витрины
    reshaped: set = set()  # слитые и переименованные: сбросить кабинеты их бегунов

    try:
        for keep_id, keep_key, drop_id, drop_key, slug in merges:
            merged_participants: set = set()

            def action(db, keep_id=keep_id, drop_id=drop_id, sink=merged_participants):
                keep = db.query(Location).filter(Location.id == keep_id).with_for_update().one()
                drop = db.query(Location).filter(Location.id == drop_id).with_for_update().one_or_none()
                if drop is None:
                    return {"skipped": "двойника уже нет"}
                merged = merge_parkrun_locations(db, keep, drop)
                sink.update(merged.participants)
                return {
                    "events_moved": merged.events_moved,
                    "events_merged": merged.events_merged,
                    "results_moved": merged.results_moved,
                    "results_dropped": merged.results_dropped,
                    "weather": [merged.weather_moved, merged.weather_dropped, merged.weather_replaced],
                    "links": [merged.links_moved, merged.links_dropped],
                    "other": merged.other_rows,
                    "rewrite": [merged.rewrite.events, merged.rewrite.results],
                }

            outcome = run_group(session_factory, args.apply, f"{drop_key} → {keep_key}", action)
            if outcome.get("ok"):
                participants.update(merged_participants)
                touched.add(keep_id)
                reshaped.add(keep_id)
                if args.apply:
                    _record(
                        args.state,
                        {
                            "kind": "merge",
                            "location_id": keep_id,
                            "drop": drop_key,
                            "participants": sorted(str(p) for p in merged_participants),
                        },
                    )
            report["merges"].append({"keep": keep_key, "drop": drop_key, "slug": slug, **outcome})
            print(f"  слияние {drop_key} → {keep_key}: {outcome}")

        for location_id, old_key, slug in renames:
            def action(db, location_id=location_id, slug=slug):
                location = db.query(Location).filter(Location.id == location_id).with_for_update().one()
                rewrite = rename_parkrun_location(db, location, slug, new_name=RENAMED_NAMES.get(slug))
                return {"rewrite": [rewrite.events, rewrite.results]}

            outcome = run_group(session_factory, args.apply, f"{old_key} → {slug}", action)
            if outcome.get("ok"):
                touched.add(location_id)
                reshaped.add(location_id)
                if args.apply:
                    _record(args.state, {"kind": "rename", "location_id": location_id, "old": old_key, "new": slug})
            report["renames"].append({"old": old_key, "new": slug, **outcome})
            print(f"  переименование {old_key} → {slug}: {outcome}")

        if not args.skip_rewrite:
            merged_away = {drop_id for _, _, drop_id, _, _ in merges}
            for location_id in all_ids:
                if location_id in merged_away:
                    continue

                def action(db, location_id=location_id):
                    location = db.query(Location).filter(Location.id == location_id).one_or_none()
                    if location is None:
                        return {"events": 0, "results": 0}
                    rewrite = rewrite_event_keys(db, location)
                    return {"events": rewrite.events, "results": rewrite.results}

                outcome = run_group(session_factory, args.apply, str(location_id), action)
                if not outcome.get("ok"):
                    report["rewrite"]["failed"].append({"location_id": str(location_id), **outcome})
                    continue
                report["rewrite"]["events"] += outcome.get("events", 0)
                report["rewrite"]["results"] += outcome.get("results", 0)
                if outcome.get("events") or outcome.get("results"):
                    touched.add(location_id)
                    if args.apply:
                        _record(args.state, {"kind": "rewrite", "location_id": location_id})
            print(
                f"  протоколы: ключей и ссылок {report['rewrite']['events']}, результатов {report['rewrite']['results']}, "
                f"сбоев {len(report['rewrite']['failed'])}"
            )

        if args.apply:
            finish(session_factory, participants, touched, reshaped, report)
    finally:
        failed = [m for m in report["merges"] + report["renames"] if not m.get("ok")]
        report["failed"] = len(failed) + len(report["rewrite"]["failed"]) + int(bool(report.get("finish_failed")))
        report["unknown"] = plan["unknown"]
        report["name_only"] = plan["name_only"]
        if args.report:
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"Готово: {'записано' if args.apply else 'холостой прогон, ничего не записано'}; сбоев: {report['failed']}")
    return 0 if report["failed"] == 0 else 1


def finish(session_factory, participants: set, touched: set, reshaped: set, report: dict) -> None:
    """После записи: флаги первого старта и рекордов локации, кэши витрин (key_plan)."""
    report.update(
        key_plan.finish_location_changes(
            session_factory,
            participants=participants,
            touched=touched,
            reshaped=reshaped,
            reason="parkrun: ключи приведены к слагам parkrun",
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
