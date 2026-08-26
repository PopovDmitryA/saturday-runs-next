from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.config import Settings
from app.core.email_templates import login_code_email
from app.models import AuthIdentity, AuthProvider, User
from app.services import newsletter_service


@pytest.fixture
def settings() -> Settings:
    return Settings(app_secret_key="test-secret", app_base_url="https://run5k.run")


def _make_email_user(db: Session, email: str, *, subscribed: bool = False) -> User:
    user = User(display_name="Бегун", consent_accepted=True, news_subscribed=subscribed)
    db.add(user)
    db.flush()
    db.add(
        AuthIdentity(
            user_id=user.id,
            provider=AuthProvider.email,
            external_id=email,
            display_name="Бегун",
            email=email,
            profile_json={},
        )
    )
    db.commit()
    return user


def test_token_round_trip(settings: Settings) -> None:
    token = newsletter_service.make_token(
        "runner@example.com", newsletter_service.ACTION_UNSUBSCRIBE, settings.app_secret_key
    )
    action, email = newsletter_service.parse_token(token, settings.app_secret_key)
    assert action == newsletter_service.ACTION_UNSUBSCRIBE
    assert email == "runner@example.com"


def test_token_signed_with_another_key_is_rejected(settings: Settings) -> None:
    """Иначе ссылку на отписку можно было бы подделать для любого адреса."""
    token = newsletter_service.make_token(
        "runner@example.com", newsletter_service.ACTION_SUBSCRIBE, "other-secret"
    )
    with pytest.raises(newsletter_service.NewsletterTokenError):
        newsletter_service.parse_token(token, settings.app_secret_key)


@pytest.mark.parametrize("token", ["", "garbage", "no-dot", "!!!.???"])
def test_malformed_tokens_are_rejected(token: str, settings: Settings) -> None:
    with pytest.raises(newsletter_service.NewsletterTokenError):
        newsletter_service.parse_token(token, settings.app_secret_key)


def test_subscribe_and_unsubscribe_links_work(db_session: Session, settings: Settings) -> None:
    email = f"runner-{uuid4().hex[:8]}@example.com"
    user = _make_email_user(db_session, email)

    subscribed, _ = newsletter_service.apply_token(
        db_session,
        newsletter_service.make_token(
            email, newsletter_service.ACTION_SUBSCRIBE, settings.app_secret_key
        ),
        settings,
    )
    assert subscribed
    assert user.news_subscribed

    unsubscribed, _ = newsletter_service.apply_token(
        db_session,
        newsletter_service.make_token(
            email, newsletter_service.ACTION_UNSUBSCRIBE, settings.app_secret_key
        ),
        settings,
    )
    assert not unsubscribed
    assert not user.news_subscribed


def test_unsubscribe_link_survives_a_missing_profile(db_session: Session, settings: Settings) -> None:
    """Профиль мог быть удалён — человек всё равно должен увидеть «готово»."""
    token = newsletter_service.make_token(
        "nobody@example.com", newsletter_service.ACTION_UNSUBSCRIBE, settings.app_secret_key
    )
    subscribed, _ = newsletter_service.apply_token(db_session, token, settings)
    assert not subscribed


def test_is_subscribed_reads_the_mailbox(db_session: Session) -> None:
    email = f"runner-{uuid4().hex[:8]}@example.com"
    _make_email_user(db_session, email, subscribed=True)
    # Алиас — тот же ящик, значит и подписка та же.
    local, _, domain = email.partition("@")
    assert newsletter_service.is_subscribed(db_session, f"{local}+news@{domain}")


def test_login_letter_has_both_parts_and_the_code() -> None:
    text, html = login_code_email("123456", minutes=10)
    assert "123456" in text
    assert "123456" in html
    # Ни внешних картинок, ни шрифтов, ни скриптов: почтовые клиенты их режут,
    # а Gmail вдобавок выбрасывает <style> из head.
    for forbidden in ["<script", "<style", "<img", "@media", "fonts.googleapis"]:
        assert forbidden not in html


def test_subscribe_offer_appears_only_when_asked() -> None:
    _text, html = login_code_email("123456", minutes=10)
    assert "Подписаться" not in html

    text, html = login_code_email("123456", minutes=10, subscribe_url="https://run5k.run/news/subscribe?token=x")
    assert "Подписаться на новости" in html
    assert "https://run5k.run/news/subscribe?token=x" in text
