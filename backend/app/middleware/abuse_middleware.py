from __future__ import annotations

import logging

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.config import get_settings
from app.core.abuse_protection import check_abuse_request
from app.core.client_ip import get_client_ip

logger = logging.getLogger(__name__)


class AbuseProtectionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        settings = get_settings()
        client_ip = get_client_ip(request)
        decision = check_abuse_request(client_ip, request.url.path, request.method, settings)

        if not decision.allowed:
            retry_after = decision.retry_after or settings.abuse_block_duration_seconds
            logger.warning(
                "Abuse protection blocked request ip=%s path=%s reason=%s",
                client_ip,
                request.url.path,
                decision.reason,
            )
            return Response(
                status_code=429,
                content='{"detail":"Too many requests. Try again later."}',
                media_type="application/json",
                headers={"Retry-After": str(retry_after)},
            )

        return await call_next(request)
