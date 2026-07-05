from __future__ import annotations

import logging

from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import get_settings
from app.core.abuse_protection import check_abuse_request
from app.core.client_ip import get_client_ip

logger = logging.getLogger(__name__)


class AbuseProtectionMiddleware:
    """Pure ASGI middleware (NOT BaseHTTPMiddleware).

    BaseHTTPMiddleware runs the downstream app in a separate anyio task, so on
    client disconnect the cancellation does not propagate cleanly and generator
    dependencies (``get_db``) can be left parked at ``yield`` — the session never
    closes and stays "idle in transaction", leaking a DB pool slot forever
    (incident 05.07.2026: login died on QueuePool exhaustion). A plain ASGI
    passthrough introduces no extra task, so cancellation and dependency cleanup
    behave normally.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        settings = get_settings()
        request = Request(scope)
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
            response = Response(
                status_code=429,
                content='{"detail":"Too many requests. Try again later."}',
                media_type="application/json",
                headers={"Retry-After": str(retry_after)},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
