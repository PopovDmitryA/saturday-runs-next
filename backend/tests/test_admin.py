from __future__ import annotations

from uuid import uuid4

from app.config import Settings
from app.core.admin import is_admin_telegram_id, is_admin_user, user_response
from app.models import AuthIdentity, AuthProvider, User


def test_is_admin_user_matches_configured_telegram_id() -> None:
    user = User(telegram_id=12345)
    settings = Settings(admin_telegram_id=12345)
    assert is_admin_user(user, settings) is True


def test_is_admin_telegram_id_matches_configured_id() -> None:
    settings = Settings(admin_telegram_id=12345)
    assert is_admin_telegram_id(12345, settings) is True
    assert is_admin_telegram_id(999, settings) is False


def test_is_admin_user_false_when_not_configured() -> None:
    user = User(telegram_id=12345)
    settings = Settings(admin_telegram_id=0, telegram_admin_chat_id=0)
    assert is_admin_user(user, settings) is False


def test_is_admin_user_falls_back_to_telegram_admin_chat_id() -> None:
    user = User(telegram_id=336690860)
    settings = Settings(admin_telegram_id=0, telegram_admin_chat_id=336690860)
    assert is_admin_user(user, settings) is True


def test_is_admin_user_matches_configured_email() -> None:
    user = User()
    user.auth_identities = [
        AuthIdentity(
            provider=AuthProvider.yandex,
            external_id="100",
            email="Popov.Dmitii@yandex.ru",
            profile_json={"login": "popov.dmitii"},
        )
    ]
    settings = Settings(admin_emails="popov.dmitii@yandex.ru")
    assert is_admin_user(user, settings) is True


def test_is_admin_user_email_from_profile_json() -> None:
    user = User()
    user.auth_identities = [
        AuthIdentity(
            provider=AuthProvider.yandex,
            external_id="100",
            email=None,
            profile_json={"default_email": "admin@yandex.ru"},
        )
    ]
    settings = Settings(admin_emails="admin@yandex.ru, other@example.com")
    assert is_admin_user(user, settings) is True


def test_user_response_sets_is_admin_flag() -> None:
    user = User(
        id=uuid4(),
        telegram_id=999,
        telegram_username="admin_user",
        display_name_customized=False,
        consent_accepted=False,
    )
    settings = Settings(admin_telegram_id=999)
    response = user_response(user, settings)
    assert response.is_admin is True
    assert response.telegram_id == 999

    other_settings = Settings(admin_telegram_id=1)
    other_response = user_response(user, other_settings)
    assert other_response.is_admin is False
