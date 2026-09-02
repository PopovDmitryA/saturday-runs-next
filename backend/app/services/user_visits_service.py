"""Журнал визитов одного пользователя (для админки).

Журнал входов (login_journal_service) отвечает на вопрос «когда человек
авторизовался». На вопрос «когда он в последний раз пользовался сайтом» он не
отвечает вовсе: сессия живёт месяцами, и между двумя входами может лежать
сотня заходов. Здесь ответ берётся из просмотров страниц — тех же событий,
на которых стоит «Популярность» (page_view_events.viewer_user_id).

Просмотры склеиваются в визиты: подряд идущие страницы с разрывом меньше
VISIT_GAP — это один заход, а не десять. Сырые события живут ограниченный срок
(settings.page_events_retention_days), поэтому глубина журнала конечна; сам
последний визит от чистки не зависит — он продублирован в users.last_seen_at.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import PageViewEvent
from app.services.page_analytics_service import STATS_TIMEZONE

# Разрыв, после которого следующая страница считается новым заходом.
VISIT_GAP = timedelta(minutes=30)

# Сколько сырых просмотров тянем на журнал: у активного человека их тысячи,
# а в панели видно последние заходы.
DEFAULT_EVENT_LIMIT = 300
# Страниц внутри одного визита показываем не больше — остальные считаем.
MAX_PAGES_PER_VISIT = 30


@dataclass
class VisitPage:
    ts: datetime
    path: str
    page_type: str
    entity_key: str
    duration_sec: int | None


@dataclass
class Visit:
    started_at: datetime
    ended_at: datetime
    views: int
    duration_sec: int
    pages: list[VisitPage] = field(default_factory=list)
    pages_hidden: int = 0


@dataclass
class VisitsJournal:
    visits: list[Visit]
    total_views: int
    # Сколько визитов попало в журнал (по взятой пачке событий), см. truncated.
    visits_shown: int
    days: int
    first_view_at: datetime | None
    last_view_at: datetime | None
    # Журнал показывает не все визиты окна: сырых событий больше, чем взяли.
    truncated: bool


def group_visits(events: list[VisitPage]) -> list[Visit]:
    """Склеить просмотры (от свежих к старым) в визиты по разрыву VISIT_GAP."""
    visits: list[Visit] = []
    current: list[VisitPage] = []
    for event in events:
        if current and current[-1].ts - event.ts > VISIT_GAP:
            visits.append(_build_visit(current))
            current = []
        current.append(event)
    if current:
        visits.append(_build_visit(current))
    return visits


def _build_visit(pages_desc: list[VisitPage]) -> Visit:
    pages = list(reversed(pages_desc))
    started_at = pages[0].ts
    last = pages[-1]
    # Хвост визита: последняя страница длилась ровно столько, сколько на ней
    # пробыли (если бекон долистался); иначе визит кончается её открытием.
    ended_at = last.ts + timedelta(seconds=last.duration_sec or 0)
    return Visit(
        started_at=started_at,
        ended_at=ended_at,
        views=len(pages),
        duration_sec=max(0, int((ended_at - started_at).total_seconds())),
        pages=pages[:MAX_PAGES_PER_VISIT],
        pages_hidden=max(0, len(pages) - MAX_PAGES_PER_VISIT),
    )


def get_user_visits(
    db: Session, user_id: UUID, *, limit_events: int = DEFAULT_EVENT_LIMIT
) -> VisitsJournal:
    rows = (
        db.query(
            PageViewEvent.ts,
            PageViewEvent.path,
            PageViewEvent.page_type,
            PageViewEvent.entity_key,
            PageViewEvent.duration_sec,
        )
        .filter(PageViewEvent.viewer_user_id == user_id, PageViewEvent.is_bot.is_(False))
        .order_by(PageViewEvent.ts.desc())
        .limit(limit_events)
        .all()
    )
    events = [
        VisitPage(
            ts=row.ts,
            path=row.path,
            page_type=row.page_type,
            entity_key=row.entity_key,
            duration_sec=row.duration_sec,
        )
        for row in rows
    ]

    # Итоги считаем по всему окну хранения, а не по взятой пачке: «сколько
    # всего страниц и в скольких днях» должно быть честным.
    totals = (
        db.query(
            func.count(PageViewEvent.id),
            func.min(PageViewEvent.ts),
            func.max(PageViewEvent.ts),
            func.count(func.distinct(_moscow_day(PageViewEvent.ts))),
        )
        .filter(PageViewEvent.viewer_user_id == user_id, PageViewEvent.is_bot.is_(False))
        .one()
    )
    total_views = int(totals[0] or 0)
    visits = group_visits(events)
    return VisitsJournal(
        visits=visits,
        total_views=total_views,
        visits_shown=len(visits),
        days=int(totals[3] or 0),
        first_view_at=totals[1],
        last_view_at=totals[2],
        truncated=total_views > len(events),
    )


def _moscow_day(column: object) -> object:
    """День визита по московскому времени — как в дневных агрегатах."""
    return func.date(func.timezone(str(STATS_TIMEZONE), column))
