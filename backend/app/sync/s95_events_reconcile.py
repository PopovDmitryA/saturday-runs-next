"""Сверка состава и нумерации событий S95 со списком площадки.

Зачем отдельная задача. Оба синка S95 опираются на `updated_at`: воскресный
скан ищет новые протоколы, понедельничный забирает новые и изменённые. Ни один
не перечитывает то, что не менялось, — а именно там и копится расхождение:

- S95 может УДАЛИТЬ событие (обычно пустое), и `updated_at` у остальных при
  этом не трогается. Про удаление мы не узнаём никогда;
- от удаления едет нумерация: номер забега мы не получаем из API, а считаем
  как хронологический ранг активности в списке площадки. Лишнее событие у нас
  сдвигает все последующие номера на единицу.

14.09.2026 так нашлось: Великий Новгород — лишняя пустая активность 550 за
01.04.2023 и сбитый номер у 165 событий (наш №128 = их №127). Хуже того,
фантом стоял primary в кросслинке с RunPark, и 61 финишёр того дня не считался
нигде: у primary ноль, а secondary из подсчётов исключён.

У 5 вёрст от этого класса есть недельный обход протоколов; у S95 такой
страховки не было. Эта сверка — её дешёвый аналог: ходит по спискам площадок
(36 небольших JSON), сами протоколы не качает.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import Event, EventSummary, Location, Platform, ProtocolSyncState, RunResult, VolunteerResult
from app.s95.api_client import S95ApiActivityRef, fetch_all_locations, fetch_event_activities
from app.s95.errors import S95BanDetected
from app.s95.fetch.priority import S95YieldForUserSync
from app.sync import upsert
from app.sync.iteration_commit import commit_step, release_before_fetch, rollback_step
from app.sync.s95_global_sync_api import event_numbers_by_date

logger = logging.getLogger(__name__)

PLATFORM_CODE = "s95"


@dataclass
class S95EventsReconcileResult:
    locations_checked: int = 0
    numbers_fixed: int = 0
    phantoms_deleted: int = 0
    # Пустое событие с кросслинком: удаление вернёт в зачёт протокол парной
    # системы, до этого он лежал secondary при нулевом primary.
    crosslinks_released: int = 0
    missing_protocols: list[str] = field(default_factory=list)
    kept_with_results: list[str] = field(default_factory=list)
    # Уступили пользовательскому синку или s95 закрылся — прогон свёрнут на
    # полпути. Это не ошибка, но и не «всё сверено».
    stopped_reason: str | None = None
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "locations_checked": self.locations_checked,
            "numbers_fixed": self.numbers_fixed,
            "phantoms_deleted": self.phantoms_deleted,
            "crosslinks_released": self.crosslinks_released,
            "missing_protocols": self.missing_protocols,
            "kept_with_results": self.kept_with_results,
            "stopped_reason": self.stopped_reason,
            "errors": self.errors,
        }


def _activity_id(source_url: str | None) -> str | None:
    if not source_url:
        return None
    tail = source_url.rsplit("/", 1)[-1].removesuffix(".json")
    return tail or None


def _delete_phantom(db: Session, event: Event) -> int:
    """Убрать событие, которого у источника больше нет. Возвращает снятые кросслинки.

    Зовётся только для пустых: с результатами ничего не удаляем — такое
    расхождение разбирает человек.
    """
    released = db.execute(
        text("delete from event_crosslinks where primary_event_id = :e or secondary_event_id = :e"),
        {"e": event.id},
    ).rowcount
    summary_ids = [
        row.id
        for row in db.query(EventSummary.id).filter(EventSummary.event_id == event.id).all()
    ]
    # Состояние синка ссылается и на событие, и на сводку — снимаем по обоим,
    # иначе останется висеть строка с битым FK.
    states = db.query(ProtocolSyncState).filter(ProtocolSyncState.event_id == event.id)
    states.delete(synchronize_session=False)
    if summary_ids:
        db.query(ProtocolSyncState).filter(
            ProtocolSyncState.event_summary_id.in_(summary_ids)
        ).delete(synchronize_session=False)
        # Сводку тоже убираем: источник эту активность больше не отдаёт, и
        # осиротевшая строка снова попала бы в планы перекачки.
        db.query(EventSummary).filter(EventSummary.id.in_(summary_ids)).delete(
            synchronize_session=False
        )
    db.delete(event)
    db.flush()
    return released or 0


def reconcile_location_events(
    db: Session,
    platform: Platform,
    location: Location,
    refs: list[S95ApiActivityRef],
    result: S95EventsReconcileResult,
    *,
    apply: bool = True,
) -> None:
    """Сверить одну площадку. Список уже получен — в сеть отсюда не ходим.

    Фетч вынесен к вызывающему специально: на проде стоит
    `idle_in_transaction_session_timeout`, и держать транзакцию открытой, пока
    ходишь за 36 списками, нельзя — соединение рвётся (см. release_before_fetch).
    """
    if not refs:
        # Пустой список — почти наверняка сбой на той стороне, а не «площадка
        # без стартов». Принять его за правду значит снести ей всю историю.
        result.errors.append(f"{location.external_key}: пустой список активностей, пропуск")
        return

    wanted_numbers = event_numbers_by_date(refs)
    wanted_by_activity = {_activity_id(ref.url): ref for ref in refs}
    wanted_by_activity.pop(None, None)

    events = (
        db.query(Event)
        .filter(Event.location_id == location.id, Event.platform_id == platform.id)
        .all()
    )
    seen: set[str] = set()
    for event in events:
        activity = _activity_id(event.source_url)
        if activity is not None:
            seen.add(activity)
        if activity is not None and activity in wanted_by_activity:
            number = wanted_numbers.get(wanted_by_activity[activity].date)
            if number is not None and event.event_number != number:
                result.numbers_fixed += 1
                if not apply:
                    continue
                event.event_number = number
                if not event.is_test_event:
                    event.title = f"{location.name} #{number}"
                summary = (
                    db.query(EventSummary)
                    .filter(
                        EventSummary.platform_id == platform.id,
                        EventSummary.event_id == event.id,
                    )
                    .one_or_none()
                )
                if summary is not None:
                    summary.event_number = number
            continue

        # Активности нет в списке источника — событие удалили на их стороне.
        label = f"{location.external_key}:{event.event_date}"
        runs = db.query(RunResult).filter(RunResult.event_id == event.id).count()
        vols = db.query(VolunteerResult).filter(VolunteerResult.event_id == event.id).count()
        if runs or vols:
            result.kept_with_results.append(f"{label} (финишей {runs}, волонтёров {vols})")
            continue
        result.phantoms_deleted += 1
        if not apply:
            continue
        result.crosslinks_released += _delete_phantom(db, event)
        logger.info("S95: убрал пустое событие %s, которого нет у источника", label)

    for activity, ref in wanted_by_activity.items():
        if activity not in seen:
            # Дозабирать протокол здесь не будем: это работа синка, а сверка
            # должна оставаться дешёвой. Достаточно показать в сводке.
            result.missing_protocols.append(f"{location.external_key}:{ref.date}")


def reconcile_s95_events(
    db: Session, *, only_slug: str | None = None, apply: bool = True
) -> S95EventsReconcileResult:
    result = S95EventsReconcileResult()
    platform = upsert.get_platform(db, PLATFORM_CODE)
    # Список площадок — тоже поход в сеть, причём по трём доменам. Без этого
    # release транзакция, открытая get_platform выше, висит весь фетч, и падает
    # первый же SELECT после него (14.09.2026 — на выборке локации «ivanovo»).
    release_before_fetch(db)
    api_locations = fetch_all_locations()
    for api_loc in api_locations:
        if only_slug and api_loc.slug != only_slug:
            continue
        location = (
            db.query(Location)
            .filter(Location.platform_id == platform.id, Location.external_key == api_loc.slug)
            .one_or_none()
        )
        if location is None:
            continue
        result.locations_checked += 1
        try:
            # Транзакцию отпускаем ПЕРЕД сетью: 36 списков подряд гарантированно
            # выходят за idle_in_transaction_session_timeout, и упадёт при этом
            # первый же SELECT после фетча — на совершенно невиновном запросе.
            release_before_fetch(db)
            refs = fetch_event_activities(f"{api_loc.domain}/events/{api_loc.slug}.json")
            reconcile_location_events(db, platform, location, refs, result, apply=apply)
            # Коммитим шаг всегда: в режиме «посмотреть» писать нечего, но
            # транзакцию за собой оставлять нельзя — следующая площадка опять
            # уйдёт в сеть.
            commit_step(db)
        except (S95BanDetected, S95YieldForUserSync) as exc:
            # Уступка пользовательскому синку — штатное дело: батч обязан
            # свернуться, а не ломиться в следующую площадку и получить то же
            # самое ещё 35 раз. Бан s95 — тем более повод остановиться.
            rollback_step(db)
            result.stopped_reason = (
                "уступили пользовательскому синку"
                if isinstance(exc, S95YieldForUserSync)
                else str(exc)
            )
            if isinstance(exc, S95BanDetected):
                result.errors.append(f"остановлено: {exc}")
            break
        except Exception as exc:  # noqa: BLE001 — одна площадка не должна ронять сверку
            rollback_step(db)
            result.errors.append(f"{api_loc.slug}: {exc}")
            logger.warning("S95 сверка событий: %s", api_loc.slug, exc_info=True)
    return result
