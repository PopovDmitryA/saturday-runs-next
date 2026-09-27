"""Публичный поиск по сайту, окно «Это вы?» и приём журнала поисковых запросов.

Все адреса открыты анониму. От перебора их закрывает общая защита
(AbuseProtectionMiddleware) отдельными тарифами «search» и «search_log» —
см. classify_route в core/abuse_protection.py; стоимость одного поиска
ограничивает сам site_search. Саму привязку по токену «Это вы?» делает
POST /api/profiles/link-by-search-token — пишущей ручке место в обычном
тарифе, а не в щедром поисковом.
"""

from __future__ import annotations

import json
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.api.deps import get_optional_session_user_id
from app.config import Settings, get_settings
from app.core.admin import is_admin_user
from app.core.bot_detection import is_bot_user_agent
from app.db.session import get_db
from app.models import User
from app.schemas.search import SearchClaimResponse, SiteSearchResponse
from app.services.participant_search_service import MAX_QUERY_LENGTH
from app.services.profile_linking_service import participant_link_state
from app.services.search_claim_service import (
    CLAIM_NOT_FOUND_MESSAGE,
    SearchClaimTokenError,
    make_claim_token,
    parse_claim_token,
)
from app.services.search_log_service import CLAIM_DECLINED, CLAIM_OPEN, record_claim_event, record_search
from app.services.site_search_service import (
    claimable_participant,
    participant_claim_card,
    people_search_slot,
    site_search,
)

router = APIRouter(prefix="/search", tags=["search"])

# Бекон с журналом — десяток полей; больше — не наш клиент.
_MAX_LOG_BODY_BYTES = 4096
# Токен «Это вы?» — 66 знаков; тело «Это не я» — он и скобки.
_MAX_CLAIM_TOKEN_LENGTH = 256
_MAX_DECLINE_BODY_BYTES = 1024


def _attach_claim_tokens(page: dict[str, Any], secret: str) -> None:
    """Внутренний participant_id строки → непрозрачный claim_token.

    Минтим здесь, а не в site_search: ключ берётся из Depends(get_settings),
    который тесты подменяют; get_settings() в сервисе (lru_cache) подписал бы
    токен боевым ключом. id участника наружу не уходит ни в каком виде.
    """
    for row in page.get("people") or []:
        participant_id = row.pop("participant_id", None)
        if row.get("kind") == "participant" and participant_id is not None:
            row["claim_token"] = make_claim_token(participant_id, secret)


@router.get("", response_model=SiteSearchResponse)
async def search_site(
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    q: Annotated[str, Query(max_length=MAX_QUERY_LENGTH * 2)] = "",
) -> SiteSearchResponse:
    """Локации (от 2 знаков) и люди (от 3 знаков) по одной строке запроса.

    Обработчик асинхронный ради одного: места для поиска людей (их три на
    процесс) запрос ждёт в цикле событий, не занимая поток. Сам поиск —
    синхронный код с базой — уходит в пул потоков, как у обычного
    обработчика. Раньше ждали в потоке, и залп поисков с одного адреса
    занимал весь пул: /health и страницы сайта стояли секундами (SKEP-2).
    """
    async with people_search_slot(q) as has_slot:
        page = await run_in_threadpool(site_search, db, q, people_allowed=has_slot)
    _attach_claim_tokens(page, settings.app_secret_key)
    return SiteSearchResponse.model_validate(page)


async def _raw_body(request: Request) -> bytes:
    """Тело запроса как есть — чтобы сам обработчик остался синхронным.

    Синхронный обработчик FastAPI уводит в пул потоков, и запись в БД не
    блокирует цикл событий; прочитать тело можно только асинхронно.
    """
    return await request.body()


@router.post("/log", status_code=204)
def log_search(
    request: Request,
    raw: Annotated[bytes, Depends(_raw_body)],
    db: Annotated[Session, Depends(get_db)],
    session_user_id: Annotated[UUID | None, Depends(get_optional_session_user_id)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    """Одна строка журнала на поиск; фронт шлёт её через navigator.sendBeacon.

    Тело разбираем сами, а не через pydantic-модель: sendBeacon со строкой
    отправляет Content-Type text/plain, и FastAPI такое тело как JSON не
    примет. Ответ всегда 204 — бекону ответ всё равно не нужен, а журнал не
    должен ничего сообщать о своих правилах отбора.
    """
    no_content = Response(status_code=204)
    if is_bot_user_agent(request.headers.get("user-agent")):
        return no_content
    if not raw or len(raw) > _MAX_LOG_BODY_BYTES:
        return no_content
    try:
        payload: Any = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return no_content
    if not isinstance(payload, dict):
        return no_content

    if session_user_id is not None:
        # Поиски админа — это его проверки сайта, а не спрос: в «Популярности»
        # его просмотры не пишутся по той же причине.
        viewer = db.get(User, session_user_id)
        if viewer is not None and is_admin_user(viewer, settings):
            return no_content
    record_search(db, payload, is_authed=session_user_id is not None)
    return no_content


def _claim_journal_allowed(request: Request, viewer: User | None, settings: Settings) -> bool:
    """Воронку «Это вы?» пишем, как журнал поиска: без ботов и без админа."""
    if is_bot_user_agent(request.headers.get("user-agent")):
        return False
    return viewer is None or not is_admin_user(viewer, settings)


@router.get("/claim", response_model=SearchClaimResponse)
def open_search_claim(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    session_user_id: Annotated[UUID | None, Depends(get_optional_session_user_id)],
    settings: Annotated[Settings, Depends(get_settings)],
    token: Annotated[str, Query(max_length=_MAX_CLAIM_TOKEN_LENGTH)] = "",
) -> SearchClaimResponse:
    """Окно «Это вы?» по строке человека из протоколов: карточка и что можно сделать.

    Вход не обязателен. Гость видит карточку и «Войти и привязать» —
    viewer_state у него всегда "guest": занят ли профиль, гостю не
    раскрываем. Вошедший получает исход привязки заранее (can_link,
    already_yours, platform_linked, taken), чтобы окно не предлагало кнопку,
    которая кончится ошибкой. Этот же вызов после входа — шаг «вошёл»
    воронки (record_claim_event).
    """
    try:
        claim = parse_claim_token(token, settings.app_secret_key)
    except SearchClaimTokenError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    found = claimable_participant(db, claim.participant_id)
    if found is None:
        raise HTTPException(status_code=404, detail=CLAIM_NOT_FOUND_MESSAGE)
    participant, platform = found

    viewer = db.get(User, session_user_id) if session_user_id is not None else None
    viewer_state = "guest" if viewer is None else participant_link_state(db, viewer, participant)[0]
    card = participant_claim_card(db, participant, platform)
    if _claim_journal_allowed(request, viewer, settings):
        record_claim_event(db, claim.ref, CLAIM_OPEN, is_authed=viewer is not None, platform_code=platform.code)
    return SearchClaimResponse(**card, viewer_state=viewer_state)


@router.post("/claim/decline", status_code=204)
def decline_search_claim(
    request: Request,
    raw: Annotated[bytes, Depends(_raw_body)],
    db: Annotated[Session, Depends(get_db)],
    session_user_id: Annotated[UUID | None, Depends(get_optional_session_user_id)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    """«Это не я» — этап воронки. Ответ всегда 204, как у журнала поиска.

    Тело разбираем сами: фронт шлёт его fetch(keepalive) и не ждёт ответа,
    а битый или истёкший токен здесь не ошибка — писать просто нечего.
    """
    no_content = Response(status_code=204)
    if not raw or len(raw) > _MAX_DECLINE_BODY_BYTES:
        return no_content
    try:
        payload: Any = json.loads(raw.decode("utf-8"))
        claim = parse_claim_token(str(payload.get("token") or ""), settings.app_secret_key)
    except (UnicodeDecodeError, ValueError, AttributeError, SearchClaimTokenError):
        return no_content
    viewer = db.get(User, session_user_id) if session_user_id is not None else None
    if not _claim_journal_allowed(request, viewer, settings):
        return no_content
    found = claimable_participant(db, claim.participant_id)
    record_claim_event(
        db,
        claim.ref,
        CLAIM_DECLINED,
        is_authed=viewer is not None,
        platform_code=found[1].code if found else None,
    )
    return no_content
