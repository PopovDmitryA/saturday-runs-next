"""Журнал поисковых запросов сайта и отчёт по нему для админки.

Зачем: понять, что люди ищут и чего не находят. Главный список — «искали и
никуда не перешли»: человек что-то увидел, но нужного там не было. Пустая
выдача — лишь частный случай: «погода» с одиннадцатью Погодаевыми пустой не
считалась, хотя страницу погоды человек так и не нашёл. Переходы по видам
(страница / локация / человек с профилем / человек из протоколов) показывают,
чем поиск вообще полезен.

Второй журнал — воронка «Это вы?» (search_claim_events): человек из протоколов
нажал свою строку, вошёл и привязал её. Этапы пишет сервер по токену строки,
отчёт — блок claim_funnel.

Журнал — диагностика, а не бизнес-логика: его сбой не должен превращаться в
ошибку у человека, поэтому запись обёрнута в try/except.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, func, not_
from sqlalchemy.orm import Session

from app.models import SearchClaimEvent, SearchQueryLog
from app.services.participant_search_service import strip_control_chars
from app.services.site_search_service import normalize_log_query

logger = logging.getLogger(__name__)

LOG_MIN_QUERY_LENGTH = 2
# person — человек с профилем на сайте (переход на /users/…), participant —
# человек из протоколов (открыл «Это вы?»; target — «система#позиция строки»).
CLICK_KINDS = ("page", "location", "person", "participant")
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
    if payload.get("people_skipped") is True and clicked_kind is None:
        # Поиск людей в этом запросе не делался (все места заняты чужим залпом,
        # таймаут — people_skipped в ответе /api/search): «никого не нашли» тут
        # неправда, и строка лишь засоряла бы «Не нашлось ничего» и «Искали и
        # никуда не перешли». С переходом — пишем: человек нашёл, что искал.
        return False
    clicked_target = payload.get("clicked_target")
    # Управляющие символы — прочь, как и из самого запроса (normalize_log_query):
    # нулевой байт Postgres в текст не примет, и каждый такой бекон писал бы
    # трейсбек в лог.
    target = strip_control_chars(str(clicked_target)).strip()[:200] if clicked_kind and clicked_target else None
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


# ---------------------------------------------------------------------------
# Воронка «Это вы?»

CLAIM_OPEN = "open"
CLAIM_LOGIN = "login"
CLAIM_LINKED = "linked"
CLAIM_FAILED = "failed"
CLAIM_DECLINED = "declined"


def _claim_event_exists(db: Session, ref: str, stage: str, *, is_authed: bool | None, status: int | None) -> bool:
    query = db.query(SearchClaimEvent.id).filter(SearchClaimEvent.ref == ref, SearchClaimEvent.stage == stage)
    if is_authed is not None:
        query = query.filter(SearchClaimEvent.is_authed.is_(is_authed))
    if status is not None:
        query = query.filter(SearchClaimEvent.status == status)
    return query.first() is not None


def record_claim_event(
    db: Session,
    ref: str,
    stage: str,
    *,
    is_authed: bool,
    platform_code: str | None = None,
    status: int | None = None,
) -> bool:
    """Записать этап воронки «Это вы?» по ref токена. False — повтор или сбой.

    Этап пишется один раз на заход: окно открывают и перезагружают по
    нескольку раз, а отчёт считает заходы (distinct ref), не нажатия, —
    повторы только раздували бы таблицу. «login» отдельного вызова не
    требует: его отмечает открытие вошедшим, если этот ref уже открывали
    гостем, — человек вошёл по «Войти и привязать».
    """
    stages = [stage]
    try:
        if (
            stage == CLAIM_OPEN
            and is_authed
            and _claim_event_exists(db, ref, CLAIM_OPEN, is_authed=False, status=None)
            and not _claim_event_exists(db, ref, CLAIM_LOGIN, is_authed=None, status=None)
        ):
            stages.append(CLAIM_LOGIN)
        written = False
        for item in stages:
            if _claim_event_exists(db, ref, item, is_authed=is_authed, status=status):
                continue
            db.add(
                SearchClaimEvent(
                    ref=ref,
                    stage=item,
                    is_authed=bool(is_authed),
                    platform_code=platform_code,
                    status=status,
                )
            )
            written = True
        if written:
            db.commit()
        return written
    except Exception:  # noqa: BLE001 — журнал не должен ломать сайт
        logger.exception("search claim: failed to record stage %s", stage)
        db.rollback()
        return False


def _claim_funnel(db: Session, since: datetime) -> dict[str, Any]:
    """Заходы «Это вы?» за период: гостевые (открыл → вошёл → привязал) и вошедших.

    Заход — ref токена. Гостевой — хоть раз открыт без входа; открытые только
    вошедшим считаются отдельно, иначе «вошли» у гостей размывалось бы теми,
    кто и так был в аккаунте.
    """
    event = SearchClaimEvent
    per_ref = (
        db.query(
            event.ref.label("ref"),
            func.bool_or(and_(event.stage == CLAIM_OPEN, event.is_authed.is_(False))).label("guest_open"),
            func.bool_or(and_(event.stage == CLAIM_OPEN, event.is_authed.is_(True))).label("authed_open"),
            func.bool_or(event.stage == CLAIM_LOGIN).label("login"),
            func.bool_or(event.stage == CLAIM_LINKED).label("linked"),
            func.bool_or(event.stage == CLAIM_DECLINED).label("declined"),
        )
        .filter(event.created_at >= since)
        .group_by(event.ref)
        .subquery()
    )
    ref = per_ref.c
    authed_only = and_(ref.authed_open, not_(ref.guest_open))
    guest_opened, guest_logged_in, guest_linked, authed_opened, authed_linked, declined = db.query(
        func.count().filter(ref.guest_open),
        func.count().filter(and_(ref.guest_open, ref.login)),
        func.count().filter(and_(ref.guest_open, ref.linked)),
        func.count().filter(authed_only),
        func.count().filter(and_(authed_only, ref.linked)),
        func.count().filter(ref.declined),
    ).one()
    failed_by_status = {
        str(status): int(count)
        for status, count in db.query(event.status, func.count(func.distinct(event.ref)))
        .filter(event.created_at >= since, event.stage == CLAIM_FAILED, event.status.isnot(None))
        .group_by(event.status)
        .order_by(event.status)
        .all()
    }
    return {
        "guest_opened": int(guest_opened or 0),
        "guest_logged_in": int(guest_logged_in or 0),
        "guest_linked": int(guest_linked or 0),
        "authed_opened": int(authed_opened or 0),
        "authed_linked": int(authed_linked or 0),
        "declined": int(declined or 0),
        "failed_by_status": failed_by_status,
    }


def _zero_results() -> Any:
    return (SearchQueryLog.pages_found + SearchQueryLog.locations_found + SearchQueryLog.people_found) == 0


def get_search_log_report(db: Session, *, period_days: int = 30) -> dict[str, Any]:
    since = datetime.now(timezone.utc) - timedelta(days=period_days)
    in_period = SearchQueryLog.created_at >= since
    zero = _zero_results()
    no_click = SearchQueryLog.clicked_kind.is_(None)

    total, zero_total, no_click_total = (
        db.query(
            func.count(SearchQueryLog.id),
            func.count(SearchQueryLog.id).filter(zero),
            func.count(SearchQueryLog.id).filter(no_click),
        )
        .filter(in_period)
        .one()
    )

    no_click_queries = [
        {
            "query": row.query,
            "count": int(row.count),
            "zero_results_count": int(row.zero_results_count),
            "last_at": row.last_at,
        }
        for row in db.query(
            SearchQueryLog.query.label("query"),
            func.count(SearchQueryLog.id).label("count"),
            func.count(SearchQueryLog.id).filter(zero).label("zero_results_count"),
            func.max(SearchQueryLog.created_at).label("last_at"),
        )
        .filter(in_period, no_click)
        .group_by(SearchQueryLog.query)
        .order_by(func.count(SearchQueryLog.id).desc(), func.max(SearchQueryLog.created_at).desc())
        .limit(TOP_LIMIT)
        .all()
    ]

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
        "no_click_total": int(no_click_total or 0),
        "no_click_queries": no_click_queries,
        "top_queries": top_queries,
        "zero_result_queries": zero_result_queries,
        "clicks_by_kind": clicks_by_kind,
        "daily": daily,
        "recent": recent,
        "claim_funnel": _claim_funnel(db, since),
    }
