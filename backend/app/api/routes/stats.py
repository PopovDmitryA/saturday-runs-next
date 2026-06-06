from __future__ import annotations

from fastapi import APIRouter, Request, Response

from app.api.deps import get_client_ip
from app.core.rate_limit import check_rate_limit
from app.core.site_stats import record_pageview
from app.schemas.admin_stats import PageviewRecordRequest

router = APIRouter(prefix="/stats", tags=["stats"])


@router.post("/pageview", status_code=204)
def record_page_view(
    body: PageviewRecordRequest,
    request: Request,
) -> Response:
    client_ip = get_client_ip(request)
    if client_ip != "unknown":
        allowed = check_rate_limit(f"stats:pageview:ip:{client_ip}", 120, 60)
        if not allowed:
            return Response(status_code=204)
    record_pageview(
        body.path,
        authenticated=body.authenticated,
        visitor_key=body.visitor_key,
    )
    return Response(status_code=204)
