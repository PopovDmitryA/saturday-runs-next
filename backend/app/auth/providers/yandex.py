from __future__ import annotations

from urllib.parse import urlencode

import httpx

from app.auth.providers.base import OAuthProfile
from app.config import Settings


def yandex_authorize_url(settings: Settings, state: str) -> str:
    redirect_uri = yandex_redirect_uri(settings)
    params = {
        "response_type": "code",
        "client_id": settings.yandex_oauth_client_id,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    # Only pass scope when explicitly configured AND enabled in oauth.yandex.ru.
    # Omitting scope uses the rights selected at app registration (default).
    scopes = settings.yandex_oauth_scopes.strip()
    if scopes:
        params["scope"] = scopes
    return f"https://oauth.yandex.ru/authorize?{urlencode(params)}"


def yandex_exchange_code(settings: Settings, code: str) -> OAuthProfile:
    redirect_uri = yandex_redirect_uri(settings)
    response = httpx.post(
        "https://oauth.yandex.ru/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": settings.yandex_oauth_client_id,
            "client_secret": settings.yandex_oauth_client_secret,
            "redirect_uri": redirect_uri,
        },
        timeout=20.0,
    )
    response.raise_for_status()
    payload = response.json()
    if "error" in payload:
        raise ValueError(payload.get("error_description") or payload["error"])

    access_token = payload["access_token"]
    profile_response = httpx.get(
        "https://login.yandex.ru/info",
        params={"format": "json"},
        headers={"Authorization": f"OAuth {access_token}"},
        timeout=20.0,
    )
    profile_response.raise_for_status()
    profile = profile_response.json()
    external_id = str(profile["id"])
    email = profile.get("default_email")
    display_name = profile.get("display_name") or profile.get("real_name") or profile.get("login")
    if not display_name and email:
        display_name = email.split("@", 1)[0]
    profile_json: dict[str, object] = {"default_email": email}
    if profile.get("login"):
        profile_json["login"] = profile.get("login")
    return OAuthProfile(
        external_id=external_id,
        display_name=display_name,
        email=email,
        profile_json=profile_json,
    )


def yandex_redirect_uri(settings: Settings) -> str:
    if settings.yandex_oauth_redirect_uri:
        return settings.yandex_oauth_redirect_uri
    return f"{settings.app_base_url.rstrip('/')}/api/auth/oauth/yandex/callback"
