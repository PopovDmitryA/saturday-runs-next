"""Что ещё сказать в напоминании накануне старта, кроме даты и ≈№ (решение
Дмитрия 30.10.2026): погода на время старта, личная веха («это будет ваша
50-я пробежка») и кто ещё туда собирается — и две кнопки под сообщением:
«Проложить маршрут» и «Мой штрихкод».

Своих правил здесь нет, только сборка из готового:

- погода — прогноз старта (weather_forecast_service.forecast_for_locations,
  как у бота «Старты рядом»), строка — только если прогноз на эту дату;
- веха — те же знаковые старты, что в «Кто у нас бежит» оргкабинета
  (planner_guests_service.people_stats), одна — самая важная: первая
  пробежка, юбилей всего, в системе, здесь, «здесь впервые — N-я локация».
  Поправка на более ранние планы — только планы этого дня: напоминание
  уходит накануне, раньше завтрашнего ничего не будет. Свой план человек
  видит и приватным: сообщение — ему одному;
- кто ещё — те же имена, что на странице локации (plan_audience: публичный
  план, открытый профиль, взрослый), остальные — числом. Самого человека в
  списке нет.

Ничего из этого не должно ронять напоминание: сбой любой части — строки
нет, сообщение уходит.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from app.config import get_settings
from app.models import User
from app.notification_markup import link
from app.ru_plural import plural_form
from app.services.notification_senders import MessageButton
from app.services.planned_starts_service import PlannedStart, planned_starts
from app.services.platform_titles import platform_title
from app.services.run_plan_service import SHOWN_NAME, _Identities, _Identity, _plan_users, plan_audience

logger = logging.getLogger(__name__)

# Имён в строке «кто ещё» — столько, дальше «и ещё N».
COMPANY_NAMES_MAX = 3
SCAN_CODE_CALLBACK_PREFIX = "scancode:"
# Якорь «Профилей беговых систем» на «Обзоре», открывающий QR штрихкода.
SCAN_CODE_ANCHOR = "scancode="
_PEOPLE_FORMS = ("человек", "человека", "человек")


@dataclass(frozen=True)
class ReminderExtras:
    weather: str | None = None
    milestone: str | None = None
    company: str | None = None


def _location_url(slug: str) -> str:
    return f"{get_settings().app_base_url.rstrip('/')}/locations/{slug}"


def weather_line(db: Any, identity: _Identity, plan_date: date) -> str | None:
    """«🌧 3°, дождь, ветер 6 м/с · Будет дождь — нужна ветровка»."""
    from app.services.weather_forecast_service import forecast_for_locations

    forecast = forecast_for_locations(db, identity.location_ids, today=plan_date)
    if not forecast or forecast.get("target_date") != plan_date.isoformat() or not forecast.get("summary"):
        return None
    line = f"{forecast.get('icon') or '🌤'} {forecast['summary']}"
    advice = forecast.get("advice") or []
    return f"{line} · {advice[0]}" if advice else line


def company_line(
    rows: list[tuple[PlannedStart, User]],
    *,
    user_id: UUID,
    slug: str,
    audience: dict[str, str],
    totals: dict[UUID, int | None],
) -> str | None:
    """«👥 С вами планируют ещё 6 человек: Анна Смирнова, Иван Петров и ещё 4».

    rows — все планирующие на эту точку и дату; audience — plan_audience по
    ним, как на странице локации. Имена — по числу пробежек, как там же. Число — ссылкой на
    страницу локации: там весь список.
    """
    from app.services.seo_service import public_profile_name

    others = [(entry, user) for entry, user in rows if user.id != user_id]
    if not others:
        return None
    seen: set[UUID] = set()
    named: list[User] = []
    for entry, user in others:
        if user.id in seen:
            continue
        seen.add(user.id)
        if audience.get(entry.ref) == SHOWN_NAME:
            named.append(user)
    total = len(seen)
    named.sort(key=lambda user: (totals.get(user.id) is None, -(totals.get(user.id) or 0)))
    names = [public_profile_name(user) for user in named[:COMPANY_NAMES_MAX]]
    count = f"{total} {plural_form(total, _PEOPLE_FORMS)}"
    head = f"👥 С вами планируют ещё {link(count, _location_url(slug)) if slug else count}"
    if not names:
        return head
    rest = total - len(names)
    tail = f" и ещё {rest}" if rest > 0 else ""
    return f"{head}: {', '.join(names)}{tail}"


def milestone_text(mark: Any, *, locations_before: int | None) -> str:
    from app.services.planner_guests_service import (
        FIRST_EVER,
        FIRST_HERE,
        JUBILEE,
        JUBILEE_HERE,
        JUBILEE_PLATFORM,
    )

    if mark.kind == FIRST_EVER:
        return "🎉 Это будет ваша первая пробежка — удачи!"
    if mark.kind == JUBILEE:
        return f"🎉 Это будет ваша {mark.number}-я пробежка"
    if mark.kind == JUBILEE_PLATFORM:
        return f"🎉 Это будет ваша {mark.number}-я пробежка в {platform_title(mark.platform_code)}"
    if mark.kind == JUBILEE_HERE:
        return f"🎉 Это будет ваша {mark.number}-я пробежка здесь"
    if mark.kind == FIRST_HERE and locations_before:
        return f"🆕 Здесь вы впервые — станет вашей {locations_before + 1}-й локацией"
    if mark.kind == FIRST_HERE:
        return "🆕 Здесь вы впервые"
    return ""


def _mark_rank(mark: Any) -> int:
    from app.services.planner_guests_service import FIRST_EVER, FIRST_HERE, JUBILEE, JUBILEE_HERE, JUBILEE_PLATFORM

    order = [FIRST_EVER, JUBILEE, JUBILEE_PLATFORM, JUBILEE_HERE, FIRST_HERE]
    return order.index(mark.kind) if mark.kind in order else len(order)


def _milestones(
    db: Any,
    identity: _Identity,
    plan_date: date,
    entries: list[PlannedStart],
    day_plans: dict[UUID, list[PlannedStart]],
    *,
    today: date,
) -> dict[UUID, str]:
    """user_id → строка вехи на завтрашний старт этой точки."""
    from app.services.achievements_service import visit_histories
    from app.services.location_catalog_service import LocationCatalogIndex
    from app.services.planner_guests_service import FIRST_HERE, AheadPlan, PlansAhead, people_stats

    user_ids = list(dict.fromkeys(entry.user_id for entry in entries))
    ahead = {
        user_id: PlansAhead(
            plans=[
                AheadPlan(
                    id=entry.ref,
                    plan_date=entry.plan_date,
                    location_key=entry.location_key,
                    key=entry.key,
                    location_id=entry.location_id,
                    platform_code=entry.platform_code,
                )
                for entry in day_plans.get(user_id, [])
            ]
        )
        for user_id in user_ids
    }
    stats = people_stats(
        db,
        identity_key=identity.key,
        location_ids=identity.location_ids,
        entries=[(user_id, plan_date) for user_id in user_ids],
        today=today,
        rows=identity.rows,
        ahead=ahead,
    )
    best: dict[UUID, Any] = {}
    for user_id in user_ids:
        person = stats.get((user_id, plan_date))
        if person is not None and person.marks:
            best[user_id] = min(person.marks, key=_mark_rank)
    newcomers = [user_id for user_id, mark in best.items() if mark.kind == FIRST_HERE]
    histories = visit_histories(db, newcomers, catalog_index=LocationCatalogIndex(db)) if newcomers else {}
    result: dict[UUID, str] = {}
    for user_id, mark in best.items():
        history = histories.get(user_id)
        text = milestone_text(mark, locations_before=len(history.keys) if history is not None else None)
        if text:
            result[user_id] = text
    return result


def reminder_extras(
    db: Any,
    groups: dict[tuple[UUID, date], list[PlannedStart]],
    items_by_group: dict[tuple[UUID, date], list[dict[str, Any]]],
    *,
    identities: _Identities,
    today: date,
) -> dict[tuple[UUID, str], ReminderExtras]:
    """(user_id, ref плана) → строки под стартом. Пакетно по точке и дате:
    сколько бы людей ни бежало в Кузьминках, погода, планирующие и вехи
    считаются один раз на Кузьминки."""
    from app.services.planner_guests_service import cached_total_runs

    by_point: dict[tuple[str, date], list[tuple[UUID, dict[str, Any]]]] = defaultdict(list)
    for (user_id, plan_date), items in items_by_group.items():
        for item in items:
            if (item.get("start") or {}).get("cancelled"):
                continue
            by_point[(item["location"]["key"], plan_date)].append((user_id, item))
    if not by_point:
        return {}
    day_plans: dict[UUID, list[PlannedStart]] = defaultdict(list)
    entry_by_ref: dict[str, PlannedStart] = {}
    for (user_id, _plan_date), group in groups.items():
        day_plans[user_id].extend(group)
        for entry in group:
            entry_by_ref[entry.ref] = entry

    result: dict[tuple[UUID, str], ReminderExtras] = {}
    for (key, plan_date), members in by_point.items():
        identity = identities.get(key)
        if identity is None:
            continue
        weather = milestones = None
        company: dict[UUID, str | None] = {}
        try:
            weather = weather_line(db, identity, plan_date)
        except Exception:
            logger.exception("plan reminders: погода для %s на %s", key, plan_date)
        try:
            entries = [entry_by_ref[item["ref"]] for _user_id, item in members if item["ref"] in entry_by_ref]
            milestones = _milestones(db, identity, plan_date, entries, day_plans, today=today)
        except Exception:
            db.rollback()
            logger.exception("plan reminders: вехи для %s на %s", key, plan_date)
        try:
            rows = _plan_users(
                db,
                planned_starts(db, today=today, dates=[plan_date], identity=(key, identity.location_ids)),
            )
            audience = plan_audience(db, rows)
            totals = cached_total_runs(db, list({user.id for _entry, user in rows}))
            for user_id, _item in members:
                company[user_id] = company_line(
                    rows, user_id=user_id, slug=identity.slug, audience=audience, totals=totals
                )
        except Exception:
            db.rollback()
            logger.exception("plan reminders: кто ещё для %s на %s", key, plan_date)
        for user_id, item in members:
            result[(user_id, item["ref"])] = ReminderExtras(
                weather=weather,
                milestone=(milestones or {}).get(user_id),
                company=company.get(user_id),
            )
    return result


def route_url(identity: _Identity) -> str | None:
    """Маршрут в Яндекс Картах от того места, где человек сейчас, до старта."""
    point = next(
        (
            (location.latitude, location.longitude)
            for location, _code in (identity.plannable or identity.rows)
            if location.latitude is not None and location.longitude is not None
            and (location.latitude, location.longitude) != (0, 0)
        ),
        None,
    )
    if point is None:
        return None
    return f"https://yandex.ru/maps/?rtext=~{point[0]},{point[1]}&rtt=auto"


def reminder_buttons(
    items: list[dict[str, Any]], *, identities: _Identities, scan_codes: set[str]
) -> list[MessageButton]:
    """«🧭 Проложить маршрут» к каждой локации дня и «🏷 Мой штрихкод» к каждой
    системе дня, где штрихкод у человека есть. Отменённый старт — без кнопок:
    идти некуда."""
    base = get_settings().app_base_url.rstrip("/")
    live = [item for item in items if not (item.get("start") or {}).get("cancelled")]
    buttons: list[MessageButton] = []
    for item in live:
        identity = identities.get(item["location"]["key"])
        url = route_url(identity) if identity is not None else None
        if url:
            label = "🧭 Проложить маршрут" if len(live) == 1 else f"🧭 Маршрут: {item['location']['name']}"
            buttons.append(MessageButton(label=label, url=url))
    platforms = list(
        dict.fromkeys(
            code for code in (item["location"].get("platform_code") for item in live) if code and code in scan_codes
        )
    )
    for code in platforms:
        label = "🏷 Мой штрихкод" if len(platforms) == 1 else f"🏷 Штрихкод {platform_title(code)}"
        buttons.append(
            MessageButton(
                label=label,
                callback=f"{SCAN_CODE_CALLBACK_PREFIX}{code}",
                url=f"{base}/dashboard#{SCAN_CODE_ANCHOR}{code}",
            )
        )
    return buttons
