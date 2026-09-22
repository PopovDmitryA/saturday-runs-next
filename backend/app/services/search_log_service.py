"""Журнал поисковых запросов сайта и отчёт по нему для админки.

Зачем: понять, что люди ищут и чего не находят. Пустые выдачи — прямой
список того, каких страниц, синонимов или локаций на сайте не хватает;
переходы по видам (страница / локация / человек) показывают, чем поиск
вообще полезен.

Журнал — диагностика, а не бизнес-логика: его сбой не должен превращаться в
ошибку у человека, поэтому запись обёрнута в try/except.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import SearchQueryLog
from app.services.site_search_service import normalize_log_query

logger = logging.getLogger(__name__)

LOG_MIN_QUERY_LENGTH = 2
CLICK_KINDS = ("page", "location", "person")
TOP_LIMIT = 50
RECENT_LIMIT = 50
_COUNT_CAP = 10_000


def _count(value: Any) -> int:
    """Счётчик из тела бекона: целое от 0; мусор — ноль, а не ошибка."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(number, _COUNT_CAP))


def record_search(db: Session, payload: dict[str, Any], *, is_authed: bool) -> bool:
    """Записать один поиск. False — запрос отброшен (пустой, короткий, сбой).

    Тело приходит от navigator.sendBeacon и никак не проверено, поэтому
    разбираем его вручную и терпим мусор: лишние поля игнорируем, незнакомый
    вид перехода записываем как «без перехода».
    """
    query = normalize_log_query(str(payload.get("query") or ""))
    if len(query) < LOG_MIN_QUERY_LENGTH:
        return False
    corrected_raw = payload.get("corrected_query")
    corrected = normalize_log_query(str(corrected_raw)) if corrected_raw else ""
    clicked_kind = payload.get("clicked_kind")
    if clicked_kind not in CLICK_KINDS:
        clicked_kind = None
    clicked_target = payload.get("clicked_target")
    target = str(clicked_target).strip()[:200] if clicked_kind and clicked_target else None
    try:
        db.add(
            SearchQueryLog(
                query=query,
                corrected_query=corrected or None,
                pages_found=_count(payload.get("pages_found")),
                locations_found=_count(payload.get("locations_found")),
                people_found=_count(payload.get("people_found")),
                clicked_kind=clicked_kind,
                clicked_target=target or None,
                is_authed=bool(is_authed),
                is_mobile=bool(payload.get("is_mobile")),
            )
        )
        db.commit()
        return True
    except Exception:  # noqa: BLE001 — журнал не должен ломать сайт
        logger.exception("search log: failed to record query")
        db.rollback()
        return False


def _zero_results() -> Any:
    return (SearchQueryLog.pages_found + SearchQueryLog.locations_found + SearchQueryLog.people_found) == 0


def get_search_log_report(db: Session, *, period_days: int = 30) -> dict[str, Any]:
    since = datetime.now(timezone.utc) - timedelta(days=period_days)
    in_period = SearchQueryLog.created_at >= since
    zero = _zero_results()

    total, zero_total = (
        db.query(func.count(SearchQueryLog.id), func.count(SearchQueryLog.id).filter(zero))
        .filter(in_period)
        .one()
    )

    top_queries = [
        {
            "query": row.query,
            "count": int(row.count),
            "zero_results_count": int(row.zero_results_count),
            "clicks": int(row.clicks),
        }
        for row in db.query(
            SearchQueryLog.query.label("query"),
            func.count(SearchQueryLog.id).label("count"),
            func.count(SearchQueryLog.id).filter(zero).label("zero_results_count"),
            func.count(SearchQueryLog.clicked_kind).label("clicks"),
        )
        .filter(in_period)
        .group_by(SearchQueryLog.query)
        .order_by(func.count(SearchQueryLog.id).desc(), SearchQueryLog.query)
        .limit(TOP_LIMIT)
        .all()
    ]

    zero_result_queries = [
        {"query": row.query, "count": int(row.count), "last_at": row.last_at}
        for row in db.query(
            SearchQueryLog.query.label("query"),
            func.count(SearchQueryLog.id).label("count"),
            func.max(SearchQueryLog.created_at).label("last_at"),
        )
        .filter(in_period, zero)
        .group_by(SearchQueryLog.query)
        .order_by(func.count(SearchQueryLog.id).desc(), func.max(SearchQueryLog.created_at).desc())
        .limit(TOP_LIMIT)
        .all()
    ]

    clicks_by_kind = {kind: 0 for kind in (*CLICK_KINDS, "none")}
    for kind, count in (
        db.query(SearchQueryLog.clicked_kind, func.count(SearchQueryLog.id))
        .filter(in_period)
        .group_by(SearchQueryLog.clicked_kind)
        .all()
    ):
        key = kind if kind in CLICK_KINDS else "none"
        clicks_by_kind[key] += int(count)

    # Сутки — московские: так читается вся остальная статистика сайта.
    local_day = func.date(func.timezone("Europe/Moscow", SearchQueryLog.created_at))
    daily = [
        {"date": day, "count": int(count)}
        for day, count in db.query(local_day, func.count(SearchQueryLog.id))
        .filter(in_period)
        .group_by(local_day)
        .order_by(local_day)
        .all()
    ]

    recent = [
        {
            "created_at": row.created_at,
            "query": row.query,
            "corrected_query": row.corrected_query,
            "pages_found": row.pages_found,
            "locations_found": row.locations_found,
            "people_found": row.people_found,
            "clicked_kind": row.clicked_kind,
            "clicked_target": row.clicked_target,
            "is_authed": row.is_authed,
            "is_mobile": row.is_mobile,
        }
        for row in db.query(SearchQueryLog)
        .filter(in_period)
        .order_by(SearchQueryLog.created_at.desc(), SearchQueryLog.id.desc())
        .limit(RECENT_LIMIT)
        .all()
    ]

    return {
        "period_days": period_days,
        "total": int(total or 0),
        "zero_result_total": int(zero_total or 0),
        "top_queries": top_queries,
        "zero_result_queries": zero_result_queries,
        "clicks_by_kind": clicks_by_kind,
        "daily": daily,
        "recent": recent,
    }
