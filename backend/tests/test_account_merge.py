from __future__ import annotations

from uuid import uuid4

from sqlalchemy.orm import Session

from app.auth.providers.base import OAuthProfile
from app.models import AuthIdentity, AuthProvider, DashboardCache, User
from app.services.account_merge_service import delete_user_with_dependencies, merge_users
from app.services.auth_identity_service import list_user_identities, upsert_oauth_identity


def _create_oauth_user(
    db: Session,
    *,
    provider: AuthProvider,
    external_id: str,
    display_name: str,
) -> User:
    user = User(display_name=display_name, consent_accepted=True)
    db.add(user)
    db.flush()
    db.add(
        AuthIdentity(
            user_id=user.id,
            provider=provider,
            external_id=external_id,
            display_name=display_name,
            profile_json={},
        )
    )
    db.commit()
    return user


def test_merge_users_moves_vk_identity_to_yandex_account(db_session: Session) -> None:
    yandex_user = _create_oauth_user(
        db_session,
        provider=AuthProvider.yandex,
        external_id=f"y-{uuid4().hex[:8]}",
        display_name="Yandex User",
    )
    vk_user = _create_oauth_user(
        db_session,
        provider=AuthProvider.vk,
        external_id=f"v-{uuid4().hex[:8]}",
        display_name="VK User",
    )

    survivor = merge_users(db_session, yandex_user.id, vk_user.id)

    assert survivor.id == yandex_user.id
    identities = list_user_identities(db_session, survivor.id)
    providers = {item.provider for item in identities}
    assert AuthProvider.yandex in providers
    assert AuthProvider.vk in providers
    assert db_session.query(User).filter(User.id == vk_user.id).one_or_none() is None


def test_delete_user_with_dependencies_removes_dashboard_cache(db_session: Session) -> None:
    survivor = User(display_name="Survivor", consent_accepted=True)
    orphan = User(display_name="Orphan", consent_accepted=True)
    db_session.add_all([survivor, orphan])
    db_session.flush()
    external_id = f"v-{uuid4().hex[:8]}"
    db_session.add(
        AuthIdentity(
            user_id=orphan.id,
            provider=AuthProvider.vk,
            external_id=external_id,
            display_name="VK",
            profile_json={},
        )
    )
    db_session.add(DashboardCache(user_id=orphan.id, stats={}))
    db_session.commit()

    profile = OAuthProfile(
        external_id=external_id,
        display_name="VK Updated",
        email=None,
        profile_json={},
    )
    upsert_oauth_identity(db_session, survivor, AuthProvider.vk, profile)
    delete_user_with_dependencies(db_session, orphan, reassign_sync_jobs_to=survivor.id)
    db_session.commit()

    assert db_session.query(DashboardCache).filter(DashboardCache.user_id == orphan.id).one_or_none() is None
    identity = db_session.query(AuthIdentity).filter(AuthIdentity.external_id == external_id).one()
    assert identity.user_id == survivor.id
    assert db_session.query(User).filter(User.id == orphan.id).one_or_none() is None


def test_upsert_oauth_identity_can_reassign_orphan_and_delete_old_user(db_session: Session) -> None:
    survivor = User(display_name="Survivor", consent_accepted=True)
    orphan = User(display_name="Orphan", consent_accepted=True)
    db_session.add_all([survivor, orphan])
    db_session.flush()
    external_id = f"v-{uuid4().hex[:8]}"
    db_session.add(
        AuthIdentity(
            user_id=orphan.id,
            provider=AuthProvider.vk,
            external_id=external_id,
            display_name="VK",
            profile_json={},
        )
    )
    db_session.commit()

    profile = OAuthProfile(
        external_id=external_id,
        display_name="VK Updated",
        email=None,
        profile_json={},
    )
    upsert_oauth_identity(db_session, survivor, AuthProvider.vk, profile)
    delete_user_with_dependencies(db_session, orphan, reassign_sync_jobs_to=survivor.id)
    db_session.commit()

    identity = db_session.query(AuthIdentity).filter(AuthIdentity.external_id == external_id).one()
    assert identity.user_id == survivor.id
    assert db_session.query(User).filter(User.id == orphan.id).one_or_none() is None
