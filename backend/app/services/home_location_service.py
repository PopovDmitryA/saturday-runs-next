from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
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
    # ISO-дата самой ранней пробежки здесь; None — если известных дат нет.
    # Разводит локации, у которых поровну и пробежек, и волонтёрств.
    first_run_date: str | None = None


class UnknownHomeLocationError(Exception):
    pass


# Заглушка для локаций без известной даты пробежки: в сравнении «кто раньше»
# они должны проигрывать любой реальной дате, а не выигрывать пустотой.
_NO_RUN_DATE = "9999-12-31"


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
            first_run_date=cast("str | None", item.get("first_run_date")),
        )
        for item in locations
    ]


def _auto_home_location(
    candidates: list[HomeLocationCandidate],
) -> HomeLocationCandidate | None:
    """Домашняя локация по умолчанию — три ступени отбора (решение Дмитрия 03.08.2026).

    1. Больше всего ПРОБЕЖЕК — основной признак.
    2. Ничья наверху — из этих же лидеров берём того, где больше ВОЛОНТЁРСТВ.
       Важно: волонтёрства сравниваются только внутри лидеров по пробежкам,
       перебить пробежки они не могут.
    3. Всё ещё ничья — локация, где сделана самая ранняя пробежка: домом
       логичнее считать ту площадку, с которой человек начал.

    Локации без известной даты пробежки уходят в конец третьей ступени — иначе
    пустая дата выигрывала бы у любой реальной. Список приходит отсортированным
    по имени, так что полная ничья разрешается стабильно и одинаково от запуска
    к запуску.
    """
    if not candidates:
        return None

    def rank(candidate: HomeLocationCandidate) -> tuple[int, int, str]:
        # Берём min(), поэтому «больше» инвертируем минусом, а дату оставляем
        # как есть: ISO-строки сравнимы лексикографически, и меньшая = более
        # ранняя. Неизвестная дата подменяется заведомо поздней.
        return (
            -candidate.run_count,
            -candidate.volunteer_count,
            candidate.first_run_date or _NO_RUN_DATE,
        )

    return min(candidates, key=rank)


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
    # Отметку времени двигаем только при РЕАЛЬНОЙ смене: повторное сохранение
    # той же площадки не должно снова обещать пересчёт таблицы рейтинга.
    if user.home_location_key != catalog_identity_key:
        user.home_location_changed_at = datetime.now(timezone.utc)
    user.home_location_key = catalog_identity_key
    db.flush()
