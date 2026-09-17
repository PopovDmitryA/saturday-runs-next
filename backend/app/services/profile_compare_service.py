"""Сравнение двух участников по локациям: где бегали оба и с каким временем.

Заявка Дмитрия 17.09.2026 к вкладке «Сравнение»: одних итогов мало, хочется
увидеть общие локации — лучший результат каждого на площадке и отдельно
отметить старты, где бежали вместе. Если вместе не пересекались, строка всё
равно полезна: видно, кто как проходит одну и ту же трассу.

Считается по двум выборкам собственных результатов (у человека их сотни, не
миллионы), поэтому кэш не нужен. Площадки схлопываются по canonical identity —
переезд локации между системами не даёт двух строк.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Event, EventCrosslink, Location, Participant, PlatformLink, RunResult
from app.services.location_catalog_service import (
    LocationCatalogIndex,
    identity_key_by_location_id,
)


@dataclass
class _Side:
    """Результаты одного человека на одной площадке."""

    runs: int = 0
    best_sec: int | None = None
    last_date: date | None = None
    event_ids: set[UUID] = field(default_factory=set)

    def add(self, event_id: UUID, event_date: date, finish_sec: int) -> None:
        self.runs += 1
        self.event_ids.add(event_id)
        if self.best_sec is None or finish_sec < self.best_sec:
            self.best_sec = finish_sec
        if self.last_date is None or event_date > self.last_date:
            self.last_date = event_date


def _finish_rows(db: Session, user_id: UUID) -> list[tuple[UUID, UUID, date, int]]:
    """(event_id, location_id, дата, время) всех зачтённых финишей человека."""
    secondary_events = select(EventCrosslink.secondary_event_id)
    return (
        db.query(Event.id, Event.location_id, Event.event_date, RunResult.finish_time_sec)
        .select_from(RunResult)
        .join(Event, Event.id == RunResult.event_id)
        .join(Participant, Participant.id == RunResult.participant_id)
        .join(
            PlatformLink,
            (PlatformLink.platform_id == Participant.platform_id)
            & (PlatformLink.external_user_id == Participant.external_user_id),
        )
        .filter(
            PlatformLink.user_id == user_id,
            RunResult.finish_time_sec.isnot(None),
            RunResult.finish_time_sec > 0,
            Event.is_test_event.is_(False),
            Event.id.notin_(secondary_events),
        )
        .all()
    )


def build_location_comparison(db: Session, viewer_id: UUID, target_id: UUID) -> dict[str, object]:
    """Общие локации двух участников: лучшее время каждого и совместные старты."""
    viewer_rows = _finish_rows(db, viewer_id)
    target_rows = _finish_rows(db, target_id)
    if not viewer_rows or not target_rows:
        return {"items": [], "shared_total": 0}

    catalog_index = LocationCatalogIndex(db)
    identity_by_location = identity_key_by_location_id(db, catalog_index)

    def collect(rows: list[tuple[UUID, UUID, date, int]]) -> dict[str, _Side]:
        sides: dict[str, _Side] = defaultdict(_Side)
        for event_id, location_id, event_date, finish_sec in rows:
            key = identity_by_location.get(location_id) or f"location:{location_id}"
            sides[key].add(event_id, event_date, int(finish_sec))
        return sides

    mine = collect(viewer_rows)
    theirs = collect(target_rows)
    shared = sorted(set(mine) & set(theirs))
    if not shared:
        return {"items": [], "shared_total": 0}

    # Название и адрес площадки — из готового каталога локаций (он уже
    # кэширован). Площадки, которых в каталоге нет (зарубежный parkrun),
    # подписываем именем самой локации.
    from app.services.location_page_service import build_locations_index

    catalog_rows = {
        str(entry.get("identity_key")): entry
        for entry in build_locations_index(db).get("items", [])  # type: ignore[union-attr]
    }
    fallback_names: dict[str, tuple[str, str | None]] = {}
    missing = [key for key in shared if key not in catalog_rows]
    if missing:
        location_ids = [
            location_id
            for location_id, key in identity_by_location.items()
            if key in set(missing)
        ]
        for location in db.query(Location).filter(Location.id.in_(location_ids)).all():
            key = identity_by_location.get(location.id)
            if key and key not in fallback_names:
                fallback_names[key] = (location.name, location.external_key)

    items: list[dict[str, object]] = []
    for key in shared:
        my_side = mine[key]
        their_side = theirs[key]
        entry = catalog_rows.get(key)
        name, slug = (
            (str(entry.get("name")), entry.get("slug"))
            if entry is not None
            else fallback_names.get(key, ("Локация", None))
        )
        together = my_side.event_ids & their_side.event_ids
        items.append(
            {
                "identity_key": key,
                "name": name,
                "slug": slug,
                "my_runs": my_side.runs,
                "my_best_sec": my_side.best_sec,
                "my_last_date": my_side.last_date,
                "their_runs": their_side.runs,
                "their_best_sec": their_side.best_sec,
                "their_last_date": their_side.last_date,
                # Сколько раз стояли на одном старте: 0 — площадка общая, но
                # бегали в разное время (об этом Дмитрий и просил сказать явно).
                "together_runs": len(together),
            }
        )

    # Сверху — где чаще пересекались, дальше по суммарному числу пробежек:
    # интереснее всего площадки, которые для обоих «свои».
    items.sort(
        key=lambda row: (
            -int(row["together_runs"]),  # type: ignore[arg-type]
            -(int(row["my_runs"]) + int(row["their_runs"])),  # type: ignore[arg-type]
            str(row["name"]).lower(),
        )
    )
    return {"items": items, "shared_total": len(items)}
