"""Долг протокола: скачанный протокол отстал от саммари площадки.

Саммари (строка в витрине «последние результаты» / в списке стартов локации)
и протокол приезжают двумя разными запросами. Синк сначала коммитит саммари,
потом идёт за протоколом — и между этими шагами прогон может кончиться:
падение сессии, кулдаун после бана, упёрлись в protocol_fetch_limit.

Без отметки о долге такой обрыв необратим: новый summary_hash уже в базе,
и следующий прогон честно считает площадку `unchanged`. Так Серов
(parkdkm, 22.08.2026) трое суток показывал победителя с 00:15:04, хотя
площадка исправила протокол в тот же день.

`summary_hash_at_fetch` ставит только успешная закачка протокола. Пока она
не равна текущему `EventSummary.summary_hash` — за площадкой висит долг.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import EventSummary, ProtocolSyncState


def protocol_is_stale(state: ProtocolSyncState | None, summary_row: EventSummary) -> bool:
    """Протокол отстал от саммари и его надо перечитать.

    NULL в `summary_hash_at_fetch` — это «не знаем», а не «долг»: так выглядят
    строки, созданные `mark_protocol_check` по удалённой (404) странице, и
    строки без единой удачной закачки. Считать их долгом нельзя — очередь
    забьётся протоколами, которых на сайте нет.
    """
    if summary_row.event_id is None:
        # Протокола ещё не было вовсе — это отдельный случай `missing_protocol`,
        # его ловят сами синки, и подменять его долгом не нужно.
        return False
    if state is None or state.summary_hash_at_fetch is None:
        return False
    return state.summary_hash_at_fetch != summary_row.summary_hash


def summary_protocol_is_stale(db: Session, summary_row: EventSummary) -> bool:
    """То же, но со свежим чтением состояния протокола из базы."""
    if summary_row.event_id is None:
        return False
    state = (
        db.query(ProtocolSyncState)
        .filter(ProtocolSyncState.event_id == summary_row.event_id)
        .one_or_none()
    )
    return protocol_is_stale(state, summary_row)
