"""Клиент NRMS — системы записи волонтёров 5 вёрст (nrms.5verst.ru).

Доступ в NRMS есть только у оргкоманды локации, публичного API нет (штаб
отказал принципиально). Поэтому сайт ходит туда под учёткой самого
организатора: он вводит логин и пароль NRMS в кабинете, мы обмениваем их на
токен и держим ТОЛЬКО токен — пароль после ответа NRMS нигде не остаётся.
Токен живёт столько, сколько прописано в его JWT (в наблюдениях 04.09.2026 —
4 часа), после чего организатора просят войти заново.

Все вызовы и формы запросов сняты с HAR-записи реальной сессии Дмитрия
(04.09.2026): login, event/listByVerstId, volunteer/role/list,
athlete/getListByIdPart, event/volunteer/list, volunteer/event/save.
Ничего сверх увиденного клиент не дёргает. Сохранение состава
(`save_event_volunteers`) ЗАМЕНЯЕТ весь состав даты: во второй записи Дмитрия
(добавление Анны Смирновой на 26.09) ушёл полный список — прежний человек плюс
новый. Поэтому вызывающий код обязан передавать текущий состав целиком плюс
добавляемого; отправка одного человека стёрла бы остальных. В NRMS есть и
`volunteer/event/clear` — его не вызываем никогда.

Даты в NRMS — строки dd.mm.yyyy, ID участника — число без буквы «A».
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import redis

from app.config import get_settings
from app.core.redis_client import get_redis_client

logger = logging.getLogger(__name__)

NRMS_BASE_URL = "https://nrms.5verst.ru/api/v1"
_TIMEOUT_SECONDS = 20.0
_USER_AGENT = "Mozilla/5.0 (compatible; run5k.run organizer cabinet)"

# Как NRMS ждёт токен: фронт NRMS (static/js/main.*.js, 04.09.2026) кладёт
# localStorage["nrms-token"] в заголовок Authorization КАК ЕСТЬ — без «Bearer».
NRMS_AUTH_HEADER = "Authorization"
NRMS_AUTH_PREFIX = ""

# upload_status_id состава даты (enum фронта NRMS): Draft — «Список волонтёров
# сохранён», Final — «загружен на сайт» (кнопка «Загрузить на сайт»). Сайт пишет
# только в черновик; финальные даты (прошедшие старты) не трогает.
UPLOAD_STATUS_DRAFT = 1
UPLOAD_STATUS_FINAL = 2

# status_id события (enum фронта NRMS `hu`): Test=1, Open=2, Cancel=3, Pause=5.
EVENT_STATUS_OPEN = 2
EVENT_STATUS_CANCEL = 3
EVENT_STATUS_PAUSE = 5

# Если в токене нет exp — считаем, что он живёт как в наблюдениях.
DEFAULT_TOKEN_TTL = timedelta(hours=4)
# Токен, которому осталось меньше минуты, считаем протухшим: запись в NRMS —
# несколько вызовов подряд, лучше попросить войти заново, чем упасть посередине.
_TOKEN_SAFETY_MARGIN = timedelta(minutes=1)

TOKEN_CACHE_PREFIX = "nrms:token:v1:"


class NrmsError(Exception):
    """Любая ошибка общения с NRMS — текст пригоден для показа организатору."""


class NrmsAuthError(NrmsError):
    """Логин отвергнут или токен протух: нужно войти заново."""


@dataclass(frozen=True)
class NrmsSession:
    token: str
    username: str
    expires_at: datetime

    def is_alive(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        return self.expires_at - _TOKEN_SAFETY_MARGIN > now


@dataclass(frozen=True)
class NrmsEvent:
    id: int
    name: str
    url: str


@dataclass(frozen=True)
class NrmsRole:
    id: int
    name: str
    is_default: bool


@dataclass(frozen=True)
class NrmsAthlete:
    id: int
    full_name: str
    home_event: str | None
    volunteering_count: int
    is_record_confirmed: bool
    is_adult: bool


@dataclass(frozen=True)
class NrmsRosterEntry:
    verst_id: int
    role_id: int
    role_name: str
    full_name: str


@dataclass(frozen=True)
class NrmsRoster:
    status_id: int | None
    upload_status_id: int | None
    entries: list[NrmsRosterEntry]


# ---------------------------------------------------------------------------
# HTTP


def _headers(token: str | None = None) -> dict[str, str]:
    headers = {"Content-Type": "application/json", "User-Agent": _USER_AGENT}
    if token:
        headers[NRMS_AUTH_HEADER] = f"{NRMS_AUTH_PREFIX}{token}"
    return headers


def _post(path: str, payload: dict[str, Any] | None, *, token: str | None) -> dict[str, Any]:
    url = f"{NRMS_BASE_URL}/{path}"
    try:
        response = httpx.post(
            url,
            content=json.dumps(payload) if payload is not None else None,
            headers=_headers(token),
            timeout=_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.warning("NRMS %s: network error %s", path, exc)
        raise NrmsError("NRMS не отвечает, попробуйте позже") from exc
    if response.status_code in (401, 403):
        raise NrmsAuthError("Сессия NRMS истекла — войдите заново")
    if response.status_code != 200:
        logger.warning("NRMS %s: HTTP %s %s", path, response.status_code, response.text[:300])
        raise NrmsError(f"NRMS ответил ошибкой {response.status_code}")
    try:
        body = response.json()
    except ValueError as exc:
        raise NrmsError("NRMS вернул не JSON") from exc
    if not isinstance(body, dict):
        raise NrmsError("NRMS вернул неожиданный ответ")
    if body.get("error"):
        message = body["error"]
        if isinstance(message, dict):
            message = message.get("message") or json.dumps(message, ensure_ascii=False)
        raise NrmsError(f"NRMS: {message}")
    result = body.get("result")
    if result is None:
        raise NrmsError("NRMS вернул пустой ответ")
    return result if isinstance(result, dict) else {"value": result}


# ---------------------------------------------------------------------------
# Токен


def _jwt_expiry(token: str) -> datetime | None:
    """exp из payload JWT без проверки подписи — нам нужен только срок."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload_raw = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_raw))
        exp = payload.get("exp")
        if isinstance(exp, (int, float)):
            return datetime.fromtimestamp(exp, tz=UTC)
    except (ValueError, TypeError):
        return None
    return None


def login(username: str, password: str) -> NrmsSession:
    """Обмен логина и пароля на токен. Пароль дальше этой функции не уходит."""
    username = username.strip()
    if not username or not password:
        raise NrmsAuthError("Введите логин и пароль NRMS")
    try:
        result = _post("auth/login", {"username": username, "password": password}, token=None)
    except NrmsAuthError as exc:
        raise NrmsAuthError("NRMS не принял логин или пароль") from exc
    token = result.get("token")
    if not isinstance(token, str) or not token:
        raise NrmsAuthError("NRMS не принял логин или пароль")
    expires_at = _jwt_expiry(token) or (datetime.now(UTC) + DEFAULT_TOKEN_TTL)
    return NrmsSession(token=token, username=username, expires_at=expires_at)


def token_cache_key(user_id: UUID) -> str:
    return f"{TOKEN_CACHE_PREFIX}{user_id}"


def _obfuscation_key() -> bytes:
    # Redis внутренний и без публичного порта, но токен NRMS — чужая учётка:
    # держим его не открытым текстом, а под ключом из секрета приложения.
    return hashlib.sha256(get_settings().app_secret_key.encode("utf-8")).digest()


def _xor_stream(data: bytes, key: bytes) -> bytes:
    stream = b""
    counter = 0
    while len(stream) < len(data):
        stream += hashlib.sha256(key + counter.to_bytes(4, "big")).digest()
        counter += 1
    return bytes(a ^ b for a, b in zip(data, stream, strict=False))


def _seal(session: NrmsSession) -> str:
    raw = json.dumps(
        {
            "token": session.token,
            "username": session.username,
            "expires_at": session.expires_at.isoformat(),
        }
    ).encode("utf-8")
    return base64.urlsafe_b64encode(_xor_stream(raw, _obfuscation_key())).decode("ascii")


def _unseal(blob: str) -> NrmsSession | None:
    try:
        raw = _xor_stream(base64.urlsafe_b64decode(blob.encode("ascii")), _obfuscation_key())
        data = json.loads(raw)
        return NrmsSession(
            token=str(data["token"]),
            username=str(data.get("username") or ""),
            expires_at=datetime.fromisoformat(data["expires_at"]),
        )
    except (ValueError, KeyError, TypeError):
        return None


def store_session(user_id: UUID, session: NrmsSession) -> None:
    ttl = int((session.expires_at - datetime.now(UTC)).total_seconds())
    if ttl <= 0:
        return
    try:
        get_redis_client().set(token_cache_key(user_id), _seal(session), ex=ttl)
    except redis.RedisError:
        logger.warning("NRMS token store failed for user %s", user_id)


def load_session(user_id: UUID) -> NrmsSession | None:
    try:
        blob = get_redis_client().get(token_cache_key(user_id))
    except redis.RedisError:
        return None
    if not blob:
        return None
    session = _unseal(blob if isinstance(blob, str) else blob.decode("utf-8"))
    if session is None or not session.is_alive():
        drop_session(user_id)
        return None
    return session


def drop_session(user_id: UUID) -> None:
    try:
        get_redis_client().delete(token_cache_key(user_id))
    except redis.RedisError:
        pass


# ---------------------------------------------------------------------------
# Чтение


def list_events(session: NrmsSession) -> list[NrmsEvent]:
    """Локации, которыми организатор управляет в NRMS. Тело запроса пустое."""
    result = _post("event/listByVerstId", None, token=session.token)
    events: list[NrmsEvent] = []
    for item in result.get("event_list") or []:
        try:
            events.append(
                NrmsEvent(id=int(item["id"]), name=str(item.get("name") or ""), url=str(item.get("url") or ""))
            )
        except (KeyError, ValueError, TypeError):
            continue
    return events


def list_roles(session: NrmsSession) -> list[NrmsRole]:
    result = _post("volunteer/role/list", None, token=session.token)
    roles: list[NrmsRole] = []
    for item in result.get("roles") or []:
        try:
            roles.append(NrmsRole(id=int(item["id"]), name=str(item["name"]), is_default=bool(item.get("is_default"))))
        except (KeyError, ValueError, TypeError):
            continue
    return roles


def find_athlete_by_id(session: NrmsSession, verst_id: int) -> NrmsAthlete | None:
    """Поиск по ID — единственный однозначный способ: у ФИО десятки тёзок."""
    result = _post("athlete/getListByIdPart", {"id": int(verst_id)}, token=session.token)
    for item in result.get("data") or []:
        try:
            if int(item["id"]) != int(verst_id):
                continue
            return NrmsAthlete(
                id=int(item["id"]),
                full_name=str(item.get("full_name") or ""),
                home_event=item.get("home_event"),
                volunteering_count=int(item.get("volunteering_count") or 0),
                is_record_confirmed=bool(item.get("is_record_confirmed")),
                is_adult=bool(item.get("is_adult", True)),
            )
        except (KeyError, ValueError, TypeError):
            continue
    return None


def format_nrms_date(value: date) -> str:
    return value.strftime("%d.%m.%Y")


def list_event_volunteers(session: NrmsSession, event_id: int, event_date: date) -> NrmsRoster:
    result = _post(
        "event/volunteer/list",
        {"event_id": int(event_id), "event_date": format_nrms_date(event_date)},
        token=session.token,
    )
    entries: list[NrmsRosterEntry] = []
    for item in result.get("volunteer_list") or []:
        try:
            entries.append(
                NrmsRosterEntry(
                    verst_id=int(item["verst_id"]),
                    role_id=int(item["role_id"]),
                    role_name=str(item.get("role_name") or ""),
                    full_name=str(item.get("full_name") or ""),
                )
            )
        except (KeyError, ValueError, TypeError):
            continue
    return NrmsRoster(
        status_id=_int_or_none(result.get("status_id")),
        upload_status_id=_int_or_none(result.get("upload_status_id")),
        entries=entries,
    )


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Запись


def save_event_volunteers(
    session: NrmsSession,
    *,
    event_id: int,
    event_date: date,
    upload_status_id: int,
    volunteers: list[tuple[int, int]],
) -> None:
    """POST volunteer/event/save — ПОЛНЫЙ состав даты (замена, не дописывание).

    Форма по HAR: {event_id, date dd.mm.yyyy, upload_status_id,
    volunteers: [{verst_id, role_id}]}. Пустой список сюда не отдаём —
    «очистить состав» не наша операция.
    """
    if upload_status_id != UPLOAD_STATUS_DRAFT:
        raise NrmsError("Сайт пишет только в черновик состава")
    if not volunteers:
        raise NrmsError("Пустой состав не сохраняем")
    payload = {
        "event_id": int(event_id),
        "date": format_nrms_date(event_date),
        "upload_status_id": int(upload_status_id),
        "volunteers": [{"verst_id": int(v), "role_id": int(r)} for v, r in volunteers],
    }
    _post("volunteer/event/save", payload, token=session.token)
