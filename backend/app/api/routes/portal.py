from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.portal import PortalHomeResponse
from app.services.portal_home_service import build_portal_home

router = APIRouter(prefix="/portal", tags=["portal"])


@router.get("/home", response_model=PortalHomeResponse)
def get_portal_home(db: Session = Depends(get_db)) -> PortalHomeResponse:
    """Публичные агрегаты для главной портала (без авторизации)."""
    return build_portal_home(db)
