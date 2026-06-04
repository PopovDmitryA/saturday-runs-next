from __future__ import annotations

from fastapi import Request


def get_client_ip(request: Request) -> str:
    """Best-effort client IP behind nginx / reverse proxy."""
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        first = forwarded_for.split(",")[0].strip()
        if first:
            return first

    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()

    if request.client is not None and request.client.host:
        return request.client.host

    return "unknown"
