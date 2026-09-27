"""Токен «Это вы?» — строка человека из протоколов в поиске по сайту.

Гость нажимает свою строку в выдаче, входит (VK, Яндекс, почта, Telegram —
любой вход уводит со страницы) и после входа привязывает ровно этого
человека, без повторного выбора среди однофамильцев. Что именно он нажал,
переживает вход только в браузере, поэтому строке нужен ключ, который можно
отдать гостю.

Внутренний id участника для этого не годится: выдача поиска отдаёт людей
без id намеренно, а стабильный id позволял бы склеивать выкачанные выдачи в
один набор. Поэтому токен непрозрачный — зашифрован и подписан, у каждой
выдачи свой (случайный nonce), и живёт два часа.

Конструкция — encrypt-then-MAC на stdlib (cryptography в образе нет):
ключи шифрования и подписи выводятся из app_secret_key со своими метками,
поток шифра — HMAC(k_enc, nonce). Метки отделяют этот токен от ссылок
отписки, рассылки и куки сессии, подписанных тем же ключом: подменить один
другим нельзя.

ref — nonce в base64: анонимный ключ воронки (search_claim_events). По нему
видно, что «открыл», «вошёл» и «привязал» — один и тот же заход, но не кто.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from uuid import UUID

# Вход по почте — до 10 минут на код, OAuth — минута-две; остальное — запас
# на «отвлёкся». Столько же фронт хранит намерение в localStorage.
SEARCH_CLAIM_TTL_SECONDS = 2 * 3600

_VERSION = b"\x01"
_NONCE_BYTES = 12
_PLAIN_BYTES = 16 + 4  # uuid + срок (unix, 4 байта big-endian)
_TAG_BYTES = 16
_TOKEN_BYTES = len(_VERSION) + _NONCE_BYTES + _PLAIN_BYTES + _TAG_BYTES
# 49 байт в base64 без «=» — ровно 66 знаков.
_TOKEN_LENGTH = (_TOKEN_BYTES * 4 + 2) // 3

# Ответы ручек «Это вы?» (GET /api/search/claim, POST
# /api/profiles/link-by-search-token) — одни и те же в обеих; фронт
# показывает текст как есть.
CLAIM_BAD_MESSAGE = "Не получилось открыть профиль — найдите себя в поиске ещё раз."
CLAIM_EXPIRED_MESSAGE = "Результаты поиска устарели — найдите себя в поиске ещё раз."
CLAIM_NOT_FOUND_MESSAGE = "Профиль не найден — найдите себя в поиске ещё раз."


class SearchClaimTokenError(Exception):
    """Токен не разобран. kind: "bad" — испорчен или чужой, "expired" — истёк.

    У истёкшего подпись верна, поэтому ref известен — воронка пишет по нему
    неудачную привязку.
    """

    def __init__(self, kind: str, ref: str | None = None) -> None:
        self.kind = kind
        self.ref = ref
        super().__init__(kind)

    @property
    def status_code(self) -> int:
        return 410 if self.kind == "expired" else 400

    @property
    def message(self) -> str:
        return CLAIM_EXPIRED_MESSAGE if self.kind == "expired" else CLAIM_BAD_MESSAGE


@dataclass(frozen=True)
class SearchClaim:
    participant_id: UUID
    ref: str
    expires_at: int


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _derive(secret: str, label: bytes) -> bytes:
    return hmac.new(secret.encode(), label, hashlib.sha256).digest()


def _keystream(secret: str, nonce: bytes) -> bytes:
    return hmac.new(_derive(secret, b"search-claim:v1:enc"), nonce, hashlib.sha256).digest()[:_PLAIN_BYTES]


def _tag(secret: str, nonce: bytes, ciphertext: bytes) -> bytes:
    key = _derive(secret, b"search-claim:v1:mac")
    return hmac.new(key, _VERSION + nonce + ciphertext, hashlib.sha256).digest()[:_TAG_BYTES]


def _xor(left: bytes, right: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(left, right, strict=True))


def make_claim_token(participant_id: UUID, secret: str, *, now: int | None = None) -> str:
    issued = int(time.time()) if now is None else now
    nonce = secrets.token_bytes(_NONCE_BYTES)
    plain = participant_id.bytes + (issued + SEARCH_CLAIM_TTL_SECONDS).to_bytes(4, "big")
    ciphertext = _xor(plain, _keystream(secret, nonce))
    return _b64(_VERSION + nonce + ciphertext + _tag(secret, nonce, ciphertext))


def parse_claim_token(token: str, secret: str, *, now: int | None = None) -> SearchClaim:
    """Разобрать токен выдачи. Бросает SearchClaimTokenError."""
    if not isinstance(token, str) or len(token) != _TOKEN_LENGTH:
        raise SearchClaimTokenError("bad")
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except ValueError as exc:  # binascii.Error и не-ASCII — оба ValueError
        raise SearchClaimTokenError("bad") from exc
    # Одна строка — один токен: base64 терпит лишние биты в последнем знаке,
    # и без этой сверки у токена было бы несколько написаний.
    if len(raw) != _TOKEN_BYTES or raw[:1] != _VERSION or _b64(raw) != token:
        raise SearchClaimTokenError("bad")
    nonce = raw[1 : 1 + _NONCE_BYTES]
    ciphertext = raw[1 + _NONCE_BYTES : 1 + _NONCE_BYTES + _PLAIN_BYTES]
    if not hmac.compare_digest(raw[-_TAG_BYTES:], _tag(secret, nonce, ciphertext)):
        raise SearchClaimTokenError("bad")
    plain = _xor(ciphertext, _keystream(secret, nonce))
    ref = _b64(nonce)
    expires_at = int.from_bytes(plain[16:], "big")
    if (int(time.time()) if now is None else now) > expires_at:
        raise SearchClaimTokenError("expired", ref=ref)
    return SearchClaim(participant_id=UUID(bytes=plain[:16]), ref=ref, expires_at=expires_at)
