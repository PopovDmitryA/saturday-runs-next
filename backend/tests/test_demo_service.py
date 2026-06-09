from __future__ import annotations

from uuid import uuid4

from app.config import Settings
from app.core.admin import find_admin_user
from app.models import AuthIdentity, AuthProvider, User
from app.services.demo_service import get_demo_user


def test_find_admin_user_by_email(db_session) -> None:
    user = User(display_name="OAuth Admin")
    db_session.add(user)
    db_session.flush()
    db_session.add(
        AuthIdentity(
            user_id=user.id,
            provider=AuthProvider.yandex,
            external_id="yandex-1",
            email="admin@example.com",
        )
    )
    db_session.commit()

    found = find_admin_user(db_session, Settings(admin_emails="admin@example.com", admin_telegram_id=999999))
    assert found is not None
    assert found.id == user.id


def test_get_demo_user_falls_back_to_oauth_admin(db_session) -> None:
    user = User(display_name="Demo Owner")
    db_session.add(user)
    db_session.flush()
    db_session.add(
        AuthIdentity(
            user_id=user.id,
            provider=AuthProvider.yandex,
            external_id="yandex-2",
            email="owner@example.com",
        )
    )
    db_session.commit()

    settings = Settings(admin_emails="owner@example.com", admin_telegram_id=336690860)

    from app.config import get_settings

    get_settings.cache_clear()
    db_session.bind.configure = None  # type: ignore[attr-defined]

    import app.services.demo_service as demo_service

    original = demo_service.get_settings
    demo_service.get_settings = lambda: settings  # type: ignore[assignment]
    try:
        found = get_demo_user(db_session)
    finally:
        demo_service.get_settings = original
        get_settings.cache_clear()

    assert found.id == user.id


def test_get_demo_user_prefers_demo_user_id(db_session) -> None:
    admin = User(display_name="Admin")
    demo = User(display_name="Explicit Demo")
    db_session.add_all([admin, demo])
    db_session.flush()
    db_session.add(
        AuthIdentity(
            user_id=admin.id,
            provider=AuthProvider.yandex,
            external_id=str(uuid4().int % 1_000_000),
            email="admin@example.com",
        )
    )
    db_session.commit()

    settings = Settings(admin_emails="admin@example.com", demo_user_id=str(demo.id))

    import app.services.demo_service as demo_service

    original = demo_service.get_settings
    demo_service.get_settings = lambda: settings  # type: ignore[assignment]
    try:
        found = get_demo_user(db_session)
    finally:
        demo_service.get_settings = original

    assert found.id == demo.id
