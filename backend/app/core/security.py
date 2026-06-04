from __future__ import annotations

import hashlib
import hmac
import secrets


def generate_token(length: int = 32) -> str:
    return secrets.token_urlsafe(length)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def generate_session_id() -> str:
    return secrets.token_urlsafe(32)


def sign_session_id(session_id: str, secret: str) -> str:
    signature = hmac.new(secret.encode(), session_id.encode(), hashlib.sha256).hexdigest()
    return f"{session_id}.{signature}"


def verify_signed_session_id(signed_value: str, secret: str) -> str | None:
    if "." not in signed_value:
        return None
    session_id, signature = signed_value.rsplit(".", 1)
    expected = hmac.new(secret.encode(), session_id.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    return session_id
