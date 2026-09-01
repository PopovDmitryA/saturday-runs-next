from __future__ import annotations

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import get_client_ip, get_optional_user
from app.config import Settings, get_settings
from app.core.admin import is_admin_user
from app.core.bot_detection import is_bot_user_agent
from app.core.rate_limit import check_rate_limit
from app.core.site_stats import record_pageview
from app.db.session import get_db
from app.models import User
from app.schemas.admin_stats import AbEventRecordRequest, PageleaveRecordRequest, PageviewRecordRequest
from app.services.ab_service import record_ab_event
from app.services.page_analytics_service import record_page_leave, record_page_view

router = APIRouter(prefix="/stats", tags=["stats"])


def _rate_limited(request: Request, bucket: str) -> bool:
    client_ip = get_client_ip(request)
    if client_ip == "unknown":
        return False
    return not check_rate_limit(f"stats:{bucket}:ip:{client_ip}", 120, 60)


def _is_bot(request: Request) -> bool:
    return is_bot_user_agent(request.headers.get("user-agent"))


@router.post("/pageview", status_code=204)
def record_page_view_endpoint(
    body: PageviewRecordRequest,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    viewer: Annotated[User | None, Depends(get_optional_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    if _rate_limited(request, "pageview"):
        return Response(status_code=204)
    # Краулеры, исполняющие JS, доходят сюда наравне с людьми, но localStorage
    # между страницами не хранят — visitor_key у них новый на каждой странице,
    # поэтому один обход сайта выглядел как тысячи «уникальных посетителей».
    if _is_bot(request):
        return Response(status_code=204)
    # Админ ходит по сайту, чтобы его проверять, а не пользоваться им: на фоне
    # десятков просмотров в сутки его обходы заметно двигают и «Популярность»,
    # и счётчики «Статистики». Поэтому его просмотры не пишутся вовсе — ни в
    # Postgres, ни в Redis. Событие pageleave по этому view_id потом обновит
    # ноль строк, отдельно его фильтровать не нужно.
    if viewer is not None and is_admin_user(viewer, settings):
        return Response(status_code=204)
    record_pageview(
        body.path,
        authenticated=body.authenticated,
        visitor_key=body.visitor_key,
    )
    record_page_view(
        db,
        view_id=body.view_id or uuid4(),
        path=body.path,
        visitor_key=body.visitor_key,
        viewer_user_id=viewer.id if viewer is not None else None,
    )
    return Response(status_code=204)


@router.post("/event", status_code=204)
def record_ab_event_endpoint(
    body: AbEventRecordRequest,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    viewer: Annotated[User | None, Depends(get_optional_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    if _rate_limited(request, "abevent"):
        return Response(status_code=204)
    if _is_bot(request):
        return Response(status_code=204)
    # Обходы админа не должны двигать продуктовые метрики — как в pageview.
    if viewer is not None and is_admin_user(viewer, settings):
        return Response(status_code=204)
    record_ab_event(
        db,
        experiment=body.experiment,
        variant=body.variant,
        visitor_key=body.visitor_key,
        event_type=body.event_type,
        value=body.value,
        path=body.path,
        viewer=viewer,
    )
    return Response(status_code=204)


@router.post("/pageleave", status_code=204)
def record_page_leave_endpoint(
    body: PageleaveRecordRequest,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    if _rate_limited(request, "pageleave"):
        return Response(status_code=204)
    # Просмотра от бота в базе нет, дозаполнять нечего — но и лишний UPDATE по
    # несуществующему view_id делать незачем.
    if _is_bot(request):
        return Response(status_code=204)
    record_page_leave(db, view_id=body.view_id, duration_sec=body.duration_sec)
    return Response(status_code=204)
