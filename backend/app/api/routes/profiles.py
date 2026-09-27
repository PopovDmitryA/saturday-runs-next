from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import Settings, get_settings
from app.core.admin import is_admin_user
from app.core.bot_detection import is_bot_user_agent
from app.core.request_cancel import RequestCancelled, run_sync_with_disconnect_watch
from app.db.session import get_db
from app.models import PlatformLink, User
from app.platform_adapters.canonical import ProfilePreview
from app.schemas.profiles import (
    LinkByParticipantRequest,
    LinkBySearchTokenRequest,
    LinkBySearchTokenResponse,
    ParticipantSearchResponse,
    ParticipantSearchResultResponse,
    PlatformLinkResponse,
    ProfileClaimRequest,
    ProfileClaimResponse,
    ProfileLinkConfirmResponse,
    ProfilePreviewResponse,
    ProfileUnlinkResponse,
    ProfileUrlRequest,
    S95ConfirmRequest,
    S95ProfileLinkConfirmResponse,
    S95ProfilePreviewResponse,
)
from app.services.participant_search_service import ParticipantSearchError, search_participants
from app.services.profile_linking_service import (
    LINK_METHOD_SITE_SEARCH,
    ProfileLinkingError,
    claim_profile_by_athlete_id,
    confirm_profile_link,
    confirm_profile_link_by_participant,
    confirm_s95_profile_link,
    list_user_profile_links,
    participant_link_state,
    preview_profile_link,
    preview_runpark_profile_link,
    preview_s95_profile_link,
)
from app.services.profile_unlink_service import unlink_user_profile
from app.services.search_claim_service import CLAIM_NOT_FOUND_MESSAGE, SearchClaimTokenError, parse_claim_token
from app.services.search_log_service import CLAIM_FAILED, CLAIM_LINKED, record_claim_event
from app.services.site_search_service import claimable_participant

router = APIRouter(prefix="/profiles", tags=["profiles"])
logger = logging.getLogger(__name__)

FIVE_VERST_CODE = "five_verst"
S95_CODE = "s95"
PARKRUN_CODE = "parkrun"
RUNPARK_CODE = "runpark"


def _handle_linking_error(exc: ProfileLinkingError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


async def _run_with_cancel(
    request: Request,
    func,
    /,
    *args,
    **kwargs,
):
    try:
        return await run_sync_with_disconnect_watch(request, func, *args, **kwargs)
    except RequestCancelled:
        return None


async def _preview_with_cancel(
    request: Request,
    db: Session,
    platform_code: str,
    profile_url: str,
    user: User,
) -> ProfilePreview | None:
    try:
        return await run_sync_with_disconnect_watch(
            request,
            preview_profile_link,
            db,
            platform_code,
            profile_url,
            user=user,
        )
    except RequestCancelled:
        return None


@router.post("/five-verst/preview", response_model=ProfilePreviewResponse)
async def five_verst_preview(
    request: Request,
    body: ProfileUrlRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> ProfilePreviewResponse | Response:
    try:
        preview = await _preview_with_cancel(request, db, FIVE_VERST_CODE, body.profile_url, user)
        if preview is None:
            return Response(status_code=499)
    except ProfileLinkingError as exc:
        raise _handle_linking_error(exc) from exc
    return ProfilePreviewResponse.model_validate(asdict(preview))


@router.post("/five-verst/confirm", response_model=ProfileLinkConfirmResponse)
def five_verst_confirm(
    body: ProfileUrlRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> ProfileLinkConfirmResponse:
    try:
        link = confirm_profile_link(db, user, FIVE_VERST_CODE, body.profile_url)
    except ProfileLinkingError as exc:
        raise _handle_linking_error(exc) from exc

    links = list_user_profile_links(db, user)
    link_data = next(item for item in links if item["id"] == link.id)
    return ProfileLinkConfirmResponse(link=PlatformLinkResponse.model_validate(link_data))


def _preview_to_response(preview: ProfilePreview) -> ProfilePreviewResponse:
    return ProfilePreviewResponse.model_validate(asdict(preview))


@router.post("/s95/preview", response_model=S95ProfilePreviewResponse)
async def s95_preview(
    request: Request,
    body: ProfileUrlRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> S95ProfilePreviewResponse | Response:
    try:
        result = await _run_with_cancel(
            request,
            preview_s95_profile_link,
            db,
            body.profile_url,
            user=user,
        )
        if result is None:
            return Response(status_code=499)
        s95_preview, parkrun_match = result

        return S95ProfilePreviewResponse(
            **_preview_to_response(s95_preview).model_dump(),
            parkrun_match=_preview_to_response(parkrun_match) if parkrun_match else None,
        )
    except RequestCancelled:
        return Response(status_code=499)
    except ProfileLinkingError as exc:
        raise _handle_linking_error(exc) from exc


@router.post("/s95/confirm", response_model=S95ProfileLinkConfirmResponse)
def s95_confirm(
    body: S95ConfirmRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> S95ProfileLinkConfirmResponse:
    try:
        s95_link, parkrun_link = confirm_s95_profile_link(
            db,
            user,
            body.profile_url,
            link_parkrun=body.link_parkrun,
        )
    except ProfileLinkingError as exc:
        raise _handle_linking_error(exc) from exc

    links = list_user_profile_links(db, user)
    s95_data = next(item for item in links if item["id"] == s95_link.id)
    parkrun_data = (
        next(item for item in links if item["id"] == parkrun_link.id)
        if parkrun_link is not None
        else None
    )
    if body.link_parkrun and parkrun_link is not None:
        message = "linked_both"
    elif body.link_parkrun:
        message = "linked_s95_parkrun_skipped"
    else:
        message = "linked"
    return S95ProfileLinkConfirmResponse(
        link=PlatformLinkResponse.model_validate(s95_data),
        parkrun_link=PlatformLinkResponse.model_validate(parkrun_data) if parkrun_data else None,
        message=message,
    )


@router.post("/parkrun/preview", response_model=ProfilePreviewResponse)
async def parkrun_preview(
    request: Request,
    body: ProfileUrlRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> ProfilePreviewResponse | Response:
    try:
        preview = await _preview_with_cancel(request, db, PARKRUN_CODE, body.profile_url, user)
        if preview is None:
            return Response(status_code=499)
    except ProfileLinkingError as exc:
        raise _handle_linking_error(exc) from exc
    return ProfilePreviewResponse.model_validate(asdict(preview))


@router.post("/parkrun/confirm", response_model=ProfileLinkConfirmResponse)
def parkrun_confirm(
    body: ProfileUrlRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> ProfileLinkConfirmResponse:
    try:
        link = confirm_profile_link(db, user, PARKRUN_CODE, body.profile_url)
    except ProfileLinkingError as exc:
        raise _handle_linking_error(exc) from exc

    links = list_user_profile_links(db, user)
    link_data = next(item for item in links if item["id"] == link.id)
    return ProfileLinkConfirmResponse(link=PlatformLinkResponse.model_validate(link_data))


@router.post("/runpark/preview", response_model=ProfilePreviewResponse)
async def runpark_preview(
    request: Request,
    body: ProfileUrlRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> ProfilePreviewResponse | Response:
    try:
        result = await _run_with_cancel(request, preview_runpark_profile_link, db, body.profile_url, user=user)
        if result is None:
            return Response(status_code=499)
    except ProfileLinkingError as exc:
        raise _handle_linking_error(exc) from exc
    from dataclasses import asdict
    return ProfilePreviewResponse.model_validate(asdict(result))


@router.post("/runpark/confirm", response_model=ProfileLinkConfirmResponse)
def runpark_confirm(
    body: ProfileUrlRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> ProfileLinkConfirmResponse:
    try:
        link = confirm_profile_link(db, user, RUNPARK_CODE, body.profile_url)
    except ProfileLinkingError as exc:
        raise _handle_linking_error(exc) from exc

    links = list_user_profile_links(db, user)
    link_data = next(item for item in links if item["id"] == link.id)
    return ProfileLinkConfirmResponse(link=PlatformLinkResponse.model_validate(link_data))


@router.get("/search", response_model=ParticipantSearchResponse)
def search_profiles(
    q: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> ParticipantSearchResponse:
    from dataclasses import asdict as dataclass_asdict

    try:
        page = search_participants(db, user, q)
    except ParticipantSearchError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return ParticipantSearchResponse(
        query=page.query,
        results=[
            ParticipantSearchResultResponse.model_validate(dataclass_asdict(item)) for item in page.results
        ],
        truncated=page.truncated,
        hidden_linked_platform_codes=page.hidden_linked_platform_codes,
    )


@router.post("/link-by-participant", response_model=ProfileLinkConfirmResponse)
def link_by_participant(
    body: LinkByParticipantRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> ProfileLinkConfirmResponse:
    try:
        link = confirm_profile_link_by_participant(db, user, body.participant_id)
    except ProfileLinkingError as exc:
        raise _handle_linking_error(exc) from exc

    links = list_user_profile_links(db, user)
    link_data = next(item for item in links if item["id"] == link.id)
    return ProfileLinkConfirmResponse(link=PlatformLinkResponse.model_validate(link_data))


# Гонка двух привязок (двойной клик, окно в двух вкладках) доходит до
# уникальных ключей platform_links — отвечаем тем же текстом, что дали бы
# проверки, а не 500.
_SEARCH_TOKEN_CONFLICT_MESSAGES = {
    "platform_linked": "Профиль на этой платформе уже привязан к вашему аккаунту",
    "taken": "Этот профиль уже привязан к другому аккаунту",
}
_SEARCH_TOKEN_CONFLICT_FALLBACK = "Профиль уже привязан — обновите страницу"


def _search_token_link_response(db: Session, user: User, status: str, link: PlatformLink) -> LinkBySearchTokenResponse:
    links = list_user_profile_links(db, user)
    link_data = next(item for item in links if item["id"] == link.id)
    return LinkBySearchTokenResponse(status=status, link=PlatformLinkResponse.model_validate(link_data))


@router.post("/link-by-search-token", response_model=LinkBySearchTokenResponse)
def link_by_search_token(
    request: Request,
    body: LinkBySearchTokenRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> LinkBySearchTokenResponse:
    """«Это я — привязать» из поиска по сайту: ровно тот человек, чью строку нажали.

    Токен строки выдачи (claim_token) переживает вход: гость нажал свою
    строку, вошёл — и привязывается она же, без повторного выбора среди
    однофамильцев. Проверки — те же, что у link-by-participant; «этот
    профиль уже мой» (повторное нажатие, вторая вкладка) — успех
    already_linked, а не 409. Путь — в /profiles, а не в /search: пишущей
    ручке место в обычном тарифе защиты, а не в щедром поисковом.
    """
    journal = not is_bot_user_agent(request.headers.get("user-agent")) and not is_admin_user(user, settings)

    def fail(ref: str | None, platform_code: str | None, status_code: int, message: str) -> HTTPException:
        if journal and ref:
            record_claim_event(
                db, ref, CLAIM_FAILED, is_authed=True, platform_code=platform_code, status=status_code
            )
        return HTTPException(status_code=status_code, detail=message)

    try:
        claim = parse_claim_token(body.token, settings.app_secret_key)
    except SearchClaimTokenError as exc:
        # У истёкшего токена подпись верна и ref известен — это тоже сход с воронки.
        raise fail(exc.ref, None, exc.status_code, exc.message) from exc
    found = claimable_participant(db, claim.participant_id)
    if found is None:
        raise fail(claim.ref, None, 404, CLAIM_NOT_FOUND_MESSAGE)
    participant, platform = found
    platform_code = platform.code

    state, own_link = participant_link_state(db, user, participant)
    if state == "already_yours" and own_link is not None:
        return _search_token_link_response(db, user, "already_linked", own_link)
    try:
        link = confirm_profile_link_by_participant(db, user, participant.id, method=LINK_METHOD_SITE_SEARCH)
    except ProfileLinkingError as exc:
        raise fail(claim.ref, platform_code, exc.status_code, exc.message) from exc
    except IntegrityError as exc:
        db.rollback()
        state, own_link = participant_link_state(db, user, participant)
        if state == "already_yours" and own_link is not None:
            return _search_token_link_response(db, user, "already_linked", own_link)
        message = _SEARCH_TOKEN_CONFLICT_MESSAGES.get(state, _SEARCH_TOKEN_CONFLICT_FALLBACK)
        raise fail(claim.ref, platform_code, 409, message) from exc

    if journal:
        record_claim_event(db, claim.ref, CLAIM_LINKED, is_authed=True, platform_code=platform_code)
    return _search_token_link_response(db, user, "linked", link)


@router.get("", response_model=list[PlatformLinkResponse])
def list_profiles(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> list[PlatformLinkResponse]:
    links = list_user_profile_links(db, user)
    return [PlatformLinkResponse.model_validate(item) for item in links]


SUPPORTED_UNLINK_PLATFORMS = frozenset({FIVE_VERST_CODE, S95_CODE, PARKRUN_CODE, RUNPARK_CODE})


@router.delete("/{platform_code}", response_model=ProfileUnlinkResponse)
def unlink_profile(
    platform_code: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> ProfileUnlinkResponse:
    if platform_code not in SUPPORTED_UNLINK_PLATFORMS:
        raise HTTPException(status_code=404, detail="Unknown platform")
    try:
        payload = unlink_user_profile(db, user, platform_code)
    except ProfileLinkingError as exc:
        raise _handle_linking_error(exc) from exc
    return ProfileUnlinkResponse.model_validate(payload)


@router.post("/claim", response_model=ProfileClaimResponse)
async def claim_profile(
    request: Request,
    body: ProfileClaimRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> ProfileClaimResponse | Response:
    """Досылка привязки после входа: человек ввёл ID в тизере ДО регистрации.

    Один вызов вместо пары «предпросмотр → подтверждение»: своё имя и свои
    цифры человек уже видел в тизере на главной, повторно подтверждать нечего.
    Внутри всё те же проверки, что и в ручной привязке, включая «этот профиль
    уже привязан к другому аккаунту».
    """
    try:
        outcome = await _run_with_cancel(
            request,
            claim_profile_by_athlete_id,
            db,
            user,
            body.platform_code,
            body.athlete_id,
        )
    except ProfileLinkingError as exc:
        raise _handle_linking_error(exc) from exc

    if outcome is None:
        # Клиент отвалился на середине внешнего фетча — как в остальных ручках.
        return Response(status_code=499)

    status, link = outcome
    if link is None:
        return ProfileClaimResponse(status=status, platform_code=body.platform_code)

    links = list_user_profile_links(db, user)
    link_data = next(item for item in links if item["id"] == link.id)
    return ProfileClaimResponse(
        status=status,
        platform_code=body.platform_code,
        link=PlatformLinkResponse.model_validate(link_data),
    )
