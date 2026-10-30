"""Уведомления планировщика: напоминание накануне старта и «старт отменён».

Человек отметил «Планирую» — и через месяц забыл. Поэтому вечером накануне
старта уходит напоминание, а если ближайший старт отменили — сообщение тем,
кто туда собирался (решение Дмитрия 25.10.2026). Это два вида уведомлений, а
не один (26.10.2026): человек может планировать, не желая напоминаний, — и
при этом знать об отмене. Напоминание — `plan_reminders`, последней строкой
у него ссылка «Выключить» прямо на эти тумблеры — в «Моих планах»
(/plans#plan-notifications: настройки планов живут там, а не в общих
настройках, решение Дмитрия 26.10.2026); отмена — `plan_cancellations`.

Вечер — по поясу локации плана, а не человека: москвич, который едет в
Екатеринбург, ложится спать по екатеринбургскому времени, и «завтра» у
старта — тоже там. Окно 18:00–21:00 местного: задача ходит раз в час, и
пропущенный заход (перезапуск beat, деплой) не съедает напоминание, а
повтор внутри окна отсекает ключ с датой. Особые даты бывают в любой день
недели — перед 01.01 напоминание уходит 31.12.

Одно сообщение на человека и дату: в Новый год бывает до трёх планов за день,
и три сообщения подряд читались бы как спам.

«Старт отменён» уходит всем, у кого план на эту локацию на её ближайший
старт, — мимо фильтров систем и мест у вида «Отмены стартов»: человек
собрался именно туда, и отмена касается его лично. Отмена, объявленная
после начала старта, — запись задним числом (как в
cancellation_notification_service.still_ahead), о ней молчим.

Личной заметки плана в сообщениях нет: её видит только владелец, а текст
уведомления лежит в журнале доставок и уходит копией админу.

Постоянный план напоминается, как любой план, накануне каждого своего старта
(решение Дмитрия по Q06: кто бегает здесь всегда, сам правит даты, когда не
придёт), — с одной строкой «Не получится? Пропущу»: одно нажатие на сайте —
и «Не бегаю» на этот день. «Старт отменён» получают и постоянные, кроме
отметивших «Пропущу» и уехавших в этот день. Третий вид — `plan_series`:
«Ещё ходите?», когда постоянный план уснул после трёх стартов без человека;
без дат и без счёта неявок — только что план на паузе и как его вернуть.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Location, LocationDescription, Platform, User, UserNotificationChannel
from app.notification_markup import bold, link
from app.ru_plural import plural_form
from app.services import notification_service as notifications
from app.services.cancellation_notification_service import FALLBACK_TIMEZONE, _zone_of, start_already_passed
from app.services.plan_reminder_extras import ReminderExtras, reminder_buttons, reminder_extras
from app.services.planned_starts_service import planned_starts, reminder_candidates
from app.services.run_plan_service import _Identities, canonical_identity_keys, plan_items, still_on
from app.services.scan_code_service import user_scan_codes
from app.services.start_calendar_service import (
    MOSCOW_TZ,
    cancelled_start_date,
    day_label,
    identity_brief,
    identity_rows,
)

if TYPE_CHECKING:
    from app.services.location_cancellation_notify import CancellationChange

logger = logging.getLogger(__name__)

REMINDER_KIND = "plan_reminders"
CANCELLATION_KIND = "plan_cancellations"
SERIES_KIND = "plan_series"
SERIES_DEDUPE_PREFIX = "plan-sleep"
# Число пропусков словом в «Ещё ходите?» — по plan_series_service.SERIES_SLEEP_AFTER_MISSES.
_MISSES_WORDS = {3: "Три"}
# Окно напоминания по местному времени локации: с 18:00 до 21:00 накануне.
REMINDER_HOUR_FROM = 18
REMINDER_HOUR_TO = 21
# Даты планов, которые смотрит один заход: от сегодня по Москве на два дня
# вперёд — «завтра» на востоке наступает раньше московского.
REMINDER_LOOKAHEAD_DAYS = 2
REMINDER_DEDUPE_PREFIX = "plan-eve"
CANCEL_DEDUPE_PREFIX = "plan-cancel"
PLANS_URL_LABEL = "Мои планы"
# Якорь тумблеров уведомлений о планах на странице «Мои планы» (/plans).
REMINDER_SETTINGS_ANCHOR = "plan-notifications"
# Привязка профилей систем — секция «Профили беговых систем» на «Обзоре».
PROFILES_PATH = "/dashboard#profiles"


def _plans_url() -> str:
    return f"{get_settings().app_base_url.rstrip('/')}/plans"


def series_url(anchor: str = "series") -> str:
    """/plans#series — карточка постоянного плана; #series-resume и
    #series-stop открывают её с подтверждением одним нажатием."""
    return f"{_plans_url()}#{anchor}"


def _skip_url(plan_date: date) -> str:
    return f"{_plans_url()}#skip={plan_date.isoformat()}"


def _reminder_settings_url() -> str:
    return f"{_plans_url()}#{REMINDER_SETTINGS_ANCHOR}"


def _profiles_url() -> str:
    return f"{get_settings().app_base_url.rstrip('/')}{PROFILES_PATH}"


def _opt_out_line() -> str:
    """Последняя строка напоминания: выключить только напоминания, а не все
    уведомления — может, человек хочет планировать, но без них (Дмитрий
    26.10.2026). Кнопка сообщения остаётся «Мои планы»."""
    return f"Напоминания не нужны? {link('Выключить', _reminder_settings_url())}"


def _location_label(name: str, slug: str) -> str:
    """Название локации — ссылка на её страницу: там «Как добраться»."""
    if not slug:
        return bold(name)
    return link(bold(name), f"{get_settings().app_base_url.rstrip('/')}/locations/{slug}")


def _clock(start_time: str) -> str:
    """«09:00» → «9:00»: так время пишут в тексте."""
    hours, _, minutes = start_time.partition(":")
    return f"{int(hours)}:{minutes}" if hours.isdigit() else start_time


def _reason(text: str | None) -> str | None:
    cleaned = (text or "").strip().rstrip(".").strip()
    return cleaned or None


def _start_phrase(start: dict[str, Any]) -> str:
    """Что известно о старте: время и ≈№, «время уточняйте» или отмена.

    Даты без старта сюда не доходят — их отсекает still_on.
    """
    if start.get("cancelled"):
        # Причину пишет организатор с заглавной — она идёт своей строкой
        # (_reason_line), как в сообщении «Старт отменён».
        return "старт отменён"
    number = start.get("number_forecast")
    start_time = start.get("start_time")
    if start_time:
        phrase = f"старт в {_clock(start_time)}"
        return f"{phrase} (≈№{number})" if number else phrase
    # Особая дата: время у каждой локации своё, и мы его не знаем.
    if number:
        return f"старт ≈№{number}, время уточняйте у организаторов"
    return "время уточняйте у организаторов"


def _reason_line(start: dict[str, Any]) -> str:
    """«\\nСубботник в парке.» у отменённого старта с причиной, иначе пусто."""
    reason = _reason(start.get("cancel_reason")) if start.get("cancelled") else None
    return f"\n{reason}." if reason else ""


def _skip_line(items: list[dict[str, Any]], plan_date: date) -> str:
    """«Не получится? Пропущу» — у вхождения постоянного плана: строки плана
    нет, и «Убрать» ему не сделать, а «Не бегаю» на день — одно нажатие на
    сайте. Ссылка — на сайт, не без входа: пересланное сообщение снимало бы
    чужую субботу. У отменённого старта строки нет: старта не будет, и
    спрашивать, получится ли прийти, незачем."""
    if not any(
        item.get("id") is None and item.get("series") and not (item.get("start") or {}).get("cancelled")
        for item in items
    ):
        return ""
    return f"\n\nНе получится? {link('Пропущу', _skip_url(plan_date))}"


def _extra_lines(extra: ReminderExtras | None) -> str:
    """Погода, веха и кто ещё собирается — строками под стартом."""
    if extra is None:
        return ""
    lines = [line for line in (extra.weather, extra.milestone, extra.company) if line]
    return "\n" + "\n".join(lines) if lines else ""


def compose_reminder(
    items: list[dict[str, Any]], *, plan_date: date, extras: dict[str, ReminderExtras] | None = None
) -> tuple[str, str]:
    """(заголовок, текст) напоминания на все планы человека на дату.

    `items` — PlanItem владельца (run_plan_service.plan_items); заметку из
    них не берём. План всегда «пробежать», поэтому что человек собирался
    делать, текст не повторяет: «Завтра, сб 31 октября — Кузьминки, старт в
    9:00 (≈№214). Не опаздывайте!». Под стартом — погода, личная веха и кто
    ещё собирается (`extras` по ref плана, reminder_extras; решение Дмитрия
    30.10.2026). У постоянного плана — ещё «Не получится? Пропущу».
    Последней строкой — «Напоминания не нужны? Выключить».
    """
    extras = extras or {}
    day = day_label(plan_date)
    skip = _skip_line(items, plan_date)
    all_cancelled = all((item.get("start") or {}).get("cancelled") for item in items)
    title = "Завтра старт отменён" if all_cancelled else "Завтра старт"
    tail = "" if all_cancelled else " Не опаздывайте!"
    if len(items) == 1:
        item = items[0]
        location = item["location"]
        place = _location_label(location["name"], location["slug"])
        start = item["start"]
        text = f"Завтра, {day} — {place}, {_start_phrase(start)}.{tail}{_reason_line(start)}"
        extra = _extra_lines(extras.get(item["ref"]))
        if extra:
            text += "\n" + extra
        return title, f"{text}{skip}\n\n{_opt_out_line()}"
    blocks = []
    for item in items:
        location = item["location"]
        place = _location_label(location["name"], location["slug"])
        blocks.append(
            f"📍 {place} — {_start_phrase(item['start'])}{_reason_line(item['start'])}"
            f"{_extra_lines(extras.get(item['ref']))}"
        )
    separator = "\n\n" if any(extras.get(item["ref"]) for item in items) else "\n"
    text = f"Завтра, {day}, у вас в планах:\n\n" + separator.join(blocks)
    if tail:
        text += "\n\n" + tail.strip()
    return title, f"{text}{skip}\n\n{_opt_out_line()}"


def _sort_key(item: dict[str, Any]) -> tuple[str, str]:
    start = item.get("start") or {}
    # Без времени — в конце: «время уточняйте» после известного.
    return (start.get("start_time") or "99:99", (item.get("location") or {}).get("name") or "")


def _plan_zones(db: Session, location_ids: list[UUID]) -> dict[UUID, tzinfo]:
    """Пояс опорной строки плана: IANA-зона из сбора погоды, иначе смещение от Москвы."""
    ids = list({location_id for location_id in location_ids if location_id is not None})
    if not ids:
        return {}
    rows = db.execute(
        select(Location.id, Location.timezone, Location.tz_offset_moscow).where(Location.id.in_(ids))
    ).all()
    return {location_id: _zone_of(zone_name, offset) for location_id, zone_name, offset in rows}


def reminder_due(local_now: datetime, plan_date: date) -> bool:
    return local_now.date() + timedelta(days=1) == plan_date and REMINDER_HOUR_FROM <= local_now.hour < REMINDER_HOUR_TO


def send_plan_reminders(db: Session, now: datetime | None = None) -> int:
    """Напомнить о завтрашних планах тем, у кого у локации плана сейчас
    вечер. Возвращает число поставленных в очередь сообщений.

    Кандидатов отбираем дёшево — разовые планы подписчиков и даты окна у их
    постоянных планов (суббота или особая дата), пояс — по опорной строке; что
    на самом деле в плане (с «Пропущу», поездками и сном), спрашиваем у
    planned_starts только у тех, кому пора."""
    now = now or datetime.now(UTC)
    today = now.astimezone(MOSCOW_TZ).date()
    subscribers = select(UserNotificationChannel.user_id).where(UserNotificationChannel.enabled.is_(True))
    candidates = reminder_candidates(
        db,
        date_from=today,
        date_to=today + timedelta(days=REMINDER_LOOKAHEAD_DAYS),
        user_filter=subscribers,
    )
    if not candidates:
        return 0
    zones = _plan_zones(db, [location_id for _user, _date, location_id in candidates if location_id is not None])

    def zone_of(location_id: UUID | None) -> tzinfo:
        if location_id is None:
            return FALLBACK_TIMEZONE
        return zones.get(location_id, FALLBACK_TIMEZONE)

    # Группа «пора», как только пора хоть одному её плану: в сообщение уходят
    # все планы дня, иначе локация из другого пояса, чей вечер наступит позже,
    # упёрлась бы в ключ повтора и пропала.
    due: set[tuple[UUID, date]] = {
        (user_id, plan_date)
        for user_id, plan_date, location_id in candidates
        if reminder_due(now.astimezone(zone_of(location_id)), plan_date)
    }
    if not due:
        return 0
    identities = _Identities(db, today)
    groups: dict[tuple[UUID, date], list[Any]] = defaultdict(list)
    for entry in planned_starts(
        db,
        today=today,
        dates={plan_date for _user_id, plan_date in due},
        user_ids={user_id for user_id, _date in due},
        identities=identities,
    ):
        if (entry.user_id, entry.plan_date) in due:
            groups[(entry.user_id, entry.plan_date)].append(entry)
    if not groups:
        return 0

    users = {
        user.id: user
        for user in db.execute(select(User).where(User.id.in_({user_id for user_id, _d in groups}))).scalars()
    }
    url = _plans_url()
    ordered = sorted(groups.items(), key=lambda item: (str(item[0][0]), item[0][1]))
    items_by_group: dict[tuple[UUID, date], list[dict[str, Any]]] = {}
    for (user_id, plan_date), group in ordered:
        user = users.get(user_id)
        if user is None:
            continue
        try:
            items = [
                item
                for item in plan_items(db, user, group, today=today, identities=identities)
                if still_on(identities, item)
            ]
        except Exception:
            db.rollback()
            identities = _Identities(db, today)
            logger.exception("plan reminders: не удалось собрать планы пользователя %s на %s", user_id, plan_date)
            continue
        if items:
            items_by_group[(user_id, plan_date)] = sorted(items, key=_sort_key)
    try:
        extras = reminder_extras(db, groups, items_by_group, identities=identities, today=today)
    except Exception:
        # Погода, вехи и «кто ещё» — украшение: без них напоминание уходит.
        db.rollback()
        identities = _Identities(db, today)
        logger.exception("plan reminders: не удалось собрать погоду, вехи и попутчиков")
        extras = {}
    queued = 0
    for (user_id, plan_date), items in items_by_group.items():
        user = users[user_id]
        try:
            title, text = compose_reminder(
                items,
                plan_date=plan_date,
                extras={item["ref"]: extras[(user_id, item["ref"])] for item in items if (user_id, item["ref"]) in extras},
            )
            buttons = reminder_buttons(
                items, identities=identities, scan_codes=set(user_scan_codes(db, user_id))
            )
            delivery = notifications.notify_user(
                db,
                user,
                REMINDER_KIND,
                title=title,
                text=text,
                dedupe_key=f"{REMINDER_DEDUPE_PREFIX}:{plan_date.isoformat()}",
                url=url,
                url_label=PLANS_URL_LABEL,
                buttons=buttons,
                commit=False,
            )
            delivery_id = delivery.id if delivery is not None else None
            db.commit()
        except Exception:
            # Сбой одного человека не роняет напоминания остальным.
            db.rollback()
            identities = _Identities(db, today)
            logger.exception("plan reminders: не удалось напомнить пользователю %s о %s", user_id, plan_date)
            continue
        if delivery_id is not None:
            notifications.enqueue_delivery(delivery_id)
            queued += 1
    return queued


# ---------------------------------------------------------------- отмены


def _cancelled_rows(db: Session, changes: list[CancellationChange]) -> list[tuple[Location, str, Any, str | None]]:
    """(строка, код системы, расписание, причина) по изменениям «отменён»."""
    reasons = {(item.platform_code, item.slug): item.reason for item in changes if item.cancelled}
    if not reasons:
        return []
    rows = db.execute(
        select(Location, Platform.code, LocationDescription.schedule_parsed)
        .join(Platform, Platform.id == Location.platform_id)
        .outerjoin(LocationDescription, LocationDescription.location_id == Location.id)
        .where(tuple_(Platform.code, Location.external_key).in_(list(reasons)))
    ).all()
    return [(location, code, schedule, reasons.get((code, location.external_key))) for location, code, schedule in rows]


def compose_cancellation(*, name: str, slug: str, start_date: date, reason: str | None) -> str:
    place = _location_label(name, slug)
    text = f"{place}, {day_label(start_date)}: старт отменён."
    cleaned = (reason or "").strip()
    if cleaned:
        text += f"\n{cleaned}"
    # Почему сообщение пришло мимо фильтров «Отмен стартов»: человек сам
    # отметил эту дату.
    text += "\n\nЭтот старт был у вас в планах."
    return text


def notify_plan_cancellations(db: Session, changes: list[CancellationChange], now: datetime | None = None) -> int:
    """«Старт отменён» тем, кто запланировал отменённый ближайший старт.
    Возвращает число поставленных в очередь сообщений.

    Дата отменённого старта — cancelled_start_date от сегодняшнего дня по
    Москве, та же, что календарь помечает «отменён»: у отмены в источниках
    даты нет, только флаг, и он о ближайшей субботе (особая дата забирает
    его, только если она впереди и её старт подтверждён прошлым годом). По
    поясу локации — только «старт уже прошёл»: у Барнаула в сб 20:51 МСК
    местное уже воскресенье, и местный день отдавал флаг следующей субботе,
    хотя календарь и общая рассылка относят его к сегодняшней (ревью
    28.10.2026, NOTIFY-1).
    """
    now = now or datetime.now(UTC)
    url = _plans_url()
    queued: list[UUID] = []
    today = now.astimezone(MOSCOW_TZ).date()
    try:
        rows = _cancelled_rows(db, changes)
        if not rows:
            return 0
        keys = canonical_identity_keys(db, [location.id for location, _code, _schedule, _reason in rows])
        for location, code, schedule, reason in rows:
            zone = _zone_of(location.timezone, location.tz_offset_moscow)
            key = keys.get(location.id, f"location:{location.id}")
            identity = identity_rows(db, key)
            history_ids = [row.id for row, _row_code in identity] or [location.id]
            start_date = cancelled_start_date(db, history_ids, code, today)
            # Старт уже прошёл или идёт — отмена записана задним числом, как в
            # still_ahead общей рассылки. Время особой даты у каждой локации
            # своё, поэтому «уже идёт» — только у субботы (start_already_passed).
            local_today = now.astimezone(zone).date()
            if start_date < local_today or (
                start_date == local_today and start_already_passed(zone, schedule or None, now)
            ):
                logger.info("plan cancel notify: %s/%s — старт уже прошёл, не шлём", code, location.external_key)
                continue
            # Разовые и постоянные: «Пропущу» и поездка в этот день вхождение
            # уже сняли — им отмена не нужна.
            plans = planned_starts(
                db,
                today=today,
                dates=[start_date],
                identity=(key, list({*history_ids, location.id})),
            )
            if not plans:
                continue
            slug, name = identity_brief(db, key, identity)
            text = compose_cancellation(
                name=name or location.name,
                slug=slug or location.external_key,
                start_date=start_date,
                reason=reason,
            )
            # Дрейф ключа мог оставить человеку две строки одной точки —
            # сообщение одно (его держит и ключ повтора).
            user_ids = list(dict.fromkeys(plan.user_id for plan in plans))
            users = db.execute(select(User).where(User.id.in_(user_ids))).scalars().all()
            for user in users:
                delivery = notifications.notify_user(
                    db,
                    user,
                    CANCELLATION_KIND,
                    title="Старт отменён",
                    text=text,
                    dedupe_key=f"{CANCEL_DEDUPE_PREFIX}:{start_date.isoformat()}:{key}",
                    url=url,
                    url_label=PLANS_URL_LABEL,
                    commit=False,
                )
                if delivery is not None:
                    queued.append(delivery.id)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("plan cancel notify: рассылка планирующим не удалась")
        return 0
    for delivery_id in queued:
        notifications.enqueue_delivery(delivery_id)
    return len(queued)


# ---------------------------------------------------------------- постоянный план уснул


def compose_series_sleep(*, name: str, slug: str, misses: int, linked: bool = True) -> tuple[str, str]:
    """(заголовок, текст) «Ещё ходите?» — постоянный план уснул.

    Без дат и без счёта неявок: «Никаких счётчиков неявок» и «без упрёка».
    Название — отдельно, через двоеточие: «в Зеленограде» из произвольного
    имени не собрать. Кнопки ведут на сайт (/plans с подтверждением одним
    нажатием), а не без входа: пересланное сообщение снимало бы чужой план.

    linked=False — у человека нет привязки к системам точки: его пробежек мы
    не видим, и «три старта подряд вас там не было» было бы неправдой. Так и
    пишем — план уснул, потому что в протоколах его нет, — и зовём привязать
    профиль: привяжет — пробежка там вернёт план сама. Имени не обещаем: у
    приватных и детей его на странице не было и до паузы.

    Каждая ссылка — своей строкой (или в конце строки): в VK и текстовой
    части письма адрес уезжает под подпись, и у двух ссылок в одной строке не
    понять, какой адрес «Продолжить», а какой «Остановить».
    """
    place = _location_label(name, slug)
    count = _MISSES_WORDS.get(misses, str(misses))
    starts = plural_form(misses, ("старт", "старта", "стартов"))
    if linked:
        why = f"{count} {starts} подряд вас там не было"
    else:
        why = f"{count} {starts} подряд мы не видели вас в протоколах"
    text = (
        f"{place}: ещё ходите? {why}, и постоянный план на паузе — на странице локации вас пока не видно.\n\n"
        f"{link('Продолжить', series_url('series-resume'))}\n{link('Остановить', series_url('series-stop'))}"
    )
    if linked:
        text += "\n\nПробежите или поволонтёрите там — план продолжится сам."
    else:
        text += (
            "\n\nПрофиль системы не привязан к сайту, и ваших пробежек нам не видно. Привяжите его — "
            f"и план продолжится сам, как только вы там пробежите: {link('Привязать профиль', _profiles_url())}"
        )
    return "Постоянный план на паузе", text
