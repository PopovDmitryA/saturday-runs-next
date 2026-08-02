from __future__ import annotations

from dataclasses import dataclass
from typing import cast
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import User
from app.services.user_unique_locations_detail import build_user_unique_location_details


@dataclass(frozen=True)
class HomeLocationCandidate:
    catalog_identity_key: str
    name: str
    city: str | None
    region: str | None
    run_count: int
    volunteer_count: int
    platform_codes: list[str]


class UnknownHomeLocationError(Exception):
    pass


def list_home_location_candidates(db: Session, user_id: UUID) -> list[HomeLocationCandidate]:
    """Unique visited locations (deduped by catalog), sorted by name.

    Reuses the same catalog-identity dedup as the unique-locations modal, so a
    location present under both five_verst and runpark shows up once.
    """
    return home_location_candidates_from_detail(build_user_unique_location_details(db, user_id))


def home_location_candidates_from_detail(detail: dict[str, object]) -> list[HomeLocationCandidate]:
    """Тот же список, но из уже посчитанной детализации: «дальность от дома»
    строит её ради координат и не должна считать всё второй раз."""
    locations = cast(list[dict[str, object]], detail["locations"])
    return [
        HomeLocationCandidate(
            catalog_identity_key=str(item["catalog_identity_key"]),
            name=str(item["name"]),
            city=cast("str | None", item["city"]),
            region=cast("str | None", item["region"]),
            run_count=int(cast(int, item["run_count"])),
            volunteer_count=int(cast(int, item["volunteer_count"])),
            platform_codes=[
                str(platform["platform_code"])
                for platform in cast(list[dict[str, object]], item["platforms"])
            ],
        )
        for item in locations
    ]


def _auto_home_location(
    candidates: list[HomeLocationCandidate],
) -> HomeLocationCandidate | None:
    """Most runs wins; ties go to whichever sorts first (candidates are name-sorted)."""
    if not candidates:
        return None
    return max(candidates, key=lambda candidate: candidate.run_count)


def resolve_home_location(
    db: Session, user: User
) -> tuple[HomeLocationCandidate | None, bool]:
    """Returns (candidate, is_auto). Falls back to auto if the stored key is stale."""
    return resolve_home_location_from_candidates(list_home_location_candidates(db, user.id), user)


def resolve_home_location_from_candidates(
    candidates: list[HomeLocationCandidate], user: User
) -> tuple[HomeLocationCandidate | None, bool]:
    if user.home_location_key:
        for candidate in candidates:
            if candidate.catalog_identity_key == user.home_location_key:
                return candidate, False
    return _auto_home_location(candidates), True


def set_home_location(db: Session, user: User, catalog_identity_key: str | None) -> None:
    """Pass None to reset back to automatic selection."""
    if catalog_identity_key is not None:
        candidates = list_home_location_candidates(db, user.id)
        valid_keys = {candidate.catalog_identity_key for candidate in candidates}
        if catalog_identity_key not in valid_keys:
            raise UnknownHomeLocationError(catalog_identity_key)
    user.home_location_key = catalog_identity_key
    db.flush()
