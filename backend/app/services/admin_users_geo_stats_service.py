from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import distinct
from sqlalchemy.orm import Session

from app.models import PlatformLink, User
from app.services.admin_home_location_service import (
    AdminHomeLocation,
    resolve_admin_home_locations,
)

# «Откуда наши люди» — регистрации в разрезе городов и площадок.
#
# Смысл среза: видно, где сарафанка уже работает (площадка приводит людей
# пачками) и где о сайте ещё не знают. Город и площадка берутся из домашней
# локации пользователя — той же, что показывает ему кабинет и рейтинг
# дальности (см. admin_home_location_service).

_UNKNOWN_CITY = "—"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class _CityTally:
    city: str
    region: str | None
    users: int = 0
    users_new_period: int = 0
    locations: set[str] = field(default_factory=set)

    def as_dict(self) -> dict[str, object]:
        return {
            "city": self.city,
            "region": self.region,
            "users": self.users,
            "users_new_period": self.users_new_period,
            "locations": len(self.locations),
        }


@dataclass
class _LocationTally:
    identity_key: str
    name: str
    slug: str | None
    city: str | None
    region: str | None
    users: int = 0
    users_new_period: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "identity_key": self.identity_key,
            "name": self.name,
            "slug": self.slug,
            "city": self.city,
            "region": self.region,
            "users": self.users,
            "users_new_period": self.users_new_period,
        }


def _city_key(home: AdminHomeLocation) -> tuple[str, str | None]:
    """Ключ города: одноимённые города разных регионов — это разные города."""
    return (home.city or _UNKNOWN_CITY, home.region)


def get_admin_users_geography(db: Session, *, period_days: int = 30) -> dict[str, object]:
    if period_days < 1 or period_days > 365:
        period_days = 30
    since = _utcnow() - timedelta(days=period_days)

    users: list[tuple[UUID, datetime | None]] = [
        (row[0], row[1]) for row in db.query(User.id, User.created_at).all()
    ]
    homes = resolve_admin_home_locations(db)
    users_with_links: set[UUID] = {
        row[0]
        for row in db.query(distinct(PlatformLink.user_id))
        .filter(PlatformLink.participant_id.isnot(None))
        .all()
    }

    city_tallies: dict[tuple[str, str | None], _CityTally] = {}
    location_tallies: dict[str, _LocationTally] = {}

    users_with_home = 0
    users_new_with_home = 0
    users_new_total = 0

    for user_id, created_at in users:
        is_new = created_at is not None and created_at >= since
        if is_new:
            users_new_total += 1
        home = homes.get(user_id)
        if home is None:
            continue
        users_with_home += 1
        if is_new:
            users_new_with_home += 1

        city_key = _city_key(home)
        city = city_tallies.get(city_key)
        if city is None:
            city = _CityTally(city=city_key[0], region=home.region)
            city_tallies[city_key] = city
        city.users += 1
        city.users_new_period += 1 if is_new else 0
        city.locations.add(home.identity_key)

        location = location_tallies.get(home.identity_key)
        if location is None:
            location = _LocationTally(
                identity_key=home.identity_key,
                name=home.name,
                slug=home.slug,
                city=home.city,
                region=home.region,
            )
            location_tallies[home.identity_key] = location
        location.users += 1
        location.users_new_period += 1 if is_new else 0

    cities = sorted(city_tallies.values(), key=lambda row: (-row.users, row.city.lower()))
    locations = sorted(location_tallies.values(), key=lambda row: (-row.users, row.name.lower()))

    users_total = len(users)
    return {
        "generated_at": _utcnow(),
        "period_days": period_days,
        "users_total": users_total,
        "users_new_period": users_new_total,
        "users_with_home": users_with_home,
        "users_new_with_home": users_new_with_home,
        # Дома нет у тех, у кого в базе нет ни одной пробежки: либо профиль не
        # привязан вовсе, либо привязан, но пробежек в нём ещё нет.
        "users_without_home": users_total - users_with_home,
        "users_without_links": sum(1 for user_id, _ in users if user_id not in users_with_links),
        "cities_total": len(cities),
        "locations_total": len(locations),
        "cities": [row.as_dict() for row in cities],
        "locations": [row.as_dict() for row in locations],
    }
