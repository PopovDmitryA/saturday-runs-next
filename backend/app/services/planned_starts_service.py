"""Кто планирует пробежать, по датам: разовые планы и постоянные.

Постоянный план («каждую субботу», решение Дмитрия 25.10.2026) — правило и
исключения, как повторяющаяся встреча в календаре: строка run_plan_series, а
строк на каждую неделю нет и ночного дописывания нет. Вхождение серии — дата
календаря её точки (суббота или особая дата систем точки) в [starts_on;
until], и считается оно здесь, при чтении. Поэтому ВСЕ места, где читаются
планы, — блок локации, оргкалендарь, «Кто у нас бежит», «Планы», «Обзор»,
строка профиля, напоминания, «Старт отменён», поправка юбилеев — берут их из
planned_starts, а не из run_plans напрямую (сторож —
test_plan_readers_use_planned_starts): читатель на голом RunPlan молча
потерял бы постоянных.

Исключения — обычные строки run_plans, новых видов нет:
- «Не бегаю» (строка дня с rest) — вхождения в этот день нет: «Пропущу» одним
  нажатием и есть эта отметка;
- разовый план в другой локации вытесняет вхождение — в любой день, обычный
  и особый: «особая суббота — по дню недели, а не по виду даты», и в Новый год
  «Пропущу» домашнего вхождения не упирается в поездку. Убрал поездку —
  суббота вернулась сама, если других разовых планов в этот день нет;
- разовый план на ту же точку поглощает вхождение: человек на дату один раз,
  и для чужих глаз он постоянный (`series`), пока серия бодрствует;
- сон: с `sleeping_since` вхождений нет, закрытые сны — `sleep_history`.

Особые даты входят в серию все, что админ завёл для систем точки (решение
Дмитрия 26.10.2026: «особые даты всегда проставляются вручную в админке, не
надо никакой логики здесь городить»), — без проверки, был ли здесь старт год
назад. Суббота входит всегда: особая дата рядом с ней (перенос на пятницу или
воскресенье) субботу не убирает — «суббота всегда существует». Особая суббота
— как суббота. Отменённый старт вхождение не снимает: человек должен узнать
«Старт отменён».

Сон проверяется лениво: результат человека на точке после первого
«пропущенного» старта будит план для чтения сразу, без записи (её делает
задача notifications.plan_series_sleep), — проснувшийся виден на странице
локации, как только загрузился протокол. Результат на одном из самих
«пропущенных» стартов (протокол догрузили позже) значит, что сна не было.

Две серии одного человека на одну прошлую дату (после объединения профилей)
дают одно вхождение — серии с самым поздним starts_on.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import or_, select, union
from sqlalchemy.orm import Session

from app.models import Event, PlatformLink, RunPlan, RunPlanSeries, RunResult, StartSpecialDate, VolunteerResult
from app.services.start_calendar_service import StartDate, normalize_identity_key

if TYPE_CHECKING:
    from app.services.run_plan_service import _Identities, _Identity

_SATURDAY = 5


def plan_ref(plan_id: UUID) -> str:
    return f"plan:{plan_id}"


def occurrence_ref(series_id: UUID, on_date: date) -> str:
    return f"series:{series_id}:{on_date.isoformat()}"


@dataclass(frozen=True, eq=False)
class PlannedStart:
    """Человек планирует пробежать здесь в эту дату: разовый план (строка
    run_plans) или вхождение постоянного плана (серии)."""

    # «plan:<uuid>» | «series:<uuid>:<YYYY-MM-DD>» — ключ фактов, поправок и
    # списков. Писать в базу — только plan.id: строка ref в UUID-поле — ошибка.
    ref: str
    user_id: UUID
    plan_date: date
    # Записанный ключ (плана или серии).
    location_key: str
    # Ключ идентичности сегодня: записанный дрейфует location:→catalog:.
    key: str
    location_id: UUID | None
    # У плана — записанная система, у вхождения — система даты календаря.
    platform_code: str | None
    # Порядок имён: у вхождения и поглощённого плана — created_at серии.
    created_at: datetime
    # Строка плана; None — вхождение без строки.
    plan: RunPlan | None = None
    # Вхождение или поглотивший его разовый план той же точки.
    series: RunPlanSeries | None = None
    # Разовый план в другой локации в день, когда серия стояла бы здесь, а
    # плана на её точку в этот день нет: суббота вытеснена (только при
    # with_displaces).
    away_from: RunPlanSeries | None = None
    # То же, и этот план — единственный разовый в этот день: убрать его —
    # суббота вернётся (окно «Вернуть Зеленоград?»). При двух поездках или
    # поездке рядом с планом на свою точку — None: обещать возврат нечего.
    displaces: RunPlanSeries | None = None

    @property
    def regular(self) -> bool:
        """Для чужих глаз — «↻ Каждую субботу»."""
        return self.series is not None

    @property
    def occurrence(self) -> bool:
        return self.plan is None

    @classmethod
    def of_plan(
        cls,
        plan: RunPlan,
        *,
        key: str | None = None,
        series: RunPlanSeries | None = None,
        away_from: RunPlanSeries | None = None,
        displaces: RunPlanSeries | None = None,
    ) -> PlannedStart:
        return cls(
            ref=plan_ref(plan.id),
            user_id=plan.user_id,
            plan_date=plan.plan_date,
            location_key=plan.location_key or "",
            key=key or normalize_identity_key(plan.location_key) or (plan.location_key or ""),
            location_id=plan.location_id,
            platform_code=plan.platform_code,
            created_at=series.created_at if series is not None else plan.created_at,
            plan=plan,
            series=series,
            away_from=away_from,
            displaces=displaces,
        )


def split_by_regular(entries: Iterable[PlannedStart]) -> tuple[list[PlannedStart], list[PlannedStart]]:
    """(разовые, постоянные) — как их видят другие."""
    one_off: list[PlannedStart] = []
    regular: list[PlannedStart] = []
    for entry in entries:
        (regular if entry.regular else one_off).append(entry)
    return one_off, regular


# ---------------------------------------------------------------- сон


@dataclass(frozen=True)
class SleepState:
    """Сон серии для чтения: закрытые сны и текущий — с учётом ленивого пробуждения."""

    # (с, по) включительно; по=None — спит до сих пор.
    intervals: tuple[tuple[date, date | None], ...] = ()
    sleeping: bool = False
    # Текущий сон — с этой даты (только у sleeping).
    since: date | None = None
    # Записан сон, а человек уже был на точке в эту дату (≥ sleeping_since):
    # для чтения серия проснулась, запишет задача сна.
    woke_on: date | None = None
    # Человек был на одном из «пропущенных» стартов (протокол пришёл позже) —
    # сна не было вовсе.
    undone: bool = False

    def asleep_on(self, on_date: date) -> bool:
        return any(start <= on_date and (end is None or on_date <= end) for start, end in self.intervals)


def history_intervals(series: RunPlanSeries) -> list[tuple[date, date]]:
    """Закрытые сны серии; битую запись пропускаем."""
    result: list[tuple[date, date]] = []
    for item in series.sleep_history or []:
        try:
            start = date.fromisoformat(item["from"])
            end = date.fromisoformat(item["to"])
        except (KeyError, TypeError, ValueError):
            continue
        if start <= end:
            result.append((start, end))
    return result


def sleep_threshold(series: RunPlanSeries) -> date | None:
    """С какой даты присутствие на точке будит: с первого «пропущенного» старта."""
    if series.sleeping_since is None:
        return None
    return min(series.sleep_missed) if series.sleep_missed else series.sleeping_since


def first_presence(db: Session, wanted: list[tuple[UUID, Collection[UUID], date]]) -> list[date | None]:
    """Для каждого (человек, строки точки, с даты) — первая дата ≥ «с даты», когда
    человек пробежал или поволонтёрил на этих строках. Один запрос на всех."""
    if not wanted:
        return []
    user_ids = {user_id for user_id, _ids, _since in wanted}
    location_ids = {location_id for _user, ids, _since in wanted for location_id in ids}
    if not location_ids:
        return [None] * len(wanted)
    since = min(since_date for _user, _ids, since_date in wanted)
    seen: dict[tuple[UUID, UUID], list[date]] = defaultdict(list)

    def present(model: type[RunResult] | type[VolunteerResult]) -> Any:
        return (
            select(PlatformLink.user_id, Event.location_id, Event.event_date)
            .select_from(model)
            .join(Event, model.event_id == Event.id)
            .join(PlatformLink, PlatformLink.participant_id == model.participant_id)
            .where(
                PlatformLink.user_id.in_(user_ids),
                Event.location_id.in_(location_ids),
                Event.event_date >= since,
                Event.is_test_event.is_(False),
            )
        )

    # Пробежки и волонтёрства — одним запросом (UNION уже без повторов).
    for user_id, location_id, event_date in db.execute(union(present(RunResult), present(VolunteerResult))):
        seen[(user_id, location_id)].append(event_date)
    result: list[date | None] = []
    for user_id, ids, since_date in wanted:
        dates = [value for location_id in ids for value in seen.get((user_id, location_id), []) if value >= since_date]
        result.append(min(dates) if dates else None)
    return result


def sleep_states(
    db: Session, series_list: list[RunPlanSeries], location_ids_of: dict[UUID, Collection[UUID]]
) -> dict[UUID, SleepState]:
    """series.id → сон для чтения. Запросы присутствия — только если кто-то спит."""
    sleeping = [series for series in series_list if series.sleeping_since is not None]
    found = first_presence(
        db,
        [
            (series.user_id, location_ids_of.get(series.id, ()), sleep_threshold(series) or series.sleeping_since)
            for series in sleeping
        ],
    )
    presence = {series.id: seen for series, seen in zip(sleeping, found, strict=True)}
    states: dict[UUID, SleepState] = {}
    for series in series_list:
        intervals: list[tuple[date, date | None]] = list(history_intervals(series))
        since = series.sleeping_since
        if since is None:
            states[series.id] = SleepState(intervals=tuple(intervals))
            continue
        seen = presence.get(series.id)
        if seen is None:
            intervals.append((since, None))
            states[series.id] = SleepState(intervals=tuple(intervals), sleeping=True, since=since)
        elif seen < since:
            states[series.id] = SleepState(intervals=tuple(intervals), undone=True)
        else:
            if seen > since:
                intervals.append((since, seen - timedelta(days=1)))
            states[series.id] = SleepState(intervals=tuple(intervals), woke_on=seen)
    return states


# ---------------------------------------------------------------- даты правила


def series_identity_filter(identity_key: str, location_ids: Collection[UUID]) -> Any:
    conditions = [RunPlanSeries.location_key == identity_key]
    if location_ids:
        conditions.append(RunPlanSeries.location_id.in_(list(location_ids)))
    return or_(*conditions)


def rule_dates(
    db: Session,
    identities: _Identities,
    identity: _Identity | None,
    *,
    starts_on: date,
    until: date | None,
    date_from: date,
    date_to: date,
    today: date,
    calendar: list[StartDate] | None = None,
) -> dict[date, StartDate]:
    """Даты правила постоянного плана в [date_from; date_to]: календарь точки в
    [starts_on; until] — все субботы и все особые даты её систем, будущие —
    только пока точка планируется. Без исключений и сна.

    calendar — готовые старты точки, покрывающие окно (planned_starts
    считает их один раз на точку)."""
    if identity is None:
        return {}
    lo = max(date_from, starts_on)
    hi = min(date_to, until) if until is not None else date_to
    if hi < lo:
        return {}
    starts = calendar if calendar is not None else identities.starts_between(identity, lo, hi)
    result: dict[date, StartDate] = {}
    for start in starts:
        on_date = start.date
        if not lo <= on_date <= hi:
            continue
        if on_date >= today and not identity.plannable:
            continue
        result[on_date] = start
    return result


def _covers(series: RunPlanSeries, on_date: date) -> bool:
    return series.starts_on <= on_date and (series.until is None or on_date <= series.until)


# ---------------------------------------------------------------- главное


def planned_starts(
    db: Session,
    *,
    today: date,
    date_from: date | None = None,
    date_to: date | None = None,
    dates: Collection[date] | None = None,
    user_ids: Collection[UUID] | None = None,
    identity: tuple[str, Collection[UUID]] | None = None,
    identities: _Identities | None = None,
    with_displaces: bool = False,
) -> list[PlannedStart]:
    """Все, кто планирует пробежать, по датам: разовые планы + вхождения серий −
    исключения. Порядок: дата, created_at, ref. Строк дня («Не бегаю») здесь
    нет — это не планы.

    Окно — [date_from; date_to] или явный набор дат `dates` (блок локации
    считает вхождения только на даты вкладок). Отбор — по людям (`user_ids`),
    по точке (`identity` = (ключ, строки идентичности)) или по обоим; общих
    выборок «всех людей» нет.

    with_displaces — у разового плана в другой локации отметить серию, чью
    субботу он вытеснил (away_from): серия бодрствует, дата в её календаре,
    на дату нет «Не бегаю» и плана на точку серии. displaces (окно «Вернуть
    Зеленоград?» при удалении поездки) — только если этот план в тот день
    единственный разовый: при второй поездке убранная суббота не вернётся.

    Запросов — константа на точку, не на число людей: разовые, серии, исключения
    людей с сериями, календарь точки (через кэш идентичностей запроса) и, если
    кто-то спит, запрос присутствия.
    """
    from app.services.run_plan_service import _Identities, _identity_plans_filter, canonical_identity_keys

    if user_ids is None and identity is None:
        raise ValueError("planned_starts: нужен отбор по людям или по локации")
    wanted: set[date] | None
    if dates is not None:
        wanted = set(dates)
        if not wanted:
            return []
        lo, hi = min(wanted), max(wanted)
    else:
        if date_from is None or date_to is None:
            raise ValueError("planned_starts: нужно окно дат или набор дат")
        if date_to < date_from:
            return []
        wanted = None
        lo, hi = date_from, date_to
    users = list(dict.fromkeys(user_ids)) if user_ids is not None else None
    if users is not None and not users:
        return []
    identities = identities or _Identities(db, today)
    here_key: str | None = None
    here_ids: set[UUID] = set()
    if identity is not None:
        here_key = normalize_identity_key(identity[0]) or identity[0]
        here_ids = set(identity[1])

    def in_window(column: Any) -> Any:
        return column.in_(wanted) if wanted is not None else column.between(lo, hi)

    # 1. Разовые планы.
    query = db.query(RunPlan).filter(RunPlan.location_key.isnot(None), in_window(RunPlan.plan_date))
    if users is not None:
        query = query.filter(RunPlan.user_id.in_(users))
    if here_key is not None:
        query = query.filter(_identity_plans_filter(here_key, list(here_ids)))
    plans = query.order_by(RunPlan.plan_date, RunPlan.created_at, RunPlan.id).all()

    # 2. Серии, чьё покрытие пересекает окно; остановленные — тоже (их прошлое).
    covers = (RunPlanSeries.starts_on <= hi, or_(RunPlanSeries.until.is_(None), RunPlanSeries.until >= lo))
    series_query = db.query(RunPlanSeries).filter(*covers)
    if users is not None:
        series_query = series_query.filter(RunPlanSeries.user_id.in_(users))
    if here_key is not None:
        series_query = series_query.filter(series_identity_filter(here_key, here_ids))
    own_series = series_query.order_by(RunPlanSeries.created_at, RunPlanSeries.id).all()
    own_ids = {series.id for series in own_series}
    all_series = list(own_series)
    if here_key is not None:
        # Серии этих людей на других точках: перекрытие прошлой даты после
        # объединения профилей и вытеснение поездкой сюда. В будущем у
        # человека активная серия одна, а остановленные кончаются вчера.
        need = {series.user_id for series in own_series}
        if with_displaces:
            need |= {plan.user_id for plan in plans}
        if need and (lo < today or with_displaces):
            all_series += [
                series
                for series in db.query(RunPlanSeries)
                .filter(*covers, RunPlanSeries.user_id.in_(need))
                .order_by(RunPlanSeries.created_at, RunPlanSeries.id)
                .all()
                if series.id not in own_ids
            ]
    by_user: dict[UUID, list[RunPlanSeries]] = defaultdict(list)
    for series in all_series:
        by_user[series.user_id].append(series)

    # 3. Исключения людей с сериями: «Не бегаю» и все их разовые планы окна
    # (любой локации — для вытеснения и поглощения).
    rest_dates: dict[UUID, set[date]] = defaultdict(set)
    day_plans: dict[tuple[UUID, date], list[RunPlan]] = defaultdict(list)
    if by_user:
        for row in db.query(RunPlan).filter(
            RunPlan.user_id.in_(list(by_user)),
            in_window(RunPlan.plan_date),
            or_(RunPlan.location_key.isnot(None), RunPlan.rest.is_(True)),
        ):
            if row.location_key is None:
                rest_dates[row.user_id].add(row.plan_date)
            else:
                day_plans[(row.user_id, row.plan_date)].append(row)

    # 4. Ключи сегодня — у отбора по точке все свои строки и серии этой точки;
    # при with_displaces — и строки дня людей с сериями (чья суббота вытеснена).
    canonical = canonical_identity_keys(
        db,
        [
            location_id
            for location_id in (
                *(plan.location_id for plan in plans if here_key is None),
                *(series.location_id for series in all_series if series.id not in own_ids or here_key is None),
                *(row.location_id for rows in day_plans.values() for row in rows if with_displaces),
            )
            if location_id is not None
        ],
    )

    def series_key(series: RunPlanSeries) -> str:
        if here_key is not None and series.id in own_ids:
            return here_key
        found = canonical.get(series.location_id) if series.location_id is not None else None
        return found or normalize_identity_key(series.location_key) or series.location_key

    def row_key(plan: RunPlan) -> str:
        found = canonical.get(plan.location_id) if plan.location_id is not None else None
        return found or normalize_identity_key(plan.location_key) or (plan.location_key or "")

    def plan_key(plan: RunPlan) -> str:
        return here_key if here_key is not None else row_key(plan)

    # Календарь и сон нужны сериям, что дают вхождения, и — при with_displaces —
    # сериям, чью субботу может вытеснить поездка.
    calendar_series = [series for series in all_series if series.id in own_ids or with_displaces]
    keys = {series.id: series_key(series) for series in all_series}
    identity_of: dict[UUID, _Identity | None] = {series.id: identities.get(keys[series.id]) for series in calendar_series}
    # Календарь — один раз на точку, на общий отрезок её серий.
    spans: dict[str, tuple[_Identity, date, date]] = {}
    for series in calendar_series:
        found_identity = identity_of[series.id]
        if found_identity is None:
            continue
        start_at = max(lo, series.starts_on)
        end_at = min(hi, series.until) if series.until is not None else hi
        if end_at < start_at:
            continue
        span = spans.get(found_identity.key)
        if span is not None:
            start_at, end_at = min(span[1], start_at), max(span[2], end_at)
        spans[found_identity.key] = (found_identity, start_at, end_at)
    calendars = {
        key: identities.starts_between(found, start_at, end_at) for key, (found, start_at, end_at) in spans.items()
    }
    rule: dict[UUID, dict[date, StartDate]] = {}
    for series in calendar_series:
        found_identity = identity_of[series.id]
        dates_here = rule_dates(
            db,
            identities,
            found_identity,
            starts_on=series.starts_on,
            until=series.until,
            date_from=lo,
            date_to=hi,
            today=today,
            calendar=calendars.get(found_identity.key) if found_identity is not None else None,
        )
        rule[series.id] = {
            on_date: start for on_date, start in dates_here.items() if wanted is None or on_date in wanted
        }
    location_ids_of: dict[UUID, Collection[UUID]] = {}
    for series in calendar_series:
        found_identity = identity_of.get(series.id)
        if found_identity is not None:
            location_ids_of[series.id] = found_identity.location_ids
        elif series.location_id is not None:
            location_ids_of[series.id] = [series.location_id]
    states = sleep_states(db, calendar_series, location_ids_of)

    def chosen(user_id: UUID, on_date: date) -> RunPlanSeries | None:
        best: RunPlanSeries | None = None
        for series in by_user.get(user_id, ()):
            if _covers(series, on_date) and (
                best is None or (series.starts_on, series.created_at) > (best.starts_on, best.created_at)
            ):
                best = series
        return best

    def would_occur(series: RunPlanSeries, on_date: date) -> bool:
        """Вхождение было бы, не будь в этот день разовых планов."""
        if series.id not in rule or on_date not in rule[series.id]:
            return False
        state = states.get(series.id)
        if state is not None and state.asleep_on(on_date):
            return False
        return on_date not in rest_dates.get(series.user_id, ())

    def same_point(plan: RunPlan, series: RunPlanSeries, key: str) -> bool:
        if key == keys[series.id]:
            return True
        if plan.location_id is not None and (
            plan.location_id == series.location_id or plan.location_id in location_ids_of.get(series.id, ())
        ):
            return True
        return normalize_identity_key(plan.location_key) == normalize_identity_key(series.location_key)

    result: list[PlannedStart] = []
    seen: set[tuple[UUID, date, str]] = set()
    for plan in plans:
        key = plan_key(plan)
        # Дрейф ключа мог оставить две строки одной точки на дату — план один.
        if (plan.user_id, plan.plan_date, key) in seen:
            continue
        seen.add((plan.user_id, plan.plan_date, key))
        series = chosen(plan.user_id, plan.plan_date)
        absorbing: RunPlanSeries | None = None
        away_from: RunPlanSeries | None = None
        displaced: RunPlanSeries | None = None
        if series is not None:
            if same_point(plan, series, key):
                if series.id in rule and would_occur(series, plan.plan_date):
                    absorbing = series
            elif with_displaces and would_occur(series, plan.plan_date):
                # Вытеснение и обещание «вернётся» — по всем разовым планам дня:
                # план на свою точку уже держит субботу, а вторая поездка не
                # отдаст её и после «Убрать» этой.
                day = day_plans.get((plan.user_id, plan.plan_date), [plan])
                if not any(same_point(row, series, row_key(row)) for row in day):
                    away_from = series
                    if all(row_key(row) == row_key(plan) for row in day):
                        displaced = series
        result.append(
            PlannedStart.of_plan(plan, key=key, series=absorbing, away_from=away_from, displaces=displaced)
        )

    for series in own_series:
        for on_date, start in rule.get(series.id, {}).items():
            if chosen(series.user_id, on_date) is not series or not would_occur(series, on_date):
                continue
            # Разовый план в этот день: на ту же точку — поглощение (он уже в
            # списке со series), на другую — вытеснение.
            if day_plans.get((series.user_id, on_date)):
                continue
            result.append(
                PlannedStart(
                    ref=occurrence_ref(series.id, on_date),
                    user_id=series.user_id,
                    plan_date=on_date,
                    location_key=series.location_key,
                    key=keys[series.id],
                    location_id=start.location_id,
                    platform_code=start.platform_code,
                    created_at=series.created_at,
                    series=series,
                )
            )
    result.sort(key=lambda entry: (entry.plan_date, entry.created_at, entry.ref))
    return result


# ---------------------------------------------------------------- дешёвые выборки


def identity_one_off_dates(
    db: Session, identity_key: str, location_ids: Collection[UUID], *, date_from: date, date_to: date
) -> list[tuple[UUID, date]]:
    """(человек, дата) разовых планов точки в окне — без календаря и серий:
    по ним блок локации выбирает даты вкладок."""
    from app.services.run_plan_service import _identity_plans_filter

    return [
        (user_id, plan_date)
        for user_id, plan_date in db.query(RunPlan.user_id, RunPlan.plan_date)
        .filter(
            RunPlan.location_key.isnot(None),
            _identity_plans_filter(normalize_identity_key(identity_key) or identity_key, list(location_ids)),
            RunPlan.plan_date >= date_from,
            RunPlan.plan_date <= date_to,
        )
        .distinct()
        .all()
    ]


def user_marked_dates(db: Session, user_id: UUID, *, date_from: date, date_to: date) -> tuple[set[date], set[date]]:
    """(даты разовых планов, даты «Не бегаю») человека в окне — одним запросом,
    без календаря: по ним постоянный план ищет поездки и «Пропущу», не
    считая вхождения на год вперёд."""
    plan_dates: set[date] = set()
    rest_dates: set[date] = set()
    for plan_date, located, rest in db.query(
        RunPlan.plan_date, RunPlan.location_key.isnot(None), RunPlan.rest
    ).filter(
        RunPlan.user_id == user_id,
        RunPlan.plan_date >= date_from,
        RunPlan.plan_date <= date_to,
        or_(RunPlan.location_key.isnot(None), RunPlan.rest.is_(True)),
    ):
        if located:
            plan_dates.add(plan_date)
        elif rest:
            rest_dates.add(plan_date)
    return plan_dates, rest_dates


def reminder_candidates(
    db: Session, *, date_from: date, date_to: date, user_filter: Any
) -> list[tuple[UUID, date, UUID | None]]:
    """(человек, дата, опорная строка) для напоминаний — дёшево, без календаря:
    разовые планы подписчиков и даты окна у их активных серий, если дата —
    суббота или особая дата. Что на самом деле в плане, уточнит planned_starts
    только у тех, кому пора."""
    candidates: list[tuple[UUID, date, UUID | None]] = [
        (user_id, plan_date, location_id)
        for user_id, plan_date, location_id in db.query(RunPlan.user_id, RunPlan.plan_date, RunPlan.location_id)
        .filter(
            RunPlan.location_key.isnot(None),
            RunPlan.plan_date >= date_from,
            RunPlan.plan_date <= date_to,
            RunPlan.user_id.in_(user_filter),
        )
        .all()
    ]
    series_rows = (
        db.query(RunPlanSeries.user_id, RunPlanSeries.location_id, RunPlanSeries.starts_on, RunPlanSeries.until)
        .filter(
            RunPlanSeries.stopped_at.is_(None),
            RunPlanSeries.starts_on <= date_to,
            or_(RunPlanSeries.until.is_(None), RunPlanSeries.until >= date_from),
            RunPlanSeries.user_id.in_(user_filter),
        )
        .all()
    )
    if series_rows:
        specials = {
            row[0]
            for row in db.query(StartSpecialDate.start_date).filter(
                StartSpecialDate.start_date >= date_from, StartSpecialDate.start_date <= date_to
            )
        }
        days = [date_from + timedelta(days=offset) for offset in range((date_to - date_from).days + 1)]
        start_days = [day for day in days if day.weekday() == _SATURDAY or day in specials]
        for user_id, location_id, starts_on, until in series_rows:
            for day in start_days:
                if starts_on <= day and (until is None or day <= until):
                    candidates.append((user_id, day, location_id))
    return candidates
