from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import AuthIdentity, AuthProvider, User
from app.services.auth_identity_service import (
    create_oauth_user,
    find_user_by_telegram_id,
    list_user_identities,
    upsert_telegram_identity,
)
from app.auth.providers.base import OAuthProfile


def test_upsert_telegram_identity_creates_user_identity(db_session: Session) -> None:
    user = User(display_name="Test", consent_accepted=True)
    db_session.add(user)
    db_session.flush()

    upsert_telegram_identity(
        db_session,
        user,
        telegram_id=12345,
        telegram_username="runner",
        telegram_first_name="Ivan",
        telegram_last_name="Petrov",
        telegram_chat_id=12345,
    )
    db_session.commit()

    identity = (
        db_session.query(AuthIdentity)
        .filter(AuthIdentity.provider == AuthProvider.telegram, AuthIdentity.external_id == "12345")
        .one()
    )
    assert identity.user_id == user.id
    assert user.telegram_id == 12345


def test_find_user_by_telegram_id_uses_identity(db_session: Session) -> None:
    user = User(display_name="Legacy", consent_accepted=True, telegram_id=777)
    db_session.add(user)
    db_session.flush()
    db_session.add(
        AuthIdentity(
            user_id=user.id,
            provider=AuthProvider.telegram,
            external_id="777",
            profile_json={},
        )
    )
    db_session.commit()

    found = find_user_by_telegram_id(db_session, 777)
    assert found is not None
    assert found.id == user.id


def test_create_oauth_user_without_telegram(db_session: Session) -> None:
    profile = OAuthProfile(
        external_id="42",
        display_name="VK User",
        email="user@example.com",
        profile_json={"login": "vkuser"},
    )
    user = create_oauth_user(db_session, profile, AuthProvider.vk, consent=True)
    db_session.commit()

    assert user.telegram_id is None
    identities = list_user_identities(db_session, user.id)
    assert len(identities) == 1
    assert identities[0].provider == AuthProvider.vk
