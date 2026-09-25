from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import Settings, get_settings
from app.core.admin import is_admin_user
from app.db.session import get_db
from app.models import User
from app.schemas.dashboard import (
    BestResultResponse,
    CoRunnerMeetingResponse,
    CoRunnerResponse,
    PersonalRecordResponse,
    RunItemResponse,
    VolunteeringItemResponse,
    VolunteerRoleStatResponse,
    WinResponse,
)
from app.schemas.tracks import (
    TrackDetailResponse,
    TrackLinkRequest,
    TrackSummaryResponse,
)
from app.services.co_runners_service import (
    list_co_runner_meetings,
    list_co_runners,
    parse_platform_codes,
)
from app.services.dashboard_service import (
    list_user_best_results,
    list_user_personal_records,
    list_user_runs,
    list_user_volunteer_role_stats,
    list_user_volunteering,
    list_user_wins,
)
from app.services.location_course_service import rebuild_for_tracks, rebuild_location_profile
from app.services.run_track_service import (
    build_track,
    delete_track,
    drop_own_previews,
    ensure_consent,
    find_existing,
    get_track,
    list_tracks,
)
from app.services.track_parsing import TrackParseError, parse_garmin_link, parse_upload

router = APIRouter(tags=["runs"])

# Трек пятикилометровой пробежки с посекундной записью весит десятки килобайт;
# лимит с большим запасом отсекает случайные архивы и чужие файлы.
MAX_TRACK_FILE_BYTES = 12 * 1024 * 1024


def _ensure_tracks_visible(user: User, settings: Settings) -> None:
    """Пока идёт сбор, треки видит только админ.

    Отвечаем 404, а не 403: наружу фича не анонсирована, и её существование
    незачем подтверждать. Открывается флагом tracks_public_enabled.
    """
    if settings.tracks_public_enabled or is_admin_user(user, settings):
        return
    raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")


@router.get("/runs", response_model=list[RunItemResponse])
def list_runs(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    include_test: Annotated[bool, Query()] = False,
) -> list[RunItemResponse]:
    items = list_user_runs(
        db,
        user.id,
        limit=limit,
        offset=offset,
        include_test_events=include_test,
    )
    return [RunItemResponse.model_validate(item) for item in items]


@router.get("/runs/co-runners", response_model=list[CoRunnerResponse])
def list_run_co_runners(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    include_test: Annotated[bool, Query()] = False,
    platforms: Annotated[
        str | None, Query(description="Коды систем через запятую; пусто — все")
    ] = None,
) -> list[CoRunnerResponse]:
    items = list_co_runners(
        db,
        user.id,
        include_test_events=include_test,
        limit=limit,
        platform_codes=parse_platform_codes(platforms),
    )
    return [CoRunnerResponse.model_validate(item) for item in items]


@router.get("/runs/co-runners/{participant_key}/meetings", response_model=list[CoRunnerMeetingResponse])
def list_run_co_runner_meetings(
    participant_key: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    include_test: Annotated[bool, Query()] = False,
    platforms: Annotated[
        str | None, Query(description="Коды систем через запятую; пусто — все")
    ] = None,
) -> list[CoRunnerMeetingResponse]:
    items = list_co_runner_meetings(
        db,
        user.id,
        participant_key,
        include_test_events=include_test,
        platform_codes=parse_platform_codes(platforms),
    )
    return [CoRunnerMeetingResponse.model_validate(item) for item in items]


@router.get("/runs/tracks", response_model=list[TrackSummaryResponse])
def list_run_tracks(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[TrackSummaryResponse]:
    _ensure_tracks_visible(user, settings)
    return [TrackSummaryResponse.model_validate(track) for track in list_tracks(db, user.id)]


@router.post("/runs/tracks/upload", response_model=TrackDetailResponse, status_code=status.HTTP_201_CREATED)
def upload_run_track(
    file: UploadFile,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TrackDetailResponse:
    """Приём файла с часов: GPX, TCX или оригинальный FIT."""
    _ensure_tracks_visible(user, settings)
    data = file.file.read(MAX_TRACK_FILE_BYTES + 1)
    if len(data) > MAX_TRACK_FILE_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Файл слишком большой.")
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Файл пустой.")
    try:
        parsed = parse_upload(file.filename or "", data)
    except TrackParseError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return _save_track(db, user, parsed)


@router.post("/runs/tracks/link", response_model=TrackDetailResponse, status_code=status.HTTP_201_CREATED)
def import_run_track_link(
    payload: TrackLinkRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TrackDetailResponse:
    """Импорт по ссылке на публичную активность Garmin Connect."""
    _ensure_tracks_visible(user, settings)
    try:
        parsed = parse_garmin_link(payload.url)
    except TrackParseError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return _save_track(db, user, parsed)


def _save_track(db: Session, user: User, parsed) -> TrackDetailResponse:
    """Разбирает трек и кладёт его ЧЕРНОВИКОМ: человек сначала смотрит разбор.

    В профиль трек попадает только после «Сохранить» (эндпоинт confirm).
    До этого он не виден ни в списке треков, ни в паспорте трассы локации —
    приложить файл и передумать должно быть можно без следов.
    """
    existing = find_existing(db, user.id, parsed.source, parsed.source_ref)
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Этот трек уже загружен.")
    try:
        track = build_track(db, user, parsed)
    except TrackParseError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if track.exclusion_reason == "far_from_location":
        # Не записываем молча: человек приложил трек другой пробежки.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{track.exclusion_note}. Похоже, это трек другой пробежки.",
        )
    drop_own_previews(db, user.id)
    track.status = "preview"
    db.add(track)
    db.flush()
    db.commit()
    db.refresh(track)
    return TrackDetailResponse.model_validate(track)


@router.get("/runs/tracks/{track_id}", response_model=TrackDetailResponse)
def get_run_track(
    track_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TrackDetailResponse:
    _ensure_tracks_visible(user, settings)
    # include_preview: свой черновик человек должен видеть, пока решает,
    # сохранять его или отменить.
    track = get_track(db, user.id, track_id, include_preview=True)
    if track is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Трек не найден.")
    return TrackDetailResponse.model_validate(track)


@router.post("/runs/tracks/{track_id}/confirm", response_model=TrackDetailResponse)
def confirm_run_track(
    track_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TrackDetailResponse:
    """«Сохранить»: черновик становится треком пробежки в профиле."""
    _ensure_tracks_visible(user, settings)
    track = get_track(db, user.id, track_id, include_preview=True)
    if track is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Трек не найден.")
    if track.status != "ok":
        ensure_consent(db, user)
        track.status = "ok"
        db.flush()
        # Паспорт трассы локации пересобираем только сейчас: черновик в
        # измерения трассы не идёт.
        rebuild_for_tracks(db, [track])
        db.commit()
        db.refresh(track)
    return TrackDetailResponse.model_validate(track)


@router.delete("/runs/tracks/{track_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_run_track(
    track_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    _ensure_tracks_visible(user, settings)
    track = get_track(db, user.id, track_id)
    location_id = track.location_id if track else None
    if not delete_track(db, user.id, track_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Трек не найден.")
    if location_id is not None:
        # Трек ушёл — паспорт трассы пересобираем без него.
        rebuild_location_profile(db, location_id)
    db.commit()



@router.get("/runs/best-results", response_model=list[BestResultResponse])
def list_best_results(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    include_test: Annotated[bool, Query()] = False,
) -> list[BestResultResponse]:
    items = list_user_best_results(db, user.id, include_test_events=include_test)
    return [BestResultResponse.model_validate(item) for item in items]


@router.get("/runs/personal-records", response_model=list[PersonalRecordResponse])
def list_personal_records(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    include_test: Annotated[bool, Query()] = False,
) -> list[PersonalRecordResponse]:
    items = list_user_personal_records(db, user.id, include_test_events=include_test)
    return [PersonalRecordResponse.model_validate(item) for item in items]


@router.get("/runs/wins", response_model=list[WinResponse])
def list_wins(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    include_test: Annotated[bool, Query()] = False,
) -> list[WinResponse]:
    items = list_user_wins(db, user.id, include_test_events=include_test)
    return [WinResponse.model_validate(item) for item in items]


@router.get("/volunteering", response_model=list[VolunteeringItemResponse])
def list_volunteering(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    include_test: Annotated[bool, Query()] = False,
) -> list[VolunteeringItemResponse]:
    items = list_user_volunteering(
        db,
        user.id,
        limit=limit,
        offset=offset,
        include_test_events=include_test,
    )
    return [VolunteeringItemResponse.model_validate(item) for item in items]


@router.get("/volunteering/role-stats", response_model=list[VolunteerRoleStatResponse])
def list_volunteer_role_stats(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    include_test: Annotated[bool, Query()] = False,
) -> list[VolunteerRoleStatResponse]:
    items = list_user_volunteer_role_stats(db, user.id, include_test_events=include_test)
    return [VolunteerRoleStatResponse.model_validate(item) for item in items]
