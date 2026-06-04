from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Platform, PlatformLink, User
from app.services.user_auto_sync_service import (
    enabled_auto_sync_platform_codes,
    get_auto_sync_preferences,
    login_auto_sync_due,
    maybe_enqueue_login_auto_sync,
    normalize_auto_sync_preferences,
    update_auto_sync_preferences,
)


def test_normalize_auto_sync_preferences() -> None:
    prefs = normalize_auto_sync_preferences({"parkrun": True, "s95": "yes", "five_verst": False})
    assert prefs == {"five_verst": False, "s95": False, "parkrun": True}


def test_auto_sync_disabled_by_default(db_session: Session) -> None:
    user = User(telegram_id=int(uuid4().int % 10_000_000_000))
    db_session.add(user)
    db_session.commit()

    assert get_auto_sync_preferences(user) == {
        "five_verst": False,
        "s95": False,
        "parkrun": False,
    }
    assert enabled_auto_sync_platform_codes(user) == []
    assert login_auto_sync_due(user, interval_seconds=86400) is False


def test_login_auto_sync_once_per_day(db_session: Session) -> None:
    user = User(telegram_id=int(uuid4().int % 10_000_000_000))
    db_session.add(user)
    db_session.commit()

    update_auto_sync_preferences(user, {"five_verst": True})
    db_session.commit()

    platform = db_session.query(Platform).filter(Platform.code == "five_verst").one()
    link = PlatformLink(
        user_id=user.id,
        platform_id=platform.id,
        external_user_id="123",
        external_url="https://5verst.ru/userstats/123/",
    )
    db_session.add(link)
    db_session.commit()

    with patch("app.services.sync_enqueue_service.enqueue_s95_user_sync"):
        with patch("app.services.sync_enqueue_service.enqueue_parkrun_user_sync"):
            with patch("app.services.sync_enqueue_service.enqueue_user_sync") as enqueue_fv:
                first = maybe_enqueue_login_auto_sync(db_session, user.id, interval_seconds=86400)
                assert first is True
                assert enqueue_fv.called

                user = db_session.query(User).filter(User.id == user.id).one()
                second = maybe_enqueue_login_auto_sync(db_session, user.id, interval_seconds=86400)
                assert second is False
                assert not login_auto_sync_due(user, interval_seconds=86400)

                user.last_login_auto_sync_at = datetime.now(timezone.utc) - timedelta(days=2)
                db_session.commit()
                assert login_auto_sync_due(user, interval_seconds=86400) is True


def test_login_auto_sync_skips_disabled_platforms(db_session: Session) -> None:
    user = User(telegram_id=int(uuid4().int % 10_000_000_000))
    db_session.add(user)
    db_session.commit()

    update_auto_sync_preferences(user, {"s95": True})
    db_session.commit()

    platform = db_session.query(Platform).filter(Platform.code == "five_verst").one()
    db_session.add(
        PlatformLink(
            user_id=user.id,
            platform_id=platform.id,
            external_user_id="999",
            external_url="https://5verst.ru/userstats/999/",
        )
    )
    db_session.commit()

    with patch("app.services.sync_enqueue_service.enqueue_user_sync") as enqueue_fv:
        result = maybe_enqueue_login_auto_sync(db_session, user.id, interval_seconds=86400)
    assert result is False
    assert not enqueue_fv.called
