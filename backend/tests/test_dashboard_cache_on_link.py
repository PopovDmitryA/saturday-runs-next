from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Participant, Platform, PlatformLink, User
from app.platform_adapters.canonical import ProfilePreview
from app.services.profile_linking_service import confirm_profile_link
from app.services.profile_preview_persist import complete_link_without_sync


@pytest.fixture
def linking_user(db_session: Session) -> User:
    user = User(consent_accepted=True, display_name="Linker")
    db_session.add(user)
    db_session.commit()
    return user


def _create_link(db_session: Session, user: User) -> PlatformLink:
    platform = db_session.query(Platform).filter(Platform.code == "five_verst").one()
    participant = Participant(
        platform_id=platform.id,
        external_user_id=f"runner-{uuid4().hex[:8]}",
        display_name="Runner",
    )
    db_session.add(participant)
    db_session.flush()
    link = PlatformLink(
        user_id=user.id,
        platform_id=platform.id,
        participant_id=participant.id,
        external_user_id=participant.external_user_id,
        external_url=f"https://example.test/{participant.external_user_id}",
    )
    db_session.add(link)
    db_session.commit()
    return link


def test_complete_link_without_sync_recomputes_dashboard_cache(
    db_session: Session,
    linking_user: User,
) -> None:
    link = _create_link(db_session, linking_user)

    with patch("app.services.dashboard_service.recompute_dashboard_cache") as recompute_mock:
        complete_link_without_sync(db_session, link)

    recompute_mock.assert_called_once_with(db_session, linking_user.id)


def test_confirm_profile_link_recomputes_dashboard_when_sync_skipped(
    db_session: Session,
    linking_user: User,
) -> None:
    platform = db_session.query(Platform).filter(Platform.code == "five_verst").one()
    participant = Participant(
        platform_id=platform.id,
        external_user_id="999888777",
        display_name="Cached Runner",
    )
    db_session.add(participant)
    db_session.commit()

    profile_url = "https://www.5verst.ru/profile/999888777/"
    preview = ProfilePreview(
        external_user_id="999888777",
        display_name="Cached Runner",
        profile_url=profile_url,
        total_runs=1,
        platform_code="five_verst",
    )

    with patch(
        "app.services.profile_linking_service.get_cached_profile_preview",
        return_value=preview,
    ):
        with patch(
            "app.services.profile_linking_service._upsert_participant",
            return_value=participant,
        ):
            with patch(
                "app.services.profile_linking_service.linking_sync_should_run",
                return_value=False,
            ):
                with patch(
                    "app.services.dashboard_service.recompute_dashboard_cache",
                ) as recompute_mock:
                    confirm_profile_link(db_session, linking_user, "five_verst", profile_url)

    recompute_mock.assert_called_once_with(db_session, linking_user.id)
