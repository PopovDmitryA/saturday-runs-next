"""Локации рядом с точкой — для бота @weekend_runs_bot (решение Дмитрия 02.10.2026).

Человек присылает боту геопозицию, бот отвечает списком локаций, куда можно
пойти в субботу. Правило выбора:

* до трёх локаций в радиусе 15 км, ближние первыми;
* в радиусе пусто — «рядом стартов нет» и одна ближайшая с расстоянием, хоть
  за 600 км (Владивосток → Хабаровск);
* «не действует» не показываем вовсе, а отменённая или ещё не открытая
  остаётся в списке, но места не занимает: к ней добавляется следующая, чтобы
  человеку было куда пойти.

Набор локаций — тот же, что на карте сайта (`list_catalog_map_locations`):
5 вёрст, С95, RunPark и исторический parkrun. Расстояние по прямой.

Плотность на 02.10.2026, отсюда радиус: в центре Москвы ближе 10 км десять
локаций, ближе 20 км — сорок одна; в Казани и Туле по одной на город, следующая
дальше 20 км; у половины локаций соседняя дальше 16 км. 15 км накрывают город и
ближнее Подмосковье, но не тянут соседний город за 40 км.

К каждой локации — ближайший старт (дата, время, прогноз номера), погода на
субботу, адрес и, если Telegram привязан к профилю, личное: «бегали здесь
N раз» или «станет вашей N-й локацией» и +1 в «Нумераторе». Личное идёт только
в чат самого человека: в inline-режиме сообщение уходит в чужие чаты.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy.orm import Session

from app.activity_date import has_real_activity_date
from app.models import Location, LocationDescription, User
from app.notification_markup import bold, link, to_telegram_html
from app.services.activity_notification_service import _MONTHS_GENITIVE
from app.services.home_distance_service import haversine_km, round_km
from app.services.location_catalog_service import LocationCatalogIndex
from app.services.location_map_service import list_catalog_map_locations
from app.services.location_page_service import _identity_cancel_reason
from app.services.location_schedule_service import start_time_for_date
from app.services.map_point_context_service import (
    NextStart,
    _challenge_for_number,
    _forecast_next_starts,
    _locations_for_identity,
    _my_start_numbers,
)
from app.services.platform_titles import PLATFORM_ORDER, platform_title
from app.services.user_unique_locations_detail import build_user_unique_location_details
from app.services.weather_forecast_service import forecast_for_locations

NEARBY_RADIUS_KM = 15.0
NEARBY_LIMIT = 3
# Отменённые места не занимают, но и бесконечно копиться им нельзя: в зимнюю
# субботу в одном городе отменяют сразу несколько стартов.
NEARBY_MAX_ITEMS = NEARBY_LIMIT + 2
# Inline-режим ищет и по названию — там это выбор, что отправить в чат, а не
# ответ «куда пойти», поэтому вариантов больше.
INLINE_SEARCH_LIMIT = 10
# Адрес из «Как добраться» бывает абзацем; в карточке бота нужна одна строка.
ADDRESS_MAX_CHARS = 120

_WEEKDAYS_SHORT = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


@dataclass(frozen=True)
class NearbyPick:
    point: dict[str, object]
    distance_km: float

    @property
    def can_go(self) -> bool:
        """Можно ли сюда пойти в ближайшую субботу."""
        return not self.point.get("is_cancelled") and not self.point.get("is_upcoming")


def _ranked(points: list[dict[str, object]], origin: tuple[float, float]) -> list[NearbyPick]:
    picks: list[NearbyPick] = []
    for point in points:
        if point.get("is_paused"):
            continue
        latitude, longitude = point.get("latitude"), point.get("longitude")
        if latitude is None or longitude is None:
            continue
        distance = haversine_km(origin, (float(cast(float, latitude)), float(cast(float, longitude))))
        picks.append(NearbyPick(point=point, distance_km=distance))
    picks.sort(key=lambda pick: pick.distance_km)
    return picks


def pick_nearby(
    points: list[dict[str, object]],
    origin: tuple[float, float],
    *,
    radius_km: float = NEARBY_RADIUS_KM,
    limit: int = NEARBY_LIMIT,
) -> tuple[list[NearbyPick], bool]:
    """Локации для ответа и признак «в радиусе что-то есть».

    За радиус выходим, только пока идти некуда: в радиусе пусто или одни
    отменённые. Тогда добавляется ровно одна ближайшая действующая.
    """
    ranked = _ranked(points, origin)
    has_nearby = any(pick.distance_km <= radius_km for pick in ranked)
    target = limit if has_nearby else 1
    picks: list[NearbyPick] = []
    can_go = 0
    for pick in ranked:
        if can_go >= target or len(picks) >= NEARBY_MAX_ITEMS:
            break
        if pick.distance_km > radius_km and can_go > 0:
            break
        picks.append(pick)
        can_go += pick.can_go
    return picks, has_nearby


def _fold(text: str) -> str:
    return text.lower().replace("ё", "е").strip()


def search_by_name(
    points: list[dict[str, object]],
    query: str,
    origin: tuple[float, float] | None,
    *,
    limit: int = INLINE_SEARCH_LIMIT,
) -> list[NearbyPick]:
    """Inline-поиск по названию и городу: с геопозицией — ближние первыми."""
    needle = _fold(query)
    if not needle:
        return []
    matched = [
        point
        for point in points
        if not point.get("is_paused")
        and (needle in _fold(str(point.get("name") or "")) or needle in _fold(str(point.get("city") or "")))
    ]
    if origin is not None:
        return _ranked(matched, origin)[:limit]
    matched.sort(key=lambda point: _fold(str(point.get("name") or "")))
    # Без точки расстояния нет: −1 читается форматтером как «не показывать».
    return [NearbyPick(point=point, distance_km=-1.0) for point in matched[:limit]]


# ---------------------------------------------------------------------------
# Подробности по каждой локации


_VENUE_PREAMBLE = "Мероприятие проводится"


def _short_address(text: str | None) -> str | None:
    if not text:
        return None
    value = " ".join(text.split())
    # У 5 вёрст вводная одна на всех: «Мероприятие проводится в парке Сокольники
    # в Москве. Москва, Сокольнический Вал, 1с1». Парк уже назван в заголовке —
    # оставляем адрес. Нет второго предложения — оставляем как есть.
    if value.startswith(_VENUE_PREAMBLE):
        _preamble, separator, rest = value.partition(". ")
        if separator and rest.strip():
            value = rest.strip()
    if len(value) <= ADDRESS_MAX_CHARS:
        return value
    cut = value[:ADDRESS_MAX_CHARS].rsplit(" ", 1)[0].rstrip(",.;:—-")
    return f"{cut}…"


def _platform_rank(code: str) -> int:
    return PLATFORM_ORDER.index(code) if code in PLATFORM_ORDER else len(PLATFORM_ORDER)


def _descriptions(db: Session, rows: list[tuple[Location, str]]) -> list[tuple[str, LocationDescription]]:
    """Описания площадки по системам — действующая система первой."""
    ids = [location.id for location, _code in rows]
    if not ids:
        return []
    by_location = {
        row.location_id: row
        for row in db.query(LocationDescription).filter(LocationDescription.location_id.in_(ids)).all()
    }
    ordered = sorted(rows, key=lambda item: _platform_rank(item[1]))
    return [(code, by_location[location.id]) for location, code in ordered if location.id in by_location]


def _start_time(
    descriptions: list[tuple[str, LocationDescription]],
    next_start: NextStart,
    weather: dict[str, Any] | None,
) -> str | None:
    """Время старта: из прогноза (он уже считал его по расписанию), иначе из
    расписания системы этого старта, иначе любой системы площадки."""
    if weather and weather.get("target_date") == next_start.event_date.isoformat():
        value = weather.get("start_time_local")
        if value:
            return str(value)
    ordered = sorted(descriptions, key=lambda item: item[0] != next_start.platform_code)
    for _code, description in ordered:
        value = start_time_for_date(description.schedule_parsed, next_start.event_date)
        if value is not None:
            return f"{value.hour}:{value.minute:02d}"
    return None


@dataclass(frozen=True)
class MyVisits:
    """Что человек уже набегал: по ключу идентичности и сколько локаций всего."""

    runs: dict[str, tuple[int, str | None]]
    unique_run_locations: int
    start_numbers: set[int]


def _my_visits(db: Session, user: User, catalog_index: LocationCatalogIndex) -> MyVisits:
    detail = build_user_unique_location_details(db, user.id, catalog_index=catalog_index)
    runs: dict[str, tuple[int, str | None]] = {}
    for location in cast(list[dict[str, Any]], detail["locations"]):
        run_count = int(location.get("run_count") or 0)
        if run_count <= 0:
            continue
        # Пробежки без даты лежат на заглушке-эпохе: «последний — 1 января 1970»
        # человеку не скажешь.
        run_dates = [
            day
            for platform in location.get("platforms") or []
            for day in (date.fromisoformat(str(value)) for value in platform.get("run_dates") or [])
            if has_real_activity_date(day)
        ]
        runs[str(location["catalog_identity_key"])] = (
            run_count,
            max(run_dates).isoformat() if run_dates else None,
        )
    overall_numbers, _by_platform = _my_start_numbers(db, user.id)
    return MyVisits(runs=runs, unique_run_locations=len(runs), start_numbers=overall_numbers)


def _site_url(base_url: str, point: dict[str, object]) -> str | None:
    slug = point.get("location_slug")
    if not slug:
        return None
    return f"{base_url.rstrip('/')}/locations/{slug}"


def describe_pick(
    db: Session,
    pick: NearbyPick,
    *,
    base_url: str,
    today: date,
    visits: MyVisits | None,
) -> dict[str, Any]:
    """Карточка одной локации: всё, что бот покажет о ней."""
    point = pick.point
    identity_key = str(point["catalog_identity_key"])
    rows = _locations_for_identity(db, identity_key)
    location_ids: list[UUID] = [location.id for location, _code in rows]
    descriptions = _descriptions(db, rows)

    status = "cancelled" if point.get("is_cancelled") else "upcoming" if point.get("is_upcoming") else "ok"
    next_starts = _forecast_next_starts(db, rows, today=today) if status == "ok" else []
    next_start = next_starts[0] if next_starts else None
    weather = forecast_for_locations(db, location_ids, today=today) if next_start else None
    if weather and next_start and weather.get("target_date") != next_start.event_date.isoformat():
        # Прогноз лежит на ближайшую субботу, а старт по прогнозу номера может
        # быть позже (площадка пропускает неделю) — чужая погода хуже никакой.
        weather = None

    next_start_payload: dict[str, Any] | None = None
    if next_start is not None:
        challenge = _challenge_for_number(next_start.number)
        next_start_payload = {
            "date": next_start.event_date.isoformat(),
            "number": next_start.number,
            "platform_code": next_start.platform_code,
            "start_time": _start_time(descriptions, next_start, weather),
            "challenge_title": challenge[1] if challenge else None,
            "plus_one": (
                challenge is not None and next_start.number not in visits.start_numbers if visits is not None else None
            ),
        }

    # Первое непустое: у S95-строки площадки адреса может не быть, а у 5 вёрст — есть.
    address = next(
        (value for _c, description in descriptions if (value := _short_address(description.travel_text))),
        None,
    )

    my_runs: int | None = None
    my_last_run: str | None = None
    if visits is not None:
        my_runs, my_last_run = visits.runs.get(identity_key, (0, None))

    platform_codes = [str(code) for code in cast(list[str], point.get("platform_codes") or [])]
    return {
        "identity_key": identity_key,
        "name": str(point.get("name") or ""),
        "city": point.get("city"),
        "latitude": float(cast(float, point["latitude"])),
        "longitude": float(cast(float, point["longitude"])),
        "distance_km": round_km(pick.distance_km) if pick.distance_km >= 0 else None,
        "platform_code": str(point.get("active_platform") or (platform_codes[0] if platform_codes else "")),
        "platform_codes": platform_codes,
        "site_url": _site_url(base_url, point),
        "status": status,
        "cancel_reason": _identity_cancel_reason(rows) if status == "cancelled" else None,
        "next_start": next_start_payload,
        "weather": (
            {
                "icon": weather.get("icon"),
                "summary": weather.get("summary"),
                "advice": (weather.get("advice") or [None])[0],
            }
            if weather
            else None
        ),
        "address": address,
        "my_runs": my_runs,
        "my_last_run": my_last_run,
    }


# ---------------------------------------------------------------------------
# Текст сообщения


def format_km(value: float | None) -> str:
    if value is None:
        return ""
    if value < 10:
        # «3 км», а не «3,0 км»; десятые — только когда они есть.
        return f"{value:.1f}".removesuffix(".0").replace(".", ",") + " км"
    return f"{int(round(value))} км"


def _date_label(value: date, today: date) -> str:
    if value == today:
        return "сегодня"
    if value == today + timedelta(days=1):
        return "завтра"
    label = f"{_WEEKDAYS_SHORT[value.weekday()]} {value.day} {_MONTHS_GENITIVE[value.month - 1]}"
    if value.year != today.year:
        label += f" {value.year}"
    return label


def _runs_word(count: int) -> str:
    tail10, tail100 = count % 10, count % 100
    if tail10 == 1 and tail100 != 11:
        return "раз"
    if tail10 in (2, 3, 4) and tail100 not in (12, 13, 14):
        return "раза"
    return "раз"


def start_line(item: dict[str, Any], today: date) -> str | None:
    """«🗓 сб 4 октября, 9:00 · старт ≈№412» — или почему старта не будет."""
    if item["status"] == "cancelled":
        reason = item.get("cancel_reason")
        return f"⚠️ Ближайший старт отменён: {reason}" if reason else "⚠️ Ближайший старт отменён"
    if item["status"] == "upcoming":
        return "🔜 Скоро открытие — первого старта ещё не было"
    start = item.get("next_start")
    if not start:
        return None
    when = _date_label(date.fromisoformat(start["date"]), today)
    if start.get("start_time"):
        when += f", {start['start_time']}"
    # Номер — прогноз (последний старт + недели), отсюда «≈», как в попапе карты.
    return f"🗓 {when} · старт ≈№{start['number']}"


def _personal_lines(item: dict[str, Any], visits: MyVisits, today: date) -> list[str]:
    lines: list[str] = []
    runs = item.get("my_runs") or 0
    if runs > 0:
        line = f"✅ Вы бегали здесь {runs} {_runs_word(runs)}"
        if item.get("my_last_run"):
            line += f", последний — {_date_label(date.fromisoformat(item['my_last_run']), today)}"
        lines.append(line)
    else:
        lines.append(f"🆕 Здесь вы ещё не бегали — станет вашей {visits.unique_run_locations + 1}-й локацией")
    start = item.get("next_start") or {}
    if start.get("plus_one") and start.get("challenge_title"):
        lines.append(f"🎯 №{start['number']} — новый номер для челленджа «{start['challenge_title']}»")
    return lines


def format_item(item: dict[str, Any], *, today: date, visits: MyVisits | None, number: int | None) -> str:
    name = bold(item["name"])
    title = link(name, item["site_url"]) if item.get("site_url") else name
    head = f"{number}. {title}" if number is not None else title
    meta = [part for part in (format_km(item.get("distance_km")), platform_title(item.get("platform_code"))) if part]
    lines = [f"{head} — {' · '.join(meta)}" if meta else head]
    start = start_line(item, today)
    if start:
        lines.append(start)
    weather = item.get("weather")
    if weather and weather.get("summary"):
        line = f"{weather.get('icon') or '🌤'} {weather['summary']}"
        if weather.get("advice"):
            line += f" · {weather['advice']}"
        lines.append(line)
    if item.get("address"):
        lines.append(f"📌 {item['address']}")
    if visits is not None:
        lines.extend(_personal_lines(item, visits, today))
    return "\n".join(lines)


NOT_LINKED_HINT = (
    "Войдите на run5k.run через Telegram — и бот будет подсказывать, где вы ещё не бегали "
    "и какой старт добавит номер в «Нумератор»."
)


def format_nearby_message(
    items: list[dict[str, Any]],
    *,
    has_nearby: bool,
    today: date,
    visits: MyVisits | None,
    linked: bool,
) -> str:
    """Ответ бота в мини-разметке уведомлений (`**жирный**`, `[подпись](url)`)."""
    if not items:
        return "Не нашёл ни одной действующей локации. Все локации — в каталоге: run5k.run/locations"
    radius = int(NEARBY_RADIUS_KM)
    if has_nearby:
        head = "📍 Ближайшая к вам локация:" if len(items) == 1 else "📍 Локации рядом с вами:"
        numbered = len(items) > 1
    else:
        head = f"📍 В радиусе {radius} км стартов нет. Ближайшая локация:"
        numbered = len(items) > 1
    blocks = [head]
    for index, item in enumerate(items, start=1):
        blocks.append(format_item(item, today=today, visits=visits, number=index if numbered else None))
    if not linked:
        blocks.append(NOT_LINKED_HINT)
    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# Вход


def build_nearby(
    db: Session,
    latitude: float,
    longitude: float,
    *,
    base_url: str,
    user: User | None = None,
    personal: bool = True,
    today: date | None = None,
) -> dict[str, Any]:
    """Ответ на геопозицию: локации, признак «рядом есть» и готовый текст.

    `personal=False` — inline-режим: сообщение уйдёт в чужой чат, личного там
    быть не должно, даже если человек привязал Telegram.
    """
    today = today or date.today()
    catalog_index = LocationCatalogIndex(db)
    points = cast(list[dict[str, object]], list_catalog_map_locations(db)["points"])
    picks, has_nearby = pick_nearby(points, (latitude, longitude))
    visits = _my_visits(db, user, catalog_index) if user is not None and personal else None
    items = [describe_pick(db, pick, base_url=base_url, today=today, visits=visits) for pick in picks]
    text = format_nearby_message(
        items,
        has_nearby=has_nearby,
        today=today,
        visits=visits,
        # Подсказку «войдите» показываем только в личном чате и только тому,
        # кого бот не узнал.
        linked=user is not None or not personal,
    )
    return {
        "has_nearby": has_nearby,
        "radius_km": NEARBY_RADIUS_KM,
        "items": items,
        "text_html": to_telegram_html(text),
        **nearby_summary(items, linked=user is not None),
    }


def nearby_summary(items: list[dict[str, Any]], *, linked: bool) -> dict[str, Any]:
    """Что из ответа уходит в журнал «где ищут старт» (nearby_query_log)."""
    nearest = items[0] if items else None
    return {
        "linked": linked,
        "nearest_identity_key": nearest["identity_key"] if nearest else None,
        "nearest_distance_km": nearest.get("distance_km") if nearest else None,
        "within_radius": sum(
            1
            for item in items
            if item.get("distance_km") is not None and float(item["distance_km"]) <= NEARBY_RADIUS_KM
        ),
    }


def build_inline_results(
    db: Session,
    *,
    base_url: str,
    latitude: float | None,
    longitude: float | None,
    query: str,
    today: date | None = None,
) -> list[dict[str, Any]]:
    """Варианты для inline-режима: без текста — те же «рядом», с текстом — поиск
    по названию и городу. Личного нет: сообщение уходит в чужой чат."""
    today = today or date.today()
    points = cast(list[dict[str, object]], list_catalog_map_locations(db)["points"])
    origin = (latitude, longitude) if latitude is not None and longitude is not None else None
    if query.strip():
        picks = search_by_name(points, query, origin)
    elif origin is not None:
        picks, _has_nearby = pick_nearby(points, origin)
    else:
        return []
    items = [describe_pick(db, pick, base_url=base_url, today=today, visits=None) for pick in picks]
    for item in items:
        # Расстояние — от того, кто отправляет, друзьям в чате оно ни о чём:
        # в списке выбора оно есть (start_label рядом), в самом сообщении нет.
        shared = {**item, "distance_km": None}
        item["text_html"] = to_telegram_html(format_item(shared, today=today, visits=None, number=None))
        item["start_label"] = start_line(item, today)
    return items


def describe_identity(
    db: Session, identity_key: str, *, base_url: str, today: date | None = None
) -> dict[str, Any] | None:
    """Одна локация по ключу — для кнопки «на карте» под ответом бота."""
    points = cast(list[dict[str, object]], list_catalog_map_locations(db)["points"])
    point = next((item for item in points if item.get("catalog_identity_key") == identity_key), None)
    if point is None:
        return None
    return describe_pick(
        db, NearbyPick(point=point, distance_km=-1.0), base_url=base_url, today=today or date.today(), visits=None
    )
