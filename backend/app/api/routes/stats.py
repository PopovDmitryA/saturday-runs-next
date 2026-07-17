from __future__ import annotations

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import get_client_ip, get_optional_session_user_id
from app.core.rate_limit import check_rate_limit
from app.core.site_stats import record_pageview
from app.db.session import get_db
from app.schemas.admin_stats import PageleaveRecordRequest, PageviewRecordRequest
from app.services.page_analytics_service import record_page_leave, record_page_view

router = APIRouter(prefix="/stats", tags=["stats"])


def _rate_limited(request: Request, bucket: str) -> bool:
    client_ip = get_client_ip(request)
    if client_ip == "unknown":
        return False
    return not check_rate_limit(f"stats:{bucket}:ip:{client_ip}", 120, 60)


@router.post("/pageview", status_code=204)
def record_page_view_endpoint(
    body: PageviewRecordRequest,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    viewer_user_id: Annotated[UUID | None, Depends(get_optional_session_user_id)],
) -> Response:
    if _rate_limited(request, "pageview"):
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
        viewer_user_id=viewer_user_id,
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
    record_page_leave(db, view_id=body.view_id, duration_sec=body.duration_sec)
    return Response(status_code=204)
