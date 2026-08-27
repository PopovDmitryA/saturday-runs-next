"""Описания площадок S95: обход страниц `/events/{slug}` пачками.

Почему отдельный проход, а не внутри реестра. Реестр локаций S95 ходит по
JSON API (`pages.json` / `events.json`) — там названий парков и текстов нет
вовсе, описание живёт только в HTML страницы мероприятия. Тянуть HTML внутри
реестра значило бы превратить один быстрый JSON-запрос в 35 загрузок страниц
под общим локом и рискнуть баном IP на ровном месте.

Поэтому здесь свой проход: за запуск берём несколько самых «протухших»
локаций (сначала те, у кого описания нет вовсе), грузим их через общий
координатор S95 (лок + пауза между запросами + детект бана) и складываем.
При бане запуск прекращается — остальные локации подождут следующего.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import nulls_first
from sqlalchemy.orm import Session

from app.models import Location, LocationDescription
from app.s95.errors import S95BanDetected
from app.s95.fetch import fetch_page_html
from app.s95.parsers.location import parse_location_description
from app.sync import upsert
from app.sync.iteration_commit import commit_step, rollback_step

logger = logging.getLogger(__name__)

PLATFORM_CODE = "s95"
DEFAULT_BATCH_SIZE = 5


@dataclass
class S95DescriptionsSyncResult:
    locations_total: int = 0
    descriptions_fetched: int = 0
    descriptions_changed: int = 0
    descriptions_empty: int = 0
    banned: bool = False
    errors: list[str] = field(default_factory=list)


def _stalest_locations(db: Session, platform_id: UUID, limit: int) -> list[Location]:
    """Локации, которым описание нужнее всего: сначала без описания вовсе,
    дальше — по давности последней загрузки."""

    return (
        db.query(Location)
        .outerjoin(LocationDescription, LocationDescription.location_id == Location.id)
        .filter(
            Location.platform_id == platform_id,
            Location.source_url.isnot(None),
            # Пропускаем закрытые площадки, а не отменённые: с 27.08.2026
            # is_cancelled означает «в эту субботу не бегут» — описание такой
            # площадки обновлять по-прежнему нужно.
            Location.is_paused.is_(False),
        )
        .order_by(nulls_first(LocationDescription.fetched_at.asc()), Location.external_key.asc())
        .limit(limit)
        .all()
    )


def sync_s95_location_descriptions(
    db: Session,
    *,
    limit: int = DEFAULT_BATCH_SIZE,
) -> S95DescriptionsSyncResult:
    platform = upsert.get_platform(db, PLATFORM_CODE)
    result = S95DescriptionsSyncResult()

    locations = _stalest_locations(db, platform.id, limit)
    result.locations_total = len(locations)

    for location in locations:
        url = location.source_url or ""
        if not url:
            continue
        try:
            html = fetch_page_html(url, reason="location_description")
        except S95BanDetected as exc:
            # Бан общий для всего домена: следующие локации получат то же самое,
            # только быстрее заработают cooldown. Останавливаемся.
            result.banned = True
            result.errors.append(f"{location.external_key}: {exc}")
            break
        except Exception as exc:
            result.errors.append(f"{location.external_key}: {exc}")
            continue

        try:
            description = parse_location_description(html, url)
            _, changed = upsert.upsert_location_description(db, location, description)
            if description.is_empty():
                result.descriptions_empty += 1
            else:
                result.descriptions_fetched += 1
            if changed:
                result.descriptions_changed += 1
            commit_step(db)
        except Exception as exc:
            rollback_step(db)
            result.errors.append(f"{location.external_key}: {exc}")

    return result
