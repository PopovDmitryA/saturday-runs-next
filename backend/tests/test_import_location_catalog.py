"""Импорт каталога локаций не имеет права менять id узлов.

26.09.2026 правка одного Раменского прогнала импорт, который делал DELETE +
INSERT всего каталога: все 121 узел получили новые id, и ключи «catalog:<id>»
в грантах кабинета организатора, домашних локациях, оценках и гео-пингах
повисли в пустоте — организаторы молча потеряли доступ к кабинету.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import String, UniqueConstraint

from app.models import (
    Base,
    Location,
    LocationCatalog,
    LocationCatalogLink,
    LocationOrganizerAccess,
    LocationRating,
    Platform,
    User,
)
from app.services.location_catalog_service import CATALOG_KEY_REFERENCES, CATALOG_KEY_UNIQUE_WITH
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


def test_unique_catalog_key_columns_declare_their_pair() -> None:
    """Ключ, уникальный в паре с другой колонкой, обязан быть в CATALOG_KEY_UNIQUE_WITH.

    Иначе перенос «location:» → «catalog:» при связке локаций упрётся в
    уникальность у человека с ключами обеих строк, и импорт каталога упадёт.
    """
    tables = {table.name: table for table in Base.metadata.sorted_tables}
    found: dict[tuple[str, str], list[str]] = {}
    for table_name, column in CATALOG_KEY_REFERENCES:
        table = tables[table_name]
        groups = [[c.name for c in table.primary_key.columns]]
        groups += [[c.name for c in item.columns] for item in table.constraints if isinstance(item, UniqueConstraint)]
        groups += [[c.name for c in index.columns] for index in table.indexes if index.unique]
        for group in groups:
            if column in group:
                found[(table_name, column)] = [name for name in group if name != column]
    assert found == {key: [pair] for key, pair in CATALOG_KEY_UNIQUE_WITH.items()}


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


def _location(db, code: str, external_key: str) -> Location:
    location = Location(platform_id=_platform(db, code).id, external_key=external_key, name=external_key)
    db.add(location)
    db.flush()
    return location


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


def test_linking_locations_moves_their_keys_to_the_node(db_session) -> None:
    """Черкизовский пруд: связка 5 вёрст + S95 переводит «location:<id>» на «catalog:<id>».

    Без переноса девять оценок 5 вёрст остались бы под старым ключом и выпали
    из витрин — как оценка Иркутска после связки острова Юность 01.08.2026.
    """
    fv_slug, s95_slug = _slug("cherkizovsky"), _slug("cherkizovo")
    five_verst = _location(db_session, "five_verst", fv_slug)
    s95 = _location(db_session, "s95", s95_slug)
    loner = _location(db_session, "five_verst", _slug("loner"))
    runner, organizer = _user(db_session), _user(db_session)
    runner.home_location_key = f"location:{five_verst.id}"
    rating = LocationRating(
        user_id=runner.id,
        location_id=five_verst.id,
        location_key=f"location:{five_verst.id}",
        event_date=date(2026, 10, 24),
        platform_code="five_verst",
        score_overall=5,
    )
    db_session.add_all(
        [
            rating,
            # Доступ к обеим строкам одной локации — после связки один доступ.
            LocationOrganizerAccess(user_id=organizer.id, location_key=f"location:{five_verst.id}"),
            LocationOrganizerAccess(user_id=organizer.id, location_key=f"location:{s95.id}"),
            LocationOrganizerAccess(user_id=runner.id, location_key=f"location:{loner.id}"),
        ]
    )
    db_session.flush()

    entries = [
        *_existing_entries(db_session),
        _entry("Черкизовский пруд", "", [("five_verst", fv_slug), ("s95", s95_slug)], "s95"),
    ]
    stats = import_to_db(entries, db=db_session)

    assert stats["catalog_created"] == 1
    assert stats["keys_rekeyed"] >= 4
    node_id = db_session.query(LocationCatalogLink.catalog_id).filter(LocationCatalogLink.external_key == fv_slug).scalar()
    assert node_id == (
        db_session.query(LocationCatalogLink.catalog_id).filter(LocationCatalogLink.external_key == s95_slug).scalar()
    )
    key = f"catalog:{node_id}"
    db_session.expire_all()
    assert db_session.get(User, runner.id).home_location_key == key
    assert db_session.get(LocationRating, rating.id).location_key == key
    grants = {
        (access.user_id, access.location_key)
        for access in db_session.query(LocationOrganizerAccess).filter(
            LocationOrganizerAccess.user_id.in_([runner.id, organizer.id])
        )
    }
    assert grants == {(organizer.id, key), (runner.id, f"location:{loner.id}")}

    # Повторный импорт того же каталога ключей не трогает.
    assert import_to_db(entries, db=db_session)["keys_rekeyed"] == 0


def test_dry_run_reports_changes_and_rolls_back(db_session, capsys) -> None:
    """Сухой прогон перед импортом на проде: печатает новый узел, в базе ничего не остаётся."""
    from scripts.import_location_catalog import dry_run

    # Своё соединение у сухого прогона видит только закоммиченное — системы
    # засеяны миграциями, а строки этого теста ему не нужны.
    slug = _slug("dry")
    nodes_before = db_session.query(LocationCatalog).count()

    stats = dry_run([*_existing_entries(db_session), _entry("Сухой прогон", "", [("five_verst", slug)])])

    assert stats["catalog_created"] == 1
    assert stats["catalog_removed"] == 0
    out = capsys.readouterr().out
    assert "узел появится: Сухой прогон" in out
    assert f"связка появится: five_verst/{slug}" in out
    assert db_session.query(LocationCatalog).count() == nodes_before
    assert db_session.query(LocationCatalogLink).filter(LocationCatalogLink.external_key == slug).count() == 0
