"""Наблюдатель отмен ближайшего старта у s95.

Реестр локаций s95 ходит раз в трое суток — для отмены это слишком редко:
объявление про субботу может провисеть незамеченным до самого старта.
Поэтому здесь отдельный лёгкий проход, который умеет ровно одно — держать в
актуальном состоянии признак `is_cancelled` и текст причины.

Стоимость прохода: два JSON-запроса на домен плюс по одной странице на каждую
площадку, которую реестр объявил неработающей (обычно ноль). Пауза и лок —
общие для всех загрузок s95.

Признак `is_paused` наблюдатель не трогает намеренно: «площадка закрылась» —
вывод синка реестра и правила молчания, и спорить с ними четыре раза в сутки
означало бы гонку статусов. Здесь отвечают только на вопрос «побегут ли в
ближайшую субботу».
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models import Location
from app.s95.api_client import fetch_all_locations
from app.s95.errors import S95BanDetected
from app.s95.fetch.priority import S95YieldForUserSync
from app.services.location_cancellation_notify import (
    CancellationChange,
    notify_cancellation_changes,
)
from app.services.location_catalog_cache import (
    flush_location_catalog_caches,
    flush_location_page_caches,
)
from app.services.sync_report_labels import location_detail_label
from app.sync import upsert
from app.sync.location_registry_status import apply_location_registry_flags
from app.sync.s95_location_status import resolve_s95_location_status

logger = logging.getLogger(__name__)

PLATFORM_CODE = "s95"


@dataclass
class S95CancellationsWatchResult:
    entries_total: int = 0
    candidates: int = 0
    cancellations_active: int = 0
    cancel_status_changed: int = 0
    cancel_changed_locations: list[str] = field(default_factory=list)
    banned: bool = False
    errors: list[str] = field(default_factory=list)


def watch_s95_cancellations(db: Session, *, notify: bool = True) -> S95CancellationsWatchResult:
    platform = upsert.get_platform(db, PLATFORM_CODE)
    result = S95CancellationsWatchResult()

    fetch_errors: list[str] = []
    try:
        entries = fetch_all_locations(errors=fetch_errors)
    except Exception as exc:
        result.errors.append(f"реестр s95: {exc}")
        logger.warning("S95 cancellations watch: реестр недоступен (%s)", exc, exc_info=True)
        return result

    result.entries_total = len(entries)
    # Реестр прочитан целиком только когда ни один запрос не упал и хоть что-то
    # вернулось. Частичный (s95 закрылся по IP, лёг один из доменов) для снятия
    # отметок непригоден: площадки недоступного домена в нём просто отсутствуют,
    # и цикл «не в списке — значит бежит» снял бы реальные отмены и разослал
    # ложные «✅ Отмена снята». Ставить новые отмены по частичным данным можно.
    registry_complete = not fetch_errors and bool(entries)
    if fetch_errors:
        result.errors.extend(f"реестр s95: {item}" for item in fetch_errors)
        logger.warning(
            "S95 cancellations watch: реестр прочитан не целиком (%s) — отметки не снимаем",
            "; ".join(fetch_errors),
        )

    candidates = [entry for entry in entries if not entry.active]
    result.candidates = len(candidates)

    cancelled: dict[str, tuple[str, str | None]] = {}
    unknown: set[str] = set()
    for index, entry in enumerate(candidates):
        try:
            status = resolve_s95_location_status(entry)
        except S95BanDetected as exc:
            # Бан общий на домен: остальные страницы получат то же самое, только
            # быстрее заработают cooldown. Всё, до чего не дошли, уходит в
            # «неизвестно»: снимать отметку с непроверенной площадки нельзя.
            result.banned = True
            result.errors.append(f"{entry.slug}: {exc}")
            unknown.update(rest.slug for rest in candidates[index:])
            break
        except S95YieldForUserSync:
            # Пришёл живой человек за своим профилем — уступаем очередь целиком.
            # Записать мы ещё ничего не успели: флаги ставятся ниже, после
            # обхода, поэтому уйти отсюда исключением безопасно.
            raise
        except Exception as exc:
            # Страницу не прочитали — про эту площадку мы ничего не знаем и
            # ничего у неё не трогаем.
            unknown.add(entry.slug)
            result.errors.append(f"{entry.slug}: {exc}")
            continue
        if status.is_cancelled:
            cancelled[entry.slug] = (entry.name, status.cancel_reason)

    result.cancellations_active = len(cancelled)

    rows = db.query(Location).filter(Location.platform_id == platform.id).all()
    by_key = {row.external_key: row for row in rows}
    changes: list[CancellationChange] = []

    for slug, (name, reason) in cancelled.items():
        row = by_key.get(slug)
        if row is None:
            # Локации нет в базе — заведёт её синк реестра, отмену подхватим
            # следующим проходом.
            result.errors.append(f"{slug}: строки локации нет в базе")
            continue
        _, _, cancel_changed = apply_location_registry_flags(
            row,
            is_paused=row.is_paused,
            is_cancelled=True,
            is_upcoming=row.is_upcoming,
            cancel_reason=reason,
        )
        if cancel_changed:
            result.cancel_status_changed += 1
            result.cancel_changed_locations.append(location_detail_label(slug, name))
            changes.append(
                CancellationChange(
                    platform_code=PLATFORM_CODE,
                    slug=slug,
                    name=name,
                    cancelled=True,
                    reason=reason,
                )
            )

    for row in rows:
        if not registry_complete:
            break  # неполный реестр: «нет в списке» не означает «отмена снята»
        if not row.is_cancelled:
            continue
        if row.external_key in cancelled or row.external_key in unknown:
            continue
        _, _, cancel_changed = apply_location_registry_flags(
            row,
            is_paused=row.is_paused,
            is_cancelled=False,
            is_upcoming=row.is_upcoming,
        )
        if cancel_changed:
            result.cancel_status_changed += 1
            result.cancel_changed_locations.append(location_detail_label(row.external_key, row.name))
            changes.append(
                CancellationChange(
                    platform_code=PLATFORM_CODE,
                    slug=row.external_key,
                    name=row.name,
                    cancelled=False,
                )
            )

    db.commit()

    if result.cancel_status_changed:
        # Витрины каталога держат снимок в Redis на часы: без сброса отмена
        # доедет до карты и каталога только к протуханию кэша.
        flush_location_catalog_caches("наблюдатель отмен s95")
        flush_location_page_caches(db, [item.slug for item in changes], "наблюдатель отмен s95")
        if notify:
            notify_cancellation_changes(changes)

    return result

