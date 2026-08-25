"""Сырые продуктовые события в ab_events: приём и белые списки.

Дополняет событийную аналитику страниц (page_analytics_service): та отвечает
на «сколько смотрят страницы», эта — на отдельные продуктовые вопросы, которые
не сводятся к просмотрам. Сводки — в /admin/page-analytics.

Таблица названа по своему первому жильцу — АБ-тесту главной (эксперимент
home_v1, 27.07–22.08.2026). Тест завершён, вариант B принят как единственная
главная, инструментовка эксперимента снята; сырые события остались в таблице,
у новых событий главной variant = "-". Живых каналов сейчас три:
- "funnel" → постоянный счётчик воронки регистрации (см. ниже);
- "home_v1" → home_link_click: куда главная уводит людей вглубь сайта;
- "share" → воронка фичи «Поделиться» (variant у них тоже "-").

visitor_key всегда анонимный ("a:<id>") и не меняется после логина — по нему
действия одного человека сшиваются сквозь VK-редирект (см. модель AbEvent).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import AbEvent, User

# Что каждому каналу разрешено писать. Список пер-канальный, а не общий: иначе
# событие с именем из чужого канала (например cta_click из давно открытой
# вкладки со старым бандлом, где оно принадлежало АБ-тесту главной) молча
# приземлялось бы в home_v1 и портило закрытую выборку теста.
_EVENT_TYPES_BY_EXPERIMENT: dict[str, frozenset[str]] = {
    # ── Постоянный счётчик воронки регистрации ──
    "funnel": frozenset(
        {
            # Знаменатель: главная открыта. Пишется и анонимам, и залогиненным —
            # различить их можно по viewer_user_id.
            "home_view",
            "cta_click",  # value: место кнопки — "hero" | "bottom" | "teaser"
            "auth_start",  # value: провайдер — "vk" | "yandex"
            "auth_done",  # cohort считает сервер: new (регистрация) | returning
        }
    ),
    # ── Главная: куда она уводит людей вглубь сайта ──
    # value — "location:<slug>" или "runner:<хендл>", сводка в /admin/page-analytics.
    # Событий завершённого АБ-теста (variant_view, scroll_depth, cta_view,
    # period, chart_tab, teaser_preview, login_complete) здесь нет намеренно.
    "home_v1": frozenset({"home_link_click"}),
    # ── Фича «Поделиться» (сводка в /admin/page-analytics) ──
    "share": frozenset(
        {
            "share_moment_shown",  # value: "сюжет:вход" — показ области-приглашения
            "share_open",  # value: "сюжет:вход" — открытие шторки
            "share_template_switch",  # value: "look:<id>" | "format:<id>"
            "share_customize",  # value: open | metrics | photo
            "share_success",  # value: "канал:сюжет", канал system | download | copy
            "og_preview_fetch",  # value: "тип:ключ" — бот развернул ссылку (пишет сервер)
        }
    ),
}

# Известные каналы; чужие имена не пишем — мусор из ручных запросов.
KNOWN_EXPERIMENTS = frozenset(_EVENT_TYPES_BY_EXPERIMENT)

# Канал постоянного счётчика воронки: главная → клик по CTA → старт входа →
# аккаунт. Не эксперимент, а приборная панель, которая включена всегда, поэтому
# variant у событий "-". Пятую ступень — привязку платформы — событием НЕ
# пишем: она уже есть в platform_links.linked_at, а дублирующее событие
# разъезжалось бы с таблицей (привязку можно сделать и снять из админки).
FUNNEL_EXPERIMENT = "funnel"

# Аккаунт моложе этого порога на момент auth_done = «вход создал аккаунт», то
# есть регистрация. Старше — вернувшийся участник, в конверсию не идёт.
NEW_USER_WINDOW = timedelta(hours=1)

# Типы событий, которым нужна когорта new/returning.
_COHORT_EVENT_TYPES = frozenset({"auth_done"})


def classify_login_cohort(user: User, now: datetime | None = None) -> str:
    moment = now or datetime.now(timezone.utc)
    created = user.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return "new" if moment - created <= NEW_USER_WINDOW else "returning"


def record_ab_event(
    db: Session,
    *,
    experiment: str,
    variant: str,
    visitor_key: str,
    event_type: str,
    value: str = "",
    path: str = "",
    viewer: User | None = None,
) -> AbEvent | None:
    """Пишет событие; неизвестный канал или тип молча отбрасывает.

    Возвращает записанное событие или None (отброшено). auth_done без
    авторизованного пользователя не имеет смысла — тоже отбрасывается.
    """
    if event_type not in _EVENT_TYPES_BY_EXPERIMENT.get(experiment, frozenset()):
        return None

    cohort = ""
    if event_type in _COHORT_EVENT_TYPES:
        if viewer is None:
            return None
        cohort = classify_login_cohort(viewer)

    event = AbEvent(
        experiment=experiment,
        variant=variant[:8],
        visitor_key=visitor_key[:80],
        event_type=event_type,
        value=value[:128],
        path=path[:256],
        cohort=cohort,
        viewer_user_id=viewer.id if viewer is not None else None,
    )
    db.add(event)
    db.commit()
    return event
