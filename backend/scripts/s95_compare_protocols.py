#!/usr/bin/env python3
"""Read-only comparison: S95 JSON API protocol vs what's stored in our DB.

Self-contained (role map + time parse inlined) so it can run on prod against the
current deployed app package without the new sync modules. Picks a mix of old and
recent S95 events, fetches each protocol via the API, and prints any differences.
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    ROOT = Path(__file__).resolve().parents[1]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
except NameError:
    pass  # run via stdin (python -); /app already on sys.path in the container

import httpx

from app.db.session import get_session_factory
from app.models import Event, Participant, Platform, RunResult, VolunteerResult
from app.time_format import parse_finish_time

API_ROLE_LABELS = {
    "director": "Директор", "marshal": "Маршал", "timer": "Секундомер",
    "photograph": "Фотограф", "tokens": "Раздача карточек позиций", "scanner": "Сканер",
    "instructor": "Инструктаж новичков", "marking_maker": "Разметка трассы",
    "event_closer": "Замыкающий", "marking_picker": "Сбор разметки",
    "cards_sorter": "Сортировка карточек", "bike_leader": "Ведущий велосипед",
    "pacemaker": "Пейсмейкер", "results_handler": "Обработка результатов",
    "equipment_supplier": "Доставка оборудования", "public_relations": "Связи с общественностью",
    "warm_up": "Проведение разминки", "other": "Разное", "attendant": "Сопровождающий",
    "finish_maker": "Организация финиша", "volunteer_coordinator": "Координатор волонтёров",
    "food_maker": "Организация питания", "videographer": "Видеограф", "designer": "Дизайнер",
    "event_preparation": "Подготовка забега",
}


def fetch(url: str) -> dict:
    with httpx.Client(timeout=30, headers={"Accept": "application/json"}, follow_redirects=True) as c:
        r = c.get(url)
        r.raise_for_status()
        return r.json()


def api_runs(activity: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for item in activity.get("results", []) or []:
        a = item.get("athlete") or {}
        if a.get("id") is None:
            continue
        uid = str(a["id"])
        sec, _ = parse_finish_time(item.get("total_time"))
        out[uid] = {"position": item.get("position"), "sec": sec, "name": a.get("name")}
    return out


def api_vols(activity: dict) -> dict[tuple, str]:
    out: dict[tuple, str] = {}
    for item in activity.get("volunteers", []) or []:
        a = item.get("athlete") or {}
        if a.get("id") is None:
            continue
        uid = str(a["id"])
        label = API_ROLE_LABELS.get((item.get("role") or "").lower(), item.get("role"))
        out[(uid, label)] = a.get("name")
    return out


def db_runs(db, event_id) -> dict[str, dict]:
    rows = (
        db.query(RunResult, Participant.external_user_id)
        .join(Participant, RunResult.participant_id == Participant.id)
        .filter(RunResult.event_id == event_id)
        .all()
    )
    return {uid: {"position": r.position, "sec": r.finish_time_sec} for r, uid in rows}


def db_vols(db, event_id) -> set[tuple]:
    rows = (
        db.query(VolunteerResult, Participant.external_user_id)
        .join(Participant, VolunteerResult.participant_id == Participant.id)
        .filter(VolunteerResult.event_id == event_id)
        .all()
    )
    return {(uid, r.role) for r, uid in rows}


def compare_event(db, event: Event) -> list[str]:
    url = event.source_url or ""
    if not url.endswith(".json"):
        url = url.rstrip("/") + ".json"
    activity = fetch(url)

    a_runs, d_runs = api_runs(activity), db_runs(db, event.id)
    a_vols, d_vols = api_vols(activity), db_vols(db, event.id)

    diffs: list[str] = []
    a_ids, d_ids = set(a_runs), set(d_runs)
    if a_ids - d_ids:
        diffs.append(f"  бегуны есть в API, нет в БД: {sorted(a_ids - d_ids)}")
    if d_ids - a_ids:
        diffs.append(f"  бегуны есть в БД, нет в API: {sorted(d_ids - a_ids)}")
    for uid in a_ids & d_ids:
        a, d = a_runs[uid], d_runs[uid]
        if a["sec"] != d["sec"]:
            diffs.append(f"  время {uid}: API={a['sec']} БД={d['sec']} ({a.get('name')})")
        if a["position"] != d["position"]:
            diffs.append(f"  позиция {uid}: API={a['position']} БД={d['position']}")

    a_vset = set(a_vols)
    if a_vset - d_vols:
        diffs.append(f"  волонтёрства в API, нет в БД: {sorted(a_vset - d_vols)}")
    if d_vols - a_vset:
        diffs.append(f"  волонтёрства в БД, нет в API: {sorted(d_vols - a_vset)}")

    header = (
        f"[{event.event_date}] {event.external_event_key} "
        f"runs API={len(a_runs)}/DB={len(d_runs)} vols API={len(a_vols)}/DB={len(d_vols)}"
    )
    return [header] + (diffs or ["  без изменений"])


def main() -> int:
    db = get_session_factory()()
    try:
        platform = db.query(Platform).filter(Platform.code == "s95").one()
        base = db.query(Event).filter(Event.platform_id == platform.id, Event.source_url.isnot(None))
        oldest = base.order_by(Event.event_date.asc()).limit(2).all()
        newest = base.order_by(Event.event_date.desc()).limit(3).all()
        events = oldest + newest
        for ev in events:
            for line in compare_event(db, ev):
                print(line)
            print()
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
