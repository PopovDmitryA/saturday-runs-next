"""«Где бежите?» — локации, на которые можно запланировать дату.

Планировать можно от локации (её страница: локация задана, дата выбирается
в её календаре) и от даты (кабинет «Мои планы» и «Ближайший план» на
«Обзоре»: дата задана, выбирается локация). Это два настоящих входа, ни один
не уводит в другой (решение Дмитрия 26.10.2026: турист расписывает два
месяца, не уходя из своего календаря и не обходя страницы локаций). Здесь —
второй вход: какие локации можно запланировать на эту дату.

Своих правил нет — те же, что у календаря локации: точка планируется
(plannable_rows_with_reason) и у неё в эту дату старт (start_dates: суббота
или особая дата её систем; особая суббота чужой системы — обычная суббота,
будняя особая дата чужой системы — мимо). Сохранение общее с окном от
локации: POST /planner/plans и PUT /planner/series.

Подсказки без запроса (до SUGGESTED_LIMIT): постоянная точка человека,
домашняя, точки его планов за RECENT_PLANS_DAYS дней (сначала недавно
отмеченные — турист видит вчерашнюю поездку первой), самые частые по его
пробежкам за год. Поиск (от двух знаков, до RESULTS_LIMIT) — выдача поиска по
сайту (каталог локаций из Redis: название, город, регион, сокращения «спб»,
«мск», «подмосковье», «е»=«ё»), из которой остаются только планируемые на
дату; обрезанный список помечен truncated — окно просит уточнить запрос
(в Москве планируемых локаций вдвое больше лимита).

Лимит дня окно знает заранее (limit — run_plan_service.day_plan_limits, то
же правило, что у сохранения и окна от локации; planned — разовые планы
человека на эту дату): на заполненный день другую локацию не выбрать, а не
узнать об этом по 409 после «Планирую». Уже запланированные локации дня
помечены mine — окно пишет «уже в планах» и второй раз их не ставит. Каталог не прогрет — поиск пуст, как и у поиска по сайту; подсказки
каталога не требуют.

Точная проверка — около десятка запросов на точку, поэтому кандидатов
проверяем не больше …_CHECK_LIMIT, а явно мёртвые (серия, только parkrun,
«Не действует» в каталоге, будняя особая дата чужой системы) отсеиваем по
записи каталога заранее.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.location_page_url import PLATFORM_ORDER
from app.models import Event, PlatformLink, RunResult, StartSpecialDate, User
from app.services.home_location_brief_service import home_identity_key
from app.services.planned_starts_service import planned_starts
from app.services.run_plan_service import (
    PlanError,
    _day_rows_between,
    _Identities,
    _Identity,
    _series_key,
    canonical_identity_keys,
    day_plan_limit,
    series_location,
)
from app.services.site_search_service import location_entries_by_key, matching_location_entries
from app.services.start_calendar_service import PLANNABLE_PLATFORMS, PLANNER_HORIZON_DAYS, moscow_today, start_day

SUGGESTED_LIMIT = 8
RESULTS_LIMIT = 20
RECENT_PLANS_DAYS = 90
FREQUENT_RUNS_DAYS = 365
# Сколько кандидатов проверять точным правилом календаря.
SUGGESTED_CHECK_LIMIT = 24
RESULTS_CHECK_LIMIT = 48
_SATURDAY = 5


def _platform_rank(code: str) -> int:
    return PLATFORM_ORDER.index(code) if code in PLATFORM_ORDER else len(PLATFORM_ORDER)


def _recent_plan_keys(db: Session, user_id: UUID, today: date, identities: _Identities) -> list[str]:
    """Точки планов человека с датой не старше RECENT_PLANS_DAYS и до конца
    горизонта — сначала недавно отмеченные. Через planned_starts, как все
    читатели планов: ключ точки — сегодняшний (дрейф location:→catalog:)."""
    entries = planned_starts(
        db,
        today=today,
        date_from=today - timedelta(days=RECENT_PLANS_DAYS),
        date_to=today + timedelta(days=PLANNER_HORIZON_DAYS),
        user_ids=[user_id],
        identities=identities,
    )
    entries.sort(key=lambda entry: (entry.created_at, entry.ref), reverse=True)
    return list(dict.fromkeys(entry.key for entry in entries if entry.key))


def _frequent_run_keys(db: Session, user_id: UUID, today: date) -> list[str]:
    """Точки по числу дней с пробежкой человека за год — больше выше, при
    равенстве — где бегал позже."""
    rows = (
        db.query(Event.location_id, func.count(func.distinct(Event.event_date)), func.max(Event.event_date))
        .select_from(RunResult)
        .join(Event, RunResult.event_id == Event.id)
        .join(PlatformLink, PlatformLink.participant_id == RunResult.participant_id)
        .filter(
            PlatformLink.user_id == user_id,
            Event.is_test_event.is_(False),
            Event.event_date >= today - timedelta(days=FREQUENT_RUNS_DAYS),
            Event.event_date <= today,
        )
        .group_by(Event.location_id)
        .all()
    )
    if not rows:
        return []
    canonical = canonical_identity_keys(db, [location_id for location_id, _count, _last in rows])
    totals: dict[str, tuple[int, date]] = {}
    for location_id, count, last in rows:
        key = canonical.get(location_id, f"location:{location_id}")
        runs, latest = totals.get(key, (0, last))
        totals[key] = (runs + int(count), max(latest, last))
    return sorted(totals, key=lambda key: (-totals[key][0], -totals[key][1].toordinal(), key))


def _prefiltered(entry: dict[str, Any], weekday_special: set[str] | None) -> bool:
    """Запись каталога заведомо не планируется на дату — точную проверку не тратим."""
    if entry.get("is_series"):
        return True
    codes = {str(code) for code in entry.get("platform_codes") or []} & set(PLANNABLE_PLATFORMS)
    if not codes:
        return True
    # «Не действует» в каталоге; отмена сильнее паузы, как у сайта.
    if entry.get("is_paused") and not entry.get("is_cancelled"):
        return True
    return weekday_special is not None and not codes & weekday_special


class _Picker:
    """Точная проверка кандидатов и сборка строк ответа на одну дату."""

    def __init__(self, db: Session, user: User, on_date: date, today: date) -> None:
        from app.services.plan_series_service import active_series

        self.db = db
        self.on_date = on_date
        self.identities = _Identities(db, today)
        self.entries = location_entries_by_key(db)
        special = db.query(StartSpecialDate).filter(StartSpecialDate.start_date == on_date).one_or_none()
        self.special_platforms: set[str] = set(special.platforms or []) if special is not None else set()
        # Будняя особая дата — только её системы; особая суббота — суббота для всех.
        self.weekday_special = (
            self.special_platforms if special is not None and on_date.weekday() != _SATURDAY else None
        )
        planned = planned_starts(db, today=today, dates=[on_date], user_ids=[user.id], identities=self.identities)
        self.mine_keys = {entry.key for entry in planned}
        self.mine_ids = {entry.location_id for entry in planned if entry.location_id is not None}
        # Разовые планы дня — их и считает лимит дня (вхождение постоянного не
        # в счёт: поездка его вытесняет).
        self.planned = [
            series_location(self.identities, entry.key, fallback_key=entry.location_key)
            for entry in planned
            if entry.plan is not None
        ]
        series = active_series(db, user.id)
        self.series_identity: _Identity | None = None
        if series is not None and (series.until is None or series.until >= today):
            self.series_identity = self.identities.get(_series_key(db, series))
        self.seen: set[str] = set()
        self.seen_ids: set[UUID] = set()

    def _is_series_point(self, identity: _Identity) -> bool:
        found = self.series_identity
        return found is not None and (
            found.key == identity.key or bool(set(found.location_ids) & set(identity.location_ids))
        )

    def option(self, key: str) -> dict[str, Any] | None:
        """Строка ответа для точки или None: не находится, уже была, не
        планируется или в эту дату у неё старта нет."""
        identity = self.identities.get(key)
        if identity is None or identity.key in self.seen or self.seen_ids & set(identity.location_ids):
            return None
        self.seen.add(identity.key)
        self.seen_ids.update(identity.location_ids)
        if not identity.plannable:
            return None
        start = self.identities.start_for(identity, self.on_date)
        if start is None:
            return None
        codes = sorted({code for _location, code in identity.plannable}, key=_platform_rank)
        if start.kind == "special":
            codes = [code for code in codes if code in self.special_platforms] or [start.platform_code]
        entry = self.entries.get(identity.key) or {}
        city = str(entry.get("city") or "").strip() or next(
            (location.city.strip() for location, _code in identity.plannable if (location.city or "").strip()), None
        )
        return {
            "key": identity.key,
            "slug": identity.slug,
            "name": identity.name,
            "city": city or None,
            "platforms": codes,
            "number_forecast": start.number_forecast,
            "cancelled": start.cancelled,
            "mine": identity.key in self.mine_keys or bool(self.mine_ids & set(identity.location_ids)),
            "regular": self._is_series_point(identity),
        }

    def collect(self, keys: Iterable[str], *, limit: int, check_limit: int) -> tuple[list[dict[str, Any]], bool]:
        """До limit строк и обрезан ли список: нашлась строка сверх лимита или
        непроверенные кандидаты остались (упёрлись в check_limit)."""
        found: list[dict[str, Any]] = []
        checked = 0
        for key in keys:
            if checked >= check_limit:
                return found, True
            checked += 1
            item = self.option(key)
            if item is not None:
                if len(found) >= limit:
                    return found, True
                found.append(item)
        return found, False


def _suggested_keys(db: Session, user: User, picker: _Picker, today: date) -> list[str]:
    keys: list[str] = []
    if picker.series_identity is not None:
        keys.append(picker.series_identity.key)
    home = home_identity_key(db, user)
    if home:
        keys.append(home)
    keys += _recent_plan_keys(db, user.id, today, picker.identities)
    keys += _frequent_run_keys(db, user.id, today)
    return list(dict.fromkeys(keys))


def _search_keys(db: Session, query: str, picker: _Picker) -> list[str]:
    keys: list[str] = []
    for entry in matching_location_entries(db, query):
        key = str(entry.get("identity_key") or "")
        if key and not _prefiltered(entry, picker.weekday_special):
            keys.append(key)
    return keys


def locations_for_date(db: Session, user: User, on_date: date, query: str | None = None) -> dict[str, Any]:
    """PlanLocationsResponse: день, лимит планов дня, «Не бегаю» и локации.

    query короче двух знаков — подсказки (suggested), иначе поиск (results).
    Дата — день старта (суббота или особая дата) от сегодня на год вперёд."""
    today = moscow_today()
    if on_date < today:
        raise PlanError("Этот старт уже прошёл")
    if on_date > today + timedelta(days=PLANNER_HORIZON_DAYS):
        raise PlanError("В этот день старта нет")
    day = start_day(db, on_date)
    if day is None:
        raise PlanError("В этот день старта нет")
    picker = _Picker(db, user, on_date, today)
    text = (query or "").strip()
    suggested: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    truncated = False
    if len(text) >= 2:
        results, truncated = picker.collect(
            _search_keys(db, text, picker), limit=RESULTS_LIMIT, check_limit=RESULTS_CHECK_LIMIT
        )
    else:
        suggested, _more = picker.collect(
            _suggested_keys(db, user, picker, today), limit=SUGGESTED_LIMIT, check_limit=SUGGESTED_CHECK_LIMIT
        )
    rest = any(row.rest for row in _day_rows_between(db, user.id, on_date, on_date))
    return {
        "date": on_date,
        "kind": day.kind,
        "title": day.title,
        # Лимит — то же правило, что у сохранения и окна от локации.
        "limit": day_plan_limit(db, on_date),
        "rest": rest,
        "planned": picker.planned,
        "suggested": suggested,
        "results": results,
        "truncated": truncated,
    }
