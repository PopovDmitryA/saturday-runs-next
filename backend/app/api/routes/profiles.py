from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.request_cancel import RequestCancelled, run_sync_with_disconnect_watch
from app.db.session import get_db
from app.models import User
from app.platform_adapters.canonical import ProfilePreview
from app.schemas.profiles import (
    PlatformLinkResponse,
    ProfileLinkConfirmResponse,
    ProfilePreviewResponse,
    ProfileUnlinkResponse,
    ProfileUrlRequest,
    S95ConfirmRequest,
    S95ProfileLinkConfirmResponse,
    S95ProfilePreviewResponse,
)
from app.services.profile_linking_service import (
    ProfileLinkingError,
    confirm_profile_link,
    confirm_s95_profile_link,
    list_user_profile_links,
    preview_profile_link,
    preview_runpark_profile_link,
    preview_s95_profile_link,
)
from app.services.profile_unlink_service import unlink_user_profile

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
    from app.debug_agent_log import agent_log

    started = __import__("time").time()
    agent_log(
        location="profiles.py:s95_preview:entry",
        message="s95 preview request",
        data={"url_len": len(body.profile_url.strip()), "has_url": bool(body.profile_url.strip())},
        hypothesis_id="D",
    )
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

        agent_log(
            location="profiles.py:s95_preview:success",
            message="s95 preview ok",
            data={
                "external_user_id": s95_preview.external_user_id,
                "has_parkrun_match": parkrun_match is not None,
            },
            hypothesis_id="A",
        )
        return S95ProfilePreviewResponse(
            **_preview_to_response(s95_preview).model_dump(),
            parkrun_match=_preview_to_response(parkrun_match) if parkrun_match else None,
        )
    except RequestCancelled:
        return Response(status_code=499)
    except ProfileLinkingError as exc:
        agent_log(
            location="profiles.py:s95_preview:linking_error",
            message="ProfileLinkingError",
            data={"status_code": exc.status_code, "error_type": type(exc).__name__},
            hypothesis_id="D",
        )
        raise _handle_linking_error(exc) from exc
    except Exception as exc:
        agent_log(
            location="profiles.py:s95_preview:unhandled",
            message="unhandled exception",
            data={"error_type": type(exc).__name__, "error_msg": str(exc)[:300]},
            hypothesis_id="D",
        )
        raise
    finally:
        agent_log(
            location="profiles.py:s95_preview:exit",
            message="s95 preview finished",
            data={"elapsed_ms": int((__import__("time").time() - started) * 1000)},
            hypothesis_id="A",
        )


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
