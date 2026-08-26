"""Лимит на создание новых аккаунтов.

Вход мы никому не ограничиваем: залогиниться существующим профилем можно
сколько угодно раз. Ограничение стоит ровно на одном действии — рождении
нового пользователя, потому что именно оно даёт мультиаккаунт.

Считаем по двум ключам сразу:

* IP — грубый, но честный признак. По журналу входов на проде максимум
  два разных аккаунта приходили с одного адреса за всю историю, так что
  порог в несколько регистраций в сутки не задевает живых людей даже за
  операторским NAT.
* устройство — подписанная кука, которую выдаём анониму перед уходом к
  провайдеру. Переживает смену IP (человек ушёл с вайфая в мобильный),
  но обнуляется в режиме инкогнито — поэтому работает в паре с IP, а не
  вместо него.

Счётчики живут в Redis сутки. Проверка идёт до создания аккаунта, а
инкремент — после успешного: сорвавшиеся попытки (человек передумал у
провайдера, отвалилась сеть) не должны съедать лимит.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from fastapi import Request, Response

from app.config import Settings
from app.core.abuse_store import record_signup_block
from app.core.rate_limit import get_redis, increment_counter
from app.core.security import generate_token, sign_session_id, verify_signed_session_id

DEVICE_COOKIE_MAX_AGE_SECONDS = 400 * 86400  # предел, который браузеры вообще хранят


@dataclass(frozen=True)
class SignupContext:
    """Кто именно заводит аккаунт: адрес и устройство."""

    ip: str
    device_id: str = ""

    @property
    def device_ref(self) -> str:
        """Короткая метка устройства для счётчиков и журнала.

        Сам device_id не храним и не показываем: он подписан нашим ключом и
        по нему можно было бы выдать себя за это устройство.
        """
        if not self.device_id:
            return ""
        return hashlib.sha256(self.device_id.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class SignupDecision:
    allowed: bool
    reason: str = ""
    retry_after: int = 0


def _ip_key(ip: str) -> str:
    return f"signup:ip:{ip}"


def _device_key(device_ref: str) -> str:
    return f"signup:device:{device_ref}"


def _count(key: str) -> int:
    value = get_redis().get(key)
    return int(value) if value else 0


def _ttl(key: str) -> int:
    ttl = get_redis().ttl(key)
    return int(ttl) if ttl and ttl > 0 else 0


def read_device_id(request: Request, settings: Settings) -> str:
    """Достать id устройства из куки, проверив подпись. Пусто — куки нет."""
    signed = request.cookies.get(settings.device_cookie_name)
    if not signed:
        return ""
    device_id = verify_signed_session_id(signed, settings.app_secret_key)
    return device_id or ""


def ensure_device_cookie(request: Request, response: Response, settings: Settings) -> str:
    """Вернуть id устройства, выдав куку, если её ещё нет.

    Зовётся на старте входа — то есть до того, как человек уйдёт к провайдеру
    и вернётся на callback. Кука SameSite=lax, поэтому при возврате навигацией
    браузер её пришлёт.
    """
    device_id = read_device_id(request, settings)
    if device_id:
        return device_id

    device_id = generate_token(16)
    response.set_cookie(
        key=settings.device_cookie_name,
        value=sign_session_id(device_id, settings.app_secret_key),
        max_age=DEVICE_COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        secure=not settings.app_debug,
        samesite="lax",
        path="/",
    )
    return device_id


def check_signup_allowed(context: SignupContext, settings: Settings) -> SignupDecision:
    """Можно ли завести ещё один аккаунт с этого адреса и устройства."""
    if not settings.signup_guard_enabled:
        return SignupDecision(allowed=True)

    device_ref = context.device_ref
    if device_ref:
        key = _device_key(device_ref)
        if _count(key) >= settings.signup_limit_per_device_daily:
            return SignupDecision(allowed=False, reason="device", retry_after=_ttl(key))

    if context.ip and context.ip != "unknown":
        key = _ip_key(context.ip)
        if _count(key) >= settings.signup_limit_per_ip_daily:
            return SignupDecision(allowed=False, reason="ip", retry_after=_ttl(key))

    return SignupDecision(allowed=True)


def register_signup(context: SignupContext, settings: Settings) -> None:
    """Отметить состоявшуюся регистрацию в счётчиках."""
    if not settings.signup_guard_enabled:
        return
    window = settings.signup_guard_window_seconds
    if context.ip and context.ip != "unknown":
        increment_counter(_ip_key(context.ip), window)
    device_ref = context.device_ref
    if device_ref:
        increment_counter(_device_key(device_ref), window)


def record_block(context: SignupContext, decision: SignupDecision, *, provider: str) -> None:
    """Положить отказ в журнал админки: по нему видно фродера и ложное срабатывание."""
    record_signup_block(
        ip=context.ip,
        device_ref=context.device_ref,
        reason=decision.reason,
        provider=provider,
    )


def signup_block_message(decision: SignupDecision) -> str:
    if decision.reason == "device":
        return (
            "С этого устройства сегодня уже создано несколько новых профилей. "
            "Войдите в существующий или попробуйте завтра."
        )
    return (
        "С этой сети сегодня уже создано несколько новых профилей. "
        "Войдите в существующий или попробуйте завтра."
    )
