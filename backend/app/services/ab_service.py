"""Сырые продуктовые события в ab_events: приём и белые списки.

Дополняет событийную аналитику страниц (page_analytics_service): та отвечает
на «сколько смотрят страницы», эта — на отдельные продуктовые вопросы, которые
не сводятся к просмотрам. Сводки — в /admin/page-analytics.

Таблица названа по своему первому жильцу — АБ-тесту главной (эксперимент
home_v1, 27.07–22.08.2026). Тест завершён, вариант B принят как единственная
главная, инструментовка эксперимента снята; сырые события остались в таблице,
у новых событий главной variant = "-". Живых каналов сейчас два:
- "home_v1" → home_link_click: куда главная уводит людей вглубь сайта;
- "share" → воронка фичи «Поделиться» (variant у них тоже "-").

visitor_key всегда анонимный ("a:<id>") и не меняется после логина — по нему
действия одного человека сшиваются сквозь VK-редирект (см. модель AbEvent).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import AbEvent, User

# Известные каналы; чужие имена не пишем — мусор из ручных запросов.
KNOWN_EXPERIMENTS = frozenset({"home_v1", "share"})

# Белый список типов событий (защита от мусора в свободном поле).
#
# Событий завершённого АБ-теста главной (variant_view, scroll_depth, cta_view,
# cta_click, period, chart_tab, teaser_preview, login_complete) здесь больше
# нет: фронт их не шлёт, а те, что ещё прилетят из давно открытых вкладок со
# старым бандлом, должны молча отброситься, а не дописываться в закрытую
# выборку.
KNOWN_EVENT_TYPES = frozenset(
    {
        # Переход по внутренней ссылке главной: value — "location:<slug>" или
        # "runner:<хендл>". Отвечает на вопрос «уводит ли главная людей вглубь
        # сайта» (сводка в /admin/page-analytics).
        "home_link_click",
        # ── Канал "share" (фича «Поделиться», сводка в /admin/page-analytics) ──
        "share_moment_shown",  # value: "сюжет:вход" — показ области-приглашения
        "share_open",  # value: "сюжет:вход" — открытие шторки
        "share_template_switch",  # value: "look:<id>" | "format:<id>"
        "share_customize",  # value: open | metrics | photo
        "share_success",  # value: "канал:сюжет", канал system | download | copy
        "og_preview_fetch",  # value: "тип:ключ" — бот развернул ссылку (пишет сервер)
    }
)

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

    Возвращает записанное событие или None (отброшено).
    """
    if experiment not in KNOWN_EXPERIMENTS or event_type not in KNOWN_EVENT_TYPES:
        return None

    event = AbEvent(
        experiment=experiment,
        variant=variant[:8],
        visitor_key=visitor_key[:80],
        event_type=event_type,
        value=value[:128],
        path=path[:256],
        cohort="",
        viewer_user_id=viewer.id if viewer is not None else None,
    )
    db.add(event)
    db.commit()
    return event
