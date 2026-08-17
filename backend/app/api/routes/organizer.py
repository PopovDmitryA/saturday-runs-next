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
    OrganizerEventDatesResponse,
    OrganizerLocationsResponse,
    SvodResponse,
)
from app.services.admin_event_report_service import build_event_svod
from app.services.location_page_service import LocationIdentity, resolve_location_identity
from app.services.organizer_access_service import (
    build_organizer_locations,
    has_organizer_access,
)
from app.services.organizer_export_service import (
    XLSX_MEDIA_TYPE,
    build_svod_workbook,
    svod_export_filename,
)
from app.services.organizer_service import (
    ABSENCE_MIN_MISSED_DEFAULT,
    ABSENCE_MIN_RUNS_DEFAULT,
    build_location_absence,
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
) -> OrganizerLocationsResponse:
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
