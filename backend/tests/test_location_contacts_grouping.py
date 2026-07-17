from __future__ import annotations

from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import (
    Location,
    LocationCatalog,
    LocationCatalogLink,
    LocationContact,
    Platform,
)
from app.services.location_contacts_service import (
    list_location_contacts,
    resolve_group_announce_targets,
)


def _platform(db: Session, code: str, name: str) -> Platform:
    platform = db.query(Platform).filter(Platform.code == code).one_or_none()
    if platform is None:
        platform = Platform(code=code, name=name)
        db.add(platform)
        db.flush()
    return platform


def _location(db: Session, platform: Platform, key: str, name: str) -> Location:
    location = Location(
        platform_id=platform.id,
        external_key=key,
        name=name,
        city="Магнитогорск",
        country="Россия",
    )
    db.add(location)
    db.flush()
    return location


def _link_to_catalog(db: Session, catalog: LocationCatalog, location: Location) -> None:
    db.add(
        LocationCatalogLink(
            catalog_id=catalog.id,
            platform_id=location.platform_id,
            external_key=location.external_key,
            location_id=location.id,
        )
    )
    db.flush()


def _make_grouped_point(db: Session) -> tuple[Location, Location, str]:
    """5 вёрст + RunPark одной физической точки, связанные каталогом.

    Имя уникально (тесты ходят в общую dev/CI-БД), чтобы поиск не цеплял
    настоящие локации.
    """
    tag = uuid4().hex[:8]
    name = f"Притяжение-{tag}"
    five = _platform(db, "five_verst", "5 вёрст")
    runpark = _platform(db, "runpark", "RunPark")
    catalog = LocationCatalog(canonical_name=name, active_platform="five_verst")
    db.add(catalog)
    db.flush()
    five_loc = _location(db, five, f"prityazhenie-{tag}", name)
    rp_loc = _location(db, runpark, f"5kmprityazheniya-{tag}", f"5км{name}")
    _link_to_catalog(db, catalog, five_loc)
    _link_to_catalog(db, catalog, rp_loc)
    return five_loc, rp_loc, name


def test_linked_locations_collapse_into_one_row(db_session: Session) -> None:
    five_loc, rp_loc, name = _make_grouped_point(db_session)
    db_session.add(LocationContact(location_id=five_loc.id, telegram_url="https://t.me/prityazhenie"))
    db_session.commit()

    items = list_location_contacts(db_session, query=name)
    groups = [i for i in items if i["display_name"] == name]
    assert len(groups) == 1, "связанные локации должны схлопнуться в одну строку"
    group = groups[0]
    platform_codes = {p["platform_code"] for p in group["platforms"]}
    assert platform_codes == {"five_verst", "runpark"}
    # Чат с 5 вёрст виден на общей строке; anchor — 5 вёрст (active_platform).
    assert group["anchor_location_id"] == five_loc.id
    assert [c["telegram_url"] for c in group["contacts"]] == ["https://t.me/prityazhenie"]


def test_runpark_record_sees_five_verst_chat(db_session: Session) -> None:
    five_loc, rp_loc, _name = _make_grouped_point(db_session)
    db_session.add(LocationContact(location_id=five_loc.id, telegram_url="https://t.me/prityazhenie"))
    db_session.commit()

    # Рекорд случился на RunPark-версии — чат должен найтись через группу.
    targets = resolve_group_announce_targets(db_session, [rp_loc.id])
    urls = [c["telegram_url"] for c in targets[rp_loc.id]["telegram_contacts"]]
    assert urls == ["https://t.me/prityazhenie"]


def test_unlinked_location_stays_standalone(db_session: Session) -> None:
    five = _platform(db_session, "five_verst", "5 вёрст")
    name = f"Одиночная точка-{uuid4().hex[:8]}"
    loc = _location(db_session, five, f"solo-{uuid4().hex[:8]}", name)
    db_session.add(LocationContact(location_id=loc.id, telegram_url="https://t.me/solo"))
    db_session.commit()

    items = list_location_contacts(db_session, query=name)
    solo = [i for i in items if i["display_name"] == name]
    assert len(solo) == 1
    assert solo[0]["group_key"] == f"location:{loc.id}"
    assert solo[0]["anchor_location_id"] == loc.id

    targets = resolve_group_announce_targets(db_session, [loc.id])
    assert [c["telegram_url"] for c in targets[loc.id]["telegram_contacts"]] == ["https://t.me/solo"]
