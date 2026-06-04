from __future__ import annotations

import base64
import hashlib
import secrets
from urllib.parse import urlencode

import httpx

from app.auth.providers.base import OAuthProfile
from app.config import Settings

VK_AUTHORIZE_URL = "https://id.vk.ru/authorize"
VK_TOKEN_URL = "https://id.vk.ru/oauth2/auth"
VK_USER_INFO_URL = "https://id.vk.ru/oauth2/user_info"
_CODE_VERIFIER_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"


def generate_code_verifier() -> str:
    return "".join(secrets.choice(_CODE_VERIFIER_ALPHABET) for _ in range(64))


def code_challenge_from_verifier(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def vk_authorize_url(settings: Settings, state: str, code_verifier: str) -> str:
    redirect_uri = vk_redirect_uri(settings)
    params = {
        "response_type": "code",
        "client_id": settings.vk_oauth_client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge_from_verifier(code_verifier),
        "code_challenge_method": "S256",
    }
    return f"{VK_AUTHORIZE_URL}?{urlencode(params)}"


def vk_exchange_code(
    settings: Settings,
    code: str,
    *,
    code_verifier: str,
    device_id: str,
    state: str,
) -> OAuthProfile:
    redirect_uri = vk_redirect_uri(settings)
    data = {
        "grant_type": "authorization_code",
        "code_verifier": code_verifier,
        "redirect_uri": redirect_uri,
        "code": code,
        "client_id": settings.vk_oauth_client_id,
        "device_id": device_id,
        "state": state,
    }
    response = httpx.post(VK_TOKEN_URL, data=data, timeout=20.0)
    response.raise_for_status()
    payload = response.json()
    if "error" in payload:
        raise ValueError(payload.get("error_description") or payload["error"])

    user_id = str(payload["user_id"])
    access_token = payload["access_token"]
    display_name, email = _fetch_vk_user_info(settings, access_token)
    return OAuthProfile(
        external_id=user_id,
        display_name=display_name,
        email=email,
        profile_json={"user_id": payload["user_id"], "email": email, "scope": payload.get("scope")},
    )


def _fetch_vk_user_info(settings: Settings, access_token: str) -> tuple[str | None, str | None]:
    response = httpx.post(
        VK_USER_INFO_URL,
        data={
            "client_id": settings.vk_oauth_client_id,
            "access_token": access_token,
        },
        timeout=20.0,
    )
    response.raise_for_status()
    payload = response.json()
    if "error" in payload:
        return None, None
    user = payload.get("user") or {}
    parts = [part for part in (user.get("first_name"), user.get("last_name")) if part]
    display_name = " ".join(parts) if parts else None
    email = user.get("email")
    return display_name, email


def vk_redirect_uri(settings: Settings) -> str:
    if settings.vk_oauth_redirect_uri:
        return settings.vk_oauth_redirect_uri
    return f"{settings.app_base_url.rstrip('/')}/api/auth/oauth/vk/callback"
