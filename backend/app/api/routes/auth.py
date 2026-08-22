from __future__ import annotations

import json
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.api.deps import get_client_ip, get_current_user, get_optional_session_user_id
from app.auth.providers import vk as vk_provider
from app.config import Settings, get_settings
from app.core.admin import user_response
from app.core.session import delete_session
from app.db.session import get_db
from app.models import AuthProvider, User
from app.schemas.auth import (
    AuthIdentityResponse,
    BotConfirmRequest,
    BotConfirmResponse,
    BotLoginStatusRequest,
    BotLoginStatusResponse,
    LoginRequestResponse,
    LoginRequestStatusResponse,
    MergeConfirmRequest,
    MergePreviewResponse,
    MessageResponse,
    OAuthFinishRequest,
    OAuthFinishResponse,
    UserDisplayNameUpdate,
    UserResponse,
)
from app.services.auth_identity_service import (
    find_user_by_telegram_id,
    identity_response_payload,
    list_user_identities,
    unlink_provider,
)
from app.services.auth_service import (
    AuthError,
    bot_confirm_login,
    consume_magic_link,
    create_login_request,
    create_user_session,
    get_login_request_status,
    update_user_display_name,
)
from app.services.oauth_service import (
    confirm_merge,
    get_merge_preview_by_token,
    handle_oauth_callback,
    start_oauth_flow,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _verify_bot_secret(
    settings: Annotated[Settings, Depends(get_settings)],
    x_bot_secret: Annotated[str | None, Header()] = None,
) -> None:
    if not settings.telegram_bot_internal_secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Bot auth not configured")
    if x_bot_secret != settings.telegram_bot_internal_secret:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid bot secret")


def _set_session_cookie(response: Response, settings: Settings, signed_session: str) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=signed_session,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=not settings.app_debug,
        samesite="lax",
        path="/",
    )


def _handle_auth_error(exc: AuthError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


def _user_payload(db: Session, user: User, settings: Settings) -> UserResponse:
    identities = list_user_identities(db, user.id)
    return user_response(user, settings, identities)


def _parse_vk_callback_params(
    *,
    code: str | None,
    state: str | None,
    device_id: str | None,
    payload: str | None,
) -> tuple[str, str, str | None]:
    if payload:
        data = json.loads(payload)
        code = code or data.get("code")
        state = state or data.get("state")
        device_id = device_id or data.get("device_id")
    if not code or not state:
        raise AuthError("OAuth callback is missing code or state.", 400)
    return str(code), str(state), str(device_id) if device_id else None


def _oauth_login_error_redirect(settings: Settings, message: str) -> RedirectResponse:
    login_url = f"{settings.app_base_url.rstrip('/')}/login?oauth_error={quote(message)}"
    return RedirectResponse(url=login_url, status_code=status.HTTP_302_FOUND)


def _oauth_redirect_url(settings: Settings, *, redirect_target: str, merge_token: str | None) -> str:
    if merge_token:
        return f"{settings.app_base_url.rstrip('/')}/settings?merge_token={merge_token}"
    return f"{settings.app_base_url.rstrip('/')}/{redirect_target}"


def _oauth_success_response(
    settings: Settings,
    signed_session: str,
    redirect_url: str,
) -> HTMLResponse:
    """Return HTML (not 302) so iOS Safari stores the session cookie before navigation."""
    safe_url = redirect_url.replace("&", "&amp;").replace('"', "&quot;")
    html = f"""<!DOCTYPE html>
<html lang="ru"><head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="0;url={safe_url}">
<title>Вход выполнен</title>
</head><body>
<p>Вход выполнен. <a href="{safe_url}">Перейти в личный кабинет</a></p>
<script>window.location.replace({json.dumps(redirect_url)});</script>
</body></html>"""
    response = HTMLResponse(content=html, status_code=status.HTTP_200_OK)
    _set_session_cookie(response, settings, signed_session)
    return response


def _complete_oauth_login(
    db: Session,
    settings: Settings,
    provider: str,
    *,
    code: str,
    state: str,
    device_id: str | None = None,
    payload: str | None = None,
) -> tuple[str, str | None, str]:
    if provider == "vk":
        code_value, state_value, device_id_value = _parse_vk_callback_params(
            code=code,
            state=state,
            device_id=device_id,
            payload=payload,
        )
    else:
        code_value, state_value, device_id_value = code, state, device_id

    user_id, merge_token, redirect_target = handle_oauth_callback(
        db,
        settings,
        provider,
        code_value,
        state_value,
        device_id=device_id_value,
    )
    signed_session = create_user_session(settings, user_id)
    redirect_url = _oauth_redirect_url(settings, redirect_target=redirect_target, merge_token=merge_token)
    return signed_session, merge_token, redirect_url


@router.post("/login-request", response_model=LoginRequestResponse)
def login_request(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    link: Annotated[bool, Query()] = False,
    user_id: Annotated[UUID | None, Depends(get_optional_session_user_id)] = None,
) -> LoginRequestResponse:
    try:
        link_user_id = user_id if link else None
        if link and link_user_id is None:
            raise AuthError("Authentication required for linking.", 401)
        result = create_login_request(db, settings, get_client_ip(request), link_user_id=link_user_id)
    except AuthError as exc:
        raise _handle_auth_error(exc) from exc
    return LoginRequestResponse.model_validate(result)


@router.get("/login-request/{request_token}/status", response_model=LoginRequestStatusResponse)
def login_request_status(
    request_token: str,
    db: Annotated[Session, Depends(get_db)],
) -> LoginRequestStatusResponse:
    return LoginRequestStatusResponse.model_validate(get_login_request_status(db, request_token))


@router.post("/bot/confirm", response_model=BotConfirmResponse, dependencies=[Depends(_verify_bot_secret)])
def bot_confirm(
    body: BotConfirmRequest,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> BotConfirmResponse:
    try:
        magic_link = bot_confirm_login(
            db,
            settings,
            body.request_token,
            body.telegram_id,
            body.telegram_username,
            body.telegram_chat_id,
            body.telegram_first_name,
            body.telegram_last_name,
            consent_accepted=body.consent_accepted,
        )
    except AuthError as exc:
        raise _handle_auth_error(exc) from exc
    return BotConfirmResponse(magic_link=magic_link or "")


@router.post("/bot/login-status", response_model=BotLoginStatusResponse, dependencies=[Depends(_verify_bot_secret)])
def bot_login_status(
    body: BotLoginStatusRequest,
    db: Annotated[Session, Depends(get_db)],
) -> BotLoginStatusResponse:
    user = find_user_by_telegram_id(db, body.telegram_id)
    return BotLoginStatusResponse(needs_consent=user is None or not user.consent_accepted)


@router.get("/oauth/{provider}/start")
def oauth_start(
    provider: str,
    settings: Annotated[Settings, Depends(get_settings)],
    request: Request,
    mode: Annotated[str, Query()] = "login",
    consent: Annotated[bool, Query()] = False,
    user_id: Annotated[UUID | None, Depends(get_optional_session_user_id)] = None,
) -> RedirectResponse:
    try:
        if mode == "link" and user_id is None:
            raise AuthError("Authentication required for linking.", 401)
        url = start_oauth_flow(
            provider,
            settings,
            mode=mode,
            link_user_id=user_id if mode == "link" else None,
            consent=consent,
        )
    except AuthError as exc:
        raise _handle_auth_error(exc) from exc
    return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)


@router.get("/oauth/vk/redirect-uri")
def vk_oauth_redirect_uri(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, str]:
    redirect_uri = vk_provider.vk_redirect_uri(settings)
    return {
        "redirect_uri": redirect_uri,
        "base_domain": "localhost",
        "note": (
            "VK ID для localhost поддерживает только порт 80. "
            "Откройте сайт на http://localhost и укажите этот redirect_uri в кабинете VK ID."
        ),
    }


@router.post("/oauth/{provider}/finish", response_model=OAuthFinishResponse)
def oauth_finish(
    provider: str,
    body: OAuthFinishRequest,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    response: Response,
) -> OAuthFinishResponse:
    try:
        signed_session, merge_token, redirect_url = _complete_oauth_login(
            db,
            settings,
            provider,
            code=body.code,
            state=body.state,
            device_id=body.device_id,
            payload=body.payload,
        )
    except (AuthError, json.JSONDecodeError) as exc:
        if isinstance(exc, json.JSONDecodeError):
            raise _handle_auth_error(AuthError("Некорректный ответ VK ID.", 400)) from exc
        raise _handle_auth_error(exc) from exc

    _set_session_cookie(response, settings, signed_session)
    redirect_path = redirect_url.removeprefix(settings.app_base_url.rstrip("/")).lstrip("/")
    return OAuthFinishResponse(redirect=redirect_path, merge_token=merge_token)


@router.get("/oauth/{provider}/callback", response_model=None)
def oauth_callback(
    provider: str,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    code: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    device_id: Annotated[str | None, Query()] = None,
    payload: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
    error_description: Annotated[str | None, Query()] = None,
) -> HTMLResponse | RedirectResponse:
    if error:
        message = error_description or error
        return _oauth_login_error_redirect(settings, message)

    try:
        if provider == "vk":
            code_value, state_value, device_id_value = _parse_vk_callback_params(
                code=code,
                state=state,
                device_id=device_id,
                payload=payload,
            )
        else:
            if not code or not state:
                raise AuthError("OAuth callback is missing code or state.", 400)
            code_value, state_value, device_id_value = code, state, device_id

        signed_session, _merge_token, redirect_url = _complete_oauth_login(
            db,
            settings,
            provider,
            code=code_value,
            state=state_value,
            device_id=device_id_value,
        )
    except (AuthError, json.JSONDecodeError) as exc:
        if isinstance(exc, json.JSONDecodeError):
            return _oauth_login_error_redirect(settings, "Некорректный ответ VK ID.")
        return _oauth_login_error_redirect(settings, exc.message)

    return _oauth_success_response(settings, signed_session, redirect_url)


@router.get("/identities", response_model=list[AuthIdentityResponse])
def list_identities(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> list[AuthIdentityResponse]:
    identities = list_user_identities(db, user.id)
    return [AuthIdentityResponse.model_validate(identity_response_payload(item)) for item in identities]


@router.delete("/identities/{provider}", response_model=MessageResponse)
def delete_identity(
    provider: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> MessageResponse:
    try:
        unlink_provider(db, user, AuthProvider(provider))
        db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return MessageResponse(message="unlinked")


@router.get("/merge/preview", response_model=MergePreviewResponse)
def merge_preview(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    merge_token: Annotated[str, Query(min_length=8)],
) -> MergePreviewResponse:
    try:
        preview = get_merge_preview_by_token(db, merge_token, survivor_user_id=user.id)
    except AuthError as exc:
        raise _handle_auth_error(exc) from exc
    return MergePreviewResponse.model_validate(preview)


@router.post("/merge/confirm", response_model=MessageResponse)
def merge_confirm(
    body: MergeConfirmRequest,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    user: Annotated[User, Depends(get_current_user)],
    response: Response,
) -> MessageResponse:
    try:
        survivor_id = confirm_merge(db, body.merge_token, survivor_user_id=user.id)
    except AuthError as exc:
        raise _handle_auth_error(exc) from exc
    if survivor_id != user.id:
        signed_session = create_user_session(settings, survivor_id)
        _set_session_cookie(response, settings, signed_session)
    return MessageResponse(message="merged")


@router.get("/callback")
def auth_callback(
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    token: Annotated[str, Query(min_length=10)],
) -> RedirectResponse:
    try:
        user_id = consume_magic_link(db, settings, token)
        signed_session = create_user_session(settings, user_id)
    except AuthError as exc:
        raise _handle_auth_error(exc) from exc

    from app.services.onboarding_service import post_login_redirect_target

    redirect_url = f"{settings.app_base_url.rstrip('/')}/{post_login_redirect_target(db, user_id)}"
    response = RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)
    _set_session_cookie(response, settings, signed_session)
    return response


@router.get("/me", response_model=UserResponse)
def auth_me(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    request: Request,
    response: Response,
) -> UserResponse:
    # Скользящая сессия: фронт зовёт /me при каждом открытии сайта — заодно
    # продлеваем max_age куки (TTL в Redis продлён при валидации сессии).
    signed_session = request.cookies.get(settings.session_cookie_name)
    if signed_session:
        _set_session_cookie(response, settings, signed_session)
    return _user_payload(db, user, settings)


@router.post("/onboarding/complete", response_model=MessageResponse)
def onboarding_complete(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> MessageResponse:
    from app.services.onboarding_service import complete_onboarding

    complete_onboarding(db, user)
    return MessageResponse(message="onboarding_completed")


@router.patch("/me", response_model=UserResponse)
def auth_me_update(
    body: UserDisplayNameUpdate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> UserResponse:
    try:
        updated = update_user_display_name(db, user, body.display_name)
    except AuthError as exc:
        raise _handle_auth_error(exc) from exc
    return _user_payload(db, updated, settings)


@router.post("/logout", response_model=MessageResponse)
def logout(
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
) -> MessageResponse:
    session_value = request.cookies.get(settings.session_cookie_name)
    if session_value:
        delete_session(settings, session_value)
    response.delete_cookie(key=settings.session_cookie_name, path="/")
    return MessageResponse(message="logged_out")
