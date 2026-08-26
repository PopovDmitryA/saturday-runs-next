"""Кабинет организатора локации.

Все эндпоинты закрыты логином + проверкой доступа к конкретной локации:
автодоступ по волонтёрствам в роли организатора или ручной грант из админки
(см. organizer_access_service). Админ проходит к любой локации.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import Settings, get_settings
from app.core.admin import is_admin_user
from app.db.session import get_db
from app.models import User
from app.schemas.organizer import (
    AbsenceResponse,
    AttendanceResponse,
    AudienceResponse,
    BenchmarkResponse,
    BenchResponse,
    LocationHealthResponse,
    MilestonesResponse,
    NewcomersResponse,
    OrganizerEventDatesResponse,
    OrganizerLocationsResponse,
    OrganizerPostResponse,
    ProtocolTimelineResponse,
    SvodResponse,
    TeamLoadResponse,
)
from app.services.admin_event_report_service import build_event_svod
from app.services.location_page_service import LocationIdentity, resolve_location_identity
from app.services.organizer_access_service import (
    build_organizer_locations,
    has_organizer_access,
)
from app.services.organizer_analytics_service import (
    build_attendance,
    build_audience,
    build_benchmark,
    build_team_load,
)
from app.services.organizer_export_service import (
    XLSX_MEDIA_TYPE,
    build_svod_workbook,
    svod_export_filename,
)
from app.services.organizer_post_service import (
    POST_TEMPLATES,
    build_event_post,
    build_travelers_post,
    build_upcoming_post,
    build_vacancies_post,
)
from app.services.organizer_service import (
    ABSENCE_MIN_MISSED_DEFAULT,
    ABSENCE_MIN_RUNS_DEFAULT,
    BENCH_MIN_RUNS_DEFAULT,
    NEWCOMERS_DEFAULT_DAYS,
    build_location_absence,
    build_location_milestones,
    build_location_newcomers,
    build_location_volunteer_bench,
    list_identity_event_dates,
)

router = APIRouter(prefix="/organizer", tags=["organizer"])


def _require_identity_access(
    db: Session, user: User, settings: Settings, slug: str
) -> LocationIdentity:
    identity = resolve_location_identity(db, slug)
    if identity is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Локация не найдена")
    if is_admin_user(user, settings):
        return identity
    if not has_organizer_access(db, user, identity.identity_key):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Раздел доступен организаторам этой локации",
        )
    return identity


@router.get("/locations", response_model=OrganizerLocationsResponse)
def organizer_locations(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> OrganizerLocationsResponse:
    # Админ видит весь каталог — «как будто организатор везде».
    if is_admin_user(user, settings):
        from app.services.organizer_access_service import build_admin_organizer_locations

        return OrganizerLocationsResponse.model_validate(build_admin_organizer_locations(db))
    payload = build_organizer_locations(db, user)
    return OrganizerLocationsResponse.model_validate(payload)


@router.get("/{slug}/absence", response_model=AbsenceResponse)
def organizer_absence(
    slug: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    min_runs: Annotated[int, Query(ge=1, le=100)] = ABSENCE_MIN_RUNS_DEFAULT,
    min_missed: Annotated[int, Query(ge=1, le=100)] = ABSENCE_MIN_MISSED_DEFAULT,
) -> AbsenceResponse:
    identity = _require_identity_access(db, user, settings, slug)
    payload = build_location_absence(db, identity, min_runs=min_runs, min_missed=min_missed)
    return AbsenceResponse.model_validate(payload)


@router.get("/{slug}/event-dates", response_model=OrganizerEventDatesResponse)
def organizer_event_dates(
    slug: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> OrganizerEventDatesResponse:
    identity = _require_identity_access(db, user, settings, slug)
    return OrganizerEventDatesResponse.model_validate(
        {
            "location": {"slug": identity.slug, "name": identity.name},
            "items": list_identity_event_dates(db, identity),
        }
    )


def _svod_payload(db: Session, identity: LocationIdentity, event_id: UUID) -> dict[str, Any]:
    payload = build_event_svod(db, event_id)
    location_ids = {location.id for location, _code in identity.locations}
    # Событие чужой локации — 404, а не свод: слаг в пути определяет и доступ.
    if payload is None or payload["event"]["location_id"] not in location_ids:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Событие не найдено")
    return payload


@router.get("/{slug}/event-report", response_model=SvodResponse)
def organizer_event_report(
    slug: str,
    event_id: Annotated[UUID, Query()],
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SvodResponse:
    identity = _require_identity_access(db, user, settings, slug)
    return SvodResponse.model_validate(_svod_payload(db, identity, event_id))


@router.get("/{slug}/event-report.xlsx")
def organizer_event_report_xlsx(
    slug: str,
    event_id: Annotated[UUID, Query()],
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    """Свод одним .xlsx: листы «Бегуны» и «Волонтёры» — для отчёта оргкоманды.

    Скачивается обычной ссылкой (кука same-origin), поэтому это GET без
    JSON-обёртки: первый эндпоинт сайта с Content-Disposition.
    """
    identity = _require_identity_access(db, user, settings, slug)
    payload = _svod_payload(db, identity, event_id)
    content = build_svod_workbook(payload)
    event = payload["event"]
    filename = svod_export_filename(identity.slug, str(event["event_date"]))
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{slug}/event-post", response_model=OrganizerPostResponse)
def organizer_event_post(
    slug: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    event_id: Annotated[UUID | None, Query()] = None,
    template: Annotated[str, Query()] = "full",
    min_run_milestone: Annotated[int, Query(ge=10, le=1000)] = 10,
    min_vol_milestone: Annotated[int, Query(ge=10, le=1000)] = 10,
    travelers_min_runs: Annotated[int, Query(ge=1, le=100)] = 5,
) -> OrganizerPostResponse:
    """Пост для Telegram по выбранному шаблону.

    Шаблоны собраны по анализу каналов локаций (см. organizer_post_service).
    «Юбилеи завтра» (upcoming) строится по локации — событие ему не нужно,
    остальным шаблонам event_id обязателен.
    """
    if template not in POST_TEMPLATES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Неизвестный шаблон")
    identity = _require_identity_access(db, user, settings, slug)
    if template == "upcoming":
        milestones = build_location_milestones(db, identity)
        return OrganizerPostResponse(
            post_text=build_upcoming_post(
                milestones,
                min_run_milestone=min_run_milestone,
                min_vol_milestone=min_vol_milestone,
            ),
            template=template,
        )
    # «Нужны волонтёры» — по живой записи 5 вёрст, событие не нужно.
    if template == "vacancies":
        return OrganizerPostResponse(
            post_text=build_vacancies_post(db, identity), template=template
        )
    if event_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Для этого шаблона нужно событие (event_id)",
        )
    if template == "travelers":
        from app.models import Event

        event = db.query(Event).filter(Event.id == event_id).one_or_none()
        location_ids = {location.id for location, _code in identity.locations}
        if event is None or event.location_id not in location_ids:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Событие не найдено"
            )
        return OrganizerPostResponse(
            post_text=build_travelers_post(db, identity, event, min_runs=travelers_min_runs),
            template=template,
        )
    payload = build_event_post(db, event_id, template)
    location_ids = {location.id for location, _code in identity.locations}
    if payload is None or payload["location_id"] not in location_ids:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Событие не найдено")
    return OrganizerPostResponse(post_text=payload["post_text"], template=template)


@router.get("/{slug}/milestones", response_model=MilestonesResponse)
def organizer_milestones(
    slug: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> MilestonesResponse:
    identity = _require_identity_access(db, user, settings, slug)
    return MilestonesResponse.model_validate(build_location_milestones(db, identity))


@router.get("/{slug}/newcomers", response_model=NewcomersResponse)
def organizer_newcomers(
    slug: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    days: Annotated[int, Query(ge=30, le=730)] = NEWCOMERS_DEFAULT_DAYS,
) -> NewcomersResponse:
    identity = _require_identity_access(db, user, settings, slug)
    return NewcomersResponse.model_validate(build_location_newcomers(db, identity, days=days))


@router.get("/{slug}/volunteers", response_model=BenchResponse)
def organizer_volunteer_bench(
    slug: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    min_runs: Annotated[int, Query(ge=1, le=100)] = BENCH_MIN_RUNS_DEFAULT,
) -> BenchResponse:
    identity = _require_identity_access(db, user, settings, slug)
    return BenchResponse.model_validate(
        build_location_volunteer_bench(db, identity, min_runs=min_runs)
    )


@router.get("/{slug}/team", response_model=TeamLoadResponse)
def organizer_team_load(
    slug: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    months: Annotated[int, Query(ge=3, le=60)] = 12,
) -> TeamLoadResponse:
    """Нагрузка на команду и bus-фактор ролей: кто выгорит первым."""
    identity = _require_identity_access(db, user, settings, slug)
    return TeamLoadResponse.model_validate(build_team_load(db, identity, months=months))


@router.get("/{slug}/attendance", response_model=AttendanceResponse)
def organizer_attendance(
    slug: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AttendanceResponse:
    """Динамика посещаемости: ряд по стартам, месячные средние, год к году."""
    identity = _require_identity_access(db, user, settings, slug)
    return AttendanceResponse.model_validate(build_attendance(db, identity))


@router.get("/{slug}/audience", response_model=AudienceResponse)
def organizer_audience(
    slug: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    months: Annotated[int, Query(ge=3, le=60)] = 12,
) -> AudienceResponse:
    """Портрет участника: возрастные группы, пол, клубы."""
    identity = _require_identity_access(db, user, settings, slug)
    return AudienceResponse.model_validate(build_audience(db, identity, months=months))


@router.get("/{slug}/benchmark", response_model=BenchmarkResponse)
def organizer_benchmark(
    slug: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    # 0 — текущий календарный год (с 1 января).
    months: Annotated[int, Query(ge=0, le=120)] = 12,
    scope: Annotated[str, Query(pattern="^(city|region|nearest|network)$")] = "network",
) -> BenchmarkResponse:
    """Сравнение с соседями: город, регион или вся система."""
    identity = _require_identity_access(db, user, settings, slug)
    return BenchmarkResponse.model_validate(
        build_benchmark(db, identity, months=months, scope=scope)
    )


@router.get("/{slug}/protocols", response_model=ProtocolTimelineResponse)
def organizer_protocols(
    slug: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ProtocolTimelineResponse:
    """Скорость выгрузки протоколов: старты, задержки, организатор дня, правки."""
    from app.services.organizer_protocol_service import build_protocol_timeline

    identity = _require_identity_access(db, user, settings, slug)
    return ProtocolTimelineResponse.model_validate(build_protocol_timeline(db, identity))


@router.get("/{slug}/health", response_model=LocationHealthResponse)
def organizer_health(
    slug: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> LocationHealthResponse:
    """Светофор здоровья локации: протокол, ротация, фотограф, новички."""
    from app.services.organizer_protocol_service import build_location_health

    identity = _require_identity_access(db, user, settings, slug)
    return LocationHealthResponse.model_validate(build_location_health(db, identity))
