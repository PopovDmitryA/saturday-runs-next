from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from app.models import Platform, User
from app.platform_adapters.canonical import ProfilePreview
from app.services.profile_linking_service import confirm_profile_link


@pytest.fixture
def linking_user(db_session: Session) -> User:
    user = User(consent_accepted=True, display_name="Linker")
    db_session.add(user)
    db_session.commit()
    return user


def test_confirm_s95_skips_enqueue_when_preview_data_in_db(
    db_session: Session,
    linking_user: User,
) -> None:
    platform = db_session.query(Platform).filter(Platform.code == "s95").one_or_none()
    if platform is None:
        pytest.skip("s95 platform not seeded")

    profile_url = "https://s95.ru/athletes/999001/"
    preview = ProfilePreview(
        external_user_id="999001",
        display_name="Cached Runner",
        profile_url=profile_url,
        total_runs=10,
        platform_code="s95",
    )

    with patch(
        "app.services.profile_linking_service.get_cached_profile_preview",
        return_value=preview,
    ):
        with patch(
            "app.services.profile_linking_service.linking_sync_should_run",
            return_value=False,
        ):
            with patch("app.services.profile_linking_service.create_sync_job") as job_mock:
                with patch("app.services.profile_linking_service.enqueue_s95_user_sync") as enqueue_mock:
                    with patch(
                        "app.services.profile_linking_service.complete_link_without_sync",
                    ) as complete_mock:
                        link = confirm_profile_link(db_session, linking_user, "s95", profile_url)

    assert link is not None
    job_mock.assert_not_called()
    enqueue_mock.assert_not_called()
    complete_mock.assert_called_once()
