"""Постоянный план: запись, остановка, сон и подсказка «вы бегаете здесь каждую субботу».

Постоянный план — одна строка run_plan_series на человека (активная одна, на
любой локации по его выбору), вхождения считает planned_starts_service при
чтении. Здесь — всё, что серию пишет:

- save_series — «Планирую каждую субботу». Срок — «пока не остановлю»
  (решение Дмитрия по Q02) или до даты (V1). Серия на другой локации —
  409 с вопросом «заменить?»; прошлые субботы старой остаются в истории.
  Серия сама строк планов в run_plans не пишет никогда (только отметки
  «Не бегаю» снятых галочек), иначе остановка и сон оставляли бы имя на
  будущей дате, а поездка упиралась бы в лимит дня.
- stop_series — будущие субботы уходят, прошлые остаются; серия без прошлого
  удаляется. «Не бегаю» на будущих датах — отметки дня, они остаются.
- Сон (решение Дмитрия по Q03 и V1): три старта подряд дома без человека —
  план засыпает (имя пропадает с будущих дат, план не удаляется), одно
  сообщение «Ещё ходите?», если уведомления включены. Просыпается сам —
  человек снова пробежал или поволонтёрил здесь — или кнопкой «Продолжить».
  Не пропуск: «Не бегаю», поездка (разовый план в другой локации), отменённый
  старт, будние особые даты, сегодняшний день (протоколы догружаются) и всё,
  что было до создания плана или до «Продолжить». Считаем по системам, к
  которым у человека есть привязка: протокол зеркала, пришедший раньше
  протокола его системы, пропуском не станет. Правило одно для всех (Дмитрий
  26.10.2026: «3 пропуска — план засыпает, пробежка возобновляет, либо
  вручную на сайте»): без привязок к системам, что проводят здесь старты,
  человека в протоколе не видно (привязка к мёртвой строке каталога —
  parkrun-эпоха, старая S95 — не в счёт), и план уснёт через три её старта —
  сообщение об этом так и говорит и зовёт привязать профиль; привязал —
  протокол будит, как у всех.
- series_hint — «Вы пробежали здесь 9 из 10 последних стартов»: самая частая
  точка за 13 недель, ≥6 из 10; «Не сейчас» прячет на 30 дней на всех
  устройствах, «Остановить» и «Отменить» после одного нажатия — тоже, иначе
  подсказка вернулась бы сразу.

Каждая запись — под блокировкой строки человека (_lock_user), как у планов:
две вкладки не дадут двух активных серий, а задача сна не потеряет
«Продолжить», нажатое в ту же секунду.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import redis
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.redis_client import get_redis_client
from app.models import Event, Platform, PlatformLink, RunPlan, RunPlanSeries, RunResult, User
from app.services.planned_starts_service import (
    SleepState,
    first_presence,
    history_intervals,
    planned_starts,
    rule_dates,
    sleep_states,
    user_marked_dates,
)
from app.services.run_plan_service import (
    NEXT_PLAN_NEAR_DAYS,
    PlanError,
    _canonical_key,
    _Identities,
    _Identity,
    _lock_user,
    _series_key,
    canonical_identity_keys,
    clean_public,
    clear_rest,
    default_public,
    series_location,
    set_rest,
)
from app.services.start_calendar_service import (
    _EPOCH_GUARD,
    MOSCOW_TZ,
    PLANNER_HORIZON_DAYS,
    identity_rows,
    moscow_today,
    normalize_identity_key,
    plannable_rows_with_reason,
    start_dates,
)

logger = logging.getLogger(__name__)

# Столько стартов дома подряд без человека — и план засыпает. Текст сообщения
# называет число словом (_MISSES_WORDS) — поменяли, поменяйте и его.
SERIES_SLEEP_AFTER_MISSES = 3
# Пробуждение задача записывает и сериям, кончившимся не раньше стольких
# дней назад; усыпляет — только те, чей срок не кончился.
SERIES_SLEEP_GRACE_DAYS = 7
# Кончившийся срок (V1 «до…») держит слот «Продлить» столько дней, потом
# серия останавливается сама, без сообщения.
SERIES_EXPIRED_STOP_DAYS = 30
# Подсказка «Вы пробежали здесь 9 из 10 последних стартов».
SERIES_HINT_STARTS = 10
SERIES_HINT_MIN_RUNS = 6
SERIES_HINT_LOOKBACK_DAYS = 91
SERIES_HINT_SNOOZE_DAYS = 30
SERIES_HINT_CACHE_TTL_SECONDS = 6 * 3600
_HINT_CACHE_PREFIX = "planner:series-hint:v1:"
# Окно V3: столько ближайших суббот галочками.
SERIES_PREVIEW_DEFAULT = 8
SERIES_PREVIEW_MAX = 60
# «Пропущу» в ответе серии — не больше стольких будущих дат.
SERIES_SKIPPED_MAX = 52

SERIES_NOT_FOUND = "Постоянного плана нет"


class SeriesConflict(PlanError):
    """Активный постоянный план на другой локации: заменить — только явно (replace)."""

    def __init__(self, message: str, series: RunPlanSeries) -> None:
        super().__init__(message, 409)
        self.series = series


_UNSET: Any = object()


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------- чтение


def active_series(db: Session, user_id: UUID) -> RunPlanSeries | None:
    """Активная серия человека (`stopped_at IS NULL`), в том числе с истёкшим
    сроком V1 — у неё «Продлить», пока задача сна её не остановит."""
    return (
        db.query(RunPlanSeries)
        .filter(RunPlanSeries.user_id == user_id, RunPlanSeries.stopped_at.is_(None))
        .one_or_none()
    )


def _identity_of(
    db: Session, identities: _Identities, series: RunPlanSeries, key: str | None = None
) -> _Identity | None:
    """Точка серии сегодня; key — её ключ, если он уже посчитан."""
    return identities.get(key or _series_key(db, series)) or identities.get(series.location_key)


def _state(db: Session, series: RunPlanSeries, identities: _Identities, *, identity: Any = _UNSET) -> SleepState:
    if identity is _UNSET:
        identity = _identity_of(db, identities, series)
    ids = identity.location_ids if identity is not None else ([series.location_id] if series.location_id else [])
    return sleep_states(db, [series], {series.id: ids})[series.id]


def _live_codes(identity: _Identity) -> set[str]:
    """Системы, что проводят старты на точке сейчас: планируемые строки, а на
    паузе — строки систем планировщика (info_rows). Не все строки каталога:
    у смешанной точки «parkrun-эпоха → 5 вёрст» или со старой строкой S95
    привязка к мёртвой системе человека в протоколе не покажет."""
    return {code for _row, code in identity.info_rows}


def wakes_by_presence(db: Session, user_id: UUID, identity: _Identity | None) -> bool:
    """Видим ли мы человека в протоколах точки: у него есть привязка к системе,
    что проводит здесь старты. Нет — пробежку не увидеть, пока он не привяжет
    профиль: сообщение о сне и карточка зовут привязать, вернуть план сейчас —
    только «Продолжить»."""
    return bool(identity is not None and _linked_codes(db, user_id) & _live_codes(identity))


def series_public_on(db: Session, user_id: UUID, on_date: date) -> bool | None:
    """Выбор «Публично / Приватно» постоянного плана, если on_date — его дата
    (суббота или особая дата его точки в сроке плана). Иначе None.

    Умолчание «Не бегаю» на такой день — это «Пропущу»: снятая суббота
    приватного плана не должна всплыть «не бегает» в календаре профиля
    (ревью 27.10.2026, PRIV-3)."""
    series = active_series(db, user_id)
    if series is None or on_date < series.starts_on or (series.until is not None and on_date > series.until):
        return None
    today = moscow_today()
    identities = _Identities(db, today)
    rule = rule_dates(
        db,
        identities,
        _identity_of(db, identities, series),
        starts_on=series.starts_on,
        until=series.until,
        date_from=on_date,
        date_to=on_date,
        today=today,
    )
    return bool(series.is_public) if on_date in rule else None


def series_sleeping_now(db: Session, series: RunPlanSeries, *, identities: _Identities | None = None) -> bool:
    """Спит ли план сейчас — с ленивым пробуждением по протоколам."""
    return _state(db, series, identities or _Identities(db, moscow_today())).sleeping


def _rest_dates(db: Session, user_id: UUID, date_from: date, date_to: date) -> set[date]:
    return {
        row[0]
        for row in db.query(RunPlan.plan_date).filter(
            RunPlan.user_id == user_id,
            RunPlan.location_key.is_(None),
            RunPlan.rest.is_(True),
            RunPlan.plan_date >= date_from,
            RunPlan.plan_date <= date_to,
        )
    }


def series_payload(
    db: Session,
    user: User,
    series: RunPlanSeries,
    *,
    today: date | None = None,
    identities: _Identities | None = None,
    key: str | None = None,
) -> dict[str, Any]:
    """PlanSeries: точка, срок, сон и что на ближайший год — ближайшее вхождение,
    будущие «Пропущу» и поездки, вытеснившие субботу.

    Её зовёт блок локации на каждый просмотр постоянного, поэтому вхождения на
    год вперёд не считаем: ближайшее — сначала в две недели (как next_plan),
    поездки и «Пропущу» — только на датах, где у человека что-то отмечено.
    key — ключ точки серии, если вызывающий его уже знает.

    Кончившийся срок — не пауза: у такого плана будущих суббот нет и без сна,
    и карточке нужен «Продлить», а не «Продолжить», который срок не продлевает.
    """
    today = today or moscow_today()
    identities = identities or _Identities(db, today)
    key = key or _series_key(db, series)
    identity = _identity_of(db, identities, series, key)
    state = _state(db, series, identities, identity=identity)
    expired = series.until is not None and series.until < today
    plannable = bool(identity is not None and identity.plannable)
    sleeping = state.sleeping and not expired
    end = today + timedelta(days=PLANNER_HORIZON_DAYS)
    if series.until is not None:
        end = min(end, series.until)
    next_date: date | None = None
    away: dict[date, dict[str, Any]] = {}
    skipped: list[date] = []
    if plannable and not expired:
        plan_dates, rest = user_marked_dates(db, user.id, date_from=today, date_to=end)
        if not sleeping:
            # Одним чтением — ближайшие две недели и даты разовых планов.
            near_end = min(end, today + timedelta(days=NEXT_PLAN_NEAR_DAYS))
            near = {today + timedelta(days=offset) for offset in range((near_end - today).days + 1)}

            def own_dates(entries: list[Any]) -> list[date]:
                return [entry.plan_date for entry in entries if entry.series is not None and entry.series.id == series.id]

            entries = planned_starts(
                db, today=today, dates=near | plan_dates, user_ids=[user.id], identities=identities, with_displaces=True
            )
            soon = [on_date for on_date in own_dates(entries) if on_date <= near_end]
            if not soon and near_end < end:
                soon = own_dates(
                    planned_starts(
                        db, today=today, date_from=near_end, date_to=end, user_ids=[user.id], identities=identities
                    )
                )
            next_date = soon[0] if soon else None
            # Две поездки в один день — одна строка: суббота вытеснена одна.
            for entry in entries:
                if entry.away_from is not None and entry.away_from.id == series.id:
                    away.setdefault(entry.plan_date, series_location(identities, entry.key))
        if rest:
            rule = rule_dates(
                db,
                identities,
                identity,
                starts_on=series.starts_on,
                until=series.until,
                date_from=min(rest),
                date_to=max(rest),
                today=today,
            )
            skipped = sorted(day for day in rest if day in rule)[:SERIES_SKIPPED_MAX]
    return {
        "id": series.id,
        "location": series_location(identities, identity.key if identity is not None else key, fallback_key=key),
        "starts_on": series.starts_on,
        "until": series.until,
        "is_public": bool(series.is_public),
        "expired": expired,
        "plannable": plannable,
        "sleeping": sleeping,
        "sleeping_since": state.since if sleeping else None,
        # Как план выходит из паузы: протоколом («пробежите — продолжится сам»)
        # или только «Продолжить» — без привязок к системам точки.
        "wakes_by_presence": wakes_by_presence(db, series.user_id, identity),
        "next_date": next_date,
        "skipped": skipped,
        "away": [{"date": on_date, "location": location} for on_date, location in sorted(away.items())],
    }


# ---------------------------------------------------------------- запись


def _close_sleep(series: RunPlanSeries, to_date: date) -> None:
    """Закрыть текущий сон по to_date включительно (в историю — если он был)."""
    since = series.sleeping_since
    if since is not None and since <= to_date:
        # Новый список, а не append: JSONB без MutableList правку на месте не видит.
        series.sleep_history = [*(series.sleep_history or []), {"from": since.isoformat(), "to": to_date.isoformat()}]
    series.sleeping_since = None
    series.sleep_missed = None
    series.sleep_notified_at = None


def _apply_wake(series: RunPlanSeries, state: SleepState) -> bool:
    """Записать пробуждение, которое чтение уже видит. True — что-то поменялось."""
    if state.undone:
        # Результат за «пропущенный» старт пришёл позже — сна не было.
        series.sleeping_since = None
        series.sleep_missed = None
        series.sleep_notified_at = None
        return True
    if state.woke_on is not None:
        _close_sleep(series, state.woke_on - timedelta(days=1))
        series.resumed_on = state.woke_on
        return True
    return False


def _resume(db: Session, series: RunPlanSeries, identities: _Identities, today: date) -> None:
    """«Продолжить»: протокол уже разбудил — записать как есть, иначе сон до
    вчера в историю и счёт пропусков — с завтрашнего старта."""
    state = _state(db, series, identities)
    if _apply_wake(series, state):
        return
    if series.sleeping_since is not None:
        _close_sleep(series, today - timedelta(days=1))
        series.resumed_on = today


def _stop_row(db: Session, series: RunPlanSeries, today: date) -> bool:
    """Остановить серию: будущие субботы уходят, прошлые остаются. Без
    прошлого — удалить. True — удалена."""
    if series.starts_on >= today:
        db.delete(series)
        db.flush()
        return True
    yesterday = today - timedelta(days=1)
    if series.until is None or series.until > yesterday:
        series.until = yesterday
    if series.sleeping_since is not None:
        _close_sleep(series, series.until)
    series.stopped_at = _now()
    series.updated_at = _now()
    db.flush()
    return False


def _same_point(db: Session, series: RunPlanSeries, keys: set[str], location_ids: set[UUID]) -> bool:
    return (
        _series_key(db, series) in keys
        or (normalize_identity_key(series.location_key) or series.location_key) in keys
        or (series.location_id is not None and series.location_id in location_ids)
    )


def _share_publicity(
    db: Session, series: RunPlanSeries, keys: set[str], location_ids: set[UUID], today: date
) -> None:
    """Разовые планы на точке серии в её будущие даты для чужих глаз — её
    вхождения, и выбор у них общий с серией: иначе после «Остановить» такой
    план вдруг показал бы имя (или спрятал его) по старому выбору."""
    query = db.query(RunPlan).filter(
        RunPlan.user_id == series.user_id,
        RunPlan.location_key.isnot(None),
        RunPlan.plan_date >= max(today, series.starts_on),
        or_(RunPlan.location_key.in_(sorted(keys)), RunPlan.location_id.in_(list(location_ids))),
    )
    if series.until is not None:
        query = query.filter(RunPlan.plan_date <= series.until)
    for plan in query:
        plan.is_public = series.is_public


def save_series(
    db: Session,
    user: User,
    *,
    identity_key: str,
    starts_on: date | None = None,
    until: date | None = None,
    skip: list[date] | tuple[date, ...] = (),
    unskip: list[date] | tuple[date, ...] = (),
    replace: bool = False,
    replace_first: bool = False,
    is_public: bool | None = None,
) -> tuple[RunPlanSeries, dict[str, Any] | None, list[date]]:
    """«Планирую каждую субботу». (серия, заменённая локация, даты skip, что не встали).

    starts_on — дата календаря ≥ сегодня (по умолчанию ближайшая), until —
    None («пока не остановлю») или дата в [starts_on; сегодня + год].
    Активная серия:
    - на этой же точке и не кончилась — правка срока (и первой даты, если она
      ещё впереди); спала — просыпается: сохранить план — это «Продолжить»;
    - на этой же точке, кончилась — старая останавливается, новая с чистого
      листа (иначе промежуток задним числом стал бы вхождениями);
    - на другой точке — SeriesConflict, пока не replace=True; кончившаяся или
      чья точка пропала — тихо останавливается.
    skip / unskip — «Не бегаю» на даты (снятые и возвращённые галочки окна V3);
    дата, которую не отметить (не день старта, поездка), — в ответе.
    replace_first — первая дата занята поездкой: убрать её в той же транзакции.
    is_public — «Публично / Приватно», один выбор на весь план (решение
    Дмитрия 27.10.2026); None — у нового плана умолчание как у профиля, у
    правки — без изменений. Выбор переходит и на разовые планы на этой же
    точке в даты плана (_share_publicity). «Пропущу» (skip) — отметки дня
    с умолчанием как у этого плана (set_rest → series_public_on).
    """
    today = moscow_today()
    normalized = normalize_identity_key(identity_key)
    if normalized is None:
        raise PlanError("Эту локацию пока нельзя запланировать")
    rows = identity_rows(db, normalized)
    plannable, _reason = plannable_rows_with_reason(db, rows, today=today)
    if not plannable:
        raise PlanError("Эту локацию пока нельзя запланировать")
    horizon = today + timedelta(days=PLANNER_HORIZON_DAYS)
    calendar = {
        start.date: start
        for start in start_dates(db, plannable, date_from=today, date_to=horizon, today=today, history_rows=rows)
    }
    if not calendar:
        raise PlanError("В этот день старта нет")
    if starts_on is None:
        starts_on = min(calendar)
    elif starts_on not in calendar:
        raise PlanError("В этот день старта нет")
    if until is not None and not (starts_on <= until <= horizon):
        raise PlanError("Постоянный план — от первой даты и не дальше чем на год вперёд")

    _lock_user(db, user.id)
    identities = _Identities(db, today)
    anchor = calendar[starts_on].location_id
    key = _canonical_key(db, normalized, anchor)
    keys = {normalized, key}
    location_ids = {location.id for location, _code in rows}
    current = active_series(db, user.id)
    replaced: dict[str, Any] | None = None
    series: RunPlanSeries | None = None
    if current is not None:
        same = _same_point(db, current, keys, location_ids)
        ended = current.until is not None and current.until < today - timedelta(days=1)
        if same and not ended:
            series = current
            if current.starts_on >= today:
                series.starts_on = starts_on
            series.until = until
            _resume(db, series, identities, today)
        else:
            current_identity = _identity_of(db, identities, current)
            if not same and not ended and current_identity is not None:
                if not replace:
                    new_name = identities.get(normalized)
                    target = new_name.name if new_name is not None else "эту локацию"
                    if current_identity.plannable:
                        message = (
                            f"Ваш постоянный план сейчас: {current_identity.name}. Заменить на {target}? "
                            "Прошлые субботы останутся в истории."
                        )
                    else:
                        # «Локация …» — не согласовывать глагол с произвольным именем.
                        message = (
                            f"Локация «{current_identity.name}» сейчас не проводит старты. "
                            f"Сделать постоянной «{target}»?"
                        )
                    raise SeriesConflict(message, current)
                replaced = series_location(identities, current_identity.key)
            _stop_row(db, current, today)
    if series is None:
        series = RunPlanSeries(
            user_id=user.id,
            location_key=key,
            location_id=anchor,
            starts_on=starts_on,
            until=until,
            is_public=clean_public(user, is_public, default_public(user)),
            sleep_history=[],
        )
        try:
            with db.begin_nested():
                db.add(series)
                db.flush()
        except IntegrityError as exc:
            raise PlanError("План уже сохранён в другой вкладке — обновите страницу", 409) from exc
    if is_public is not None:
        series.is_public = clean_public(user, is_public, series.is_public)
        _share_publicity(db, series, keys, location_ids, today)
    series.updated_at = _now()
    db.flush()

    if replace_first:
        # Все поездки первой даты: при двух одна оставшаяся держала бы субботу.
        for entry in planned_starts(db, today=today, dates=[starts_on], user_ids=[user.id], with_displaces=True):
            if entry.plan is not None and entry.away_from is not None and entry.away_from.id == series.id:
                db.delete(entry.plan)
        db.flush()
    failed: list[date] = []
    skip_set = set(skip)
    for day in sorted(skip_set):
        try:
            with db.begin_nested():
                set_rest(db, user, day)
        except PlanError:
            failed.append(day)
    for day in sorted(set(unskip) - skip_set):
        clear_rest(db, user, day)
    db.flush()
    forget_series_hint(user.id)
    return series, replaced, failed


def stop_series(db: Session, user: User, *, today: date | None = None) -> None:
    """«Остановить» и «Отменить» после одного нажатия: будущее уходит, прошлое
    остаётся, план без прошлого удаляется. Подсказка «каждую субботу» после
    этого молчит 30 дней — сам же человек только что сказал «нет»."""
    today = today or moscow_today()
    _lock_user(db, user.id)
    current = active_series(db, user.id)
    if current is not None:
        _stop_row(db, current, today)
    user.plans_hint_snoozed_until = _now() + timedelta(days=SERIES_HINT_SNOOZE_DAYS)
    db.flush()
    forget_series_hint(user.id)


def resume_series(db: Session, user: User, *, today: date | None = None) -> RunPlanSeries:
    """«Продолжить». Плана нет — PlanError 404; не спит — без изменений."""
    today = today or moscow_today()
    _lock_user(db, user.id)
    current = active_series(db, user.id)
    if current is None:
        raise PlanError(SERIES_NOT_FOUND, 404)
    _resume(db, current, _Identities(db, today), today)
    current.updated_at = _now()
    db.flush()
    return current


# ---------------------------------------------------------------- окно «каждую субботу»


def series_preview(
    db: Session,
    user: User,
    *,
    identity_key: str,
    starts_on: date | None = None,
    until: date | None = None,
    limit: int = SERIES_PREVIEW_DEFAULT,
) -> dict[str, Any]:
    """Что встанет в постоянный план: ближайшие даты правила с состоянием —
    on (встанет), skipped («Не бегаю»), away (там разовый план в другой
    локации — other), cancelled (старт отменён). total — все даты правила в
    сроке (без срока — на год), on — сколько из них встанет."""
    today = moscow_today()
    normalized = normalize_identity_key(identity_key) or ""
    identities = _Identities(db, today)
    identity = identities.get(normalized)
    if identity is None:
        raise PlanError("Локация не найдена", 404)
    if not identity.plannable:
        raise PlanError("Эту локацию пока нельзя запланировать")
    horizon = today + timedelta(days=PLANNER_HORIZON_DAYS)
    calendar = identities.starts_between(identity, today, horizon)
    if not calendar:
        raise PlanError("В этот день старта нет")
    if starts_on is None:
        starts_on = calendar[0].date
    elif starts_on not in {start.date for start in calendar}:
        raise PlanError("В этот день старта нет")
    if until is not None and not (starts_on <= until <= horizon):
        raise PlanError("Постоянный план — от первой даты и не дальше чем на год вперёд")
    end = until or horizon
    rule = rule_dates(
        db, identities, identity, starts_on=starts_on, until=until, date_from=today, date_to=end, today=today
    )
    here_ids = set(identity.location_ids)
    away: dict[date, dict[str, Any]] = {}
    # План на эту точку в тот же день держит субботу сам (её поглотит серия),
    # и поездка рядом с ним дату не отнимает.
    own_dates: set[date] = set()
    for entry in planned_starts(
        db, today=today, date_from=starts_on, date_to=end, user_ids=[user.id], identities=identities
    ):
        if entry.plan is None:
            continue
        if entry.key == identity.key or entry.location_id in here_ids:
            own_dates.add(entry.plan_date)
            continue
        away.setdefault(entry.plan_date, series_location(identities, entry.key))
    for on_date in own_dates:
        away.pop(on_date, None)
    rest = _rest_dates(db, user.id, starts_on, end)
    dates: list[dict[str, Any]] = []
    on = 0
    for on_date in sorted(rule):
        start = rule[on_date]
        other = away.get(on_date)
        if other is not None:
            state = "away"
        elif on_date in rest:
            state = "skipped"
        elif start.cancelled:
            state = "cancelled"
        else:
            state = "on"
            on += 1
        dates.append(
            {
                "date": on_date,
                "kind": start.kind,
                "title": start.title,
                "start_time": start.start_time,
                "number_forecast": start.number_forecast,
                "state": state,
                "other": other,
            }
        )
    return {
        "key": identity.key,
        "starts_on": starts_on,
        "until": until,
        "total": len(dates),
        "on": on,
        "dates": dates[: max(1, min(SERIES_PREVIEW_MAX, limit))],
        # Окну нового плана — как он выйдет из паузы: без привязки к системам
        # точки обещать «пробежите — продолжится сам» нельзя.
        "wakes_by_presence": wakes_by_presence(db, user.id, identity),
    }


# ---------------------------------------------------------------- подсказка «9 из 10»


def forget_series_hint(user_id: UUID) -> None:
    try:
        get_redis_client().delete(f"{_HINT_CACHE_PREFIX}{user_id}")
    except redis.RedisError:
        pass


def _hint_cache_read(user_id: UUID) -> dict[str, Any] | None | bool:
    """Что в кэше: dict — подсказка, None — «подсказки нет», False — кэша нет."""
    try:
        raw = get_redis_client().get(f"{_HINT_CACHE_PREFIX}{user_id}")
    except redis.RedisError:
        return False
    if raw is None:
        return False
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return False
    return value if isinstance(value, dict) and value else None


def _hint_cache_write(user_id: UUID, value: dict[str, Any] | None) -> None:
    try:
        get_redis_client().setex(f"{_HINT_CACHE_PREFIX}{user_id}", SERIES_HINT_CACHE_TTL_SECONDS, json.dumps(value or {}))
    except redis.RedisError:
        pass


def _compute_hint(db: Session, user_id: UUID, today: date) -> dict[str, Any] | None:
    """Самая частая планируемая точка за 13 недель и сколько из её последних
    десяти стартов человек пробежал. Волонтёрство не считаем: текст —
    «пробежали»."""
    rows = (
        db.query(Event.location_id, Event.event_date)
        .select_from(RunResult)
        .join(Event, RunResult.event_id == Event.id)
        .join(PlatformLink, PlatformLink.participant_id == RunResult.participant_id)
        .filter(
            PlatformLink.user_id == user_id,
            Event.is_test_event.is_(False),
            Event.event_date >= today - timedelta(days=SERIES_HINT_LOOKBACK_DAYS),
            Event.event_date <= today,
        )
        .distinct()
        .all()
    )
    if not rows:
        return None
    keys = canonical_identity_keys(db, [location_id for location_id, _date in rows])
    days: dict[str, set[date]] = {}
    for location_id, event_date in rows:
        days.setdefault(keys.get(location_id, f"location:{location_id}"), set()).add(event_date)
    identities = _Identities(db, today)
    for key in sorted(days, key=lambda item: (len(days[item]), max(days[item])), reverse=True):
        identity = identities.get(key)
        if identity is None or not identity.plannable:
            continue
        starts = [
            row[0]
            for row in db.query(Event.event_date)
            .filter(
                Event.location_id.in_(identity.location_ids),
                Event.is_test_event.is_(False),
                Event.event_date > _EPOCH_GUARD,
                Event.event_date <= today,
            )
            .distinct()
            .order_by(Event.event_date.desc())
            .limit(SERIES_HINT_STARTS)
        ]
        ran = {
            row[0]
            for row in db.query(Event.event_date)
            .select_from(RunResult)
            .join(Event, RunResult.event_id == Event.id)
            .join(PlatformLink, PlatformLink.participant_id == RunResult.participant_id)
            .filter(
                PlatformLink.user_id == user_id,
                Event.location_id.in_(identity.location_ids),
                Event.is_test_event.is_(False),
                Event.event_date.in_(starts),
            )
            .distinct()
        } if starts else set()
        runs, of = len(ran), len(starts)
        # «≥6 из 10»; у молодой точки с семью стартами — «5 из 7».
        if of >= SERIES_HINT_MIN_RUNS and runs * SERIES_HINT_STARTS >= SERIES_HINT_MIN_RUNS * of:
            return {"key": identity.key, "slug": identity.slug, "name": identity.name, "runs": runs, "of": of}
        return None
    return None


def series_hint(db: Session, user: User, *, today: date | None = None) -> dict[str, Any] | None:
    """SeriesHint или None: постоянного плана на сегодня и дальше нет, «Не
    сейчас» не действует, а самая частая точка — почти каждую субботу.
    Расчёт — в Redis на 6 часов (сброс — запись серии и «Не сейчас»)."""
    today = today or moscow_today()
    snoozed = user.plans_hint_snoozed_until
    if snoozed is not None and snoozed > _now():
        return None
    current = active_series(db, user.id)
    if current is not None and (current.until is None or current.until >= today):
        return None
    cached = _hint_cache_read(user.id)
    if cached is False:
        found = _compute_hint(db, user.id, today)
        _hint_cache_write(user.id, found)
    else:
        found = cached if isinstance(cached, dict) else None
    if found is None:
        return None
    return {
        "location": {"key": found["key"], "slug": found["slug"], "name": found["name"], "platform_code": None},
        "runs": int(found["runs"]),
        "of": int(found["of"]),
    }


def snooze_series_hint(db: Session, user: User) -> None:
    user.plans_hint_snoozed_until = _now() + timedelta(days=SERIES_HINT_SNOOZE_DAYS)
    db.flush()
    forget_series_hint(user.id)


# ---------------------------------------------------------------- сон


def _linked_codes(db: Session, user_id: UUID) -> set[str]:
    return {
        row[0]
        for row in db.query(Platform.code)
        .join(PlatformLink, PlatformLink.platform_id == Platform.id)
        .filter(PlatformLink.user_id == user_id, PlatformLink.participant_id.isnot(None))
        .distinct()
    }


def _not_misses(db: Session, user_id: UUID, identity: _Identity, first: date, last: date, today: date) -> set[date]:
    """Дни, которые пропуском не считаются: «Не бегаю» и поездки — разовый план
    в другой локации."""
    here = set(identity.location_ids)
    excluded = _rest_dates(db, user_id, first, last)
    for entry in planned_starts(db, today=today, date_from=first, date_to=last, user_ids=[user_id]):
        if entry.plan is not None and entry.key != identity.key and entry.location_id not in here:
            excluded.add(entry.plan_date)
    return excluded


def missed_home_starts(
    db: Session, series: RunPlanSeries, identity: _Identity | None, *, today: date
) -> list[date] | None:
    """Три последних старта дома без человека — или None (решения нет).

    Окно — строго после дня создания и дня «Продолжить» (создал вечером после
    старта — не пропуск) и по вчера включительно: сегодняшние протоколы ещё
    догружаются. «Старт дома» — суббота, где у строк точки есть нетестовое
    событие системы, к которой у человека есть привязка с участником, а без
    привязок к системам, что проводят здесь старты, — любой из них: в
    протоколе его тогда не видно, и план уснёт через три старта, как у всех
    (будние особые даты и
    отменённые старты пропуском не считаются). Не в счёт — «Не бегаю»,
    поездка, прошлые сны.

    «Был» — как у чтения (sleep_states): любой результат на точке с первого
    старта окна, в том числе в день не в счёт (поездка, «Не бегаю», будняя
    особая дата) и сегодня, — и сна нет. Иначе задача усыпляла бы того, кого
    чтение тут же будит, слала бы «три старта подряд вас не было» человеку,
    который был между ними, и каждый день переписывала бы сон туда и обратно."""
    if identity is None:
        return None
    created_on = series.created_at.astimezone(MOSCOW_TZ).date() if series.created_at else series.starts_on
    after = max(created_on, series.resumed_on) if series.resumed_on is not None else created_on
    first = max(series.starts_on, after + timedelta(days=1))
    last = today - timedelta(days=1)
    if series.until is not None:
        last = min(last, series.until)
    if last < first:
        return None
    codes_by_row = {location.id: code for location, code in identity.rows}
    # Системы, чьи старты считаем: привязанные у человека из тех, что проводят
    # здесь старты, а без таких привязок — все живые системы точки. Привязка к
    # мёртвой строке (parkrun-эпоха, старая S95) — как её отсутствие: иначе
    # стартов в счёт не нашлось бы и план не уснул бы никогда.
    live_codes = _live_codes(identity) or set(codes_by_row.values())
    counted_codes = (_linked_codes(db, series.user_id) & live_codes) or live_codes
    home: dict[date, set[str]] = {}
    for event_date, location_id in (
        db.query(Event.event_date, Event.location_id)
        .filter(
            Event.location_id.in_(identity.location_ids),
            Event.is_test_event.is_(False),
            Event.event_date >= first,
            Event.event_date <= last,
        )
        .distinct()
    ):
        if event_date.weekday() == 5:
            home.setdefault(event_date, set()).add(codes_by_row.get(location_id, ""))
    history = history_intervals(series)
    excluded = _not_misses(db, series.user_id, identity, first, last, today)

    def counted(day: date) -> bool:
        return day not in excluded and not any(start <= day <= end for start, end in history)

    starts = sorted(day for day, codes in home.items() if codes & counted_codes and counted(day))
    if len(starts) < SERIES_SLEEP_AFTER_MISSES:
        return None
    window = starts[-SERIES_SLEEP_AFTER_MISSES:]
    (seen,) = first_presence(db, [(series.user_id, identity.location_ids, window[0])])
    return window if seen is None else None


def _check_one(
    db: Session, series: RunPlanSeries, today: date, now: datetime, summary: dict[str, int]
) -> UUID | None:
    """Сон одной серии под блокировкой человека. Возвращает id доставки
    «Ещё ходите?», если она поставлена в транзакцию."""
    from app.services import notification_service as notifications
    from app.services.plan_notification_service import (
        PLANS_URL_LABEL,
        SERIES_DEDUPE_PREFIX,
        SERIES_KIND,
        compose_series_sleep,
        series_url,
    )

    identities = _Identities(db, today)
    identity = _identity_of(db, identities, series)
    user = db.get(User, series.user_id)
    if user is None:
        return None
    if series.sleeping_since is not None:
        if _apply_wake(series, _state(db, series, identities)):
            series.updated_at = now
            summary["woke"] += 1
            return None
    else:
        # Срок V1 кончился — будущих суббот нет и без сна: спрашивать «Ещё
        # ходите?» не о чем, а «Продолжить» срок не продлит; карточке хватает
        # «Продлить».
        if series.until is not None and series.until < today:
            return None
        missed = missed_home_starts(db, series, identity, today=today)
        if missed is None:
            return None
        series.sleeping_since = missed[-1] + timedelta(days=1)
        series.sleep_missed = missed
        series.sleep_notified_at = None
        series.updated_at = now
        summary["asleep"] += 1
    if series.sleep_notified_at is not None or series.sleeping_since is None:
        return None
    linked = wakes_by_presence(db, series.user_id, identity)
    name = identity.name if identity is not None else "Ваша локация"
    slug = identity.slug if identity is not None else ""
    title, text = compose_series_sleep(name=name, slug=slug, misses=SERIES_SLEEP_AFTER_MISSES, linked=linked)
    delivery = notifications.notify_user(
        db,
        user,
        SERIES_KIND,
        title=title,
        text=text,
        dedupe_key=f"{SERIES_DEDUPE_PREFIX}:{series.id}:{series.sleeping_since.isoformat()}",
        url=series_url(),
        url_label=PLANS_URL_LABEL,
        commit=False,
    )
    # Решение «сообщили» — и тогда, когда уведомления выключены: иначе каждое
    # утро задача пыталась бы снова.
    series.sleep_notified_at = now
    return delivery.id if delivery is not None else None


def check_series_sleep(
    db: Session, now: datetime | None = None, *, user_ids: list[UUID] | None = None
) -> dict[str, int]:
    """Задача сна: раз в сутки. Кончившиеся больше месяца назад сроки V1 —
    остановить; спящих, кто снова был на точке, — разбудить; остальным —
    правило трёх пропусков и одно «Ещё ходите?». Каждая серия — своей
    транзакцией под блокировкой человека; повтор задачи сообщений не
    дублирует (ключ повтора и sleep_notified_at).

    user_ids — только эти люди (стенд: не трогать чужие планы)."""
    from app.services import notification_service as notifications

    now = now or _now()
    today = now.astimezone(MOSCOW_TZ).date()
    summary = {"checked": 0, "asleep": 0, "woke": 0, "notified": 0, "stopped": 0}

    def scoped(query: Any) -> Any:
        return query.filter(RunPlanSeries.user_id.in_(user_ids)) if user_ids is not None else query

    expired = scoped(
        db.query(RunPlanSeries.id, RunPlanSeries.user_id).filter(
            RunPlanSeries.stopped_at.is_(None),
            RunPlanSeries.until < today - timedelta(days=SERIES_EXPIRED_STOP_DAYS),
        )
    ).all()
    for series_id, user_id in expired:
        try:
            _lock_user(db, user_id)
            series = (
                db.query(RunPlanSeries)
                .filter(RunPlanSeries.id == series_id, RunPlanSeries.stopped_at.is_(None))
                .populate_existing()
                .one_or_none()
            )
            if series is not None:
                if series.sleeping_since is not None and series.until is not None:
                    _close_sleep(series, series.until)
                series.stopped_at = now
                series.updated_at = now
                summary["stopped"] += 1
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("plan series: не удалось остановить кончившийся план %s", series_id)

    candidates = scoped(
        db.query(RunPlanSeries.id, RunPlanSeries.user_id).filter(
            RunPlanSeries.stopped_at.is_(None),
            or_(RunPlanSeries.until.is_(None), RunPlanSeries.until >= today - timedelta(days=SERIES_SLEEP_GRACE_DAYS)),
        )
    ).all()
    for series_id, user_id in candidates:
        delivery_id: UUID | None = None
        try:
            _lock_user(db, user_id)
            # Под блокировкой — заново: человек мог нажать «Продолжить» или
            # «Остановить» между выборкой и этой строкой.
            series = (
                db.query(RunPlanSeries)
                .filter(RunPlanSeries.id == series_id, RunPlanSeries.stopped_at.is_(None))
                .populate_existing()
                .one_or_none()
            )
            if series is None:
                db.commit()
                continue
            summary["checked"] += 1
            delivery_id = _check_one(db, series, today, now, summary)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("plan series: сон плана %s не посчитан", series_id)
            continue
        if delivery_id is not None:
            notifications.enqueue_delivery(delivery_id)
            summary["notified"] += 1
    return summary
