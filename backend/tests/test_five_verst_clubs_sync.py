from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Club, ClubCatalogLink, ClubMember, Participant, Platform
from app.platform_adapters.five_verst.clubs_parser import (
    ParsedClubDetail,
    ParsedClubMember,
    ParsedClubsListEntry,
)
from app.sync.five_verst_clubs import (
    ClubsRegistrySyncOptions,
    sync_club_details_batch,
    sync_clubs_registry,
)


@pytest.fixture
def five_verst_platform(db_session: Session) -> Platform:
    row = db_session.query(Platform).filter(Platform.code == "five_verst").one_or_none()
    if row is None:
        pytest.skip("five_verst platform not seeded")
    return row


def _entry(slug: str, name: str, *, members: int = 10, row_hash: str = "hash-1") -> ParsedClubsListEntry:
    return ParsedClubsListEntry(
        slug=slug,
        name=name,
        source_url=f"https://5verst.ru/clubs/{slug}/",
        members_count=members,
        finishes_count=100,
        volunteerings_count=50,
        avg_time_seconds=1600,
        best_female_time_seconds=1200,
        best_male_time_seconds=1000,
        external_links=[{"kind": "telegram", "url": f"https://t.me/{slug}"}],
        row_hash=row_hash,
    )


def _detail(slug: str, members: list[tuple[str, str]], *, title: str | None = None) -> ParsedClubDetail:
    return ParsedClubDetail(
        slug=slug,
        title=title or slug,
        description="Тестовый клуб",
        logo_url=None,
        external_links=[],
        members=[
            ParsedClubMember(
                userstats_id=user_id,
                display_name=name,
                profile_url=f"https://5verst.ru/userstats/{user_id}/",
            )
            for user_id, name in members
        ],
        members_skipped=0,
    )


def _get_club(db: Session, platform: Platform, slug: str) -> Club:
    return db.query(Club).filter(Club.platform_id == platform.id, Club.external_key == slug).one()


def _deactivate_other_clubs(db: Session, keep_slugs: list[str]) -> None:
    """Dev DB may contain real clubs; keep detail-batch selection scoped to test rows."""
    db.query(Club).filter(Club.external_key.notin_(keep_slugs)).update(
        {"is_active": False}, synchronize_session=False
    )
    db.commit()


def test_registry_creates_clubs_and_catalog_links(db_session: Session, five_verst_platform: Platform) -> None:
    slug = f"testclub-{uuid4().hex[:8]}"
    entries = [_entry(slug, "Test Club")]

    with patch("app.platform_adapters.five_verst.clubs_parser.fetch_clubs_page", return_value=(entries, "<html/>")):
        result = sync_clubs_registry(db_session, ClubsRegistrySyncOptions(limit=1))

    assert result.clubs_created == 1
    assert result.catalog_links_created == 1
    assert result.marked_for_detail_sync == 1

    club = _get_club(db_session, five_verst_platform, slug)
    assert club.name == "Test Club"
    assert club.members_count == 10
    assert club.needs_detail_sync is True
    assert club.is_active is True
    assert club.list_row_hash == "hash-1"

    link = (
        db_session.query(ClubCatalogLink)
        .filter(ClubCatalogLink.platform_id == five_verst_platform.id, ClubCatalogLink.external_key == slug)
        .one()
    )
    assert link.club_id == club.id
    assert link.link_source == "auto_name"
    assert link.catalog.normalized_name == "test club"


def test_registry_marks_changed_rows_for_detail_sync(db_session: Session, five_verst_platform: Platform) -> None:
    slug = f"testclub-{uuid4().hex[:8]}"

    with patch(
        "app.platform_adapters.five_verst.clubs_parser.fetch_clubs_page",
        return_value=([_entry(slug, "Test Club", row_hash="hash-1")], "<html/>"),
    ):
        sync_clubs_registry(db_session, ClubsRegistrySyncOptions(limit=1))

    club = _get_club(db_session, five_verst_platform, slug)
    club.needs_detail_sync = False
    db_session.commit()

    # Same row hash → unchanged.
    with patch(
        "app.platform_adapters.five_verst.clubs_parser.fetch_clubs_page",
        return_value=([_entry(slug, "Test Club", row_hash="hash-1")], "<html/>"),
    ):
        result = sync_clubs_registry(db_session, ClubsRegistrySyncOptions(limit=1))
    assert result.clubs_unchanged == 1
    db_session.refresh(club)
    assert club.needs_detail_sync is False

    # Changed row hash (e.g. members count) → marked for detail sync.
    with patch(
        "app.platform_adapters.five_verst.clubs_parser.fetch_clubs_page",
        return_value=([_entry(slug, "Test Club", members=11, row_hash="hash-2")], "<html/>"),
    ):
        result = sync_clubs_registry(db_session, ClubsRegistrySyncOptions(limit=1))
    assert result.clubs_updated == 1
    assert result.marked_for_detail_sync == 1
    db_session.refresh(club)
    assert club.needs_detail_sync is True
    assert club.members_count == 11


def test_registry_deactivates_missing_clubs(db_session: Session, five_verst_platform: Platform) -> None:
    stale_slug = f"stale-{uuid4().hex[:8]}"
    fresh_slug = f"fresh-{uuid4().hex[:8]}"
    db_session.add(
        Club(
            platform_id=five_verst_platform.id,
            external_key=stale_slug,
            name="Stale Club",
            is_active=True,
        )
    )
    db_session.commit()

    # Full parse (no limit) → clubs missing from the list get deactivated.
    with patch(
        "app.platform_adapters.five_verst.clubs_parser.fetch_clubs_page",
        return_value=([_entry(fresh_slug, "Fresh Club")], "<html/>"),
    ):
        result = sync_clubs_registry(db_session)

    assert stale_slug in result.deactivated_clubs
    stale = _get_club(db_session, five_verst_platform, stale_slug)
    assert stale.is_active is False

    # Reappearing club gets reactivated.
    with patch(
        "app.platform_adapters.five_verst.clubs_parser.fetch_clubs_page",
        return_value=([_entry(stale_slug, "Stale Club")], "<html/>"),
    ):
        result = sync_clubs_registry(db_session)
    assert result.clubs_reactivated == 1
    db_session.refresh(stale)
    assert stale.is_active is True


def test_details_sync_roster(db_session: Session, five_verst_platform: Platform) -> None:
    slug = f"testclub-{uuid4().hex[:8]}"
    uid_a, uid_b, uid_c = (str(700000000 + i) for i in range(3))
    club = Club(
        platform_id=five_verst_platform.id,
        external_key=slug,
        name="Roster Club",
        is_active=True,
        needs_detail_sync=True,
    )
    db_session.add(club)
    db_session.commit()
    _deactivate_other_clubs(db_session, [slug])

    with patch(
        "app.platform_adapters.five_verst.clubs_parser.fetch_club_detail",
        return_value=(_detail(slug, [(uid_a, "Анна А"), (uid_b, "Борис Б")]), "<html/>"),
    ):
        result = sync_club_details_batch(db_session, limit=5)

    assert result.clubs_synced == 1
    assert result.members_added == 2
    db_session.refresh(club)
    assert club.needs_detail_sync is False
    assert club.detail_synced_at is not None
    assert club.description == "Тестовый клуб"

    members = db_session.query(ClubMember).filter(ClubMember.club_id == club.id).all()
    assert len(members) == 2
    assert all(m.is_active for m in members)
    participant_a = (
        db_session.query(Participant)
        .filter(Participant.platform_id == five_verst_platform.id, Participant.external_user_id == uid_a)
        .one()
    )
    assert participant_a.display_name == "Анна А"
    assert participant_a.club_name == "Roster Club"

    # Second pass: Анна ушла, Виктор пришёл.
    club.needs_detail_sync = True
    db_session.commit()
    with patch(
        "app.platform_adapters.five_verst.clubs_parser.fetch_club_detail",
        return_value=(_detail(slug, [(uid_b, "Борис Б"), (uid_c, "Виктор В")]), "<html/>"),
    ):
        result = sync_club_details_batch(db_session, limit=5)

    assert result.members_added == 1
    assert result.members_deactivated == 1
    members = {m.participant.external_user_id: m for m in db_session.query(ClubMember).filter(ClubMember.club_id == club.id)}
    assert members[uid_a].is_active is False
    assert members[uid_b].is_active is True
    assert members[uid_c].is_active is True


def test_details_batch_prioritizes_dirty_and_stale(db_session: Session, five_verst_platform: Platform) -> None:
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    dirty_slug = f"dirty-{uuid4().hex[:8]}"
    stale_slug = f"stale-{uuid4().hex[:8]}"
    fresh_slug = f"fresh-{uuid4().hex[:8]}"
    db_session.add_all(
        [
            Club(
                platform_id=five_verst_platform.id,
                external_key=dirty_slug,
                name="Dirty",
                needs_detail_sync=True,
                detail_synced_at=now - timedelta(days=1),
            ),
            Club(
                platform_id=five_verst_platform.id,
                external_key=stale_slug,
                name="Stale",
                detail_synced_at=now - timedelta(days=30),
            ),
            Club(
                platform_id=five_verst_platform.id,
                external_key=fresh_slug,
                name="Fresh",
                detail_synced_at=now,
            ),
        ]
    )
    db_session.commit()
    _deactivate_other_clubs(db_session, [dirty_slug, stale_slug, fresh_slug])

    synced: list[str] = []

    def _fake_fetch(slug: str):
        synced.append(slug)
        return _detail(slug, []), "<html/>"

    with patch("app.platform_adapters.five_verst.clubs_parser.fetch_club_detail", side_effect=_fake_fetch):
        sync_club_details_batch(db_session, limit=2)

    assert synced == [dirty_slug, stale_slug]
