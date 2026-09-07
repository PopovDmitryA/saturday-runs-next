"""Старты сообществ 5 вёрст: сбор разовых стартов из /starti-soobshchestv/.

Раздел появился отдельно от площадок: «Зелёные 5 км», «День физкультурника.
Тула». У такого старта нет ни страницы `/{slug}/results/all/`, ни строки в
реестре площадок — только один протокол. Поэтому весь остальной синк 5 вёрст
их не видит: он ходит по реестру `/events/` и по таблицам площадок.

При этом 5 вёрст засчитывает эти финиши в личный счётчик человека, и без них
наши числа расходятся с источником ровно на единицу у каждого, кто там бежал
(на 07.09.2026 — 522 человека, 683 финиша). Отсюда и жалобы «у тебя на сайте
на одну пробежку меньше».

Собранный старт живёт как локация с флагом `is_community_event`: финиши идут в
личные итоги, но в каталоге, на карте, в туризме и в рейтингах по локациям
такой «площадки» нет (см. app/services/community_events.py).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.five_verst.errors import FiveVerstBanDetected
from app.five_verst.fetch.protocol_pause import wait_between_protocols
from app.models import Platform, SyncRun, SyncRunStatus
from app.platform_adapters.canonical import CanonicalLocation
from app.platform_adapters.five_verst import bulk_parser
from app.platform_adapters.five_verst.http import NotFoundError
from app.sync import upsert
from app.sync.iteration_commit import commit_step, release_before_fetch, rollback_step

PLATFORM_CODE = "five_verst"
logger = logging.getLogger(__name__)


@dataclass
class CommunitySyncOptions:
    dry_run: bool = False
    limit: int | None = None
    slug: str | None = None


@dataclass
class CommunitySyncResult:
    slugs_total: int = 0
    events_upserted: int = 0
    run_results_upserted: int = 0
    volunteer_results_upserted: int = 0
    planned: list[str] = field(default_factory=list)
    changed_events: list[str] = field(default_factory=list)
    pages_missing: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _start_sync_run(db: Session, platform: Platform) -> SyncRun:
    run = SyncRun(
        platform_id=platform.id,
        sync_type="five_verst:community_events",
        status=SyncRunStatus.running,
        parser_version=upsert.PARSER_VERSION,
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()
    return run


def _finish_sync_run(
    db: Session,
    run: SyncRun,
    *,
    success: bool,
    fetched: int,
    upserted: int,
    error: str | None = None,
) -> None:
    run.status = SyncRunStatus.success if success else SyncRunStatus.failed
    run.finished_at = datetime.now(timezone.utc)
    run.records_fetched = fetched
    run.records_upserted = upserted
    run.error_message = error
    db.flush()


def _sync_one(
    db: Session,
    platform: Platform,
    slug: str,
    display_name: str | None,
    result: CommunitySyncResult,
) -> None:
    # Транзакцию отпускаем ДО похода в сеть: фетч ждёт общий интервал между
    # запросами к 5verst.ru и может уступать пользовательскому синку минутами,
    # а прод рвёт сессии, висящие «idle in transaction». Ровно на этом прогон
    # и упал на первом же живом запуске.
    release_before_fetch(db)
    page, html = bulk_parser.fetch_community_event(slug, display_name=display_name)
    if page is None:
        # Заголовок с датой — единственный источник даты старта; без него
        # событие завести не к чему.
        result.errors.append(f"{slug}: не разобрался заголовок с датой")
        return

    location_row, _ = upsert.upsert_location(
        db,
        platform,
        CanonicalLocation(
            external_key=slug,
            name=page.name,
            source_url=page.source_url,
        ),
        source_hash=bulk_parser.source_hash(html),
    )
    # Флаг ставим здесь, а не в upsert_location: это единственное место, где
    # заводятся старты сообществ, и общий upsert о них знать не должен.
    if not location_row.is_community_event:
        location_row.is_community_event = True
    db.flush()

    event_row = upsert.upsert_event_for_profile(
        db,
        platform,
        location_row,
        external_event_key=f"{slug}:{page.event_date.isoformat()}",
        event_date=page.event_date,
        event_number=None,
        location_name=page.name,
        location_slug=slug,
        source_url=page.source_url,
    )
    result.events_upserted += 1

    runs = upsert.replace_event_run_results(db, event_row, platform, page.run_results)
    vols = upsert.replace_event_volunteer_results(db, event_row, platform, page.volunteer_results)
    result.run_results_upserted += runs
    result.volunteer_results_upserted += vols

    source_hash = bulk_parser.source_hash(html)
    if event_row.source_hash != source_hash:
        result.changed_events.append(f"{slug} {page.event_date.isoformat()}")
    event_row.source_hash = source_hash
    event_row.fetched_at = datetime.now(timezone.utc)
    db.flush()


def sync_community_events(
    db: Session,
    options: CommunitySyncOptions | None = None,
) -> CommunitySyncResult:
    options = options or CommunitySyncOptions()
    platform = upsert.get_platform(db, PLATFORM_CODE)
    result = CommunitySyncResult()

    # get_platform выше уже открыл транзакцию, а список слагов — это две
    # страницы с теми же паузами: отпускаем её перед ними.
    release_before_fetch(db)
    entries = (
        {options.slug: ""} if options.slug else bulk_parser.fetch_community_entries()
    )
    slugs = list(entries)
    if options.limit is not None:
        slugs = slugs[: options.limit]
    result.slugs_total = len(slugs)
    result.planned = list(slugs)
    if options.dry_run or not slugs:
        return result

    sync_run = _start_sync_run(db, platform)
    db.commit()
    try:
        for index, slug in enumerate(slugs):
            try:
                _sync_one(db, platform, slug, entries.get(slug) or None, result)
                commit_step(db)
            except FiveVerstBanDetected as exc:
                rollback_step(db)
                result.errors.append(f"{slug}: {exc}; остаток отложен")
                break
            except NotFoundError as exc:
                # Старт убрали с сайта — не сбой прогона.
                rollback_step(db)
                result.pages_missing.append(f"{slug}: {exc}")
            except Exception as exc:
                rollback_step(db)
                result.errors.append(f"{slug}: {exc}")
            if index + 1 < len(slugs):
                wait_between_protocols(reason="community")

        _finish_sync_run(
            db,
            sync_run,
            success=not result.errors,
            fetched=result.slugs_total,
            upserted=result.run_results_upserted + result.volunteer_results_upserted,
            error="; ".join(result.errors) or None,
        )
        db.commit()
        return result
    except Exception as exc:
        db.rollback()
        _finish_sync_run(db, sync_run, success=False, fetched=0, upserted=0, error=str(exc))
        db.commit()
        raise
