from __future__ import annotations

import json
import logging
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.api.deps import get_client_ip, get_current_user, get_optional_session_user_id
from app.auth.providers import vk as vk_provider
from app.auth.providers.telegram_widget import bot_id as telegram_bot_id
from app.config import Settings, get_settings
from app.core.admin import user_response
from app.core.redis_client import get_redis_client
from app.core.security import generate_token
from app.core.session import delete_session
from app.core.signup_guard import SignupContext, ensure_device_cookie, read_device_id
from app.core.site_stats import record_login
from app.db.session import get_db
from app.models import AuthProvider, User
from app.schemas.auth import (
    AuthIdentityResponse,
    BotConfirmRequest,
    BotConfirmResponse,
    BotLoginStatusRequest,
    BotLoginStatusResponse,
    DisplayNameOptionsResponse,
    DisplayNamePreferencesUpdate,
    EmailCodeRequest,
    EmailCodeResponse,
    EmailLinkResponse,
    EmailVerifyRequest,
    EmailVerifyResponse,
    LoginRequestResponse,
    LoginRequestStatusResponse,
    MergeConfirmRequest,
    MergePreviewResponse,
    MessageResponse,
    NoAccountPlatformRequest,
    NoAccountPlatformsResponse,
    OAuthFinishRequest,
    OAuthFinishResponse,
    TelegramLoginConfigResponse,
    TelegramWidgetLoginRequest,
    TelegramWidgetLoginResponse,
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
)
from app.services.email_auth_service import link_email
from app.services.email_auth_service import request_code as request_email_code
from app.services.email_auth_service import verify_code as verify_email_code
from app.services.login_journal_service import (
    EVENT_LOGIN,
    EVENT_LOGOUT,
    record_login_event,
    session_ref_from_signed,
)
from app.services.oauth_service import (
    confirm_merge,
    get_merge_preview_by_token,
    handle_oauth_callback,
    start_oauth_flow,
)
from app.services.telegram_login_service import (
    authorize_url as telegram_authorize_url,
)
from app.services.telegram_login_service import (
    is_configured as telegram_login_configured,
)
from app.services.telegram_login_service import (
    link_telegram,
    login_bot_token,
    login_bot_username,
    login_with_widget,
    telegram_fields,
)
from app.services.user_display_name_service import (
    dismiss_display_name_notice,
    display_name_options,
    display_name_suggestion,
    set_display_name_preferences,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

KNOWN_DEVICE_COOKIE_MAX_AGE_SECONDS = 400 * 86400  # предел, который браузеры хранят

TELEGRAM_STATE_PREFIX = "auth:tg:state:"
TELEGRAM_STATE_TTL_SECONDS = 600


def _request_origin(request: Request) -> str:
    """Схема и домен, на которых человек сейчас находится.

    Не app_base_url: дев-стенд живёт на другом домене (туннель), а Telegram
    сверяет и origin, и return_to с доменом, прописанным боту.
    """
    host = request.headers.get("X-Forwarded-Host") or request.headers.get("Host") or request.url.netloc
    # Схему определяем по хосту, а не по X-Forwarded-Proto: заголовок ставит
    # внутренний nginx, который сам получает запрос по http — TLS терминируется
    # раньше, на внешнем прокси. Доверять такому заголовку нельзя, иначе origin
    # уезжает в Telegram как http://, и тот его не принимает. Наружу сайт всегда
    # за TLS; http остаётся только у localhost, где мы и разрабатываем.
    bare_host = host.split(":")[0]
    scheme = "http" if bare_host in {"localhost", "127.0.0.1"} else "https"
    return f"{scheme}://{host}"


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
    # Метка «здесь уже входили» — переживает выход и читается экраном входа,
    # чтобы не спрашивать согласие у того, кто его давно дал. Не HttpOnly
    # намеренно: её читает фронт. Секрета в ней нет.
    response.set_cookie(
        key=settings.known_device_cookie_name,
        value="1",
        max_age=KNOWN_DEVICE_COOKIE_MAX_AGE_SECONDS,
        httponly=False,
        secure=not settings.app_debug,
        samesite="lax",
        path="/",
    )


def _handle_auth_error(exc: AuthError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


def _log_login(
    db: Session,
    settings: Settings,
    *,
    user_id: UUID,
    provider: str,
    signed_session: str,
    request: Request | None,
) -> None:
    """Записать вход в журнал. Диагностика не должна ронять сам логин.

    Здесь же считаем вход в дневную метрику: точка общая для всех способов
    входа. Раньше record_login() стоял только на magic link, и входы через
    OAuth (то есть большинство) в статистику не попадали вовсе.
    """
    try:
        record_login_event(
            db,
            user_id=user_id,
            event_type=EVENT_LOGIN,
            provider=provider,
            session_ref=session_ref_from_signed(signed_session, settings.app_secret_key),
            request=request,
        )
        db.commit()
    except Exception:  # noqa: BLE001 — журнал не критичен для входа
        logger.exception("login journal: failed to record login for user %s", user_id)
        db.rollback()

    # Отдельным try: метрика живёт в Redis, и её падение не должно стоить нам
    # записи в журнале (она важнее — по ней разбираем потерю сессий).
    try:
        record_login()
    except Exception:  # noqa: BLE001 — счётчик не критичен для входа
        logger.exception("login stats: failed to count login for user %s", user_id)


def _user_payload(db: Session, user: User, settings: Settings) -> UserResponse:
    identities = list_user_identities(db, user.id)
    # Расхождение алгоритма с зафиксированным источником отдаём вместе с /me:
    # баннер про смену имени рисуется по этому полю, отдельного запроса не нужно.
    return user_response(
        user,
        settings,
        identities,
        db=db,
        display_name_suggestion=display_name_suggestion(db, user),
    )


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


def _signup_context(request: Request | None, settings: Settings) -> SignupContext | None:
    """Кто заводит аккаунт. None — источник без запроса (бот, тесты): лимит не считаем."""
    if request is None:
        return None
    return SignupContext(ip=get_client_ip(request), device_id=read_device_id(request, settings))


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
    request: Request | None = None,
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
        signup_context=_signup_context(request, settings),
    )
    signed_session = create_user_session(settings, user_id)
    _log_login(
        db,
        settings,
        user_id=user_id,
        provider=provider,
        signed_session=signed_session,
        request=request,
    )
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
    response = RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)
    # Метку устройства выдаём здесь, до ухода к провайдеру: на callback браузер
    # вернёт её вместе с навигацией (SameSite=lax), и лимит регистраций сможет
    # отличить второе устройство от второго захода с того же.
    ensure_device_cookie(request, response, settings)
    return response


@router.post("/email/request-code", response_model=EmailCodeResponse)
def email_request_code(
    body: EmailCodeRequest,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    request: Request,
    response: Response,
) -> EmailCodeResponse:
    if not body.consent:
        raise _handle_auth_error(
            AuthError("Для входа необходимо принять условия обработки персональных данных.", 400)
        )
    try:
        result = request_email_code(
            db,
            settings,
            body.email,
            client_ip=get_client_ip(request),
            consent=body.consent,
            news_consent=body.news_consent,
        )
    except AuthError as exc:
        raise _handle_auth_error(exc) from exc

    # Метку устройства выдаём здесь по той же причине, что и перед уходом к
    # OAuth-провайдеру: к моменту ввода кода лимит регистраций должен уметь
    # отличить второе устройство от второго захода с того же.
    ensure_device_cookie(request, response, settings)
    return EmailCodeResponse(expires_in=int(result["expires_in"]))


@router.post("/email/verify", response_model=EmailVerifyResponse)
def email_verify(
    body: EmailVerifyRequest,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    request: Request,
    response: Response,
) -> EmailVerifyResponse:
    try:
        user_id = verify_email_code(
            db,
            settings,
            body.email,
            body.code,
            signup_context=_signup_context(request, settings),
        )
    except AuthError as exc:
        raise _handle_auth_error(exc) from exc

    signed_session = create_user_session(settings, user_id)
    _log_login(
        db,
        settings,
        user_id=user_id,
        provider="email",
        signed_session=signed_session,
        request=request,
    )
    _set_session_cookie(response, settings, signed_session)
    # Новичка ведём туда же, куда и после OAuth: на онбординг, если профиль
    # ещё не привязан к беговой системе.
    from app.services.onboarding_service import post_login_redirect_target

    return EmailVerifyResponse(redirect=post_login_redirect_target(db, user_id))


@router.get("/telegram/config", response_model=TelegramLoginConfigResponse)
def telegram_login_config(
    settings: Annotated[Settings, Depends(get_settings)],
) -> TelegramLoginConfigResponse:
    """Что нужно виджету в браузере, чтобы нарисовать вход через Telegram."""
    if not telegram_login_configured(settings):
        return TelegramLoginConfigResponse(enabled=False)
    return TelegramLoginConfigResponse(
        enabled=True,
        bot_id=telegram_bot_id(login_bot_token(settings)),
        bot_username=login_bot_username(settings),
    )


@router.get("/telegram/start", response_model=None)
def telegram_login_start(
    settings: Annotated[Settings, Depends(get_settings)],
    request: Request,
    consent: Annotated[bool, Query()] = False,
    mode: Annotated[str, Query()] = "login",
    user_id: Annotated[UUID | None, Depends(get_optional_session_user_id)] = None,
) -> RedirectResponse:
    """Отправить человека подтверждать вход в Telegram.

    mode=link — привязка к профилю, в котором человек уже сидит; тогда согласие
    спрашивать не нужно, оно давно дано.

    И режим, и согласие храним в Redis под state, а не в адресе: всё, что
    вернётся в return_to, Telegram не подписывает, и подменить такой параметр
    может кто угодно.
    """
    if not telegram_login_configured(settings):
        raise _handle_auth_error(AuthError("Вход через Telegram недоступен.", 503))
    if mode == "link" and user_id is None:
        raise _handle_auth_error(AuthError("Для привязки нужно войти в профиль.", 401))
    if mode != "link" and not consent:
        raise _handle_auth_error(
            AuthError("Для входа необходимо принять условия обработки персональных данных.", 400)
        )

    # Домен берём из самого запроса: у дев-стенда он свой (туннель), у прода —
    # свой, и оба должны совпасть с тем, что прописан боту через /setdomain.
    origin = _request_origin(request)
    state = generate_token(24)
    get_redis_client().setex(
        f"{TELEGRAM_STATE_PREFIX}{state}",
        TELEGRAM_STATE_TTL_SECONDS,
        json.dumps({"mode": mode, "link_user_id": str(user_id) if mode == "link" else None}),
    )
    return_to = f"{origin}/auth/telegram/return?state={state}"
    url = telegram_authorize_url(settings, origin=origin, return_to=return_to)
    response = RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)
    ensure_device_cookie(request, response, settings)
    return response


@router.post("/telegram/widget", response_model=TelegramWidgetLoginResponse)
def telegram_widget_login(
    body: TelegramWidgetLoginRequest,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    request: Request,
    response: Response,
) -> TelegramWidgetLoginResponse:
    # Метку выдал /telegram/start и она одноразовая: без неё это чужой POST,
    # а не возврат человека из Telegram.
    raw_state = get_redis_client().getdel(f"{TELEGRAM_STATE_PREFIX}{body.state}") if body.state else None
    if not raw_state:
        raise _handle_auth_error(AuthError("Сессия входа истекла. Попробуйте ещё раз.", 400))
    state_payload = json.loads(raw_state)
    link_user_id = state_payload.get("link_user_id")

    if link_user_id:
        try:
            merge_token = link_telegram(
                db,
                settings,
                UUID(str(link_user_id)),
                telegram_fields(body.data),
            )
        except AuthError as exc:
            raise _handle_auth_error(exc) from exc
        return TelegramWidgetLoginResponse(redirect="settings", merge_token=merge_token)

    try:
        user_id = login_with_widget(
            db,
            settings,
            telegram_fields(body.data),
            consent=True,
            signup_context=_signup_context(request, settings),
        )
    except AuthError as exc:
        raise _handle_auth_error(exc) from exc

    signed_session = create_user_session(settings, user_id)
    _log_login(
        db,
        settings,
        user_id=user_id,
        provider="telegram",
        signed_session=signed_session,
        request=request,
    )
    _set_session_cookie(response, settings, signed_session)

    from app.services.onboarding_service import post_login_redirect_target

    return TelegramWidgetLoginResponse(redirect=post_login_redirect_target(db, user_id))


@router.post("/email/link", response_model=EmailLinkResponse)
def email_link(
    body: EmailVerifyRequest,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    user: Annotated[User, Depends(get_current_user)],
) -> EmailLinkResponse:
    """Добавить почту как способ входа в профиль, где человек уже сидит."""
    try:
        merge_token = link_email(db, settings, user, body.email, body.code)
    except AuthError as exc:
        raise _handle_auth_error(exc) from exc
    return EmailLinkResponse(merge_token=merge_token)


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
    request: Request,
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
            request=request,
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
    request: Request,
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
            request=request,
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
    request: Request,
) -> MessageResponse:
    try:
        survivor_id = confirm_merge(db, body.merge_token, survivor_user_id=user.id)
    except AuthError as exc:
        raise _handle_auth_error(exc) from exc
    if survivor_id != user.id:
        signed_session = create_user_session(settings, survivor_id)
        _log_login(
            db,
            settings,
            user_id=survivor_id,
            provider="merge",
            signed_session=signed_session,
            request=request,
        )
        _set_session_cookie(response, settings, signed_session)
    return MessageResponse(message="merged")


@router.get("/callback")
def auth_callback(
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    request: Request,
    token: Annotated[str, Query(min_length=10)],
) -> RedirectResponse:
    try:
        user_id = consume_magic_link(db, settings, token)
        signed_session = create_user_session(settings, user_id)
    except AuthError as exc:
        raise _handle_auth_error(exc) from exc

    _log_login(
        db,
        settings,
        user_id=user_id,
        provider="magic_link",
        signed_session=signed_session,
        request=request,
    )

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


@router.post("/onboarding/no-account", response_model=NoAccountPlatformsResponse)
def onboarding_no_account(
    body: NoAccountPlatformRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> NoAccountPlatformsResponse:
    from app.services.onboarding_service import OnboardingError, set_platform_no_account

    try:
        platforms = set_platform_no_account(db, user, body.platform_code, body.no_account)
    except OnboardingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return NoAccountPlatformsResponse(no_account_platforms=platforms)


@router.get("/me/display-name", response_model=DisplayNameOptionsResponse)
def auth_me_display_name_options(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> DisplayNameOptionsResponse:
    """Варианты имени для селектора в кабинете.

    Свободного ввода нет: имя берётся из профилей беговых систем, человек лишь
    выбирает стиль и, если имена в системах разошлись, систему-источник.
    """
    return DisplayNameOptionsResponse.model_validate(display_name_options(db, user))


@router.patch("/me/display-name", response_model=UserResponse)
def auth_me_display_name_update(
    body: DisplayNamePreferencesUpdate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> UserResponse:
    try:
        updated = set_display_name_preferences(db, user, style=body.style, platform_code=body.platform_code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _user_payload(db, updated, settings)


@router.post("/me/display-name/keep", response_model=UserResponse)
def auth_me_display_name_keep(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> UserResponse:
    """«Оставить как есть»: гасит баннер и запоминает отклонённое предложение."""
    return _user_payload(db, dismiss_display_name_notice(db, user), settings)


@router.post("/logout", response_model=MessageResponse)
def logout(
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[Session, Depends(get_db)],
    user_id: Annotated[UUID | None, Depends(get_optional_session_user_id)] = None,
) -> MessageResponse:
    session_value = request.cookies.get(settings.session_cookie_name)
    # Журнал пишем до удаления сессии: умышленный выход — единственное
    # законное основание потерять авторизацию, по нему и отличаем баг.
    if user_id is not None:
        try:
            record_login_event(
                db,
                user_id=user_id,
                event_type=EVENT_LOGOUT,
                session_ref=session_ref_from_signed(session_value, settings.app_secret_key),
                request=request,
            )
            db.commit()
        except Exception:  # noqa: BLE001 — журнал не критичен для выхода
            logger.exception("login journal: failed to record logout for user %s", user_id)
            db.rollback()
    if session_value:
        delete_session(settings, session_value)
    response.delete_cookie(key=settings.session_cookie_name, path="/")
    return MessageResponse(message="logged_out")
