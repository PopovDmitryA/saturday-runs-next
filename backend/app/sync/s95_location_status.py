"""Что означает «неработающая» площадка s95: отмена субботы или закрытие.

JSON-реестр s95 обе ситуации показывает одинаково — `active=false` в
`/events.json` (и исчезнувшая карточка в `/pages.json`). Различить их можно
только на HTML-странице площадки: при отмене ближайшего старта там висит
красная плашка «Внимание! Ближайший старт отменён» и, как правило, причина.

До 27.08.2026 у s95 недельных отмен не встречалось, поэтому любую закрытую
карточку синк считал «не действует». В тот день Иваново отменило старт
29 августа — и площадка, которая бегает каждую субботу, получила бы у нас
статус закрытой навсегда. Отсюда этот разбор: заявление системы читаем целиком,
а не по одному биту.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from app.s95.api_client import S95ApiLocation
from app.s95.fetch import fetch_page_html
from app.s95.parsers.location import parse_location_alert

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class S95LocationStatus:
    is_paused: bool
    is_cancelled: bool
    cancel_reason: str | None = None


ACTIVE_STATUS = S95LocationStatus(is_paused=False, is_cancelled=False, cancel_reason=None)


def location_page_url(entry: S95ApiLocation) -> str:
    return f"{entry.domain}/events/{entry.slug}"


def resolve_s95_location_status(
    entry: S95ApiLocation,
    *,
    fetch_html: Callable[[str], str] | None = None,
) -> S95LocationStatus:
    """Статус площадки по реестру, с походом на страницу для неработающих.

    Страница грузится только когда реестр сказал `active=false`: у s95 таких
    площадок единицы, а у работающих плашки нет по определению. Ошибку загрузки
    наверх не глушим — вызывающий сам решает, оставить ли прежние признаки.
    """
    if entry.active:
        return ACTIVE_STATUS

    fetch = fetch_html or (lambda url: fetch_page_html(url, reason="location_cancellation"))
    html = fetch(location_page_url(entry))
    alert = parse_location_alert(html)
    if alert is not None and alert.is_cancelled:
        logger.info("S95 %s: отмена ближайшего старта (%s)", entry.slug, alert.reason or "без причины")
        return S95LocationStatus(is_paused=False, is_cancelled=True, cancel_reason=alert.reason)
    # Плашки нет — карточка закрыта совсем, это «не действует».
    return S95LocationStatus(is_paused=True, is_cancelled=False, cancel_reason=None)
