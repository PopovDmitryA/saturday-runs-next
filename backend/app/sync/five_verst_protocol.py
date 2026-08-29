from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import (
    EventSummary,
    Location,
    Platform,
    ProtocolRevision,
    ProtocolSyncState,
    RunResult,
    SyncStatus,
    VolunteerResult,
)
from app.platform_adapters.canonical import CanonicalEventSummary
from app.platform_adapters.five_verst import bulk_parser
from app.services.gender_position_service import recalculate_event_gender_positions
from app.sync import upsert


@dataclass
class ProtocolUpsertResult:
    event_id: str
    run_results_upserted: int
    volunteer_results_upserted: int
    run_results_count: int
    volunteer_results_count: int
    protocol_source_hash: str
    protocol_changed: bool


def _get_or_create_protocol_sync_state(
    db: Session,
    *,
    event_id,
    event_summary_id,
) -> ProtocolSyncState:
    row = db.query(ProtocolSyncState).filter(ProtocolSyncState.event_id == event_id).one_or_none()
    if row is None:
        row = ProtocolSyncState(event_id=event_id, event_summary_id=event_summary_id)
        db.add(row)
        db.flush()
    elif event_summary_id and row.event_summary_id != event_summary_id:
        row.event_summary_id = event_summary_id
    return row


def _touch_protocol_check(db: Session, state: ProtocolSyncState) -> None:
    now = datetime.now(timezone.utc)
    state.last_protocol_check_at = now
    db.flush()


def mark_protocol_check(db: Session, event_id) -> None:
    state = db.query(ProtocolSyncState).filter(ProtocolSyncState.event_id == event_id).one_or_none()
    if state is None:
        state = ProtocolSyncState(event_id=event_id)
        db.add(state)
    _touch_protocol_check(db, state)


def _result_snapshot(db: Session, event_id) -> dict[str, tuple]:
    """Слепок протокола до перезаписи: ключ — external_result_key."""

    return {
        row.external_result_key: (row.position, row.finish_time_sec, row.status)
        for row in db.query(RunResult).filter(RunResult.event_id == event_id).all()
    }


_REVISION_SAMPLE_LIMIT = 20


def record_protocol_revision(db: Session, event_id, before: dict[str, tuple], after: dict[str, tuple]) -> None:
    """Сравнить слепки протокола и записать правку в журнал.

    «Неизвестный стал известным» (та же позиция и время, статус unknown → нет)
    правкой не считается — решение Дмитрия 23.08.2026. Всё остальное — считается:
    добавленные и убранные строки, сдвиги позиций, изменения времени.
    """

    added = [key for key in after if key not in before]
    removed = [key for key in before if key not in after]
    time_changes: list[dict[str, object]] = []
    time_changes_count = 0
    position_changes = 0
    identified = 0
    for key, (old_pos, old_time, old_status) in before.items():
        new = after.get(key)
        if new is None:
            continue
        new_pos, new_time, new_status = new
        if old_pos == new_pos and old_time == new_time:
            if old_status == "unknown" and new_status != "unknown":
                identified += 1
            continue
        if old_time != new_time:
            time_changes_count += 1
            if len(time_changes) < _REVISION_SAMPLE_LIMIT:
                time_changes.append({"position": new_pos, "old_sec": old_time, "new_sec": new_time})
        if old_pos != new_pos:
            position_changes += 1

    # Замена «Неизвестного» на имя приходит и парой удаление+добавление: у 5в
    # external_result_key завязан на участника. Если состав изменений — только
    # такие пары (та же позиция и время у убранной и добавленной строки),
    # правкой это тоже не считаем.
    if not time_changes_count and not position_changes and (added or removed):
        # Сравниваем мультимножества ВСЕХ убранных и добавленных строк, а не
        # только отфильтрованных «неизвестных»: иначе настоящая пропажа
        # известной строки, пришедшая вместе с парами «неизвестный→имя»
        # (удаления не дают добавлений, равенство отфильтрованных наборов
        # сохранялось), молча проглатывалась без записи в журнал.
        all_removed_unknown = all(before[key][2] == "unknown" for key in removed)
        all_added_known = all(after[key][2] != "unknown" for key in added)
        removed_pairs = sorted((before[key][0], before[key][1]) for key in removed)
        added_pairs = sorted((after[key][0], after[key][1]) for key in added)
        if removed and all_removed_unknown and all_added_known and removed_pairs == added_pairs:
            return

    if not added and not removed and not time_changes_count and not position_changes:
        return

    if added or removed:
        kind = "results_changed"
    elif time_changes_count:
        kind = "times_changed"
    else:
        kind = "positions_changed"

    db.add(
        ProtocolRevision(
            event_id=event_id,
            detected_at=datetime.now(timezone.utc),
            kind=kind,
            details={
                "added": len(added),
                "removed": len(removed),
                "time_changes": time_changes,
                "time_changes_total": time_changes_count,
                "position_changes": position_changes,
                "identified": identified,
            },
        )
    )
    db.flush()


def fetch_and_upsert_event_protocol(
    db: Session,
    platform: Platform,
    location: Location,
    summary: CanonicalEventSummary,
    summary_row: EventSummary,
) -> ProtocolUpsertResult:
    slug = summary.location_external_key
    # Сетевой запрос делаем БЕЗ открытой транзакции. Вызывающие циклы к этому
    # моменту уже сделали свои SELECT'ы, то есть транзакция открыта, а фетч
    # страницы 5verst.ru — это до пяти попыток по 30 секунд плюс ожидание
    # общего лока загрузок; всё это время соединение висит «idle in
    # transaction». На проде стоит idle_in_transaction_session_timeout=15min,
    # и он регулярно рвал прогоны latest и reconcile на середине очереди.
    db.commit()
    run_results, volunteer_results, protocol_html = bulk_parser.fetch_event_protocol(
        slug,
        summary.event_date,
        summary.event_number,
    )
    protocol_source_hash = bulk_parser.source_hash(protocol_html)
    event_row = upsert.upsert_event_for_summary(db, platform, location, summary, summary_row)
    state = _get_or_create_protocol_sync_state(
        db,
        event_id=event_row.id,
        event_summary_id=summary_row.id,
    )
    previous_hash = state.protocol_source_hash
    # Журнал правок: если протокол у источника изменился, сравниваем слепки
    # до и после перезаписи (см. record_protocol_revision).
    before_snapshot: dict[str, tuple] | None = None
    if previous_hash is not None and previous_hash != protocol_source_hash:
        before_snapshot = _result_snapshot(db, event_row.id)
    run_results_upserted = upsert.replace_event_run_results(db, event_row, platform, run_results)
    if before_snapshot is not None:
        record_protocol_revision(db, event_row.id, before_snapshot, _result_snapshot(db, event_row.id))
    recalculate_event_gender_positions(db, event_row.id, platform.code)
    volunteer_results_upserted = upsert.replace_event_volunteer_results(db, event_row, platform, volunteer_results)
    run_results_count = (
        db.query(RunResult).filter(RunResult.event_id == event_row.id).count()
    )
    volunteer_results_count = (
        db.query(VolunteerResult).filter(VolunteerResult.event_id == event_row.id).count()
    )
    now = datetime.now(timezone.utc)
    state.last_protocol_fetched_at = now
    state.last_protocol_check_at = now
    state.protocol_source_hash = protocol_source_hash
    state.finishers_at_fetch = summary.finishers_count
    # Расписка «этот протокол соответствует вот такому саммари». Пока она не
    # совпадает с текущим summary_hash — за площадкой висит долг, и его увидит
    # любой следующий прогон (см. protocol_debt.py). До этой отметки триггер
    # «саммари изменилось» жил ровно один прогон: хэш коммитился в первом
    # цикле, а если до второго цикла дело не доходило (падение, бан-кулдаун,
    # protocol_fetch_limit), расхождение застывало навсегда.
    state.summary_hash_at_fetch = summary.summary_hash
    state.run_results_count = run_results_count
    state.volunteer_results_count = volunteer_results_count
    summary_row.sync_status = SyncStatus.ok
    summary_row.error_message = None
    db.flush()
    return ProtocolUpsertResult(
        event_id=str(event_row.id),
        run_results_upserted=run_results_upserted,
        volunteer_results_upserted=volunteer_results_upserted,
        run_results_count=run_results_count,
        volunteer_results_count=volunteer_results_count,
        protocol_source_hash=protocol_source_hash,
        protocol_changed=previous_hash is not None and previous_hash != protocol_source_hash,
    )
