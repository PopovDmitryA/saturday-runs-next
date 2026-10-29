"""Планировщик забегов: планы участников, отметки «Не бегаю», блок локации.

План — человек × локация × дата старта: «планирую пробежать». Волонтёров
планировщик не ведёт — они записываются в кабинетах систем, и намерения у
плана нет (решение Дмитрия 25.10.2026). Отметка ни к чему не обязывает: ни
неявок, ни счётчиков «не пришёл». Прошедший план владелец видит с фактом из
протоколов: пробежал, был на старте волонтёром или тихая серая отметка «не
получилось».

Публичность — выбор человека на каждом плане (`is_public`, решение Дмитрия
27.10.2026: «эту неделю публично, следующую приватно»), у постоянного — один
на весь план. Отдельного согласия на показ нет: о планах — абзац в общем
согласии. Умолчание переключателя в окне плана — как профиль (default_public):
открыт — «Публично». Итог считается ПРИ ЧТЕНИИ (plan_audience): имя
показываем, только если план публичный, профиль открыт и по протоколам
человек не ребёнок. Ребёнок с публичным планом — числом «1 ребёнок», без
имени. Закрыл профиль — планы сразу приватные, без пересчёта строк, и новый
публичным не сохранить. Приватный план — только в числе «и ещё N —
приватно», имени не видит никто, даже организаторы.

Отметка «Не бегаю» (`rest` на строке дня — строке без локации, решение
Дмитрия 26.10.2026) — как план: публичная видна в календаре профиля («не
бегает»), приватная — только владельцу. Заметок в планировщике нет — ни к
плану, ни на день (решение Дмитрия 27.10.2026: перегружали интерфейс).

Профиль (решение Дмитрия 27.10.2026): в шапке — ближайший публичный план на
любую дату (profile_plan_line), во вкладке «Планы» — календарь публичных
планов и публичных «Не бегаю» по месяцам (profile_plan_calendar). Правила
видимости — те же, что у имени на странице локации; закрытый профиль —
календаря нет вовсе.

Планы и «Не бегаю» — только на день старта (субботу или особую дату): будних
дней в календаре планов нет (решение Дмитрия 27.10.2026).

План ищется по `location_key = ключ OR location_id IN (строки идентичности)`:
ключ дрейфует (`location:<id>` → `catalog:<id>`, когда локацию заводят в
каталог), а опорная строка — нет.

Постоянный план («каждую субботу») — правило, а не строки: кто планирует на
какие даты, все читатели здесь берут из planned_starts_service.planned_starts
(разовые планы + вхождения серий − «Не бегаю», поездки и сон). Запись серии,
сон и подсказка «9 из 10» — plan_series_service.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select, tuple_
from sqlalchemy.orm import Session, aliased

from app.core.age_groups import (
    age_group_is_plausible,
    normalize_age_group,
    normalize_five_verst_age_group,
    normalize_parkrun_age_group,
)
from app.models import (
    Event,
    EventCrosslink,
    Location,
    Participant,
    Platform,
    PlatformLink,
    RunPlan,
    RunResult,
    StartSpecialDate,
    User,
    VolunteerResult,
)
from app.services.location_catalog_service import catalog_ids_for_locations
from app.services.planned_starts_service import (
    PlannedStart,
    identity_one_off_dates,
    plan_ref,
    planned_starts,
    split_by_regular,
    user_marked_dates,
)
from app.services.start_calendar_service import (
    PLANNABLE_PLATFORMS,
    PLANNER_HORIZON_DAYS,
    StartDate,
    StartDay,
    _next_saturday,
    day_label,
    identity_brief,
    identity_rows,
    moscow_today,
    nearest_start_day,
    normalize_identity_key,
    plannable_rows_with_reason,
    special_dates,
    start_dates,
    start_day,
)

# Лимит планов на дату: на обычную субботу человек бежит в одном месте, а в
# Новый год бывает два-три старта за день (решение Дмитрия). Считает его одна
# функция — day_plan_limits: сохранение, окно от локации и окно от даты.
REGULAR_DATE_LIMIT = 1
SPECIAL_DATE_LIMIT = 3
# Блок «Планируют пробежать»: столько ближайших дат с планами.
LOCATION_BLOCK_DATES = 6
# Календарь организатора листается на год назад и на год вперёд.
ORGANIZER_CALENDAR_MONTHS = 12

# Возрастные ступени детей: имя ребёнка в планах не показываем никогда, только
# числом «1 ребёнок» (plan_audience). У RunPark «11–14» normalize_age_group уже
# сводит к «10–14».
MINOR_AGE_GROUPS = frozenset({"<10", "10–14"})
# Сколько последних категорий человека смотреть: последняя бывает мусорной
# («М120» у участника без даты рождения) — тогда берём предыдущую.
_AGE_SAMPLE = 5

# Почему имя не видно при любом выборе (forced). Закрытый профиль — человек
# только в числе приватных, публичный план не сохранить; ребёнок — имя
# скрыто, но «Публично» / «Приватно» он выбирает сам (T02).
FORCED_PROFILE_PRIVATE = "profile_private"
FORCED_MINOR = "minor"

# Как план виден чужим (plan_audience).
SHOWN_NAME = "name"
SHOWN_CHILD = "child"
SHOWN_PRIVATE = "private"


class PlanError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class StartDateError(PlanError):
    """Ошибка админки особых дат."""


class RestConflict(PlanError):
    """«Не бегаю» на день, где у человека планы пробежки: сначала спросить."""

    def __init__(self, message: str, plans: list[RunPlan]) -> None:
        super().__init__(message, 409)
        self.plans = plans


# ---------------------------------------------------------------- приватность


def _clean_age_group(raw: str | None, platform_code: str) -> str | None:
    if platform_code == "parkrun":
        raw = normalize_parkrun_age_group(raw)
    else:
        raw = normalize_five_verst_age_group(raw)
    group = normalize_age_group(raw)
    if group is None or not age_group_is_plausible(group):
        return None
    return group


def minor_user_ids(db: Session, user_ids: list[UUID]) -> set[UUID]:
    """Кто по протоколам ребёнок: последняя возрастная категория «<10» или «10–14».

    Последняя — по дате старта в run_results (5 вёрст, RunPark; у S95
    категорий нет). У parkrun в run_results лежит age grade, поэтому его
    категория — из participants. Категории нет нигде — взрослый.
    """
    ids = list({user_id for user_id in user_ids if user_id is not None})
    if not ids:
        return set()
    link_join = (PlatformLink.platform_id == Participant.platform_id) & (
        PlatformLink.external_user_id == Participant.external_user_id
    )
    ranked = (
        select(
            PlatformLink.user_id.label("user_id"),
            Platform.code.label("platform_code"),
            RunResult.age_category.label("age_category"),
            func.row_number()
            .over(partition_by=PlatformLink.user_id, order_by=Event.event_date.desc())
            .label("rn"),
        )
        .select_from(RunResult)
        .join(Event, RunResult.event_id == Event.id)
        .join(Participant, RunResult.participant_id == Participant.id)
        .join(PlatformLink, link_join)
        .join(Platform, Event.platform_id == Platform.id)
        .where(
            PlatformLink.user_id.in_(ids),
            Platform.code != "parkrun",
            RunResult.age_category.isnot(None),
            RunResult.age_category != "",
        )
        .subquery()
    )
    groups: dict[UUID, str] = {}
    rows = db.execute(
        select(ranked.c.user_id, ranked.c.platform_code, ranked.c.age_category)
        .where(ranked.c.rn <= _AGE_SAMPLE)
        .order_by(ranked.c.user_id, ranked.c.rn)
    ).all()
    for user_id, platform_code, raw in rows:
        if user_id in groups:
            continue
        group = _clean_age_group(raw, platform_code)
        if group is not None:
            groups[user_id] = group
    missing = [user_id for user_id in ids if user_id not in groups]
    if missing:
        parkrun_rows = (
            db.query(PlatformLink.user_id, Participant.age_category)
            .join(Participant, link_join)
            .join(Platform, Participant.platform_id == Platform.id)
            .filter(
                PlatformLink.user_id.in_(missing),
                Platform.code == "parkrun",
                Participant.age_category.isnot(None),
            )
            .all()
        )
        for user_id, raw in parkrun_rows:
            group = _clean_age_group(raw, "parkrun")
            if group is not None and user_id not in groups:
                groups[user_id] = group
    return {user_id for user_id, group in groups.items() if group in MINOR_AGE_GROUPS}


def _forced_private(user: User, minors: set[UUID]) -> str | None:
    if user.profile_private:
        return FORCED_PROFILE_PRIVATE
    if user.id in minors:
        return FORCED_MINOR
    return None


def default_public(user: User) -> bool:
    """Умолчание «Публично / Приватно» в окне плана и у «Не бегаю» — как
    профиль: открыт — публично (решение Дмитрия 27.10.2026)."""
    return not user.profile_private


def clean_public(user: User, is_public: bool | None, fallback: bool) -> bool:
    """Что записать в is_public: выбор из запроса, без него — fallback.
    Закрытый профиль публичного плана не даёт: переключатель в окне выключен,
    а запрос в обход него молча становится приватным."""
    value = fallback if is_public is None else is_public
    return bool(value) and not user.profile_private


def entry_public(entry: PlannedStart) -> bool:
    """Выбор человека для плана. У вхождения постоянного — выбор серии: он один
    на весь план. Разовый план на его же точку (`series` — для чужих глаз он
    постоянный) публичен, только если публичны и серия, и сама строка:
    приватная строка, поставленная, пока серия спала или её ещё не было,
    остаётся приватной и после «Продолжить», пробуждения протоколом и
    постоянного плана из подсказки — открыть её может только явный выбор
    («Публично» в окне постоянного переносит его на такие строки,
    _share_publicity). У остальных — выбор строки."""
    if entry.series is not None:
        return bool(entry.series.is_public) and (entry.plan is None or bool(entry.plan.is_public))
    return bool(entry.plan is not None and entry.plan.is_public)


def plan_audience(db: Session, rows: Sequence[tuple[PlannedStart, User]]) -> dict[str, str]:
    """ref плана → как он показан чужим. Считается на каждом чтении.

    SHOWN_NAME — по имени: план публичный, профиль открыт, взрослый.
    SHOWN_CHILD — публичный план и открытый профиль, но по протоколам ребёнок:
    только числом «1 ребёнок» (решение Дмитрия 25.10.2026, T02).
    SHOWN_PRIVATE — приватный план или закрытый профиль: в общем числе
    приватных.

    Возраст смотрим только у людей с публичным планом и открытым профилем —
    у ребёнка, чьи планы приватны, его не раскрываем даже числом детей
    (замечание Дмитрия 25.10.2026, T01).
    """
    shown = {(entry.ref, user.id) for entry, user in rows if entry_public(entry) and not user.profile_private}
    minors = minor_user_ids(db, list({user_id for _ref, user_id in shown}))
    audience: dict[str, str] = {}
    for entry, user in rows:
        if (entry.ref, user.id) not in shown:
            audience[entry.ref] = SHOWN_PRIVATE
        elif user.id in minors:
            audience[entry.ref] = SHOWN_CHILD
        else:
            audience[entry.ref] = SHOWN_NAME
    return audience


def planner_settings(db: Session, user: User) -> dict[str, Any]:
    """Что окну плана знать о публичности: умолчание переключателя и почему имя
    не видно при любом выборе (forced: закрытый профиль — переключатель
    выключен; ребёнок — доступен, но публичный план виден только числом)."""
    minors = minor_user_ids(db, [user.id]) if not user.profile_private else set()
    return {"default_public": default_public(user), "forced": _forced_private(user, minors)}


# ---------------------------------------------------------------- контекст идентичностей


@dataclass
class _Identity:
    key: str
    rows: list[tuple[Location, str]]
    plannable: list[tuple[Location, str]]
    reason: str | None
    slug: str
    name: str
    # Старты для подписи плана: даты → StartDate.
    starts: dict[date, StartDate] = field(default_factory=dict)
    starts_range: tuple[date, date] | None = None

    @property
    def location_ids(self) -> list[UUID]:
        return [location.id for location, _code in self.rows]

    @property
    def info_rows(self) -> list[tuple[Location, str]]:
        """Строки для подписи даты: планируемые, а если сейчас таких нет
        (локация ушла на паузу) — строки систем планировщика, чтобы у старых
        планов осталось время и название особой даты."""
        if self.plannable:
            return self.plannable
        return [row for row in self.rows if row[1] in PLANNABLE_PLATFORMS]


class _Identities:
    """Кэш идентичностей на один запрос: у человека десятки планов, а
    локаций у них — единицы."""

    def __init__(self, db: Session, today: date) -> None:
        self.db = db
        self.today = today
        self._cache: dict[str, _Identity | None] = {}

    def get(self, key: str | None) -> _Identity | None:
        key = normalize_identity_key(key)
        if key is None:
            return None
        if key not in self._cache:
            rows = identity_rows(self.db, key)
            if not rows:
                self._cache[key] = None
            else:
                plannable, reason = plannable_rows_with_reason(self.db, rows, today=self.today)
                slug, name = identity_brief(self.db, key, rows)
                self._cache[key] = _Identity(
                    key=key, rows=rows, plannable=plannable, reason=reason, slug=slug, name=name
                )
        return self._cache[key]

    def start_for(self, identity: _Identity, on_date: date, *, span: tuple[date, date] | None = None) -> StartDate | None:
        date_from, date_to = span or (on_date, on_date)
        date_from, date_to = min(date_from, on_date), max(date_to, on_date)
        covered = identity.starts_range
        if covered is None or not (covered[0] <= date_from and date_to <= covered[1]):
            if covered is not None:
                date_from, date_to = min(date_from, covered[0]), max(date_to, covered[1])
            identity.starts = {
                item.date: item
                for item in start_dates(
                    self.db,
                    identity.info_rows,
                    date_from=date_from,
                    date_to=date_to,
                    today=self.today,
                    history_rows=identity.rows,
                )
            }
            identity.starts_range = (date_from, date_to)
        return identity.starts.get(on_date)

    def starts_between(self, identity: _Identity, date_from: date, date_to: date) -> list[StartDate]:
        """Старты календаря точки в [date_from; date_to] по возрастанию — из того
        же кэша, что подпись плана (start_for)."""
        if date_to < date_from or not identity.info_rows:
            return []
        self.start_for(identity, date_from, span=(date_from, date_to))
        return [identity.starts[on_date] for on_date in sorted(identity.starts) if date_from <= on_date <= date_to]


def canonical_identity_keys(db: Session, location_ids: list[UUID]) -> dict[UUID, str]:
    """location_id → ключ идентичности сегодня — дёшево, без полного индекса каталога."""
    ids = list({location_id for location_id in location_ids if location_id is not None})
    if not ids:
        return {}
    rows = (
        db.query(Location.id, Platform.code, Location.external_key)
        .join(Platform, Location.platform_id == Platform.id)
        .filter(Location.id.in_(ids))
        .all()
    )
    catalog_ids = catalog_ids_for_locations(db, {location_id: (code, slug) for location_id, code, slug in rows})
    return {
        location_id: f"catalog:{catalog_ids[location_id]}" if location_id in catalog_ids else f"location:{location_id}"
        for location_id, _code, _slug in rows
    }


def _identity_plans_filter(identity_key: str, location_ids: list[UUID]) -> Any:
    conditions = [RunPlan.location_key == identity_key]
    if location_ids:
        conditions.append(RunPlan.location_id.in_(location_ids))
    return or_(*conditions)


# ---------------------------------------------------------------- план → факт


def _as_entry(plan: RunPlan | PlannedStart) -> PlannedStart:
    return plan if isinstance(plan, PlannedStart) else PlannedStart.of_plan(plan)


def plan_facts(
    db: Session,
    user_id: UUID,
    plans: Sequence[RunPlan | PlannedStart],
    *,
    identities: _Identities | None = None,
    today: date | None = None,
) -> dict[str, dict[str, Any]]:
    """ref плана → что было на старте по протоколам, только для прошедших планов.

    ran / volunteered — у человека есть строка протокола на событиях
    идентичности в эту дату: пробежка важнее, а волонтёрство вместо
    пробежки — тоже «был на старте», не «не получилось»; missed — событие
    было, а человека в протоколе нет; unknown — события нет (протокол не
    загружен или старта не было): такой план серым не красим.

    Ran / volunteered ищем по ВСЕМ событиям идентичности, без дедупа
    кросслинков: человек мог быть привязан только к зеркалу (RunPark дублирует
    протоколы S95 у части точек), и его событие — вторичное. А номер и missed —
    по приоритету событий дня (_event_priority): иначе протокол зеркала,
    пришедший раньше протокола системы плана, красил план серым «не
    получилось», а номер брался у той системы, что попалась первой (ревью
    25.10.2026, CAL-2). Missed — только если событие есть у строки плана или
    у строк её системы: «протокол вашей системы ещё не пришёл» — это unknown.

    Вхождение постоянного плана, где человека здесь нет, а пробежка в этот
    день где-то ещё есть, — elsewhere («бегали в Кусково», с локацией): у
    серии серых отметок нет, а поездка без замены плана — не «не получилось».
    """
    today = today or moscow_today()
    identities = identities or _Identities(db, today)
    past = [
        entry
        for entry in (_as_entry(plan) for plan in plans if not (isinstance(plan, RunPlan) and plan.location_key is None))
        if entry.plan_date < today
    ]
    if not past:
        return {}
    # Ключ мог устареть (`location:` до появления узла каталога) — состав
    # идентичности берём и по сегодняшнему ключу опорной строки, иначе старт
    # на второй системе точки остался бы без факта.
    canonical = canonical_identity_keys(db, [plan.location_id for plan in past if plan.location_id is not None])
    ids_by_plan: dict[str, set[UUID]] = {}
    pairs: set[tuple[UUID, date]] = set()
    for plan in past:
        location_ids: set[UUID] = set()
        for key in {plan.location_key, plan.key, canonical.get(plan.location_id) if plan.location_id else None}:
            identity = identities.get(key)
            if identity is not None:
                location_ids.update(identity.location_ids)
        if plan.location_id is not None:
            location_ids.add(plan.location_id)
        ids_by_plan[plan.ref] = location_ids
        pairs.update((location_id, plan.plan_date) for location_id in location_ids)
    events: dict[tuple[UUID, date], list[_DayEvent]] = defaultdict(list)
    if pairs:
        for event_id, location_id, event_date, number, code in (
            db.query(Event.id, Event.location_id, Event.event_date, Event.event_number, Platform.code)
            .join(Platform, Event.platform_id == Platform.id)
            .filter(tuple_(Event.location_id, Event.event_date).in_(list(pairs)), Event.is_test_event.is_(False))
            .all()
        ):
            events[(location_id, event_date)].append(_DayEvent(event_id, location_id, code, number))
    event_ids = [item.event_id for items in events.values() for item in items]
    # Вторичная сторона кросслинка: тот же старт в протоколе другой системы.
    crosslinks: set[tuple[UUID, UUID]] = set()
    if event_ids:
        crosslinks = {
            (primary_id, secondary_id)
            for primary_id, secondary_id in db.query(EventCrosslink.primary_event_id, EventCrosslink.secondary_event_id)
            .filter(EventCrosslink.secondary_event_id.in_(event_ids))
            .all()
        }
    runs: dict[UUID, int | None] = {}
    volunteered: set[UUID] = set()
    if event_ids:
        link_join = (PlatformLink.platform_id == Participant.platform_id) & (
            PlatformLink.external_user_id == Participant.external_user_id
        )
        for event_id, finish in (
            db.query(RunResult.event_id, RunResult.finish_time_sec)
            .join(Participant, RunResult.participant_id == Participant.id)
            .join(PlatformLink, link_join)
            .filter(PlatformLink.user_id == user_id, RunResult.event_id.in_(event_ids))
            .all()
        ):
            current = runs.get(event_id)
            runs[event_id] = finish if current is None else (min(current, finish) if finish is not None else current)
        volunteered = {
            event_id
            for (event_id,) in db.query(VolunteerResult.event_id)
            .join(Participant, VolunteerResult.participant_id == Participant.id)
            .join(PlatformLink, link_join)
            .filter(PlatformLink.user_id == user_id, VolunteerResult.event_id.in_(event_ids))
            .all()
        }

    facts: dict[str, dict[str, Any]] = {}
    for plan in past:
        day_events = [item for location_id in ids_by_plan[plan.ref] for item in events.get((location_id, plan.plan_date), [])]
        day_ids = {item.event_id for item in day_events}
        secondary = {secondary_id for primary_id, secondary_id in crosslinks if primary_id in day_ids}
        # Внутри одного приоритета — стабильный порядок, а не порядок множества.
        day_events.sort(key=lambda item: (_event_priority(item, plan, secondary), item.platform_code, str(item.event_id)))
        ran_event = next((item for item in day_events if item.event_id in runs), None)
        vol_event = next((item for item in day_events if item.event_id in volunteered), None)
        own_events = [item for item in day_events if _event_priority(item, plan, secondary) <= 1]
        if ran_event is not None:
            fact = {"status": "ran", "finish_time_sec": runs[ran_event.event_id], "event_number": ran_event.number}
        elif vol_event is not None:
            fact = {"status": "volunteered", "finish_time_sec": None, "event_number": vol_event.number}
        elif own_events:
            fact = {"status": "missed", "finish_time_sec": None, "event_number": own_events[0].number}
        else:
            fact = {"status": "unknown", "finish_time_sec": None, "event_number": None}
        facts[plan.ref] = fact
    facts.update(_elsewhere_facts(db, user_id, [plan for plan in past if plan.occurrence and facts[plan.ref]["status"] not in ("ran", "volunteered")], ids_by_plan, identities))
    return facts


def _elsewhere_facts(
    db: Session,
    user_id: UUID,
    entries: list[PlannedStart],
    ids_by_plan: dict[str, set[UUID]],
    identities: _Identities,
) -> dict[str, dict[str, Any]]:
    """Вхождение без человека здесь, а пробежка в этот день где-то ещё есть:
    «бегали в Кусково» — время и номер самой быстрой пробежки дня и где."""
    if not entries:
        return {}
    link_join = (PlatformLink.platform_id == Participant.platform_id) & (
        PlatformLink.external_user_id == Participant.external_user_id
    )
    runs: dict[date, list[tuple[UUID, int | None, int | None]]] = defaultdict(list)
    for event_date, location_id, finish, number in (
        db.query(Event.event_date, Event.location_id, RunResult.finish_time_sec, Event.event_number)
        .select_from(RunResult)
        .join(Event, RunResult.event_id == Event.id)
        .join(Participant, RunResult.participant_id == Participant.id)
        .join(PlatformLink, link_join)
        .filter(
            PlatformLink.user_id == user_id,
            Event.event_date.in_({entry.plan_date for entry in entries}),
            Event.is_test_event.is_(False),
        )
        .order_by(Event.event_date, RunResult.finish_time_sec.asc().nulls_last(), Event.event_number)
        .all()
    ):
        runs[event_date].append((location_id, finish, number))
    elsewhere = [
        (entry, next((run for run in runs.get(entry.plan_date, []) if run[0] not in ids_by_plan[entry.ref]), None))
        for entry in entries
    ]
    keys = canonical_identity_keys(db, [run[0] for _entry, run in elsewhere if run is not None])
    facts: dict[str, dict[str, Any]] = {}
    for entry, run in elsewhere:
        if run is None:
            continue
        location_id, finish, number = run
        identity = identities.get(keys.get(location_id))
        facts[entry.ref] = {
            "status": "elsewhere",
            "finish_time_sec": finish,
            "event_number": number,
            "location": {"name": identity.name, "slug": identity.slug} if identity is not None else None,
        }
    return facts


def _rest_facts(db: Session, user_id: UUID, rows: list[RunPlan], *, today: date) -> dict[str, dict[str, Any]]:
    """Прошедший день с «Не бегаю»: пробежал всё-таки — «ran» (время и номер
    самой быстрой пробежки дня), иначе «rested» — тихо «не бегали», без оценок.

    Где бежал — неважно: отметка о дне, а не о локации. Волонтёрство
    пробежкой не считается — «не бегали» и есть правда.
    """
    past = [row for row in rows if row.location_key is None and row.rest and row.plan_date < today]
    if not past:
        return {}
    link_join = (PlatformLink.platform_id == Participant.platform_id) & (
        PlatformLink.external_user_id == Participant.external_user_id
    )
    best: dict[date, tuple[int | None, int | None]] = {}
    for event_date, finish, number in (
        db.query(Event.event_date, RunResult.finish_time_sec, Event.event_number)
        .select_from(RunResult)
        .join(Event, RunResult.event_id == Event.id)
        .join(Participant, RunResult.participant_id == Participant.id)
        .join(PlatformLink, link_join)
        .filter(
            PlatformLink.user_id == user_id,
            Event.event_date.in_({row.plan_date for row in past}),
            Event.is_test_event.is_(False),
        )
        .order_by(Event.event_date, RunResult.finish_time_sec.asc().nulls_last(), Event.event_number)
        .all()
    ):
        best.setdefault(event_date, (finish, number))
    facts: dict[str, dict[str, Any]] = {}
    for row in past:
        if row.plan_date in best:
            finish, number = best[row.plan_date]
            facts[plan_ref(row.id)] = {"status": "ran", "finish_time_sec": finish, "event_number": number}
        else:
            facts[plan_ref(row.id)] = {"status": "rested", "finish_time_sec": None, "event_number": None}
    return facts


@dataclass(frozen=True)
class _DayEvent:
    event_id: UUID
    location_id: UUID
    platform_code: str
    number: int | None


def _event_priority(item: _DayEvent, plan: RunPlan | PlannedStart, secondary: set[UUID]) -> int:
    """Чьё событие дня главное для плана: 0 — опорной строки плана, 1 — строк
    системы плана, 2 — первичные стороны кросслинков и одиночные события
    других систем, 3 — зеркала (вторичная сторона кросслинка)."""
    if plan.location_id is not None and item.location_id == plan.location_id:
        return 0
    if plan.platform_code and item.platform_code == plan.platform_code:
        return 1
    if item.event_id not in secondary:
        return 2
    return 3


# ---------------------------------------------------------------- элементы ответа


def _start_payload(start: StartDate | None) -> dict[str, Any] | None:
    if start is None:
        return None
    return {
        "kind": start.kind,
        "title": start.title,
        "start_time": start.start_time,
        "number_forecast": start.number_forecast,
        "cancelled": start.cancelled,
        "cancel_reason": start.cancel_reason,
    }


def series_location(identities: _Identities, key: str | None, *, fallback_key: str = "") -> dict[str, Any]:
    """PlanLocation точки по ключу; ключ ни во что не разрешается — «Локация»."""
    identity = identities.get(key) if key else None
    if identity is None:
        return {"key": key or fallback_key, "slug": "", "name": "Локация", "platform_code": None}
    return {"key": identity.key, "slug": identity.slug, "name": identity.name, "platform_code": None}


def _series_key(db: Session, series: Any, canonical: dict[UUID, str] | None = None) -> str:
    """Ключ точки серии сегодня — по опорной строке, без неё — записанный."""
    if series.location_id is not None:
        found = (canonical if canonical is not None else canonical_identity_keys(db, [series.location_id])).get(
            series.location_id
        )
        if found:
            return found
    return normalize_identity_key(series.location_key) or series.location_key


def plan_items(
    db: Session,
    user: User,
    plans: Sequence[RunPlan | PlannedStart],
    *,
    today: date | None = None,
    identities: _Identities | None = None,
) -> list[dict[str, Any]]:
    """PlanItem владельца — с подписью старта и фактом прошедших.

    На входе — строки дня (RunPlan без локации) и планы: PlannedStart из
    planned_starts или голые строки планов (тогда без отметок серии). У
    вхождения постоянного плана id нет (строки нет), `series` — его серия;
    у разового плана в другой локации, вытеснившего субботу серии, —
    `displaces`. Ключ ответа — ref.
    """
    today = today or moscow_today()
    identities = identities or _Identities(db, today)
    entries: list[RunPlan | PlannedStart] = [
        plan if isinstance(plan, RunPlan) and plan.location_key is None else _as_entry(plan) for plan in plans
    ]
    planned = [entry for entry in entries if isinstance(entry, PlannedStart)]
    canonical = canonical_identity_keys(
        db,
        [
            location_id
            for location_id in (
                *(entry.location_id for entry in planned),
                *(entry.displaces.location_id for entry in planned if entry.displaces is not None),
            )
            if location_id is not None
        ],
    )
    dates_by_key: dict[str, list[date]] = defaultdict(list)
    keys: dict[str, str] = {}
    for entry in planned:
        key = canonical.get(entry.location_id) if entry.location_id is not None else None
        if key is None or identities.get(key) is None:
            key = entry.key if identities.get(entry.key) is not None else entry.location_key
        keys[entry.ref] = key
        dates_by_key[key].append(entry.plan_date)
    facts = plan_facts(db, user.id, planned, identities=identities, today=today)
    facts.update(_rest_facts(db, user.id, [entry for entry in entries if isinstance(entry, RunPlan)], today=today))

    items: list[dict[str, Any]] = []
    for entry in entries:
        if isinstance(entry, RunPlan):
            ref = plan_ref(entry.id)
            items.append(
                {
                    "id": entry.id,
                    "ref": ref,
                    "plan_date": entry.plan_date,
                    # «day» — строка дня без локации: отметка «Не бегаю».
                    "kind": "day",
                    "rest": bool(entry.rest),
                    # Видна ли отметка «Не бегаю» в календаре профиля.
                    "is_public": bool(entry.is_public),
                    "location": None,
                    "start": None,
                    "past": entry.plan_date < today,
                    "fact": facts.get(ref),
                    "series": None,
                    "displaces": None,
                }
            )
            continue
        key = keys[entry.ref]
        identity = identities.get(key)
        start = None
        if identity is not None:
            dates = dates_by_key[key]
            start = identities.start_for(identity, entry.plan_date, span=(min(dates), max(dates)))
            location = {
                "key": key,
                "slug": identity.slug,
                "name": identity.name,
                "platform_code": entry.platform_code,
            }
        else:
            location = {"key": key, "slug": "", "name": "Локация", "platform_code": entry.platform_code}
        displaces = None
        if entry.displaces is not None:
            displaces = {
                "id": entry.displaces.id,
                "location": series_location(identities, _series_key(db, entry.displaces, canonical)),
            }
        items.append(
            {
                "id": entry.plan.id if entry.plan is not None else None,
                "ref": entry.ref,
                "plan_date": entry.plan_date,
                "kind": "plan",
                "rest": False,
                "is_public": entry_public(entry),
                "location": location,
                "start": _start_payload(start),
                "past": entry.plan_date < today,
                "fact": facts.get(entry.ref),
                "series": {"id": entry.series.id} if entry.series is not None else None,
                "displaces": displaces,
            }
        )
    return items


def plan_entry(db: Session, user: User, plan: RunPlan, *, today: date | None = None) -> PlannedStart:
    """План как его видит planned_starts — с серией, которую он поглотил или
    вытеснил. Строки на эту дату в выдаче нет (дубль дрейфа ключа) — как есть."""
    today = today or moscow_today()
    ref = plan_ref(plan.id)
    for entry in planned_starts(db, today=today, dates=[plan.plan_date], user_ids=[user.id], with_displaces=True):
        if entry.ref == ref:
            return entry
    return PlannedStart.of_plan(plan)


def plan_item(db: Session, user: User, plan: RunPlan) -> dict[str, Any]:
    if plan.location_key is None:
        return plan_items(db, user, [plan])[0]
    return plan_items(db, user, [plan_entry(db, user, plan)])[0]


def still_on(identities: _Identities, item: dict[str, Any]) -> bool:
    """План в силе: локация находится, сейчас проводит старты, и дата плана —
    всё ещё дата её календаря.

    Строку удалили (ключ ни во что не разрешается), локация ушла на паузу
    после того, как человек отметил дату, или админ убрал, перенёс или
    переназначил на другие системы особую дату — такой план не напоминается
    («Завтра старт… Не опаздывайте!» было бы неправдой, ревью 25.10.2026,
    NT-3/INTEG-1) и не показывается в профиле.
    """
    location = item.get("location")
    if not location or not location.get("slug"):
        return False
    if item.get("start") is None:
        return False
    identity = identities.get(location.get("key"))
    return identity is not None and bool(identity.plannable)


# ---------------------------------------------------------------- запись


def _lock_user(db: Session, user_id: UUID) -> None:
    """Два «Планирую» в одну секунду не должны обойти лимит дня.

    Её же берёт каждая запись строки дня и удаление плана: «Не бегаю»
    поверх планов дня их удаляет, «Планирую» снимает отметку, и без
    блокировки параллельные запросы расходились с ответом сервера (ревью
    26.10.2026, PLAN-REV-2).
    """
    db.execute(select(User.id).where(User.id == user_id).with_for_update())


def _canonical_key(db: Session, identity_key: str, location_id: UUID) -> str:
    """Ключ пришёл `location:<id>`, а у строки уже есть узел каталога —
    сохраняем `catalog:<id>`: так план найдут по ключу и с других систем точки.

    `identity_key` уже в единственной записи (normalize_identity_key).
    """
    if identity_key.startswith("catalog:"):
        return identity_key
    return canonical_identity_keys(db, [location_id]).get(location_id, identity_key)


def day_plan_limits(db: Session, dates: Collection[date]) -> dict[date, int]:
    """Сколько разовых планов можно на каждую дату — одно правило для
    сохранения, окна от локации (дата «день заполнен») и окна от даты
    (список локаций, «+ ещё локация» в «Моих планах»).

    Лимит — по самой дате: особая она в таблице или нет. Не по виду старта
    локации: особая дата не для всех систем у одной локации — особая, у
    другой — обычная суббота, и итог зависел бы от порядка нажатий (ревью
    25.10.2026, BE-4). Вхождение постоянного плана в лимит не идёт — разовый
    план в другой локации его вытесняет; закреплённое разовым (keep_series в
    upsert_plan) считается как разовый.
    """
    wanted = sorted(set(dates))
    if not wanted:
        return {}
    special = {row[0] for row in db.query(StartSpecialDate.start_date).filter(StartSpecialDate.start_date.in_(wanted))}
    return {on_date: SPECIAL_DATE_LIMIT if on_date in special else REGULAR_DATE_LIMIT for on_date in wanted}


def day_plan_limit(db: Session, plan_date: date) -> int:
    return day_plan_limits(db, [plan_date])[plan_date]


def _series_occurrence_on(
    db: Session, user: User, plan_date: date, keys: set[str], location_ids: set[UUID], *, today: date
) -> PlannedStart | None:
    """Вхождение постоянного плана человека на дату в ДРУГОЙ точке — то, что
    вытеснил бы новый разовый план сюда. Нет такого — None."""
    for entry in planned_starts(db, today=today, dates=[plan_date], user_ids=[user.id]):
        if entry.plan is None and entry.series is not None:
            if entry.key in keys or entry.location_id in location_ids:
                return None
            return entry
    return None


def _series_here(db: Session, user: User, plan_date: date, keys: set[str], location_ids: set[UUID]) -> Any:
    """Постоянный план, если он покрывает дату на этой же точке: такой
    разовый план для чужих глаз — его вхождение, и публичность у них одна.
    Иначе None."""
    from app.services.plan_series_service import _same_point, active_series

    series = active_series(db, user.id)
    if series is None or plan_date < series.starts_on or (series.until is not None and plan_date > series.until):
        return None
    return series if _same_point(db, series, keys, location_ids) else None


# Разовый план на точке своего постоянного в его дату: публичность у них одна.
SERIES_OWNS_PUBLICITY = (
    "Это суббота вашего постоянного плана — публичность у него одна на все субботы, поменяйте её в постоянном плане"
)


def upsert_plan(
    db: Session,
    user: User,
    *,
    identity_key: str,
    plan_date: date,
    is_public: bool | None = None,
    keep_series: bool = False,
    replace_key: str | None = None,
) -> RunPlan:
    """Создать или поправить план «пробежать» на локацию и дату.

    is_public — выбор «Публично / Приватно»; None — у нового плана умолчание
    (как профиль, а на точке своего постоянного — как он), у старого — без
    изменений. На точке своего бодрствующего постоянного в его дату правка
    публичности стоящего плана — 409, а новый план не публичнее постоянного
    (возврат домой после «Пропущу» или поездки).

    Вхождение постоянного плана в лимит дня не идёт: разовый план в другой
    локации его вытесняет (поездка, в любой день), а на ту же точку —
    поглощает. Что стало с серией, ответ сохранения говорит отдельно
    (plan_save_context).

    keep_series — на особую дату («+ ещё локация» 1 января) постоянный план
    не вытеснять, а закрепить разовым планом на его точке в той же
    транзакции: в особый день бывает несколько стартов, и «ещё одна»
    локация не должна молча снимать дом (решение 27.10.2026; это то же, что
    правило К2а предлагает делать руками: «кто хочет 1 января и домой, и в
    гости, ставит домашний старт разовым»). Закреплённый дом считается в
    лимит дня. На обычную дату (лимит 1) флаг ничего не делает.

    replace_key — день заполнен, а человек выбрал другую локацию: план
    этого дня на точке replace_key убирается в той же транзакции, и ставится
    новый (решение Дмитрия 27.10.2026: окно не закрывает дату, а
    предупреждает «План в Кусково на сб 14 ноября будет отменён»). Убирается
    только если без этого не уложиться в лимит; такого плана уже нет (убрали
    в другой вкладке) — обычный 409 о заполненном дне.
    """
    today = moscow_today()
    normalized = normalize_identity_key(identity_key)
    if normalized is None:
        raise PlanError("Эту локацию пока нельзя запланировать")
    identity_key = normalized
    rows = identity_rows(db, identity_key)
    plannable, _reason = plannable_rows_with_reason(db, rows, today=today)
    if not plannable:
        raise PlanError("Эту локацию пока нельзя запланировать")
    if plan_date < today:
        raise PlanError("Этот старт уже прошёл")
    if plan_date > today + timedelta(days=PLANNER_HORIZON_DAYS):
        raise PlanError("В этот день старта нет")
    starts = start_dates(db, plannable, date_from=plan_date, date_to=plan_date, today=today, history_rows=rows)
    if not starts:
        raise PlanError("В этот день старта нет")
    start = starts[0]

    _lock_user(db, user.id)
    location_ids = [location.id for location, _code in rows]
    key = _canonical_key(db, identity_key, start.location_id)
    same_day = (
        db.query(RunPlan)
        .filter(RunPlan.user_id == user.id, RunPlan.plan_date == plan_date, RunPlan.location_key.isnot(None))
        .order_by(RunPlan.created_at, RunPlan.id)
        .all()
    )
    location_set = set(location_ids)
    keys = {identity_key, key}
    existing = [plan for plan in same_day if plan.location_key in keys or plan.location_id in location_set]
    others = [plan for plan in same_day if plan not in existing]
    limit = day_plan_limit(db, plan_date)
    # Дом, который «+ ещё локация» закрепит разовым, — тоже план дня.
    pin = (
        _series_occurrence_on(db, user, plan_date, keys, location_set, today=today)
        if keep_series and not existing and limit > REGULAR_DATE_LIMIT
        else None
    )
    taken = len(others) + (1 if pin is not None else 0)
    # Планов дня больше лимита (админ убрал особую дату, объединение
    # профилей): замена одного места не освободит — окно обещало отменить
    # один план, а лишние молча не снимаем (ревью 28.10.2026, SD-1).
    if replace_key and not existing and taken > limit:
        names = _plan_names(db, today, [other.location_key for other in others])
        raise PlanError(
            f"На {day_label(plan_date)} у вас планов больше, чем можно на этот день: {', '.join(names)}. "
            "Уберите лишние в «Моих планах»",
            409,
        )
    # Замена: день заполнен — убираем выбранный план этого дня. Сначала —
    # чтобы лимит ниже считал уже без него.
    if replace_key and not existing and taken >= limit:
        replaced = _plans_at_point(db, others, replace_key)
        for plan in replaced:
            db.delete(plan)
        if replaced:
            db.flush()
            others = [plan for plan in others if plan not in replaced]
    # Правка своего плана число планов дня не меняет — лимит только для
    # нового. Иначе после удалённой особой даты (три плана на обычной теперь
    # субботе) или объединения профилей ни один план нельзя было бы поправить
    # (BE-3).
    if not existing and len(others) + (1 if pin is not None else 0) >= limit:
        names = _plan_names(
            db, today, [*(other.location_key for other in others), *([pin.key] if pin is not None else [])]
        )
        raise PlanError(f"На {day_label(plan_date)} у вас уже запланирован старт: {', '.join(names)}", 409)
    if pin is not None:
        # Тем же путём, что «Планирую» на точке постоянного: строка поглощает
        # его субботу, публичность — как у него.
        upsert_plan(db, user, identity_key=pin.key, plan_date=plan_date)

    series_here = _series_here(db, user, plan_date, keys, location_set)
    # Бодрствующий постоянный план поглотит строку: чужие видят её его
    # вхождением. Спящий субботу не держит — там выбор у самой строки.
    absorbing = False
    if series_here is not None and (not existing or is_public is not None):
        from app.services.plan_series_service import series_sleeping_now

        absorbing = not series_sleeping_now(db, series_here)
    # Правка поглощённого плана: публичность у него одна с постоянным (ревью
    # 27.10.2026, PRIV-2). Окно переключатель здесь не даёт; другой выбор в
    # обход него — внятный отказ, а не тихая запись.
    if (
        absorbing
        and existing
        and is_public is not None
        and not user.profile_private
        and bool(is_public) != bool(series_here.is_public)
    ):
        raise PlanError(SERIES_OWNS_PUBLICITY, 409)

    plan = existing[0] if existing else None
    # Дрейф ключа мог оставить две строки одной точки — оставляем одну.
    for duplicate in existing[1:]:
        db.delete(duplicate)
    if existing[1:]:
        db.flush()
    if plan is None:
        fallback = default_public(user) if series_here is None else bool(series_here.is_public)
        public = clean_public(user, is_public, fallback)
        if absorbing:
            # Новая строка на точке бодрствующего постоянного — возврат домой
            # после «Пропущу» или поездки. Окно на такой дате шлёт умолчание
            # профиля, а не выбор постоянного: «Публично» у приватного плана
            # не откроет дату (запишем как у него), «Приватно» у публичного —
            # спрячет её (entry_public). 409 здесь ронял бы возврат домой
            # (ревью 28.10.2026, CORE-2).
            public = public and bool(series_here.is_public)
        plan = RunPlan(user_id=user.id, plan_date=plan_date, is_public=public)
        db.add(plan)
    elif is_public is not None:
        plan.is_public = clean_public(user, is_public, plan.is_public)
    plan.location_key = key
    plan.location_id = start.location_id
    plan.platform_code = start.platform_code
    plan.updated_at = datetime.now(UTC)
    # Передумал отдыхать: план пробежки снимает «Не бегаю» этого дня.
    day = _day_row(db, user, plan_date)
    if day is not None and day.rest:
        _drop_rest(db, day)
    db.flush()
    return plan


def _plan_names(db: Session, today: date, keys: list[str | None]) -> list[str]:
    """Названия точек планов для текста отказа."""
    identities = _Identities(db, today)
    names = []
    for key in keys:
        identity = identities.get(key)
        names.append(identity.name if identity is not None else "другая локация")
    return names


def _plans_at_point(db: Session, plans: list[RunPlan], identity_key: str) -> list[RunPlan]:
    """Планы из списка на точке identity_key — по ключу (дрейф location:→
    catalog:) или по строке идентичности, как их находит лимит дня."""
    normalized = normalize_identity_key(identity_key)
    if normalized is None:
        return []
    location_ids = {location.id for location, _code in identity_rows(db, normalized)}
    keys = {normalized}
    if location_ids:
        keys |= set(canonical_identity_keys(db, list(location_ids)).values())
    return [plan for plan in plans if plan.location_key in keys or plan.location_id in location_ids]


def delete_plan(db: Session, user: User, plan_id: UUID, *, rest: bool = False) -> None:
    """Удалить план. rest=True — и в той же транзакции отметить «Не бегаю» на
    его дату: «Убрать поездку» с выбором «вообще не бегаю в этот день», а не
    «вернуть постоянную локацию» (решение Дмитрия по Q07, 25.10.2026)."""
    _lock_user(db, user.id)
    plan = db.query(RunPlan).filter(RunPlan.id == plan_id, RunPlan.user_id == user.id).one_or_none()
    if plan is None:
        raise PlanError("План не найден", 404)
    plan_date, located = plan.plan_date, plan.location_key is not None
    db.delete(plan)
    db.flush()
    if rest and located:
        set_rest(db, user, plan_date)


def plan_save_context(db: Session, user: User, plan: RunPlan) -> dict[str, Any]:
    """Ответ «Планирую»: сам план и что стало с постоянным планом.

    displaced — серия, чью субботу вытеснил этот план («В Зеленограде 14 ноября
    не считаем — вернётся, если уберёте этот план»); только если без него
    вхождение было бы. series_sleeping — план на точку своей серии, а серия
    спит: явное «Планирую» туда похоже на «я снова хожу», и страница
    предлагает «Продолжить», но молча серию не будим — человек мог отметить
    одну дату.
    """
    from app.services.plan_series_service import active_series, series_sleeping_now

    today = moscow_today()
    identities = _Identities(db, today)
    entry = plan_entry(db, user, plan, today=today)
    item = plan_items(db, user, [entry], today=today, identities=identities)[0]
    sleeping = False
    current = active_series(db, user.id)
    if current is not None and entry.series is None and entry.displaces is None:
        if _series_key(db, current) == item["location"]["key"] or current.location_id == plan.location_id:
            sleeping = series_sleeping_now(db, current, identities=identities)
    return {
        "plan": item,
        "displaced": item["displaces"]["location"] if item["displaces"] is not None else None,
        "series_sleeping": sleeping,
    }


def _day_row(db: Session, user: User, plan_date: date) -> RunPlan | None:
    """Строка дня без локации — отметка «Не бегаю»."""
    return (
        db.query(RunPlan)
        .filter(RunPlan.user_id == user.id, RunPlan.plan_date == plan_date, RunPlan.location_key.is_(None))
        .one_or_none()
    )


def _drop_rest(db: Session, row: RunPlan) -> None:
    """Снять «Не бегаю»: строки дня без отметки не бывает (CHECK)."""
    db.delete(row)
    db.flush()


def _rest_question(db: Session, plan_date: date, plans: list[RunPlan]) -> str:
    """Текст окна подтверждения: какие планы уйдут вместе с «Не бегаю»."""
    identities = _Identities(db, moscow_today())
    names = []
    for plan in plans:
        identity = identities.get(plan.location_key)
        names.append(identity.name if identity is not None else "другая локация")
    what = "план" if len(plans) == 1 else "планы"
    return f"На {day_label(plan_date)} у вас в планах: {', '.join(names)}. Убрать {what} и отметить «Не бегаю»?"


def set_rest(
    db: Session, user: User, plan_date: date, *, replace: bool = False, is_public: bool | None = None
) -> RunPlan:
    """Отметить «Не бегаю» на день старта. Возвращает строку дня.

    Только день старта — суббота или особая дата — в горизонте календаря:
    отметка отвечает на «где ты в эту субботу». Лимиты дня её не считают.
    На день с планами пробежки — RestConflict (409) со списком планов;
    replace=True — планы дня удаляются, и ставится отметка.

    is_public — видна ли отметка в календаре профиля («не бегает»); None — у
    новой отметки умолчание как у плана (профиль открыт — публично), а на
    дату своего постоянного плана («Пропущу») — как у него; у стоящей — без
    изменений. Повтор с is_public на стоящей отметке — правка её публичности.
    """
    today = moscow_today()
    if plan_date < today:
        raise PlanError("Этот старт уже прошёл")
    if plan_date > today + timedelta(days=PLANNER_HORIZON_DAYS):
        raise PlanError("Отметить можно не дальше чем на год вперёд")
    if start_day(db, plan_date) is None:
        raise PlanError("«Не бегаю» отмечается на день старта — субботу или особую дату")
    _lock_user(db, user.id)
    plans = (
        db.query(RunPlan)
        .filter(RunPlan.user_id == user.id, RunPlan.plan_date == plan_date, RunPlan.location_key.isnot(None))
        .order_by(RunPlan.created_at, RunPlan.id)
        .all()
    )
    if plans and not replace:
        raise RestConflict(_rest_question(db, plan_date, plans), plans)
    for plan in plans:
        db.delete(plan)
    if plans:
        db.flush()
    row = _day_row(db, user, plan_date)
    if row is None or not row.rest:
        fallback = default_public(user)
        if is_public is None:
            # Снятая суббота приватного постоянного плана не должна всплыть
            # «не бегает» в профиле (ревью 27.10.2026, PRIV-3). До новой
            # строки: запрос сбросил бы в базу пустую строку дня.
            from app.services.plan_series_service import series_public_on

            series_public = series_public_on(db, user.id, plan_date)
            if series_public is not None:
                fallback = series_public
        if row is None:
            row = RunPlan(user_id=user.id, plan_date=plan_date)
            db.add(row)
        row.is_public = clean_public(user, is_public, fallback)
    elif is_public is not None:
        row.is_public = clean_public(user, is_public, row.is_public)
    row.rest = True
    row.updated_at = datetime.now(UTC)
    db.flush()
    return row


def clear_rest(db: Session, user: User, plan_date: date) -> None:
    """Снять «Не бегаю»: строка дня уходит целиком."""
    _lock_user(db, user.id)
    row = _day_row(db, user, plan_date)
    if row is not None:
        _drop_rest(db, row)


# ---------------------------------------------------------------- чтение владельцем


def _day_rows_between(db: Session, user_id: UUID, date_from: date, date_to: date) -> list[RunPlan]:
    """Строки дня («Не бегаю») — это не планы, их читает владелец."""
    return (
        db.query(RunPlan)
        .filter(
            RunPlan.user_id == user_id,
            RunPlan.location_key.is_(None),
            RunPlan.plan_date >= date_from,
            RunPlan.plan_date <= date_to,
        )
        .order_by(RunPlan.plan_date, RunPlan.id)
        .all()
    )


def list_my_plans(db: Session, user: User, date_from: date, date_to: date) -> list[dict[str, Any]]:
    """Месяц «Планов» и «Ближайшие»: планы (разовые и вхождения постоянного, в
    т.ч. прошлые субботы остановленной серии) и строки дня. В дне — сначала
    планы, потом строка дня."""
    today = moscow_today()
    identities = _Identities(db, today)
    entries: list[RunPlan | PlannedStart] = [
        *planned_starts(
            db,
            today=today,
            date_from=date_from,
            date_to=date_to,
            user_ids=[user.id],
            identities=identities,
            with_displaces=True,
        ),
        *_day_rows_between(db, user.id, date_from, date_to),
    ]
    entries.sort(
        key=lambda entry: (
            entry.plan_date,
            isinstance(entry, RunPlan),
            entry.created_at if isinstance(entry, PlannedStart) else datetime.min.replace(tzinfo=UTC),
            entry.ref if isinstance(entry, PlannedStart) else plan_ref(entry.id),
        )
    )
    return plan_items(db, user, entries, today=today, identities=identities)


# «Обзор» на каждом заходе: сначала две недели, и только пусто — год.
NEXT_PLAN_NEAR_DAYS = 14


def next_plan(db: Session, user: User) -> dict[str, Any] | None:
    """Ближайший план — разовый или вхождение постоянного. План на дату,
    которой больше нет в календаре (админ убрал особую дату), ближайшим не
    считается: карточка «Обзора» писала бы «Дальше: пт 1 января» о старте,
    которого нет, и теряла локацию настоящего плана (ревью 28.10.2026, SD-3)."""
    today = moscow_today()
    identities = _Identities(db, today)
    for horizon in (NEXT_PLAN_NEAR_DAYS, PLANNER_HORIZON_DAYS):
        entries = planned_starts(
            db,
            today=today,
            date_from=today,
            date_to=today + timedelta(days=horizon),
            user_ids=[user.id],
            identities=identities,
            with_displaces=True,
        )
        for entry in entries:
            item = plan_items(db, user, [entry], today=today, identities=identities)[0]
            if item["start"] is not None:
                return item
    return None


def _nearest_start_day_for(db: Session, user: User, today: date) -> StartDay:
    """Ближайший день старта человека: особые даты — только его систем.

    Системы — привязки (parkrun не планируется) и системы его будущих
    планов: без привязок человек тоже планирует. План на особую дату любой
    системы делает её его стартом. Систем не знаем — особые даты без отбора.
    Спрашивает карточка «Ближайший план» на «Обзоре».
    """
    linked = {
        code
        for (code,) in db.query(Platform.code)
        .join(PlatformLink, PlatformLink.platform_id == Platform.id)
        .filter(PlatformLink.user_id == user.id)
    }
    # Особая дата до ближайшей субботы — только в окне [сегодня; суббота].
    planned = [
        (entry.plan_date, entry.platform_code)
        for entry in planned_starts(
            db, today=today, date_from=today, date_to=_next_saturday(today), user_ids=[user.id]
        )
    ]
    codes = (linked | {code for _plan_date, code in planned if code}) & set(PLANNABLE_PLATFORMS)
    return nearest_start_day(
        db, today, platform_codes=codes or None, planned={plan_date for plan_date, _code in planned}
    )


def next_start_day_state(db: Session, user: User) -> dict[str, Any]:
    """Ближайший день старта для карточки «Ближайший план»: есть ли на него
    план, постоянный ли он и стоит ли «Не бегаю». Плана нет — карточка
    предлагает «Планирую» и «Не бегаю», отметка стоит — «не бегаете ·
    Отменить», план дня — вхождение постоянного — «↻ каждую субботу · Пропущу»."""
    today = moscow_today()
    day = _nearest_start_day_for(db, user, today)
    entries = planned_starts(db, today=today, dates=[day.date], user_ids=[user.id])
    rest = any(row.rest for row in _day_rows_between(db, user.id, day.date, day.date))
    return {
        "date": day.date,
        "kind": day.kind,
        "title": day.title,
        "planned": bool(entries),
        "series": any(entry.regular for entry in entries),
        "rest": rest,
    }


# ---------------------------------------------------------------- профиль

# Календарь планов в профиле листается, как оргкалендарь, на год в обе
# стороны; прошедших дней в нём нет — только сегодня и дальше.
PROFILE_CALENDAR_MONTHS = 12


class ProfileHidden(PlanError):
    """Профиль закрыт: календаря планов чужим нет вовсе."""

    def __init__(self) -> None:
        super().__init__("Профиль скрыт пользователем", 404)


def _name_shown(db: Session, user: User) -> bool:
    """Публичные планы человека видны чужим по имени: профиль открыт и по
    протоколам он не ребёнок — то же правило, что в plan_audience."""
    return not user.profile_private and not minor_user_ids(db, [user.id])


def _profile_places(
    db: Session,
    target: User,
    entries: list[PlannedStart],
    *,
    today: date,
    identities: _Identities,
    public_refs: Collection[str],
) -> dict[date, dict[str, Any]]:
    """Дата → подпись старта и места для профиля. Только планы в силе
    (still_on): локация на паузе или дата пропала из календаря — не план.
    Дрейф ключа мог оставить две строки одной точки — место одно."""
    days: dict[date, dict[str, Any]] = {}
    for entry, item in zip(entries, plan_items(db, target, entries, today=today, identities=identities), strict=True):
        if not still_on(identities, item):
            continue
        day = days.setdefault(
            entry.plan_date,
            {"kind": item["start"]["kind"], "title": item["start"]["title"], "places": {}, "public": False},
        )
        location = item["location"]
        day["public"] = day["public"] or entry.ref in public_refs
        day["places"].setdefault(
            location["key"],
            {
                "name": location["name"],
                "slug": location["slug"],
                "cancelled": bool(item["start"]["cancelled"]),
                "regular": item["series"] is not None,
            },
        )
    return days


def profile_plan_line(db: Session, target: User, viewer: User | None) -> dict[str, Any] | None:
    """«📅 Ближайший план: сб 31 октября — Зеленоград · все планы» в шапке профиля.

    Ближайший ПУБЛИЧНЫЙ план на любую дату (решение Дмитрия 27.10.2026: не
    только ближайшая суббота — у человека есть календарь, и в него можно
    провалиться) по тем же правилам, что имя на странице локации: план
    публичный, профиль открыт, взрослый. Хозяину — ближайший план любой, с
    `only_me`, если на эту дату чужим не видно ничего. upcoming_count —
    сколько дат с публичными планами впереди (их покажет вкладка «Планы»).
    «Не бегаю» в строку не идёт: это о дне, а не о плане.
    """
    owner = viewer is not None and viewer.id == target.id
    named = _name_shown(db, target)
    if not owner and not named:
        return None
    today = moscow_today()
    identities = _Identities(db, today)
    entries = planned_starts(
        db,
        today=today,
        date_from=today,
        date_to=today + timedelta(days=PLANNER_HORIZON_DAYS),
        user_ids=[target.id],
        identities=identities,
    )
    public_refs = {entry.ref for entry in entries if named and entry_public(entry)}
    wanted = entries if owner else [entry for entry in entries if entry.ref in public_refs]
    if not wanted:
        return None
    days = _profile_places(db, target, wanted, today=today, identities=identities, public_refs=public_refs)
    if not days:
        return None
    nearest = min(days)
    day = days[nearest]
    return {
        "date": nearest,
        "kind": day["kind"],
        "title": day["title"],
        "places": list(day["places"].values()),
        "upcoming_count": sum(1 for item in days.values() if item["public"]),
        "only_me": not day["public"],
    }


def profile_plans_tab(db: Session, target: User, viewer: User | None) -> str:
    """Активна ли вкладка «Планы» профиля (решение Дмитрия 27.10.2026: пустой
    календарь чужого профиля — неактивный пункт с подсказкой).

    «public» — есть что показать: будущий публичный план (как его видит
    вкладка: entry_public, имя показывается) или публичное «Не бегаю»;
    хозяину — всегда. «closed» — профиль закрыт. «empty» — показывать нечего:
    планы приватные, их нет или это ребёнок (тот же пустой календарь, что
    отдаст вкладка, — почему, не говорим).

    Лёгкий запрос, а не календарь: зовётся на каждом открытии профиля, а
    planned_starts одного человека на год — десятки миллисекунд.
    """
    if viewer is not None and viewer.id == target.id:
        return "public"
    if target.profile_private:
        return "closed"
    if not _name_shown(db, target):
        return "empty"
    today = moscow_today()
    horizon = today + timedelta(days=PLANNER_HORIZON_DAYS)
    rest_dates = [
        row[0]
        for row in db.query(RunPlan.plan_date).filter(
            RunPlan.user_id == target.id,
            RunPlan.location_key.is_(None),
            RunPlan.rest.is_(True),
            RunPlan.is_public.is_(True),
            RunPlan.plan_date >= today,
            RunPlan.plan_date <= horizon,
        )
    ]
    if _start_days(db, rest_dates):
        return "public"
    # Публичный план, которого календарь не покажет (дату убрал админ,
    # локация на паузе), вкладку не открывает: внутри было бы пусто (ревью
    # 28.10.2026, SD-4). Обычно в силе первый же — plan_items на один план.
    identities = _Identities(db, today)
    for entry in planned_starts(
        db, today=today, date_from=today, date_to=horizon, user_ids=[target.id], identities=identities
    ):
        if entry_public(entry) and still_on(
            identities, plan_items(db, target, [entry], today=today, identities=identities)[0]
        ):
            return "public"
    return "empty"


def _start_days(db: Session, dates: Collection[date]) -> dict[date, str | None]:
    """Дни старта из dates — суббота или особая дата любой системы (как
    start_day у «Не бегаю») → название особой даты (у субботы — None).
    Отметка на дне, который админ потом убрал из особых, днём старта больше
    не считается."""
    wanted = set(dates)
    if not wanted:
        return {}
    specials = {item.start_date: item.title for item in special_dates(db, min(wanted), max(wanted))}
    return {
        on_date: specials.get(on_date)
        for on_date in sorted(wanted)
        if on_date in specials or on_date.weekday() == 5
    }


def profile_plan_calendar(db: Session, target: User, viewer: User | None, month: date) -> dict[str, Any]:
    """Вкладка «Планы» профиля: публичные планы и публичные «Не бегаю» по дням
    месяца — только для чтения (решение Дмитрия 27.10.2026: «на его странице
    мы можем в его календарь провалиться, если это публично; если план
    приватный — в чужом профиле это будет просто пустота»).

    Видят все, и хозяин — то же, что другие (свой полный календарь — «Планы»
    в кабинете). Правило — как у имени на странице локации: публичный план,
    открытый профиль, взрослый; ребёнку календарь пустой. Закрытый профиль —
    ProfileHidden (404) всем, кроме хозяина. Прошедших дней нет: факт по
    протоколам — для владельца, а «планировал и не пришёл» чужим не
    показываем. Заметок нет ни у кого.
    """
    owner = viewer is not None and viewer.id == target.id
    if target.profile_private and not owner:
        raise ProfileHidden
    today = moscow_today()
    first, last = _month_bounds(month, today=today, months=PROFILE_CALENDAR_MONTHS)
    date_from = max(first, today)
    date_to = min(last, today + timedelta(days=PLANNER_HORIZON_DAYS))
    days: dict[date, dict[str, Any]] = {}
    if date_from <= date_to and _name_shown(db, target):
        identities = _Identities(db, today)
        entries = [
            entry
            for entry in planned_starts(
                db, today=today, date_from=date_from, date_to=date_to, user_ids=[target.id], identities=identities
            )
            if entry_public(entry)
        ]
        for on_date, day in _profile_places(
            db, target, entries, today=today, identities=identities, public_refs={entry.ref for entry in entries}
        ).items():
            days[on_date] = {
                "date": on_date,
                "kind": day["kind"],
                "title": day["title"],
                "places": list(day["places"].values()),
                "rest": False,
            }
        rest_days = [
            row.plan_date
            for row in _day_rows_between(db, target.id, date_from, date_to)
            if row.rest and row.is_public and row.plan_date not in days
        ]
        # Только дни старта: «не бегает» в пятницу, которую админ убрал из
        # особых, — не отметка о старте (SD-4).
        for on_date, title in _start_days(db, rest_days).items():
            days[on_date] = {
                "date": on_date,
                "kind": "special" if title is not None else "regular",
                "title": title,
                "places": [],
                "rest": True,
            }
    return {
        "month": f"{first.year:04d}-{first.month:02d}",
        "today": today,
        "owner": owner,
        "days": [days[on_date] for on_date in sorted(days)],
    }


# ---------------------------------------------------------------- блок локации


def _person(user: User) -> dict[str, Any]:
    from app.services.seo_service import public_profile_name

    handle = (user.public_slug or "").strip() or (str(user.serial_id) if user.serial_id else None)
    return {"name": public_profile_name(user), "handle": handle}


def _date_fallback(on_date: date) -> dict[str, Any]:
    """Подпись даты, которой уже нет в календаре (админ убрал особую дату)."""
    return {
        "kind": "regular" if on_date.weekday() == 5 else "special",
        "title": None,
        "start_time": None,
        "number_forecast": None,
    }


def _people_by_date(
    db: Session,
    rows: list[tuple[PlannedStart, User]],
    *,
    identity_key: str,
    location_ids: list[UUID],
    today: date,
    location_rows: list[tuple[Location, str]] | None = None,
    marks: bool = False,
    regular_stats_dates: Collection[date] | None = None,
    ahead: dict[UUID, Any] | None = None,
) -> dict[date, dict[str, Any]]:
    """Планирующие по датам, двумя группами: разовые (`one_off`) и постоянные
    (`regulars`, «↻ Каждую субботу»). В группе — всего, имена у публичных
    планов, число приватных и число детей (без имён, см. plan_audience); на
    дате — ещё `total` и `user_ids` обеих групп. Человек на дату считается
    один раз, даже если дрейф ключа оставил ему две строки одной точки; и
    разовый, и постоянный сразу — постоянный.

    Имена — по числу пробежек, больше — выше, без привязок — в конце
    (решение Дмитрия 25.10.2026: «сортируй тех, у кого больше пробежек»); при
    равенстве — кто раньше отметился: rows приходят в порядке created_at, ref,
    и имена не прыгают между перезагрузками.

    Рядом с именем у будущей даты — всего пробежек. marks=False — страница
    локации: больше ничего, число из Redis (cached_total_runs). marks=True —
    оргкабинет: ещё «впервые у нас», юбилей и знаковый старт списком
    (planner_guests_service.people_stats; location_rows — строки идентичности
    для горизонта юбилеев), а `shown` — те же люди парами (User, PersonStats)
    для «Кто у нас бежит». Цифры — только у названных по имени: приватных и
    детей не считаем.

    regular_stats_dates — у постоянных метки считаются только на эти даты
    (горизонт юбилеев «Кто у нас бежит»: 25 постоянных × 52 недели — это 1300
    пар); на остальных датах у них цифры без меток — те же, что на их первой
    дате.

    ahead — готовые plans_ahead (оргкабинет считает их раз на запрос и для
    столбца «Челленджи»); без них — свои.
    """
    from app.services.planner_guests_service import PersonStats, cached_total_runs, people_stats

    audience = plan_audience(db, rows)
    regular_pairs = {(entry.plan_date, user.id) for entry, user in rows if entry.regular}

    def empty() -> dict[str, Any]:
        return {"total": 0, "user_ids": set(), "shown": [], "private": 0, "children": 0}

    grouped: dict[date, dict[str, Any]] = {}
    for entry, user in rows:
        day = grouped.setdefault(
            entry.plan_date, {"total": 0, "user_ids": set(), "one_off": empty(), "regulars": empty()}
        )
        if user.id in day["user_ids"]:
            continue
        regular = (entry.plan_date, user.id) in regular_pairs
        if regular and not entry.regular:
            continue
        day["user_ids"].add(user.id)
        day["total"] += 1
        bucket = day["regulars"] if regular else day["one_off"]
        bucket["user_ids"].add(user.id)
        bucket["total"] += 1
        shown = audience.get(entry.ref, SHOWN_PRIVATE)
        if shown == SHOWN_NAME:
            bucket["shown"].append(user)
        elif shown == SHOWN_CHILD:
            bucket["children"] += 1
        else:
            bucket["private"] += 1
    # Прошедшим дням оргкалендаря имена не нужны — и цифры для них не считаем.
    entries: list[tuple[UUID, date]] = []
    first_regular: dict[UUID, date] = {}
    for on_date in sorted(grouped):
        if on_date < today:
            continue
        day = grouped[on_date]
        entries.extend((user.id, on_date) for user in day["one_off"]["shown"])
        for user in day["regulars"]["shown"]:
            first_regular.setdefault(user.id, on_date)
            if regular_stats_dates is None or on_date in regular_stats_dates or first_regular[user.id] == on_date:
                entries.append((user.id, on_date))
    entries = list(dict.fromkeys(entries))
    if marks:
        stats = people_stats(
            db,
            identity_key=identity_key,
            location_ids=location_ids,
            entries=entries,
            today=today,
            rows=location_rows,
            ahead=ahead,
        )
    else:
        totals = cached_total_runs(db, [user_id for user_id, _date in entries])
        stats = {
            (user_id, on_date): PersonStats(runs=totals.get(user_id), runs_here=None, first_here=False, jubilee=None)
            for user_id, on_date in entries
        }

    def stats_of(user_id: UUID, on_date: date, *, regular: bool) -> Any:
        found = stats.get((user_id, on_date))
        if found is not None or not regular or user_id not in first_regular:
            return found
        # За горизонтом меток у постоянного — цифры его первой даты, без меток.
        base = stats.get((user_id, first_regular[user_id]))
        return replace(base, first_here=False, jubilee=None, marks=()) if base is not None else None

    for on_date, day in grouped.items():
        for name, regular in (("one_off", False), ("regulars", True)):
            bucket = day[name]
            pairs = [(user, stats_of(user.id, on_date, regular=regular)) for user in bucket["shown"]]
            # sort устойчив: при равном числе — прежний порядок отметок.
            pairs.sort(key=lambda pair: _runs_order(pair[1]))
            bucket["shown"] = pairs
            bucket["people"] = [_plan_person(user, person_stats, marks=marks) for user, person_stats in pairs]
    return grouped


def _runs_order(stats: Any) -> tuple[bool, int]:
    runs = stats.runs if stats is not None else None
    return (runs is None, -(runs or 0))


def _plan_person(user: User, stats: Any, *, marks: bool) -> dict[str, Any]:
    """PlanPerson (страница локации) или OrganizerPlanPerson (оргкалендарь);
    у прошедшего дня цифр нет, а поля на месте."""
    person = {**_person(user), "runs": stats.runs if stats is not None else None}
    if marks:
        person["first_here"] = stats.first_here if stats is not None else False
        person["jubilee"] = stats.jubilee if stats is not None else None
        person["marks"] = [mark.payload() for mark in stats.marks] if stats is not None else []
    return person


def _people_payload(bucket: dict[str, Any] | None) -> dict[str, Any]:
    """people / private / children группы для ответа; нет планов — пусто."""
    if bucket is None:
        return {"people": [], "private": 0, "children": 0}
    return {"people": bucket["people"], "private": bucket["private"], "children": bucket["children"]}


def _merged_people(day: dict[str, Any]) -> dict[str, Any]:
    """Блок локации: разовые и постоянные одним списком (решение Дмитрия
    27.10.2026: отдельная строка «↻ Каждую субботу» избыточна — у имени
    постоянного значок ↻, `regular`). Порядок — по числу пробежек, как у
    каждой группы; при равенстве — разовые раньше, внутри группы — кто раньше
    отметился. Приватные и дети — общими числами."""
    pairs = [
        (stats, {**person, "regular": regular})
        for name, regular in (("one_off", False), ("regulars", True))
        for (_user, stats), person in zip(day[name]["shown"], day[name]["people"], strict=True)
    ]
    pairs.sort(key=lambda pair: _runs_order(pair[0]))
    return {
        "people": [person for _stats, person in pairs],
        "private": day["one_off"]["private"] + day["regulars"]["private"],
        "children": day["one_off"]["children"] + day["regulars"]["children"],
    }


def _audience_group(bucket: dict[str, Any] | None) -> dict[str, Any]:
    """OrganizerPlanAudience (regulars дня оргкалендаря): всего и как видны
    люди группы."""
    return {"total": bucket["total"] if bucket is not None else 0, **_people_payload(bucket)}


def _plan_users(db: Session, entries: list[PlannedStart]) -> list[tuple[PlannedStart, User]]:
    ids = list({entry.user_id for entry in entries})
    users = {user.id: user for user in db.query(User).filter(User.id.in_(ids)).all()} if ids else {}
    return [(entry, users[entry.user_id]) for entry in entries if entry.user_id in users]


# Блок локации: вхождения постоянных считаем не на год, а на даты-кандидаты
# вкладок — столько ближайших суббот плюс даты разовых и особые даты.
LOCATION_BLOCK_NEAREST_STARTS = 6


def _plans_elsewhere(
    db: Session, user_id: UUID, identity: _Identity, identities: _Identities, *, today: date, horizon: date
) -> list[dict[str, Any]]:
    """Даты с разовыми планами человека в ДРУГИХ локациях — для окна плана от
    локации: чип такой даты говорит «бегу в Битцу», а заполненный день (лимит
    как у save_plan: по самой дате) не нажимается — иначе человек узнавал бы о
    лимите по 409 после «Планирую».

    Как у постоянного плана (series_payload): сначала даты разовых одним
    запросом, вхождения считаются только на них — не на год вперёд."""
    plan_dates, _rest = user_marked_dates(db, user_id, date_from=today, date_to=horizon)
    if not plan_dates:
        return []
    here = set(identity.location_ids)
    by_date: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for entry in planned_starts(db, today=today, dates=plan_dates, user_ids=[user_id], identities=identities):
        # Вхождение постоянного в лимит дня не идёт — поездка его вытесняет.
        if entry.plan is None or entry.key == identity.key or entry.location_id in here:
            continue
        by_date[entry.plan_date].append(series_location(identities, entry.key, fallback_key=entry.location_key))
    if not by_date:
        return []
    limits = day_plan_limits(db, by_date)
    return [
        {
            "date": on_date,
            "locations": locations,
            "full": len(locations) >= limits[on_date],
            "limit": limits[on_date],
        }
        for on_date, locations in sorted(by_date.items())
    ]


def location_plans_block(db: Session, identity_key: str, viewer: User | None) -> dict[str, Any]:
    """«Планируют пробежать» на странице локации.

    Видят все, включая гостей. У даты — один список имён: рядом только всего
    пробежек, больше — выше («впервые у нас» и юбилеи — только в
    оргкабинете), у постоянных — значок ↻ (`regular`; решение Дмитрия
    27.10.2026 вместо отдельной строки «↻ Каждую субботу: N»). Дети и
    приватные — общими числами. Людей отдаём всех: сколько показать сразу,
    решает страница («и ещё N» раскрывает список на месте). Заметок здесь нет ни у кого, кроме
    собственных планов зрителя (`mine`).

    Вкладки — до LOCATION_BLOCK_DATES дат: ближайшая, где кто-то планирует, и
    дальше те, где есть разовые или которые особые. Иначе шесть ближайших
    суббот заняли бы постоянные, а ★ 01.01 и даты гостей пропали бы. Вхождения
    считаются только на даты-кандидаты, а не на год вперёд: блок зовут на
    каждый просмотр страницы.

    Вошедшему — его постоянный план на ЭТОЙ точке (`my_series`) и подсказка
    «Вы пробежали здесь 9 из 10 последних стартов» (`series_hint`), если её
    точка — эта, а постоянного плана у него нет.
    """
    from app.services.plan_series_service import active_series, series_hint, series_payload

    today = moscow_today()
    identities = _Identities(db, today)
    identity_key = normalize_identity_key(identity_key) or ""
    identity = identities.get(identity_key)
    if identity is None:
        raise PlanError("Локация не найдена", 404)
    horizon = today + timedelta(days=PLANNER_HORIZON_DAYS)
    pairs = identity_one_off_dates(db, identity_key, identity.location_ids, date_from=today, date_to=horizon)
    # Кандидаты без календаря на год: ближайшие субботы, особые даты систем
    # точки (как их берёт start_dates) и даты разовых. Календарь потом
    # считается только на отрезке кандидатов.
    codes = {code for _location, code in identity.info_rows}
    special_set = (
        {item.start_date for item in special_dates(db, today, horizon) if codes & set(item.platforms or [])}
        if codes
        else set()
    )
    saturdays = (
        {_next_saturday(today) + timedelta(weeks=week) for week in range(LOCATION_BLOCK_NEAREST_STARTS)}
        if identity.plannable
        else set()
    )
    # Без обрезки: пустые субботы и особые даты без планов не должны вытеснять
    # даты разовых — обрезается уже набор дат, где кто-то планирует (до
    # LOCATION_BLOCK_DATES вкладок). Дат разовых и особых за год — единицы.
    candidates = sorted(saturdays | {plan_date for _user_id, plan_date in pairs} | special_set)
    viewer_dates = {plan_date for user_id, plan_date in pairs if viewer is not None and user_id == viewer.id}
    entries = planned_starts(
        db,
        today=today,
        dates=set(candidates) | viewer_dates,
        identity=(identity_key, identity.location_ids),
        identities=identities,
    )
    by_date: dict[date, list[PlannedStart]] = defaultdict(list)
    for entry in entries:
        by_date[entry.plan_date].append(entry)
    candidate_set = set(candidates)
    # Даты, которой больше нет в календаре (админ убрал, перенёс или отдал
    # другим системам особую дату), — не план: не напоминается, в профиле не
    # видна (still_on) и здесь именами не показывается (ревью 28.10.2026, SD-2).
    in_calendar = (
        {item.date for item in identities.starts_between(identity, min(by_date), max(by_date))}
        if by_date and identity.info_rows
        else None
    )
    upcoming_dates: list[date] = []
    for on_date in sorted(by_date):
        if on_date not in candidate_set or (in_calendar is not None and on_date not in in_calendar):
            continue
        one_off, _regular = split_by_regular(by_date[on_date])
        if not upcoming_dates or one_off or on_date in special_set:
            upcoming_dates.append(on_date)
        if len(upcoming_dates) >= LOCATION_BLOCK_DATES:
            break
    shown = set(upcoming_dates)
    grouped = _people_by_date(
        db,
        _plan_users(db, [entry for entry in entries if entry.plan_date in shown]),
        identity_key=identity_key,
        location_ids=identity.location_ids,
        today=today,
    )
    dates_payload = []
    for on_date in upcoming_dates:
        start = (
            identities.start_for(identity, on_date, span=(upcoming_dates[0], upcoming_dates[-1]))
            if identity.info_rows
            else None
        )
        info = (
            {
                "kind": start.kind,
                "title": start.title,
                "start_time": start.start_time,
                "number_forecast": start.number_forecast,
            }
            if start is not None
            else _date_fallback(on_date)
        )
        day = grouped[on_date]
        dates_payload.append({"date": on_date, **info, "total": day["total"], **_merged_people(day)})
    mine: list[dict[str, Any]] = []
    elsewhere: list[dict[str, Any]] = []
    my_series = None
    hint = None
    if viewer is not None:
        elsewhere = _plans_elsewhere(db, viewer.id, identity, identities, today=today, horizon=horizon)
        own_dates = shown | viewer_dates
        mine_entries = planned_starts(
            db,
            today=today,
            dates=own_dates,
            user_ids=[viewer.id],
            identity=(identity_key, identity.location_ids),
            identities=identities,
            with_displaces=True,
        ) if own_dates else []
        mine = plan_items(db, viewer, mine_entries, today=today, identities=identities)
        current = active_series(db, viewer.id)
        if current is not None:
            if current.location_id in set(identity.location_ids) or _series_key(db, current) == identity_key:
                my_series = series_payload(db, viewer, current, today=today, identities=identities, key=identity.key)
        else:
            found = series_hint(db, viewer, today=today)
            if found is not None and found["location"]["key"] == identity_key:
                hint = found
    return {
        "identity_key": identity_key,
        "plannable": bool(identity.plannable),
        "reason": identity.reason,
        "dates": dates_payload,
        "mine": mine,
        "elsewhere": elsewhere,
        "my_series": my_series,
        "series_hint": hint,
    }


def location_start_dates(db: Session, identity_key: str) -> dict[str, Any]:
    """Даты для окна «Планирую»: календарь локации на год вперёд."""
    today = moscow_today()
    identity_key = normalize_identity_key(identity_key) or ""
    rows = identity_rows(db, identity_key)
    if not rows:
        raise PlanError("Локация не найдена", 404)
    plannable, reason = plannable_rows_with_reason(db, rows, today=today)
    dates = (
        start_dates(
            db,
            plannable,
            date_from=today,
            date_to=today + timedelta(days=PLANNER_HORIZON_DAYS),
            today=today,
            history_rows=rows,
        )
        if plannable
        else []
    )
    return {
        "identity_key": identity_key,
        "plannable": bool(plannable),
        "reason": reason,
        "dates": [item.as_dict() for item in dates],
    }


# ---------------------------------------------------------------- оргкабинет


def _month_bounds(month: date, *, today: date, months: int) -> tuple[date, date]:
    """Первый и последний день месяца, если он не дальше `months` месяцев от
    сегодняшнего, — иначе PlanError. Диапазон — до арифметики дат: у
    9999-12 следующего месяца нет, и запрос с таким месяцем отвечал 500."""
    first = month.replace(day=1)
    if first < _shift_months(today, -months) or first > _shift_months(today, months):
        raise PlanError("Календарь листается на год назад и на год вперёд")
    following = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first, following - timedelta(days=1)


def _shift_months(value: date, months: int) -> date:
    index = value.year * 12 + value.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def _came_by_date(
    db: Session, location_ids: list[UUID], planners: dict[date, set[UUID]]
) -> dict[date, int]:
    """Сколько из планировавших были в протоколе идентичности в эту дату."""
    dates = [on_date for on_date, users in planners.items() if users]
    user_ids = list({user_id for users in planners.values() for user_id in users})
    if not dates or not user_ids or not location_ids:
        return {}
    link_join = (PlatformLink.platform_id == Participant.platform_id) & (
        PlatformLink.external_user_id == Participant.external_user_id
    )
    present: set[tuple[date, UUID]] = set()
    for model in (RunResult, VolunteerResult):
        rows = (
            db.query(Event.event_date, PlatformLink.user_id)
            .select_from(model)
            .join(Event, model.event_id == Event.id)
            .join(Participant, model.participant_id == Participant.id)
            .join(PlatformLink, link_join)
            .filter(
                Event.location_id.in_(location_ids),
                Event.event_date.in_(dates),
                Event.is_test_event.is_(False),
                PlatformLink.user_id.in_(user_ids),
            )
            .distinct()
            .all()
        )
        present.update((event_date, user_id) for event_date, user_id in rows)
    return {
        on_date: sum(1 for user_id in users if (on_date, user_id) in present) for on_date, users in planners.items()
    }


def _mirror_location_ids(db: Session, location_ids: list[UUID]) -> set[UUID]:
    """Строки-зеркала идентичности: их события — вторичная сторона кросслинка
    к другой строке этой же точки (RunPark, который дублирует протоколы S95)."""
    if not location_ids:
        return set()
    secondary = aliased(Event)
    primary = aliased(Event)
    rows = (
        db.query(secondary.location_id)
        .select_from(EventCrosslink)
        .join(secondary, EventCrosslink.secondary_event_id == secondary.id)
        .join(primary, EventCrosslink.primary_event_id == primary.id)
        .filter(
            secondary.location_id.in_(location_ids),
            primary.location_id.in_(location_ids),
            primary.location_id != secondary.location_id,
        )
        .distinct()
        .all()
    )
    return {row[0] for row in rows}


def _past_day_numbers(
    db: Session, rows: list[tuple[Location, str]], events: list[dict[str, Any]]
) -> dict[date, int | None]:
    """Номер каждого прошедшего дня со стартом.

    Событий за день бывает несколько: кросслинк ещё не связал их, или старт
    один, а протоколов два. Номер — у системы с наименьшим рангом в строках
    идентичности (основная система первая), а не у первой по алфавиту:
    «runpark» < «s95», и у Новгорода дни без связки получали номер RunPark
    (ревью 25.10.2026, CAL-3). День, где событие есть только у зеркала, —
    без номера: номер зеркала у этой точки чужой. Обычный переезд (Раменское:
    5 вёрст → S95) зеркалом не считается и номер прежней системы сохраняет.
    """
    rank: dict[str, int] = {}
    for _location, code in rows:
        rank.setdefault(code, len(rank))
    mirrors = _mirror_location_ids(db, [location.id for location, _code in rows])
    location_by_event: dict[UUID, UUID] = {}
    event_ids = [event["event_id"] for event in events]
    if event_ids and mirrors:
        location_by_event = dict(db.query(Event.id, Event.location_id).filter(Event.id.in_(event_ids)).all())
    by_date: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_date[event["event_date"]].append(event)
    numbers: dict[date, int | None] = {}
    for event_date, day_events in by_date.items():
        own = [event for event in day_events if location_by_event.get(event["event_id"]) not in mirrors]
        if not own:
            numbers[event_date] = None
            continue
        best = min(own, key=lambda event: (rank.get(event["platform_code"], len(rank)), event["platform_code"]))
        numbers[event_date] = best["event_number"]
    return numbers


def organizer_calendar(db: Session, identity: Any, month: date) -> dict[str, Any]:
    """Календарь локации в оргкабинете: месяц стартов и планы на них.

    Будущий старт — разовые (`people`…) и постоянные (`regulars`) отдельно:
    имена публичных (больше пробежек — выше) с метками «впервые у нас» и
    юбилеем, которых на странице локации нет, «ещё K — приватно», дети
    числом. Страница сливает их в один список со значком ↻ у постоянных и
    строкой «Планируют 41 · из них ↻ постоянных 9» (решение Дмитрия
    27.10.2026, как на странице локации). «Обычно ~N» не пишем (решение Дмитрия по
    Q05). Прошедший — «Планировали разово N — пришли M · постоянных на старте
    K из L» без имён: организатору нужна явка, а не список, кто передумал.
    «Пришли» — были на старте: пробежали или волонтёрили. Постоянные прошлого
    дня — те, чья серия покрывала дату и не спала (вхождение, а не «пообещал
    ходить всегда»).
    """
    from app.services.organizer_service import list_identity_event_dates

    today = moscow_today()
    first, last = _month_bounds(month, today=today, months=ORGANIZER_CALENDAR_MONTHS)
    rows = list(identity.locations)
    location_ids = [location.id for location, _code in rows]
    codes = {code for _location, code in rows}
    plannable, _reason = plannable_rows_with_reason(db, rows, today=today)

    days: dict[date, dict[str, Any]] = {}

    def day(on_date: date) -> dict[str, Any]:
        if on_date not in days:
            days[on_date] = {
                "date": on_date,
                **_date_fallback(on_date),
                "number": None,
                "past": on_date < today,
                "planned": 0,
                **_people_payload(None),
                "regulars": _audience_group(None),
                "came": None,
                "regulars_came": None,
            }
        return days[on_date]

    calendar_dates: set[date] | None = None
    if last >= today and plannable:
        calendar_dates = set()
        for start in start_dates(
            db, plannable, date_from=max(first, today), date_to=last, today=today, history_rows=rows
        ):
            calendar_dates.add(start.date)
            item = day(start.date)
            item.update(
                kind=start.kind,
                title=start.title,
                start_time=start.start_time,
                number_forecast=start.number_forecast,
            )

    # Особые даты прошлого — только систем этой локации, как у будущих в
    # start_dates: праздник S95 не подписывает перенесённый старт 5 вёрст
    # (ревью 25.10.2026, BE-6).
    specials = {
        item.start_date: item
        for item in db.query(StartSpecialDate)
        .filter(StartSpecialDate.start_date >= first, StartSpecialDate.start_date <= min(last, today - timedelta(days=1)))
        .all()
        if codes & set(item.platforms or [])
    }
    past_events: set[date] = set()
    if first < today:
        month_events = [
            event
            for event in list_identity_event_dates(db, identity)
            if first <= event["event_date"] <= last and event["event_date"] < today
        ]
        for event_date, number in _past_day_numbers(db, rows, month_events).items():
            past_events.add(event_date)
            day(event_date)["number"] = number

    entries = planned_starts(
        db,
        today=today,
        date_from=first,
        date_to=last,
        identity=(identity.identity_key, location_ids),
    )
    if calendar_dates is not None:
        # Будущей даты больше нет в календаре — не план (still_on): ни имён,
        # ни числа, как на странице локации (SD-2).
        entries = [entry for entry in entries if entry.plan_date < today or entry.plan_date in calendar_dates]
    grouped = _people_by_date(
        db,
        _plan_users(db, entries),
        identity_key=identity.identity_key,
        location_ids=location_ids,
        today=today,
        location_rows=rows,
        marks=True,
    )
    planners: dict[date, set[UUID]] = {}
    regular_planners: dict[date, set[UUID]] = {}
    for on_date, bucket in grouped.items():
        item = day(on_date)
        item["planned"] = bucket["total"]
        if on_date >= today:
            item.update(_people_payload(bucket["one_off"]))
            item["regulars"] = _audience_group(bucket["regulars"])
        else:
            # Прошедшему дню имена не нужны: организатору — явка.
            item["regulars"] = {**_audience_group(None), "total": bucket["regulars"]["total"]}
            if on_date in past_events:
                planners[on_date] = bucket["one_off"]["user_ids"]
                regular_planners[on_date] = bucket["regulars"]["user_ids"]
    came = _came_by_date(db, location_ids, planners)
    regulars_came = _came_by_date(db, location_ids, regular_planners)
    for on_date in past_events:
        days[on_date]["came"] = came.get(on_date, 0)
        days[on_date]["regulars_came"] = regulars_came.get(on_date, 0)
    # Подпись прошедшего дня — после планов: день только с планами (старта
    # не было или протокол не пришёл) тоже бывает особой датой. Прошедший
    # старт не в субботу без особой даты — перенос (01.11 → 02.11) или
    # воскресный RunPark: это обычный старт, а не праздник.
    for on_date, item in days.items():
        if on_date >= today:
            continue
        special = specials.get(on_date)
        if special is not None:
            item.update(kind="special", title=special.title)
        elif on_date in past_events:
            item.update(kind="regular", title=None)

    return {
        "location": {"slug": identity.slug, "name": identity.name},
        "month": f"{first.year:04d}-{first.month:02d}",
        "days": [days[on_date] for on_date in sorted(days)],
    }


# ---------------------------------------------------------------- особые даты для всех

# Публичная выдача особых дат: кабинет «Планы» подсвечивает их в месяце. Год
# с запасом — сетка месяца захватывает соседние недели.
PUBLIC_SPECIAL_DATES_MAX_DAYS = 400


def public_special_dates(db: Session, date_from: date, date_to: date) -> list[dict[str, Any]]:
    """Особые даты в [date_from, date_to] по возрастанию: дата, название, системы.

    Без id и времени правки — это витрина календаря, а не админка.
    """
    if date_to < date_from or (date_to - date_from).days > PUBLIC_SPECIAL_DATES_MAX_DAYS:
        raise PlanError(f"Диапазон дат — не больше {PUBLIC_SPECIAL_DATES_MAX_DAYS} дней")
    return [
        {"date": item.start_date, "title": item.title, "platforms": list(item.platforms or [])}
        for item in special_dates(db, date_from, date_to)
    ]


# ---------------------------------------------------------------- админка особых дат


def _special_payload(item: StartSpecialDate) -> dict[str, Any]:
    return {"id": item.id, "start_date": item.start_date, "title": item.title, "platforms": list(item.platforms or [])}


def _clean_special(title: str, platforms: list[str]) -> tuple[str, list[str]]:
    cleaned_title = (title or "").strip()
    if not cleaned_title:
        raise StartDateError("Укажите название особой даты")
    if len(cleaned_title) > 128:
        raise StartDateError("Название длиннее 128 знаков")
    codes = {(code or "").strip() for code in platforms or []}
    unknown = sorted(code for code in codes if code not in PLANNABLE_PLATFORMS)
    if unknown:
        raise StartDateError(f"Неизвестная система: {', '.join(unknown)}")
    if not codes:
        raise StartDateError("Выберите хотя бы одну систему")
    return cleaned_title, [code for code in PLANNABLE_PLATFORMS if code in codes]


def admin_list_special_dates(db: Session) -> list[dict[str, Any]]:
    return [_special_payload(item) for item in db.query(StartSpecialDate).order_by(StartSpecialDate.start_date).all()]


def _ensure_free_date(db: Session, start_date: date, *, exclude_id: UUID | None = None) -> None:
    query = db.query(StartSpecialDate.id).filter(StartSpecialDate.start_date == start_date)
    if exclude_id is not None:
        query = query.filter(StartSpecialDate.id != exclude_id)
    if query.first() is not None:
        raise StartDateError("На эту дату особый старт уже есть", 409)


def admin_create_special_date(db: Session, *, start_date: date, title: str, platforms: list[str]) -> dict[str, Any]:
    cleaned_title, codes = _clean_special(title, platforms)
    _ensure_free_date(db, start_date)
    item = StartSpecialDate(start_date=start_date, title=cleaned_title, platforms=codes)
    db.add(item)
    db.flush()
    return _special_payload(item)


def admin_update_special_date(
    db: Session, special_id: UUID, *, start_date: date, title: str, platforms: list[str]
) -> dict[str, Any]:
    item = db.get(StartSpecialDate, special_id)
    if item is None:
        raise StartDateError("Особая дата не найдена", 404)
    cleaned_title, codes = _clean_special(title, platforms)
    _ensure_free_date(db, start_date, exclude_id=special_id)
    item.start_date = start_date
    item.title = cleaned_title
    item.platforms = codes
    item.updated_at = datetime.now(UTC)
    db.flush()
    return _special_payload(item)


def admin_delete_special_date(db: Session, special_id: UUID) -> None:
    item = db.get(StartSpecialDate, special_id)
    if item is None:
        raise StartDateError("Особая дата не найдена", 404)
    db.delete(item)
    db.flush()
