from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Platform, User
from app.platform_adapters.canonical import ProfilePreview
from app.services.profile_linking_service import ProfileLinkingError, confirm_profile_link


def test_confirm_rejects_external_profile_linked_to_other_user(db_session: Session) -> None:
    platform = db_session.query(Platform).filter(Platform.code == "five_verst").one_or_none()
    if platform is None:
        pytest.skip("five_verst platform not seeded")

    user_a = User(telegram_id=int(uuid4().int % 10_000_000_000), consent_accepted=True)
    user_b = User(telegram_id=int(uuid4().int % 10_000_000_000), consent_accepted=True)
    db_session.add_all([user_a, user_b])
    db_session.flush()

    external_user_id = str(uuid4().int % 1_000_000_000)
    profile_url = f"https://5verst.ru/userstats/{external_user_id}/"
    preview = ProfilePreview(
        external_user_id=external_user_id,
        display_name="Shared Profile",
        profile_url=profile_url,
        platform_code="five_verst",
    )

    from unittest.mock import patch

    with patch(
        "app.platform_adapters.five_verst.adapter.FiveVerstAdapter.fetch_profile_preview",
        return_value=preview,
    ):
        confirm_profile_link(db_session, user_a, "five_verst", profile_url)

        with pytest.raises(ProfileLinkingError) as exc_info:
            confirm_profile_link(db_session, user_b, "five_verst", profile_url)

    assert exc_info.value.status_code == 409
