"""Импорт каталога локаций не имеет права менять id узлов.

26.09.2026 правка одного Раменского прогнала импорт, который делал DELETE +
INSERT всего каталога: все 121 узел получили новые id, и ключи «catalog:<id>»
в грантах кабинета организатора, домашних локациях, оценках и гео-пингах
повисли в пустоте — организаторы молча потеряли доступ к кабинету.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import String

from app.models import Base, LocationCatalog, LocationCatalogLink, LocationOrganizerAccess, Platform, User
from app.services.location_catalog_service import CATALOG_KEY_REFERENCES
from scripts.import_location_catalog import (
    CatalogEntry,
    ReferencedNodeRemovalError,
    import_to_db,
)

# Колонки с похожим именем, где лежит НЕ ключ каталога. Дописывать сюда —
# только с объяснением.
NOT_CATALOG_KEYS: dict[tuple[str, str], str] = {}


def test_every_catalog_key_column_is_registered() -> None:
    """Колонка с identity key локации обязана быть в CATALOG_KEY_REFERENCES.

    Иначе импорт каталога не увидит её при удалении узла, а восстановление
    после сбоя — при переносе ключей.
    """
    registered = set(CATALOG_KEY_REFERENCES)
    missing = []
    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            if not isinstance(column.type, String):
                continue
            if not (column.name.endswith("location_key") or column.name.endswith("identity_key")):
                continue
            key = (table.name, column.name)
            if key not in registered and key not in NOT_CATALOG_KEYS:
                missing.append(f"{table.name}.{column.name}")
    assert not missing, (
        "Колонки с ключом локации не внесены в CATALOG_KEY_REFERENCES "
        f"(app/services/location_catalog_service.py): {missing}"
    )


def _platform(db, code: str) -> Platform:
    platform = db.query(Platform).filter(Platform.code == code).one_or_none()
    if platform is None:
        platform = Platform(code=code, name=code, base_url=f"https://{code}.test")
        db.add(platform)
        db.flush()
    return platform


def _slug(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:10]}"


def _seed_node(db, name: str, parkrun_slug: str, links: list[tuple[str, str]]) -> LocationCatalog:
    node = LocationCatalog(canonical_name=name, legacy_parkrun_slug=parkrun_slug, active_platform="five_verst")
    db.add(node)
    db.flush()
    for code, external_key in links:
        db.add(
            LocationCatalogLink(
                catalog_id=node.id, platform_id=_platform(db, code).id, external_key=external_key
            )
        )
    db.flush()
    return node


def _entry(name: str, parkrun_slug: str, links: list[tuple[str, str]], active: str = "five_verst") -> CatalogEntry:
    return CatalogEntry(
        parkrun_location=name,
        canonical_name=name,
        active_platform=active,
        legacy_parkrun_slug=parkrun_slug,
        links=[{"platform": code, "external_key": key} for code, key in links],
    )


def _existing_entries(db) -> list[CatalogEntry]:
    """Весь текущий каталог как JSON — чтобы импорт в тесте не счёл его устаревшим."""
    entries = []
    for node in db.query(LocationCatalog).all():
        links = [
            (code, link.external_key)
            for link, code in db.query(LocationCatalogLink, Platform.code)
            .join(Platform, LocationCatalogLink.platform_id == Platform.id)
            .filter(LocationCatalogLink.catalog_id == node.id)
        ]
        entries.append(
            CatalogEntry(
                parkrun_location=node.canonical_name,
                canonical_name=node.canonical_name,
                active_platform=node.active_platform,
                legacy_parkrun_slug=node.legacy_parkrun_slug or "",
                is_closed=node.is_closed,
                notes=node.notes or "",
                links=[{"platform": code, "external_key": key} for code, key in links],
            )
        )
    return entries


def _user(db) -> User:
    user = User(display_name="Организатор")
    db.add(user)
    db.flush()
    return user


def test_reimport_keeps_node_ids_and_organizer_grants(db_session) -> None:
    parkrun_slug = _slug("ramenskoe")
    fv_slug = _slug("fv")
    s95_slug = _slug("s95")
    node = _seed_node(db_session, "Раменское Городской парк", parkrun_slug, [("parkrun", parkrun_slug), ("five_verst", fv_slug)])
    other = _seed_node(db_session, "Пермь Балатово", _slug("perm"), [("five_verst", _slug("perm"))])
    user = _user(db_session)
    db_session.add(LocationOrganizerAccess(user_id=user.id, location_key=f"catalog:{other.id}"))
    db_session.flush()

    # Ровно правка 26.09.2026: узел переименован, основная система сменилась,
    # добавилась связка S95.
    entries = [
        entry
        for entry in _existing_entries(db_session)
        if entry.legacy_parkrun_slug != parkrun_slug
    ]
    entries.append(
        _entry("Раменское", parkrun_slug, [("parkrun", parkrun_slug), ("five_verst", fv_slug), ("s95", s95_slug)], "s95")
    )
    stats = import_to_db(entries, db=db_session)

    assert stats["catalog_created"] == 0
    assert stats["catalog_removed"] == 0
    db_session.expire_all()
    renamed = db_session.get(LocationCatalog, node.id)
    assert renamed is not None and renamed.canonical_name == "Раменское"
    assert renamed.active_platform == "s95"
    assert db_session.get(LocationCatalog, other.id) is not None
    s95_link = (
        db_session.query(LocationCatalogLink)
        .filter(LocationCatalogLink.external_key == s95_slug)
        .one()
    )
    assert s95_link.catalog_id == node.id


def test_new_entry_gets_new_node_and_link_can_move_between_nodes(db_session) -> None:
    moving = _slug("moving")
    a = _seed_node(db_session, "Узел А", _slug("a"), [("five_verst", moving)])
    entries = [e for e in _existing_entries(db_session) if e.canonical_name != "Узел А"]
    a_entry = _entry("Узел А", a.legacy_parkrun_slug, [("parkrun", a.legacy_parkrun_slug)])
    b_slug = _slug("b")
    b_entry = _entry("Узел Б", b_slug, [("parkrun", b_slug), ("five_verst", moving)])
    stats = import_to_db([*entries, a_entry, b_entry], db=db_session)

    assert stats["catalog_created"] == 1
    db_session.expire_all()
    # «Узел А» отдал связку, но сохранил id: его держат по parkrun-слагу.
    assert db_session.get(LocationCatalog, a.id).canonical_name == "Узел А"
    link = db_session.query(LocationCatalogLink).filter(LocationCatalogLink.external_key == moving).one()
    new_node = db_session.get(LocationCatalog, link.catalog_id)
    assert new_node.canonical_name == "Узел Б" and new_node.id != a.id


def test_dropping_referenced_node_is_refused(db_session) -> None:
    doomed = _seed_node(db_session, "Удаляемая", _slug("gone"), [("five_verst", _slug("gone"))])
    user = _user(db_session)
    db_session.add(LocationOrganizerAccess(user_id=user.id, location_key=f"catalog:{doomed.id}"))
    db_session.flush()

    entries = [e for e in _existing_entries(db_session) if e.canonical_name != "Удаляемая"]
    with pytest.raises(ReferencedNodeRemovalError):
        # force=False: сжатие каталога само по себе тоже запрещено, поэтому
        # добавляем фиктивную связку, чтобы сработал именно сторож ссылок.
        extra = _slug("extra")
        import_to_db([*entries, _entry("Новая", extra, [("parkrun", extra), ("five_verst", extra)])], db=db_session)
