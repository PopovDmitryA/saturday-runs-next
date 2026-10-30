"""Кто бежит на локации: цифры о планирующих и раздел оргкабинета «Кто у нас бежит».

Решение Дмитрия 25.10.2026 — разделить. На странице локации у человека с
публичным планом рядом с именем только всего пробежек в скобках: «впервые у
нас» и юбилеи отмечаются только в оргкабинете, чтобы блок участников был
компактным (вечерняя правка того же дня; до неё метки стояли и на странице).
В оргкабинете — оргкалендарь с теми же метками (people_stats), таблица гостей
по датам с домашней локацией и столбцом «Знаковый старт» (organizer_guests,
метки — Mark) и страница участника с ключевыми цифрами, юбилеями и тем, что
даст ему визит по челленджам (organizer_guest_card).

Юбилеи и «первая пробежка» — только на три ближайших старта локации
(JUBILEE_HORIZON_STARTS, jubilee_horizon): дальше не поручиться, что человек
действительно пробежит всё, что до них. «Впервые у нас» — на любую дату: это
не прогноз номера.

Всё это — только про планы, которые и так видно по имени (SHOWN_NAME в
plan_audience): приватные планы и дети остаются числом, их цифр не считаем и
не отдаём никому, организатору тоже. Карточку чужого человека без его
публичного плана сюда не откроешь — 404, как будто его нет; в ней — только
его публичные планы сюда.

Своих правил подсчёта здесь нет — только сборка:
- всего пробежек — dashboard_service.total_runs_for_users (плитка кабинета),
  в блоке — через Redis на десять минут (cached_total_runs);
- три числа карточки (пробежки, волонтёрства, локации) —
  dashboard_service.cabinet_headline_stats: кэш кабинета, а без него — те же
  функции, что считают кабинет, но без рекордов, погоды и дальности;
- юбилей — organizer_service.is_milestone («Календарь юбилеев»), счётчик в
  системе — как там, но вторичная сторона кросслинка выпадает, только если
  есть и первичная (_system_runs);
- дом — admin_home_location_service (три ступени кабинета, ручной выбор
  сильнее);
- челленджи — achievements_service.planned_visit_gains (карточка: ближайший
  визит и следующие даты в горизонте) и visit_gains по пробежкам пачкой
  (столбец «Челленджи», guest_gains); номерные (≈№) — только в горизонте.

Пробежки и волонтёрства «здесь» — дни на строках идентичности, как
run_count в детализации локаций профиля: зеркало протокола в двух системах в
один день — один визит.

Запланированный визит — следующий по счёту только если раньше у человека
других планов нет: две субботы подряд с планами — вторая станет не 100-й, а
101-й. Поправка — именно планы, а не «бегал бы каждую субботу»: две субботы
без планов и план на третью — третья станет 100-й, а не 102-й. Поэтому номер
считается с поправкой на его более ранние будущие ПУБЛИЧНЫЕ планы (где
угодно; приватный в поправку не идёт — по сдвигу номера организатор узнал бы
о нём), а «впервые у нас» — только у первой даты сюда. Поправка считает
планы, а не даты: в особый день их до трёх, и каждый — пробежка. В каком
порядке пройдут старты такого дня, не известно, поэтому «будет N-я» у
нескольких планов одного дня не ставится ни на один. Планы, которые уже не
сбудутся, в поправку не идут (plans_ahead): старт отменён, даты больше нет
в календаре или локация ушла на паузу.

Постоянный план считается планом на каждую свою дату (вхождения из
planned_starts): юбилей на второй субботе у постоянного — с поправкой на
первую, как у двух разовых подряд. В «Кто у нас бежит» постоянные — своей
группой: состав один раз (regulars_people), а у даты — только кто и метки
горизонта (25 постоянных × 52 недели не превращаются в 1300 строк).

День старта: сегодняшний план — ещё «впереди», пока человека нет в
сегодняшнем протоколе и протокол локации плана не загружен. Появился
(пробежал или волонтёрил, где угодно) — план больше не поправка: его пробежка
уже в счёте, а «впервые» и «будет N-я» у сегодняшней даты — о прошлом и
снимаются. Протокол загружен без него — не пришёл, план не сбылся и тоже
снимается. Иначе до полуночи номер на следующих датах был бы на единицу
больше. В особый день планы снимаются поштучно: утром пробежал в одном месте —
дневной старт в другом ещё впереди.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Any
from uuid import UUID

import redis
from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from app.core.redis_client import get_redis_client
from app.models import (
    Event,
    EventCrosslink,
    Location,
    Platform,
    PlatformLink,
    RunResult,
    VolunteerResult,
)
from app.services.dashboard_service import cabinet_headline_stats, total_runs_for_users, total_runs_from_protocols
from app.services.organizer_service import is_milestone
from app.services.planned_starts_service import PlannedStart, planned_starts, split_by_regular
from app.services.platform_titles import platform_title
from app.services.start_calendar_service import (
    PLANNABLE_PLATFORMS,
    StartDate,
    _had_start,
    _next_saturday,
    identity_rows,
    moscow_today,
    normalize_identity_key,
    plannable_rows_with_reason,
    special_dates,
    start_dates,
)

# «Кто у нас бежит»: столько недель вперёд по умолчанию и не больше стольких.
GUESTS_WEEKS_DEFAULT = 12
GUESTS_WEEKS_MAX = 52

# Юбилеи — только на столько ближайших стартов локации (решение Дмитрия
# 25.10.2026): дальше не поручиться, что человек пробежит всё до них. Текст
# карточки называет число словом — поменяли константу, поменяйте и его.
JUBILEE_HORIZON_STARTS = 3
JUBILEE_HORIZON_NOTE = "Юбилеи считаем на три ближайших старта"
# Номерные челленджи («Нумератор», Фибоначчи, «Клуб Нельсона»…) держатся на
# прогнозе ≈№ и тоже считаются только в этом горизонте.
NUMBERS_HORIZON_NOTE = "Номерные челленджи считаем на три ближайших старта"

_EPOCH_GUARD = date(1970, 1, 1)

# Цифры человека живут в Redis по user_id. «Всего пробежек» — десять минут:
# блок зовут на каждом просмотре страницы локации, а пачка на 60 самых
# бегающих без кэша кабинета стоит на боевой базе ~135 мс против ~13 мс из
# него (замер 25.10.2026); после новой пробежки число в скобках отстаёт на
# эти минуты — не беда. Дом — полчаса: resolve_admin_home_locations обходит
# все локации ради любого числа людей (~0,4 с на стенде), а дом меняется
# раз в месяцы. None («привязок нет», «дома нет») запоминается тоже.
RUNS_CACHE_TTL_SECONDS = 600
HOME_CACHE_TTL_SECONDS = 1800
_RUNS_CACHE_PREFIX = "planner:runs:v1:"
_HOME_CACHE_PREFIX = "planner:home:v2:"


def _cache_read(prefix: str, ids: list[UUID]) -> dict[UUID, Any]:
    """Что есть в кэше: user_id → значение (None — тоже значение)."""
    try:
        raw_values = get_redis_client().mget([f"{prefix}{user_id}" for user_id in ids])
    except redis.RedisError:
        return {}
    found: dict[UUID, Any] = {}
    for user_id, raw in zip(ids, raw_values, strict=True):
        if raw is None:
            continue
        try:
            found[user_id] = json.loads(raw)
        except (TypeError, ValueError):
            continue
    return found


def _cache_write(prefix: str, values: dict[UUID, Any], ttl_seconds: int) -> None:
    if not values:
        return
    try:
        pipeline = get_redis_client().pipeline()
        for user_id, value in values.items():
            pipeline.setex(f"{prefix}{user_id}", ttl_seconds, json.dumps(value))
        pipeline.execute()
    except redis.RedisError:
        pass


def cached_total_runs(db: Session, user_ids: list[UUID]) -> dict[UUID, int | None]:
    """user_id → всего пробежек (None — не посчитать), через Redis на десять минут."""
    ids = list(dict.fromkeys(user_id for user_id in user_ids if user_id is not None))
    if not ids:
        return {}
    result: dict[UUID, int | None] = _cache_read(_RUNS_CACHE_PREFIX, ids)
    missing = [user_id for user_id in ids if user_id not in result]
    if missing:
        totals = total_runs_for_users(db, missing)
        fresh = {user_id: totals.get(user_id) for user_id in missing}
        result.update(fresh)
        remember_total_runs(fresh)
    return result


def remember_total_runs(values: dict[UUID, int | None]) -> None:
    """Положить свежие числа в кэш блока: карточка гостя считает без кэша, и
    после неё список и блок показывают то же число."""
    _cache_write(_RUNS_CACHE_PREFIX, dict(values), RUNS_CACHE_TTL_SECONDS)


@dataclass
class IdentityVisits:
    """Что у человека было на этой локации (на всех строках идентичности)."""

    run_days: int = 0
    volunteer_days: int = 0
    first_date: date | None = None
    last_date: date | None = None
    best_sec: int | None = None

    def visit(self, first: date | None, last: date | None) -> None:
        if first is not None and (self.first_date is None or first < self.first_date):
            self.first_date = first
        if last is not None and (self.last_date is None or last > self.last_date):
            self.last_date = last


# Виды знакового старта (GuestMark.kind).
FIRST_EVER = "first_ever"
JUBILEE = "jubilee"
JUBILEE_HERE = "jubilee_here"
JUBILEE_PLATFORM = "jubilee_platform"
FIRST_HERE = "first_here"


@dataclass(frozen=True)
class Mark:
    """Знаковый старт: чем станет запланированная пробежка — первой вообще,
    юбилейной всего, здесь или в системе, первым визитом сюда."""

    kind: str
    number: int | None = None
    platform_code: str | None = None

    def short_label(self) -> str:
        """Подпись в столбце «Знаковый старт» списка гостей."""
        if self.kind == FIRST_EVER:
            return "первая пробежка"
        if self.kind == JUBILEE:
            return f"{self.number}-я пробежка"
        if self.kind == JUBILEE_HERE:
            return f"{self.number}-я у нас"
        if self.kind == JUBILEE_PLATFORM:
            return f"{self.number}-я в {platform_title(self.platform_code)}"
        return "впервые у нас"

    def card_label(self) -> str:
        """Строка «Юбилеи» на странице участника (у «впервые у нас» там своё
        место — в цифрах «здесь»)."""
        if self.kind == FIRST_EVER:
            return "Будет первая пробежка"
        if self.kind == JUBILEE_HERE:
            return f"Будет {self.number}-я пробежка здесь"
        if self.kind == JUBILEE_PLATFORM:
            return f"Будет {self.number}-я пробежка в системе {platform_title(self.platform_code)}"
        return f"Будет {self.number}-я пробежка"

    def payload(self) -> dict[str, str]:
        return {"kind": self.kind, "label": self.short_label()}


@dataclass(frozen=True)
class PersonStats:
    """Цифры человека к одной дате плана."""

    runs: int | None
    # None — привязок с участником нет: «0 здесь» было бы неправдой.
    runs_here: int | None
    first_here: bool
    # Юбилей «всего» — только в горизонте (JUBILEE_HORIZON_STARTS).
    jubilee: int | None
    # Знаковый старт этой даты — столбец списка гостей.
    marks: tuple[Mark, ...] = ()

    def organizer(self) -> dict[str, Any]:
        """Поля человека в будущем дне оргкалендаря."""
        return {"runs": self.runs, "first_here": self.first_here, "jubilee": self.jubilee}


def identity_visits(db: Session, location_ids: list[UUID], user_ids: list[UUID]) -> dict[UUID, IdentityVisits]:
    """user_id → пробежки и волонтёрства на строках идентичности: дни, первый и
    последний визит, лучшее время. Два group by на всех сразу.

    Человек ↔ протокол — через participant_id привязки, как в детализации
    локаций профиля (build_user_unique_location_details).
    """
    ids = list({user_id for user_id in user_ids if user_id is not None})
    if not ids or not location_ids:
        return {}
    result: dict[UUID, IdentityVisits] = {}
    runs = (
        db.query(
            PlatformLink.user_id,
            func.count(func.distinct(Event.event_date)),
            func.min(Event.event_date),
            func.max(Event.event_date),
            func.min(RunResult.finish_time_sec).filter(RunResult.finish_time_sec > 0),
        )
        .select_from(RunResult)
        .join(Event, RunResult.event_id == Event.id)
        .join(PlatformLink, PlatformLink.participant_id == RunResult.participant_id)
        .filter(
            PlatformLink.user_id.in_(ids),
            Event.location_id.in_(location_ids),
            Event.is_test_event.is_(False),
            Event.event_date > _EPOCH_GUARD,
        )
        .group_by(PlatformLink.user_id)
        .all()
    )
    for user_id, days, first, last, best in runs:
        visits = result.setdefault(user_id, IdentityVisits())
        visits.run_days = int(days or 0)
        visits.best_sec = int(best) if best is not None else None
        visits.visit(first, last)
    volunteering = (
        db.query(
            PlatformLink.user_id,
            func.count(func.distinct(Event.event_date)),
            func.min(Event.event_date),
            func.max(Event.event_date),
        )
        .select_from(VolunteerResult)
        .join(Event, VolunteerResult.event_id == Event.id)
        .join(PlatformLink, PlatformLink.participant_id == VolunteerResult.participant_id)
        .filter(
            PlatformLink.user_id.in_(ids),
            Event.location_id.in_(location_ids),
            Event.is_test_event.is_(False),
            Event.event_date > _EPOCH_GUARD,
        )
        .group_by(PlatformLink.user_id)
        .all()
    )
    for user_id, days, first, last in volunteering:
        visits = result.setdefault(user_id, IdentityVisits())
        visits.volunteer_days = int(days or 0)
        visits.visit(first, last)
    return result


@dataclass(frozen=True)
class AheadPlan:
    """Будущий план человека — пробежка, которая ещё может случиться: разовый
    план или вхождение постоянного."""

    # PlannedStart.ref («plan:<uuid>» | «series:<uuid>:<дата>») — ключ
    # gone / settled / visit_codes.
    id: str
    plan_date: date
    location_key: str | None
    # Ключ идентичности сегодня: записанный дрейфует location:→catalog:,
    # поэтому — по опорной строке, а без неё — записанный.
    key: str | None
    location_id: UUID | None
    platform_code: str | None


@dataclass
class PlansAhead:
    """Будущие планы человека (где угодно), которые ещё могут сбыться, по
    порядку дат. today — человек в сегодняшнем протоколе: True — пробежал,
    False — только волонтёрил, None — сегодняшних строк нет."""

    plans: list[AheadPlan] = field(default_factory=list)
    today: bool | None = None
    # План → система, которой ляжет его пробежка (_route_visits); кого здесь
    # нет — система старта, записанная в плане.
    visit_codes: dict[str, str | None] = field(default_factory=dict)

    def here(self, identity_key: str, location_ids: set[UUID]) -> list[AheadPlan]:
        """Планы на эту локацию — как их находит _identity_plans_filter."""
        return [plan for plan in self.plans if plan.location_key == identity_key or plan.location_id in location_ids]

    def _in_system(self, plan: AheadPlan, platform_code: str | None) -> bool:
        return platform_code is None or self.visit_codes.get(plan.id, plan.platform_code) == platform_code

    def earlier(self, on_date: date, platform_code: str | None = None) -> int:
        """Сколько пробежек до on_date добавят планы: каждый план — пробежка,
        даже если их несколько в один день."""
        return sum(1 for plan in self.plans if plan.plan_date < on_date and self._in_system(plan, platform_code))

    def alone_on(self, on_date: date, platform_code: str | None = None) -> bool:
        """План на on_date у человека один. Два-три старта особого дня идут в
        неизвестном порядке: какой из них станет юбилейным, не сказать, — и
        «будет N-я» не получает ни один."""
        return sum(1 for plan in self.plans if plan.plan_date == on_date and self._in_system(plan, platform_code)) <= 1


def _plans_that_will_not_happen(db: Session, plans: list[AheadPlan], today: date) -> set[str]:
    """Планы, пробежки которых не будет при любом раскладе: старт отменён,
    даты больше нет в календаре или локация ушла на паузу. Отмена у систем —
    только «ближайший старт» (cancelled_start_date — не позже ближайшей
    субботы), поэтому дальние планы её не проверяют; суббота в календаре есть
    всегда, пока локация проводит старты, особая дата — пока её не убрал админ.

    Пауза — как у напоминания (plan_notification_service._still_on): локация
    ушла на паузу или стала «скоро открытие» после того, как человек отметил
    дату, и планируемых строк у неё не осталось — календарь пуст. Строки
    идентичности смотрим только у плана на строку с таким флагом: у точки
    может остаться живое зеркало другой системы.

    Дёшево для блока локации: календарь идентичности (start_dates) считается
    только у плана на отменённой локации или на особую дату, которую
    переназначили на другие системы, — остальным хватает трёх запросов.
    """
    from app.services.run_plan_service import _Identities, canonical_identity_keys

    if not plans:
        return set()
    gone: set[str] = set()
    plan_location_ids = list({plan.location_id for plan in plans if plan.location_id is not None})
    flagged = (
        {
            location_id
            for (location_id,) in db.query(Location.id).filter(
                Location.id.in_(plan_location_ids),
                Location.is_paused.is_(True),
            )
        }
        if plan_location_ids
        else set()
    )
    paused = [plan for plan in plans if plan.location_id in flagged]
    specials = {
        item.start_date: set(item.platforms or [])
        for item in special_dates(db, today, max(plan.plan_date for plan in plans))
    }
    to_check: list[AheadPlan] = []
    for plan in plans:
        if plan.plan_date.weekday() == 5:  # суббота в календаре всегда
            continue
        platforms = specials.get(plan.plan_date)
        if platforms is None:
            gone.add(plan.id)
        elif plan.platform_code and plan.platform_code not in platforms:
            # Дату отдали другим системам — у точки может быть и такая.
            to_check.append(plan)
    near = [plan for plan in plans if plan.id not in gone and plan.plan_date <= _next_saturday(today)]
    if near:
        cancelled_ids = [
            location_id for (location_id,) in db.query(Location.id).filter(Location.is_cancelled.is_(True))
        ]
        if cancelled_ids:
            cancelled_keys = set(canonical_identity_keys(db, cancelled_ids).values())
            queued = {plan.id for plan in to_check}
            to_check.extend(plan for plan in near if plan.key in cancelled_keys and plan.id not in queued)
    if not to_check and not paused:
        return gone
    identities = _Identities(db, today)
    for plan in paused:
        identity = identities.get(plan.key)
        if identity is not None and not identity.plannable:
            gone.add(plan.id)
    for plan in to_check:
        if plan.id in gone:
            continue
        identity = identities.get(plan.key)
        if identity is None or not identity.info_rows:
            continue
        start = identities.start_for(identity, plan.plan_date)
        if start is None or start.cancelled:
            gone.add(plan.id)
    return gone


def _in_protocol_on(db: Session, user_ids: list[UUID], on_date: date) -> dict[UUID, tuple[bool, set[UUID]]]:
    """Кто уже есть в протоколах дня on_date: user_id → (пробежал ли — False,
    если только волонтёрил; строки локаций, где он в протоколе). Любая
    система, без тестовых стартов; нет в ответе — строк этого дня у человека нет."""
    ids = list({user_id for user_id in user_ids if user_id is not None})
    if not ids:
        return {}

    def present(model: type[RunResult] | type[VolunteerResult]) -> dict[UUID, set[UUID]]:
        found: dict[UUID, set[UUID]] = defaultdict(set)
        for user_id, location_id in (
            db.query(PlatformLink.user_id, Event.location_id)
            .join(model, model.participant_id == PlatformLink.participant_id)
            .join(Event, model.event_id == Event.id)
            .filter(PlatformLink.user_id.in_(ids), Event.event_date == on_date, Event.is_test_event.is_(False))
            .distinct()
        ):
            found[user_id].add(location_id)
        return found

    ran = present(RunResult)
    helped = present(VolunteerResult)
    return {
        user_id: (user_id in ran, ran.get(user_id, set()) | helped.get(user_id, set()))
        for user_id in set(ran) | set(helped)
    }


def _settled_today(
    db: Session,
    todays: dict[UUID, list[AheadPlan]],
    present: dict[UUID, tuple[bool, set[UUID]]],
    today: date,
) -> set[str]:
    """Сегодняшние планы, которые уже не впереди.

    Протокол локации плана загружен — старт прошёл, с человеком или без него
    (так же прошедшим его считает горизонт юбилеев, jubilee_horizon). Без
    него — не пришёл, и план не сбылся: иначе до полуночи он прибавлял бы
    пробежку всем следующим датам, а сегодняшняя строка писала бы «впервые у
    нас» о прошедшем старте.

    Человек в сегодняшнем протоколе: в обычный день старт у него один — визит
    дня уже в счёте, пробежал ли он по плану или передумал и побежал в другом
    месте. В особый день стартов бывает два-три (01.01), и план снимается
    поштучно: человек есть в протоколе этой локации или её протокол уже
    загружен. Протокола локации ещё нет — её старт впереди: утром пробежал в
    одном месте, днём собирается в другое.
    """
    from app.services.run_plan_service import canonical_identity_keys

    special = bool(special_dates(db, today, today))
    seen = (
        canonical_identity_keys(
            db, list({location_id for _ran, locations in present.values() for location_id in locations})
        )
        if special
        else {}
    )
    started: dict[str, bool] = {}

    def protocol_loaded(key: str) -> bool:
        if key not in started:
            started[key] = bool(_had_start(db, [location.id for location, _code in identity_rows(db, key)], [today]))
        return started[key]

    settled: set[str] = set()
    for user_id, plans in todays.items():
        if user_id in present and not special:
            settled.update(plan.id for plan in plans)
            continue
        _ran, locations = present.get(user_id, (False, set()))
        visited = {seen.get(location_id, f"location:{location_id}") for location_id in locations}
        for plan in plans:
            if plan.key is not None and (plan.key in visited or protocol_loaded(plan.key)):
                settled.add(plan.id)
    return settled


def plans_ahead(
    db: Session, user_ids: list[UUID], *, today: date, until: date | None = None
) -> dict[UUID, PlansAhead]:
    """user_id → будущие планы, которые ещё могут сбыться, — для поправки
    «этот визит не следующий». Разовые и вхождения постоянных (planned_starts).

    until — до какой даты смотреть (по умолчанию — горизонт календаря):
    поправка нужна только до последней даты, о которой спрашивают, а у
    постоянного на год — 52 вхождения.

    Не впереди: план на отменённый старт, на дату, которой нет в календаре, и
    на локацию, ушедшую на паузу (_plans_that_will_not_happen), и сегодняшний,
    чей старт прошёл или который уже лёг в протокол (_settled_today).

    Только публичные планы (entry_public): цифры видит организатор, а
    приватный план — «видите только вы». Иначе поездка в другую локацию,
    отмеченная приватно, сдвигала бы «будет N-я», «впервые у нас» и «что даст
    визит» и выдавала бы себя (ревью 27.10.2026, PRIV-7).
    """
    from app.services.run_plan_service import entry_public
    from app.services.start_calendar_service import PLANNER_HORIZON_DAYS

    ids = list({user_id for user_id in user_ids if user_id is not None})
    if not ids:
        return {}
    until = until or today + timedelta(days=PLANNER_HORIZON_DAYS)
    rows: list[tuple[UUID, AheadPlan]] = []
    seen: set[tuple[UUID, date, str | None]] = set()
    for entry in planned_starts(db, today=today, date_from=today, date_to=max(today, until), user_ids=ids):
        if not entry_public(entry):
            continue
        # Дрейф ключа оставляет две строки одной точки на дату — пробежка одна.
        if (entry.user_id, entry.plan_date, entry.key) in seen:
            continue
        seen.add((entry.user_id, entry.plan_date, entry.key))
        plan = AheadPlan(
            id=entry.ref,
            plan_date=entry.plan_date,
            location_key=entry.location_key,
            key=entry.key,
            location_id=entry.location_id,
            platform_code=entry.platform_code,
        )
        rows.append((entry.user_id, plan))
    gone = _plans_that_will_not_happen(db, [plan for _user_id, plan in rows], today)
    result: dict[UUID, PlansAhead] = {}
    for user_id, plan in rows:
        if plan.id not in gone:
            result.setdefault(user_id, PlansAhead()).plans.append(plan)
    todays = {user_id: [plan for plan in item.plans if plan.plan_date == today] for user_id, item in result.items()}
    todays = {user_id: plans for user_id, plans in todays.items() if plans}
    if todays:
        present = _in_protocol_on(db, list(todays), today)
        settled = _settled_today(db, todays, present, today)
        for user_id in todays:
            item = result[user_id]
            if user_id in present:
                item.today = present[user_id][0]
            item.plans = [plan for plan in item.plans if plan.id not in settled]
    return result


def _numbered_marks(
    *,
    on_date: date,
    runs: int,
    runs_here: int,
    ahead: PlansAhead,
    platform_code: str | None,
    system_runs: int | None,
) -> list[Mark]:
    """Нумерованные знаковые старты одной даты — по правилу «Календаря
    юбилеев» (is_milestone): первая пробежка вообще, юбилей всего, здесь и в
    системе, которой ляжет пробежка.

    runs — всего пробежек (уже с сегодняшней), runs_here — пробежек здесь
    вместе с более ранними планами сюда, system_runs — финиши в системе
    визита (None — привязки к ней нет). Номер — следующий по счёту, если
    сбудутся более ранние планы (ahead): поправка — планы, а не «каждую
    субботу». Несколько планов в один день — их порядок не известен, и номер
    «всего» (и в системе) не ставится ни на один; сюда план на день один —
    номер здесь известен и в особый день. Совпал с «всего» — второй раз не
    пишем: человек бегает в одной системе или только здесь, «100-я пробежка»
    говорит то же.
    """
    marks: list[Mark] = []
    shown_total: int | None = None
    if ahead.alone_on(on_date):
        number = runs + 1 + ahead.earlier(on_date)
        if number == 1:
            marks.append(Mark(FIRST_EVER, 1))
            shown_total = 1
        elif is_milestone(number):
            marks.append(Mark(JUBILEE, number))
            shown_total = number
    here_number = runs_here + 1
    if is_milestone(here_number) and here_number != shown_total:
        marks.append(Mark(JUBILEE_HERE, here_number))
    # Счётчик системы — как в «Календаре юбилеев»: у каждой системы своя
    # юбилейная арифметика (футболки клубов дают системы, а не сайт).
    if platform_code and system_runs is not None and ahead.alone_on(on_date, platform_code):
        in_system = system_runs + 1 + ahead.earlier(on_date, platform_code)
        if is_milestone(in_system) and in_system != shown_total:
            marks.append(Mark(JUBILEE_PLATFORM, in_system, platform_code))
    return marks


def _jubilee_runs(db: Session, user_ids: list[UUID], totals: dict[UUID, int | None]) -> dict[UUID, int]:
    """user_id → всего пробежек для номера юбилея: большее из числа кабинета
    (totals) и счёта по протоколам. Кэш кабинета отстаёт только вниз — его
    сбрасывает прогрев после синка, а протокол с новой пробежкой ложится
    раньше: 5 вёрст latest идёт до полутора часов (ревью 28.10.2026, TOT-1).
    Без привязок (None в totals) номера нет."""
    ids = [user_id for user_id in dict.fromkeys(user_ids) if totals.get(user_id) is not None]
    counted = total_runs_from_protocols(db, ids)
    return {user_id: max(int(totals[user_id] or 0), counted.get(user_id, 0)) for user_id in ids}


def people_stats(
    db: Session,
    *,
    identity_key: str,
    location_ids: list[UUID],
    entries: list[tuple[UUID, date]],
    today: date | None = None,
    rows: list[tuple[Location, str]] | None = None,
    ahead: dict[UUID, PlansAhead] | None = None,
) -> dict[tuple[UUID, date], PersonStats]:
    """(user_id, дата плана) → цифры и знаковый старт для оргкабинета.

    runs — всего пробежек, как в профиле; None, если у человека нет ни одной
    привязки с участником (тогда и меток нет: про него нечего сказать).
    first_here — ни пробежки, ни волонтёрства на этой локации до даты плана и
    это его первый план сюда; на любую дату. Нумерованные метки (_numbered_marks;
    jubilee — номер «всего» из них) — только на три ближайших старта локации
    (jubilee_horizon). План уже не впереди (сегодняшний старт прошёл, старт
    отменён, локация на паузе) — меток нет. «Первая пробежка» поглощает «впервые у нас»:
    первый старт вообще — и здесь впервые как бегун.

    rows — строки идентичности (без них — identity_rows по ключу): по ним
    календарь горизонта и система визита. ahead — готовые plans_ahead не
    короче последней даты entries (оргкабинет считает их раз на запрос).

    Пакетно: около пятнадцати запросов, сколько бы людей ни было (всего
    пробежек — из Redis, cached_total_runs; счёт по протоколам для номера и
    счётчики систем — только у тех, чья дата в горизонте); в день старта у
    кого план на сегодня — ещё сегодняшний протокол.
    """
    if not entries:
        return {}
    today = today or moscow_today()
    user_ids = list({user_id for user_id, _date in entries})
    totals = cached_total_runs(db, user_ids)
    visits = identity_visits(db, location_ids, user_ids)
    if ahead is None:
        ahead = plans_ahead(db, user_ids, today=today, until=max(on_date for _user_id, on_date in entries))
    rows = rows if rows is not None else identity_rows(db, identity_key)
    plannable, _reason = plannable_rows_with_reason(db, rows, today=today)
    horizon = jubilee_horizon(db, rows, today, plannable=plannable)
    in_horizon = [user_id for user_id, on_date in entries if on_date in horizon and totals.get(user_id) is not None]
    # Номер — и по протоколам у всех, чья дата в горизонте (_jubilee_runs):
    # метка «250-я» не должна ждать прогрева кэша кабинета. Число у имени
    # остаётся из кэша — отстать на минуты ему можно, метке нет.
    counted = _jubilee_runs(db, in_horizon, totals)
    systems = _system_runs(db, in_horizon)
    location_set = set(location_ids)
    plannable_of = _plannable_lookup(
        db, today, identity_key=identity_key, location_ids=location_ids, plannable=plannable
    )
    until = max(horizon, default=today)
    result: dict[tuple[UUID, date], PersonStats] = {}
    for user_id, on_date in entries:
        runs = totals.get(user_id)
        if runs is None:
            # Привязок нет — сказать нечего, «0 здесь» тоже.
            result[(user_id, on_date)] = PersonStats(runs=None, runs_here=None, first_here=False, jubilee=None)
            continue
        mine = visits.get(user_id) or IdentityVisits()
        plans = ahead.get(user_id) or PlansAhead()
        here = plans.here(identity_key, location_set)
        if not any(plan.plan_date == on_date for plan in here):
            # Сегодняшний старт прошёл, старт отменён, локация на паузе или
            # даты нет в календаре — «впервые» и «будет N-я» о нём не предсказание.
            result[(user_id, on_date)] = PersonStats(runs=runs, runs_here=mine.run_days, first_here=False, jubilee=None)
            continue
        been_here = mine.first_date is not None and mine.first_date < on_date
        first_here = not been_here and not any(plan.plan_date < on_date for plan in here)
        marks: list[Mark] = []
        start = horizon.get(on_date)
        if start is not None:
            mine_systems = systems.get(user_id, {})
            platform_code = _visit_platform(start.platform_code, plannable, set(mine_systems))
            _route_visits(plans, set(mine_systems), until=until, plannable_of=plannable_of)
            marks = _numbered_marks(
                on_date=on_date,
                runs=counted.get(user_id, runs),
                runs_here=mine.run_days + len({plan.plan_date for plan in here if plan.plan_date < on_date}),
                ahead=plans,
                platform_code=platform_code,
                system_runs=mine_systems.get(platform_code) if platform_code else None,
            )
        if first_here and not any(mark.kind == FIRST_EVER for mark in marks):
            marks.append(Mark(FIRST_HERE))
        result[(user_id, on_date)] = PersonStats(
            runs=runs,
            runs_here=mine.run_days,
            first_here=first_here,
            jubilee=next((mark.number for mark in marks if mark.kind == JUBILEE), None),
            marks=tuple(marks),
        )
    return result


# ---------------------------------------------------------------- даты и дом


def _start_infos(
    db: Session,
    rows: list[tuple[Location, str]],
    dates: list[date],
    today: date,
    *,
    plannable: list[tuple[Location, str]] | None = None,
) -> dict[date, StartDate]:
    """Старты календаря локации на эти даты — подпись даты, ≈№ и система.

    Локация ушла на паузу — подписываем по строкам систем планировщика, как
    `_Identity.info_rows`: у планов остаётся название особой даты.
    """
    if not dates:
        return {}
    if plannable is None:
        plannable, _reason = plannable_rows_with_reason(db, rows, today=today)
    info_rows = plannable or [row for row in rows if row[1] in PLANNABLE_PLATFORMS]
    if not info_rows:
        return {}
    wanted = set(dates)
    return {
        item.date: item
        for item in start_dates(
            db, info_rows, date_from=min(dates), date_to=max(dates), today=today, history_rows=rows
        )
        if item.date in wanted
    }


def _in_calendar(
    entries: list[PlannedStart],
    starts: dict[date, StartDate],
    rows: list[tuple[Location, str]],
    plannable: list[tuple[Location, str]],
) -> list[PlannedStart]:
    """Планы на даты, что есть в календаре локации (starts — _start_infos на
    их даты). Дату убрал, перенёс или отдал другим системам админ — не план
    (still_on): организатор его не видит, как и страница локации (ревью
    28.10.2026, SD-2). Календаря нет вовсе (строк систем планировщика нет) —
    как есть."""
    if not (plannable or any(code in PLANNABLE_PLATFORMS for _location, code in rows)):
        return entries
    return [entry for entry in entries if entry.plan_date in starts]


def jubilee_horizon(
    db: Session,
    rows: list[tuple[Location, str]],
    today: date,
    *,
    plannable: list[tuple[Location, str]] | None = None,
) -> dict[date, StartDate]:
    """Три ближайших старта календаря локации (date → старт): юбилеи и
    «первая пробежка» ставятся только на них.

    Старт — дата календаря, как в окне «Планирую»: суббота или особая дата
    систем точки (особая дата среди трёх ближайших — тоже старт, и четвёртая
    суббота за горизонт уходит). Отменённый в тройку не входит — пробежки не
    будет; сегодняшний, чей протокол уже загружен, тоже — он прошёл. Строки —
    как у подписи даты (_start_infos): на паузе — строки систем планировщика.
    """
    if plannable is None:
        plannable, _reason = plannable_rows_with_reason(db, rows, today=today)
    info_rows = plannable or [row for row in rows if row[1] in PLANNABLE_PLATFORMS]
    if not info_rows:
        return {}
    # Суббота — каждую неделю: с запасом на отменённый и уже прошедший сегодня
    # старт три найдутся за JUBILEE_HORIZON_STARTS + 2 недели.
    upcoming = [
        item
        for item in start_dates(
            db,
            info_rows,
            date_from=today,
            date_to=today + timedelta(weeks=JUBILEE_HORIZON_STARTS + 2),
            today=today,
            history_rows=rows,
        )
        if not item.cancelled
    ]
    if upcoming and upcoming[0].date == today and _had_start(db, [location.id for location, _code in rows], [today]):
        upcoming = upcoming[1:]
    return {item.date: item for item in upcoming[:JUBILEE_HORIZON_STARTS]}


def _linked_systems(db: Session, user_ids: list[UUID]) -> dict[UUID, set[str]]:
    """user_id → системы, к которым есть привязка с участником, — те же
    ключи, что у _system_runs, без подсчёта финишей: визиту (_visit) нужна
    только привязка."""
    ids = list({user_id for user_id in user_ids if user_id is not None})
    if not ids:
        return {}
    result: dict[UUID, set[str]] = defaultdict(set)
    for user_id, code in (
        db.query(PlatformLink.user_id, Platform.code)
        .join(Platform, Platform.id == PlatformLink.platform_id)
        .filter(PlatformLink.user_id.in_(ids), PlatformLink.participant_id.isnot(None))
        .distinct()
    ):
        result[user_id].add(code)
    return dict(result)


def _system_runs(db: Session, user_ids: list[UUID]) -> dict[UUID, dict[str, int]]:
    """user_id → система → финиши в ней. Системы без привязки с участником в
    словаре нет — её счётчик не известен. Три запроса на всех.

    Как счётчик системы в «Календаре юбилеев» (organizer_service.
    platform_result_counts): без тестовых стартов, только со временем финиша.
    Но вторичная сторона кросслинка выпадает, только если у человека есть и
    первичная, — правило «всего пробежек» (total_runs_from_protocols).
    Зеркало RunPark у точки S95 — всегда вторичная сторона: у человека только
    с RunPark «Календарь» выбросил бы все его финиши там, а визит сюда ляжет
    RunPark-строкой (_visit_platform), и сам RunPark их считает.
    """
    ids = list({user_id for user_id in user_ids if user_id is not None})
    if not ids:
        return {}
    links = (
        db.query(PlatformLink.user_id, Platform.code)
        .join(Platform, Platform.id == PlatformLink.platform_id)
        .filter(PlatformLink.user_id.in_(ids), PlatformLink.participant_id.isnot(None))
        .distinct()
        .all()
    )
    if not links:
        return {}
    result: dict[UUID, dict[str, int]] = defaultdict(dict)
    for user_id, code in links:
        result[user_id][code] = 0

    def finishes(*, duplicates: bool) -> list[tuple[UUID, str, int]]:
        query = (
            db.query(PlatformLink.user_id, Platform.code, func.count(func.distinct(RunResult.event_id)))
            .join(Platform, Platform.id == PlatformLink.platform_id)
            .join(RunResult, RunResult.participant_id == PlatformLink.participant_id)
            .join(Event, RunResult.event_id == Event.id)
            .filter(
                PlatformLink.user_id.in_(ids),
                Event.is_test_event.is_(False),
                RunResult.finish_time_sec.isnot(None),
            )
        )
        if duplicates:
            # Дубль — как в total_runs_from_protocols: строка на вторичном
            # событии кросслинка, если на первичном у человека тоже есть пробежка.
            primary_run = aliased(RunResult)
            primary_link = aliased(PlatformLink)
            has_primary = (
                select(primary_run.id)
                .join(primary_link, primary_link.participant_id == primary_run.participant_id)
                .where(
                    primary_link.user_id == PlatformLink.user_id,
                    primary_run.event_id == EventCrosslink.primary_event_id,
                )
                .exists()
            )
            query = query.join(EventCrosslink, EventCrosslink.secondary_event_id == Event.id).filter(has_primary)
        return query.group_by(PlatformLink.user_id, Platform.code).all()

    for user_id, code, count in finishes(duplicates=False):
        result[user_id][code] = result[user_id].get(code, 0) + int(count or 0)
    for user_id, code, count in finishes(duplicates=True):
        result[user_id][code] = max(0, result[user_id].get(code, 0) - int(count or 0))
    return dict(result)


def _mirror_row(
    plannable: list[tuple[Location, str]], platform_code: str | None, linked: set[str]
) -> tuple[Location, str] | None:
    """Строка точки, которой ляжет пробежка вместо системы старта: к системе
    старта привязки нет, а к другой действующей системе точки есть — зеркало
    RunPark повторяет протокол S95, и у человека только с RunPark финиш придёт
    RunPark-строкой. None — пробежка ляжет системой старта (привязок к
    системам точки нет вовсе — будет и новая система)."""
    if platform_code in linked:
        return None
    return next((row for row in plannable if row[1] in linked), None)


def _visit_platform(platform_code: str | None, plannable: list[tuple[Location, str]], linked: set[str]) -> str | None:
    """Система, которой ляжет пробежка (см. _mirror_row)."""
    row = _mirror_row(plannable, platform_code, linked)
    return row[1] if row is not None else platform_code


def _plannable_lookup(
    db: Session,
    today: date,
    *,
    identity_key: str,
    location_ids: list[UUID],
    plannable: list[tuple[Location, str]],
) -> Callable[[AheadPlan], list[tuple[Location, str]]]:
    """Строки, которые сейчас планируются, у локации плана: у этой — уже
    посчитанные, у остальных — через кэш идентичностей запроса."""
    from app.services.run_plan_service import _Identities

    identities = _Identities(db, today)
    here_key = normalize_identity_key(identity_key)
    here_ids = set(location_ids)

    def lookup(plan: AheadPlan) -> list[tuple[Location, str]]:
        if plan.key == here_key or plan.location_id in here_ids:
            return plannable
        identity = identities.get(plan.key)
        return identity.plannable if identity is not None else []

    return lookup


def _route_visits(
    ahead: PlansAhead,
    linked: set[str],
    *,
    until: date,
    plannable_of: Callable[[AheadPlan], list[tuple[Location, str]]],
) -> None:
    """Система, которой ляжет пробежка каждого плана до until, — то же
    правило, что у самой даты (_visit_platform), для поправки в системе
    (PlansAhead.earlier с системой). План хранит систему старта: у точки S95
    с зеркалом RunPark — s95, а у человека только с RunPark пробежка ляжет
    RunPark-строкой. Без этого ранние планы в RunPark не находились, и «25-я
    в RunPark» стояла на двух датах подряд.

    Строки идентичности нужны только плану, к системе старта которого у
    человека нет привязки: иначе пробежка ляжет ею же.
    """
    for plan in ahead.plans:
        if plan.plan_date > until or plan.id in ahead.visit_codes:
            continue
        if not plan.platform_code or plan.platform_code in linked:
            continue
        ahead.visit_codes[plan.id] = _visit_platform(plan.platform_code, plannable_of(plan), linked)


def _date_head(on_date: date, start: StartDate | None) -> dict[str, Any]:
    from app.services.run_plan_service import _date_fallback

    if start is None:
        fallback = _date_fallback(on_date)
        return {"date": on_date, "kind": fallback["kind"], "title": fallback["title"]}
    return {"date": on_date, "kind": start.kind, "title": start.title}


def _homes(db: Session, user_ids: list[UUID], identity_key: str) -> dict[UUID, dict[str, Any] | None]:
    """user_id → {name, slug, city, here} домашней локации, как её считает
    сайт, через Redis на полчаса. here — дом и есть эта локация: сравниваем
    ключи идентичности, а не слаги — у дома слаг по старшинству систем, у
    страницы — по действующей (Раменское: ramenskoegorodskoypark и ramenskoe).

    Дом без страницы на сайте (нет слага) не отдаём: ссылку из него не
    сделать, а одно имя организатору мало что скажет.
    """
    ids = list(dict.fromkeys(user_id for user_id in user_ids if user_id is not None))
    if not ids:
        return {}
    cached: dict[UUID, dict[str, Any] | None] = _cache_read(_HOME_CACHE_PREFIX, ids)
    missing = [user_id for user_id in ids if user_id not in cached]
    if missing:
        from app.services.admin_home_location_service import resolve_admin_home_locations

        resolved = resolve_admin_home_locations(db, missing)
        fresh: dict[UUID, dict[str, Any] | None] = {}
        for user_id in missing:
            home = resolved.get(user_id)
            fresh[user_id] = (
                {"name": home.name, "slug": home.slug, "city": home.city, "key": home.identity_key}
                if home is not None and home.slug
                else None
            )
        cached.update(fresh)
        _cache_write(_HOME_CACHE_PREFIX, fresh, HOME_CACHE_TTL_SECONDS)
    here_key = normalize_identity_key(identity_key)
    return {
        user_id: (
            {
                "name": home["name"],
                "slug": home["slug"],
                "city": home["city"],
                "here": normalize_identity_key(home.get("key")) == here_key,
            }
            if home is not None
            else None
        )
        for user_id, home in cached.items()
    }


# ---------------------------------------------------------------- «Кто у нас бежит»


def _guest_row(person: dict[str, Any], stats: PersonStats | None, home: dict[str, Any] | None) -> dict[str, Any]:
    stats = stats or PersonStats(runs=None, runs_here=None, first_here=False, jubilee=None)
    return {
        "name": person["name"],
        "handle": person["handle"],
        "runs": stats.runs,
        "runs_here": stats.runs_here,
        "first_here": stats.first_here,
        "jubilee": stats.jubilee,
        "marks": [mark.payload() for mark in stats.marks],
        "home": home,
    }


def organizer_guests(db: Session, identity: Any, weeks: int = GUESTS_WEEKS_DEFAULT) -> dict[str, Any]:
    """«Кто у нас бежит»: будущие даты с планами на weeks недель вперёд.

    Публичные — строками таблицы с цифрами, домом и знаковым стартом (marks),
    больше пробежек — выше; приватные и дети — числом, как в блоке локации:
    имени и цифр приватного не видит и организатор.

    Постоянные — своей группой с теми же столбцами (решение Дмитрия по Q05):
    состав один раз, без меток (`regulars_people`, порядок — больше пробежек
    выше), а у даты — кто из них в этот день (`regulars.handles`; «Пропущу» и
    поездки вычтены), их число, приватные, дети и метки — только у дат
    горизонта юбилеев: дальше трёх ближайших стартов меток нет ни у кого.
    """
    from app.services.run_plan_service import _people_by_date, _plan_users

    today = moscow_today()
    weeks = max(1, min(GUESTS_WEEKS_MAX, weeks))
    rows = list(identity.locations)
    location_ids = [location.id for location, _code in rows]
    entries = planned_starts(
        db,
        today=today,
        date_from=today,
        date_to=today + timedelta(weeks=weeks),
        identity=(identity.identity_key, location_ids),
    )
    plannable, _reason = plannable_rows_with_reason(db, rows, today=today)
    starts = _start_infos(db, rows, sorted({entry.plan_date for entry in entries}), today, plannable=plannable)
    entries = _in_calendar(entries, starts, rows, plannable)
    horizon = jubilee_horizon(db, rows, today, plannable=plannable)
    users = _plan_users(db, entries)
    # Будущие планы — раз на запрос: и для меток, и для «Челленджей». Шире,
    # чем нужно меткам (у приватных), но это один и тот же запрос.
    ahead = (
        plans_ahead(db, [user.id for _entry, user in users], today=today, until=max(entry.plan_date for entry in entries))
        if entries
        else {}
    )
    grouped = _people_by_date(
        db,
        users,
        identity_key=identity.identity_key,
        location_ids=location_ids,
        today=today,
        location_rows=rows,
        marks=True,
        regular_stats_dates=set(horizon),
        ahead=ahead,
    )
    homes = _homes(
        db,
        list(
            {
                user.id
                for day in grouped.values()
                for bucket in (day["one_off"], day["regulars"])
                for user, _stats in bucket["shown"]
            }
        ),
        identity.identity_key,
    )
    # «Челленджи» — на три ближайших старта, как юбилеи, и у тех, у кого
    # есть привязки: без них сказать нечего.
    gains = guest_gains(
        db,
        identity_key=identity.identity_key,
        rows=rows,
        plannable=plannable,
        starts=horizon,
        pairs=[
            (user.id, on_date)
            for on_date, day in grouped.items()
            for bucket in (day["one_off"], day["regulars"])
            for user, stats in bucket["shown"]
            if stats is not None and stats.runs is not None
        ],
        today=today,
        catalog_index=getattr(identity, "catalog_index", None),
        ahead=ahead,
    )
    regulars_people: dict[UUID, dict[str, Any]] = {}
    regulars_order: dict[UUID, tuple[bool, int]] = {}
    dates_payload = []
    for on_date in sorted(grouped):
        day = grouped[on_date]
        one_off = day["one_off"]
        regulars = day["regulars"]
        people = [
            {**_guest_row(person, stats, homes.get(user.id)), "gains": gains.get((user.id, on_date), [])}
            for (user, stats), person in zip(one_off["shown"], one_off["people"], strict=True)
        ]
        handles: list[str] = []
        marks: dict[str, list[dict[str, str]]] = {}
        regular_gains: dict[str, list[dict[str, str]]] = {}
        for (user, stats), person in zip(regulars["shown"], regulars["people"], strict=True):
            handle = person["handle"] or ""
            handles.append(handle)
            if user.id not in regulars_people:
                # Состав — без меток: метки у дат, и только в горизонте.
                bare = replace(stats, first_here=False, jubilee=None, marks=()) if stats is not None else None
                regulars_people[user.id] = _guest_row(person, bare, homes.get(user.id))
                regulars_order[user.id] = (stats.runs is None if stats else True, -((stats.runs or 0) if stats else 0))
            if on_date in horizon and stats is not None and stats.marks:
                marks[handle] = [mark.payload() for mark in stats.marks]
            if (user.id, on_date) in gains:
                regular_gains[handle] = gains[(user.id, on_date)]
        dates_payload.append(
            {
                **_date_head(on_date, starts.get(on_date)),
                "total": day["total"],
                "private": one_off["private"],
                "children": one_off["children"],
                "people": people,
                "regulars": {
                    "total": regulars["total"],
                    "private": regulars["private"],
                    "children": regulars["children"],
                    "handles": handles,
                    "marks": marks,
                    "gains": regular_gains,
                },
            }
        )
    ordered = sorted(regulars_people, key=lambda user_id: regulars_order[user_id])
    return {
        "location": {"slug": identity.slug, "name": identity.name},
        "weeks": weeks,
        "dates": dates_payload,
        "regulars_people": [regulars_people[user_id] for user_id in ordered],
    }


class GuestNotFound(Exception):
    """Нет такого гостя: человека нет, его план не публичен или планов сюда нет."""


def _jubilee_labels(
    *,
    runs: int | None,
    runs_here: int,
    planned: list[tuple[date, str | None]],
    ahead: PlansAhead,
    horizon: dict[date, StartDate],
    systems: dict[str, int],
) -> list[dict[str, str]]:
    """Юбилеи запланированных пробежек сюда — те же метки, что столбец
    «Знаковый старт» списка (_numbered_marks), и тоже только в горизонте.

    planned — (дата, система, которой ляжет пробежка) каждой даты сюда, что
    ещё впереди, по порядку; ahead — все такие планы человека, где угодно;
    runs — всего пробежек, уже с сегодняшней; systems — финиши по системам
    (_system_runs). Пробежка — следующая по счёту, если сбудутся более ранние
    планы: так же, как в people_stats. Поэтому юбилей бывает и не у ближайшей
    даты: в списке «Кто у нас бежит» метка «250-я пробежка» стоит на второй
    субботе, и страница участника обязана сказать то же. Дат несколько — у
    юбилея подписана дата.
    """
    if runs is None or not planned:
        return []
    from app.services.start_calendar_service import day_label

    labels: list[dict[str, str]] = []
    for index, (on_date, platform_code) in enumerate(planned):
        if on_date not in horizon:
            continue
        suffix = f" — {day_label(on_date)}" if len(planned) > 1 else ""
        for mark in _numbered_marks(
            on_date=on_date,
            runs=runs,
            runs_here=runs_here + index,
            ahead=ahead,
            platform_code=platform_code,
            system_runs=systems.get(platform_code) if platform_code else None,
        ):
            labels.append({"label": f"{mark.card_label()}{suffix}"})
    return labels


@dataclass(frozen=True)
class _Visit:
    """Запланированная пробежка так, как она ляжет в протокол человека."""

    location: Location
    platform_code: str
    number: int | None


def _visit(
    db: Session,
    *,
    rows: list[tuple[Location, str]],
    plannable: list[tuple[Location, str]],
    start: StartDate | None,
    plan: AheadPlan,
    linked: set[str],
    today: date,
) -> _Visit | None:
    """Строка идентичности, которой ляжет пробежка человека в дату плана.

    Строка старта календаря — если к её системе есть привязка. Нет — строка
    действующей системы точки, к которой привязка есть (_mirror_row), со своим
    номером, без «новой системы».
    """
    platform_code = (start.platform_code if start else None) or plan.platform_code
    location_id = (start.location_id if start else None) or plan.location_id
    number = start.number_forecast if start else None
    other = _mirror_row(plannable, platform_code, linked)
    if other is not None:
        forecast = start_dates(
            db, [other], date_from=plan.plan_date, date_to=plan.plan_date, today=today, history_rows=rows
        )
        return _Visit(
            location=other[0],
            platform_code=other[1],
            number=forecast[0].number_forecast if forecast else None,
        )
    location = next((row for row, _code in rows if row.id == location_id), rows[0][0] if rows else None)
    if location is None or not platform_code:
        return None
    return _Visit(location=location, platform_code=platform_code, number=number)


def _earlier_visits(
    db: Session,
    plans: list[AheadPlan],
    *,
    linked: set[str],
    today: date,
    identities: Any = None,
    memo: dict[tuple[Any, ...], tuple[Any, StartDate | None]] | None = None,
) -> list[tuple[Location, str, int | None]]:
    """Более ранние будущие визиты человека (где угодно) — для «что даст
    визит» как уже случившиеся: если сбудется план в новой локации на
    прошлой неделе, эта станет уже не второй, а третьей.

    identities и memo — общие на запрос, когда визиты считаются для многих
    людей сразу (столбец «Челленджи»): у соседей по таблице планы на одни и
    те же старты."""
    from app.services.run_plan_service import _Identities

    identities = identities if identities is not None else _Identities(db, today)
    result: list[tuple[Location, str, int | None]] = []
    for plan in plans:
        # Точка и её старт в дату — общие у всех, кто туда собирается; визит
        # от привязок человека зависит, но без зеркала (_mirror_row) не ходит в базу.
        key = (plan.key, plan.plan_date)
        if memo is not None and key in memo:
            identity, start = memo[key]
        else:
            identity = identities.get(plan.key)
            start = identities.start_for(identity, plan.plan_date) if identity is not None and identity.info_rows else None
            if memo is not None:
                memo[key] = (identity, start)
        if identity is None:
            continue
        visit = _visit(
            db, rows=identity.rows, plannable=identity.plannable, start=start, plan=plan, linked=linked, today=today
        )
        if visit is not None:
            result.append((visit.location, visit.platform_code, visit.number))
    return result


def guest_gains(
    db: Session,
    *,
    identity_key: str,
    rows: list[tuple[Location, str]],
    plannable: list[tuple[Location, str]],
    starts: dict[date, StartDate],
    pairs: list[tuple[UUID, date]],
    today: date,
    catalog_index: Any = None,
    ahead: dict[UUID, PlansAhead] | None = None,
) -> dict[tuple[UUID, date], list[dict[str, str]]]:
    """(user_id, дата) → что даст этот старт по челленджам — столбец
    «Челленджи» в «Кто у нас бежит» (решение Дмитрия 27.10.2026). Тот же
    расчёт, что «Что даст визит» на странице участника: визит — строкой,
    которой ляжет пробежка (_visit), более ранние ПУБЛИЧНЫЕ планы человека —
    уже случившимися (_earlier_visits). На ближайшей дате человека ответ тот
    же, что у карточки.

    starts — старты, на которые считать (date → старт календаря); остальные
    даты молчат. «Кто у нас бежит» отдаёт горизонт юбилеев — три ближайших
    старта, как «Знаковый старт»: ответ должен укладываться в 1,5 с
    холодным, а на двенадцати неделях Парк Горького (сорок человек на 31.10)
    выходил на самую границу — ранние планы туристов в других локациях
    тянут календари этих локаций (замер 27.10.2026: 1,35–1,45 с против
    ~1,5 с). Пакетно: история всех людей — одним запросом-сводкой
    (visit_histories), а не сбором челленджей на каждого; без привязок
    (pairs у таких не бывает) сказать нечего.

    Строка ответа — {label, hint}: label — пара слов в столбце, hint —
    полный текст в подсказке.
    """
    from app.services.achievements_service import VisitHistory, alphabet_letters, visit_gains, visit_histories
    from app.services.location_catalog_service import LocationCatalogIndex
    from app.services.run_plan_service import _Identities

    wanted = [(user_id, on_date) for user_id, on_date in dict.fromkeys(pairs) if on_date in starts]
    if not wanted:
        return {}
    user_ids = list(dict.fromkeys(user_id for user_id, _date in wanted))
    if ahead is None:
        ahead = plans_ahead(db, user_ids, today=today, until=max(on_date for _user_id, on_date in wanted))
    systems = _linked_systems(db, user_ids)
    catalog_index = catalog_index or LocationCatalogIndex(db)
    histories = visit_histories(db, user_ids, catalog_index=catalog_index)
    alphabet = alphabet_letters(db)
    identities = _Identities(db, today)
    memo: dict[tuple[Any, ...], tuple[Any, StartDate | None]] = {}
    location_set = {location.id for location, _code in rows}
    here_key = normalize_identity_key(identity_key)
    result: dict[tuple[UUID, date], list[dict[str, str]]] = {}
    for user_id, on_date in wanted:
        plans = ahead.get(user_id) or PlansAhead()
        here = next(
            (
                plan
                for plan in plans.plans
                if plan.plan_date == on_date and (plan.key == here_key or plan.location_id in location_set)
            ),
            None,
        )
        if here is None:
            # Старт уже не впереди (прошёл сегодня, отменён) — не предсказание.
            continue
        linked = systems.get(user_id, set())
        visit = _visit(
            db, rows=rows, plannable=plannable, start=starts.get(on_date), plan=here, linked=linked, today=today
        )
        if visit is None:
            continue
        earlier = _earlier_visits(
            db,
            [plan for plan in plans.plans if plan.plan_date < on_date],
            linked=linked,
            today=today,
            identities=identities,
            memo=memo,
        )
        found = visit_gains(
            histories.get(user_id) or VisitHistory(),
            location=visit.location,
            platform_code=visit.platform_code,
            number=visit.number,
            catalog_index=catalog_index,
            earlier=earlier,
            alphabet=alphabet,
            alone=plans.alone_on(on_date),
        )
        # Номерные челленджи одного визита — одной строкой в столбце (у них
        # одна подпись, number_gain_rows): строки с одной подписью сливаются,
        # подсказки — списком.
        labels: dict[str, list[str]] = {}
        for gain in found:
            labels.setdefault(gain["short"], []).append(gain.get("hint") or f"{gain['title']} — {gain['text']}")
        if labels:
            result[(user_id, on_date)] = [
                {"label": label, "hint": "\n".join(hints)} for label, hints in labels.items()
            ]
    return result


def organizer_guest_card(db: Session, identity: Any, handle: str) -> dict[str, Any]:
    """Страница участника в «Кто у нас бежит».

    Открывается только на человека с будущим ПУБЛИЧНЫМ планом сюда: иначе
    организатор смотрел бы так кого угодно — GuestNotFound (404), один ответ
    на «нет человека», «план приватный», «ребёнок» и «закрытый профиль».
    Даты карточки — только публичные планы: приватная суббота того же
    человека организатору не видна и здесь (решение Дмитрия 27.10.2026 —
    публичность у каждого плана своя).
    """
    from app.services.profile_slug_service import resolve_profile_handle
    from app.services.run_plan_service import SHOWN_NAME, _person, plan_audience
    from app.services.start_calendar_service import PLANNER_HORIZON_DAYS

    user = resolve_profile_handle(db, handle)
    if user is None:
        raise GuestNotFound
    today = moscow_today()
    rows = list(identity.locations)
    location_ids = [location.id for location, _code in rows]
    plans = planned_starts(
        db,
        today=today,
        date_from=today,
        date_to=today + timedelta(days=PLANNER_HORIZON_DAYS),
        user_ids=[user.id],
        identity=(identity.identity_key, location_ids),
    )
    audience = plan_audience(db, [(plan, user) for plan in plans])
    plans = [plan for plan in plans if audience.get(plan.ref) == SHOWN_NAME]
    plannable, _reason = plannable_rows_with_reason(db, rows, today=today)
    starts = _start_infos(db, rows, sorted({plan.plan_date for plan in plans}), today, plannable=plannable)
    plans = _in_calendar(plans, starts, rows, plannable)
    if not plans:
        raise GuestNotFound

    # Разовые даты — все, постоянный план — три ближайших вхождения: дальше
    # юбилеев и «что даст визит» всё равно нет, а 52 субботы — не список.
    one_off, regular = split_by_regular(plans)
    regular_dates = sorted({plan.plan_date for plan in regular})[:JUBILEE_HORIZON_STARTS]
    dates = sorted({plan.plan_date for plan in one_off} | set(regular_dates))
    planned_set = set(dates)
    series = next((plan.series for plan in regular if plan.series is not None), None)
    horizon = jubilee_horizon(db, rows, today, plannable=plannable)

    # Три плитки профиля — те же числа, что он покажет: кэш кабинета, без него —
    # те же функции подсчёта (сам кабинет у бегуна со ста локациями — секунды).
    headline = cabinet_headline_stats(db, user.id, catalog_index=getattr(identity, "catalog_index", None))
    runs = headline["total_runs"] if headline is not None else None
    remember_total_runs({user.id: runs})
    visits = identity_visits(db, location_ids, [user.id]).get(user.id) or IdentityVisits()

    # Что впереди — как в people_stats: прошедший сегодняшний старт,
    # отменённый, пропавшая из календаря дата и локация на паузе — не впереди.
    ahead = plans_ahead(db, [user.id], today=today, until=max(dates)).get(user.id) or PlansAhead()
    # Номер юбилея — как в people_stats: кэш кабинета до прогрева после синка
    # ещё без сегодняшней пробежки.
    jubilee_runs = _jubilee_runs(db, [user.id], {user.id: runs}).get(user.id, runs)
    here_ahead: list[AheadPlan] = []
    for plan in ahead.here(identity.identity_key, set(location_ids)):
        if plan.plan_date not in planned_set:
            continue
        if not here_ahead or here_ahead[-1].plan_date != plan.plan_date:
            here_ahead.append(plan)
    first_here = (
        runs is not None
        and bool(here_ahead)
        and (visits.first_date is None or visits.first_date >= here_ahead[0].plan_date)
    )
    systems = _system_runs(db, [user.id]).get(user.id, {})
    linked = set(systems)
    _route_visits(
        ahead,
        linked,
        until=max(horizon, default=today),
        plannable_of=_plannable_lookup(
            db, today, identity_key=identity.identity_key, location_ids=location_ids, plannable=plannable
        ),
    )
    here_visits = [
        _visit(
            db,
            rows=rows,
            plannable=plannable,
            start=starts.get(plan.plan_date),
            plan=plan,
            linked=linked,
            today=today,
        )
        for plan in here_ahead
    ]
    # Ближайший визит — первый, что ещё впереди: сегодняшний, уже лёгший в
    # протокол, ничего нового не даст, а у следующего свой ≈№.
    nearest = here_ahead[0] if here_ahead else None
    nearest_visit = here_visits[0] if here_visits else None
    challenges: list[dict[str, str]] = []
    challenges_note: str | None = None
    later_visits: list[dict[str, Any]] = []
    if runs is not None and nearest is not None and nearest_visit is not None:
        from app.services.achievements_service import PlannedVisit, planned_visit_gains
        from app.services.run_plan_service import _Identities

        # Ближайший визит — всегда (новая локация и буква — не прогноз), и
        # следующие даты сюда в горизонте юбилеев: у каждой в «Кто у нас
        # бежит» своя строка в «Челленджах», и страница, открытая из второй
        # субботы, должна сказать то же. Номерные челленджи — только в
        # горизонте: дальше ≈№ не прогноз, а надежда.
        chosen = [(nearest, nearest_visit)] + [
            (plan, visit)
            for plan, visit in zip(here_ahead[1:], here_visits[1:], strict=True)
            if visit is not None and plan.plan_date in horizon
        ]
        identities = _Identities(db, today)
        memo: dict[tuple[Any, ...], tuple[Any, StartDate | None]] = {}
        found = planned_visit_gains(
            db,
            user.id,
            [
                PlannedVisit(
                    location=visit.location,
                    platform_code=visit.platform_code,
                    number=visit.number if plan.plan_date in horizon else None,
                    earlier=tuple(
                        _earlier_visits(
                            db,
                            [earlier for earlier in ahead.plans if earlier.plan_date < plan.plan_date],
                            linked=linked,
                            today=today,
                            identities=identities,
                            memo=memo,
                        )
                    ),
                    alone=ahead.alone_on(plan.plan_date),
                )
                for plan, visit in chosen
            ],
            catalog_index=getattr(identity, "catalog_index", None),
        )
        challenges = found[0]
        if nearest.plan_date not in horizon and nearest_visit.number is not None:
            challenges_note = NUMBERS_HORIZON_NOTE
        later_visits = [
            {"date": plan.plan_date, "challenges": items}
            for (plan, _visit), items in zip(chosen[1:], found[1:], strict=True)
            if items
        ]
    return {
        "location": {"slug": identity.slug, "name": identity.name},
        "person": _person(user),
        "planned_dates": [_date_head(on_date, starts.get(on_date)) for on_date in dates],
        # Постоянный план сюда: «↻ Каждую субботу с 31 октября» над датами.
        "series": {"starts_on": series.starts_on, "until": series.until} if series is not None else None,
        "stats": {
            "runs": runs,
            "volunteering": headline["total_volunteering"] if headline is not None else None,
            "locations": headline["unique_locations"] if headline is not None else None,
            # Привязок нет — «0 здесь» было бы неправдой: под непривязанным
            # штрихкодом у человека могут быть десятки пробежек здесь.
            "runs_here": visits.run_days if runs is not None else None,
            "volunteering_here": visits.volunteer_days if runs is not None else None,
            "last_here": visits.last_date,
            "best_here_sec": visits.best_sec,
            "first_here": first_here,
        },
        "home": _homes(db, [user.id], identity.identity_key).get(user.id),
        "jubilees": _jubilee_labels(
            runs=jubilee_runs,
            runs_here=visits.run_days,
            planned=[
                (plan.plan_date, visit.platform_code if visit is not None else plan.platform_code)
                for plan, visit in zip(here_ahead, here_visits, strict=True)
            ],
            ahead=ahead,
            horizon=horizon,
            systems=systems,
        ),
        # План сюда есть, но дальше трёх ближайших стартов — юбилей у него не
        # считаем, и страница говорит почему, а не «юбилеев нет».
        "jubilees_note": (
            JUBILEE_HORIZON_NOTE
            if runs is not None and any(plan.plan_date not in horizon for plan in here_ahead)
            else None
        ),
        # Дата визита, о котором «что даст визит»; впереди визитов нет — None.
        "challenges_date": nearest.plan_date if nearest is not None else None,
        "challenges": challenges,
        # Ближайший визит дальше трёх ближайших стартов — номерных строк нет,
        # и страница говорит почему.
        "challenges_note": challenges_note,
        # Следующие даты сюда в горизонте, где визит что-то даст.
        "later_visits": later_visits,
    }
