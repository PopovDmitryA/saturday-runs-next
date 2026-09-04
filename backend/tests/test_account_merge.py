from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.auth.providers.base import OAuthProfile
from app.models import (
    AuthIdentity,
    AuthLoginRequest,
    AuthOneTimeToken,
    AuthProvider,
    DashboardCache,
    Participant,
    Platform,
    PlatformLink,
    User,
    UserGoal,
)
from app.services.account_merge_service import (
    AccountMergeError,
    delete_user_with_dependencies,
    merge_users,
)
from app.services.auth_identity_service import (
    list_user_identities,
    list_user_platform_links,
    merge_preview_payload,
    upsert_oauth_identity,
)
from app.services.auth_service import purge_old_one_time_tokens


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
    delete_user_with_dependencies(db_session, orphan, reassign_to=survivor.id)
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
    delete_user_with_dependencies(db_session, orphan, reassign_to=survivor.id)
    db_session.commit()

    identity = db_session.query(AuthIdentity).filter(AuthIdentity.external_id == external_id).one()
    assert identity.user_id == survivor.id
    assert db_session.query(User).filter(User.id == orphan.id).one_or_none() is None


def _add_bot_login_token(db: Session, user: User) -> AuthOneTimeToken:
    """Строка, которую оставляет за собой вход через Telegram-бота.

    consume_magic_link её не удаляет, только помечает used_at, поэтому у
    любого, кто хоть раз входил ссылкой из бота, она лежит в базе.
    """
    login_request = AuthLoginRequest(
        request_token=f"req-{uuid4().hex}",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    db.add(login_request)
    db.flush()
    token = AuthOneTimeToken(
        token_hash=uuid4().hex * 2,
        user_id=user.id,
        login_request_id=login_request.id,
        used_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    db.add(token)
    db.commit()
    return token


def test_merge_users_survives_bot_login_token_on_merged_account(db_session: Session) -> None:
    """Регрессия: одноразовый токен входа держал FK и валил объединение.

    ForeignKeyViolation на auth_one_time_tokens_user_id_fkey означал, что
    поглотить нельзя ни один аккаунт, который хоть раз входил через бота.
    """
    survivor = _create_oauth_user(
        db_session,
        provider=AuthProvider.vk,
        external_id=f"v-{uuid4().hex[:8]}",
        display_name="VK User",
    )
    merged = _create_oauth_user(
        db_session,
        provider=AuthProvider.telegram,
        # Внешний ключ Telegram — число: merge_users переносит его в users.telegram_id.
        external_id=str(uuid4().int % 10**9),
        display_name="Telegram User",
    )
    token = _add_bot_login_token(db_session, merged)

    result = merge_users(db_session, survivor.id, merged.id)

    assert result.id == survivor.id
    assert db_session.query(User).filter(User.id == merged.id).one_or_none() is None
    assert db_session.query(AuthOneTimeToken).filter(AuthOneTimeToken.id == token.id).one_or_none() is None
    providers = {item.provider for item in list_user_identities(db_session, survivor.id)}
    assert providers == {AuthProvider.vk, AuthProvider.telegram}


def test_purge_old_one_time_tokens_keeps_fresh_rows(db_session: Session) -> None:
    user = _create_oauth_user(
        db_session,
        provider=AuthProvider.telegram,
        external_id=str(uuid4().int % 10**9),
        display_name="Telegram User",
    )
    fresh = _add_bot_login_token(db_session, user)
    stale = _add_bot_login_token(db_session, user)
    stale.expires_at = datetime.now(timezone.utc) - timedelta(days=40)
    db_session.commit()

    purge_old_one_time_tokens(db_session, retention_days=30)

    assert db_session.query(AuthOneTimeToken).filter(AuthOneTimeToken.id == stale.id).one_or_none() is None
    assert db_session.query(AuthOneTimeToken).filter(AuthOneTimeToken.id == fresh.id).one_or_none() is not None


def _link_platform(db: Session, user: User, platform_code: str) -> PlatformLink:
    """Привязка учётки беговой системы к профилю."""
    platform = db.query(Platform).filter(Platform.code == platform_code).one()
    external_user_id = str(uuid4().int % 1_000_000_000)
    participant = Participant(
        platform_id=platform.id,
        external_user_id=external_user_id,
        display_name=f"Бегун {external_user_id}",
        profile_url=f"https://example.test/{external_user_id}/",
    )
    db.add(participant)
    db.flush()
    link = PlatformLink(
        user_id=user.id,
        platform_id=platform.id,
        participant_id=participant.id,
        external_user_id=external_user_id,
        external_url=participant.profile_url,
    )
    db.add(link)
    db.commit()
    return link


def _linked_codes(db: Session, user_id) -> set[str]:
    return {item["platform_code"] for item in list_user_platform_links(db, user_id)}


def _merge_pair(db: Session) -> tuple[User, User]:
    survivor = _create_oauth_user(
        db, provider=AuthProvider.vk, external_id=f"v-{uuid4().hex[:8]}", display_name="VK User"
    )
    merged = _create_oauth_user(
        db, provider=AuthProvider.yandex, external_id=f"y-{uuid4().hex[:8]}", display_name="Yandex User"
    )
    return survivor, merged


def test_union_keeps_links_of_both_accounts(db_session: Session) -> None:
    """Разные системы в двух профилях — объединение забирает обе."""
    survivor, merged = _merge_pair(db_session)
    _link_platform(db_session, survivor, "five_verst")
    _link_platform(db_session, merged, "s95")

    merge_users(db_session, survivor.id, merged.id, strategy="union")

    assert _linked_codes(db_session, survivor.id) == {"five_verst", "s95"}


def test_survivor_only_drops_links_of_merged_account(db_session: Session) -> None:
    """Человек отказался объединять привязки — остаются только его."""
    survivor, merged = _merge_pair(db_session)
    _link_platform(db_session, survivor, "five_verst")
    _link_platform(db_session, merged, "s95")

    merge_users(db_session, survivor.id, merged.id, strategy="survivor_only")

    assert _linked_codes(db_session, survivor.id) == {"five_verst"}


def test_union_pulls_links_into_empty_survivor(db_session: Session) -> None:
    """Пустой аккаунт присоединяет наполненный: привязки должны переехать,
    а не отвязаться, как было раньше."""
    survivor, merged = _merge_pair(db_session)
    _link_platform(db_session, merged, "five_verst")
    _link_platform(db_session, merged, "s95")

    merge_users(db_session, survivor.id, merged.id, strategy="union")

    assert _linked_codes(db_session, survivor.id) == {"five_verst", "s95"}


def test_union_requires_choice_on_conflicting_platform(db_session: Session) -> None:
    """Одна система с обеих сторон — молча выбрать за человека нельзя."""
    survivor, merged = _merge_pair(db_session)
    _link_platform(db_session, survivor, "five_verst")
    _link_platform(db_session, merged, "five_verst")

    with pytest.raises(AccountMergeError):
        merge_users(db_session, survivor.id, merged.id, strategy="union")

    # Отказ не должен ничего испортить: обе привязки на месте, профиль цел.
    assert db_session.query(User).filter(User.id == merged.id).one_or_none() is not None
    assert _linked_codes(db_session, survivor.id) == {"five_verst"}
    assert _linked_codes(db_session, merged.id) == {"five_verst"}


def test_conflict_choice_keeps_selected_profile(db_session: Session) -> None:
    survivor, merged = _merge_pair(db_session)
    _link_platform(db_session, survivor, "five_verst")
    merged_link = _link_platform(db_session, merged, "five_verst")
    kept_external_id = merged_link.external_user_id

    merge_users(
        db_session,
        survivor.id,
        merged.id,
        strategy="union",
        conflict_choices={"five_verst": "merged"},
    )

    links = list_user_platform_links(db_session, survivor.id)
    assert [item["platform_code"] for item in links] == ["five_verst"]
    assert links[0]["external_user_id"] == kept_external_id


def test_conflict_choice_can_keep_current_profile(db_session: Session) -> None:
    survivor, merged = _merge_pair(db_session)
    survivor_link = _link_platform(db_session, survivor, "five_verst")
    kept_external_id = survivor_link.external_user_id
    _link_platform(db_session, merged, "five_verst")
    _link_platform(db_session, merged, "s95")

    merge_users(
        db_session,
        survivor.id,
        merged.id,
        strategy="union",
        conflict_choices={"five_verst": "survivor"},
    )

    links = {item["platform_code"]: item["external_user_id"] for item in list_user_platform_links(db_session, survivor.id)}
    assert links["five_verst"] == kept_external_id
    assert "s95" in links


def test_preview_reports_conflicts_and_both_sides(db_session: Session) -> None:
    survivor, merged = _merge_pair(db_session)
    _link_platform(db_session, survivor, "five_verst")
    _link_platform(db_session, merged, "five_verst")
    _link_platform(db_session, merged, "s95")

    preview = merge_preview_payload(db_session, survivor, merged)

    assert preview["requires_choice"] is True
    assert [item["platform_code"] for item in preview["conflicts"]] == ["five_verst"]
    assert {item["platform_code"] for item in preview["merged_links"]} == {"five_verst", "s95"}
    assert preview["conflicts"][0]["survivor"]["display_name"]


def test_preview_needs_no_choice_when_merged_account_is_empty(db_session: Session) -> None:
    survivor, merged = _merge_pair(db_session)
    _link_platform(db_session, survivor, "five_verst")

    preview = merge_preview_payload(db_session, survivor, merged)

    assert preview["requires_choice"] is False
    assert preview["conflicts"] == []


def test_merge_moves_goals_instead_of_deleting_them(db_session: Session) -> None:
    """user_goals висят на users с ON DELETE CASCADE: без переноса цели
    поглощаемого профиля молча исчезали бы вместе с ним."""
    survivor, merged = _merge_pair(db_session)
    db_session.add(UserGoal(user_id=merged.id, year=2026, goal_type="runs", target_value=30))
    db_session.commit()

    merge_users(db_session, survivor.id, merged.id)

    goals = db_session.query(UserGoal).filter(UserGoal.user_id == survivor.id).all()
    assert [(goal.year, goal.goal_type, goal.target_value) for goal in goals] == [(2026, "runs", 30)]


def test_preview_payload_matches_api_schema(db_session: Session) -> None:
    """Схема ответа и то, что кладёт сервис, должны сходиться: разъехавшись,
    они уронят /auth/merge/preview уже у человека."""
    from app.schemas.auth import MergePreviewResponse

    survivor, merged = _merge_pair(db_session)
    _link_platform(db_session, survivor, "five_verst")
    _link_platform(db_session, merged, "five_verst")

    payload = merge_preview_payload(db_session, survivor, merged)
    payload["merge_token"] = "t" * 24
    response = MergePreviewResponse.model_validate(payload)

    assert response.requires_choice is True
    assert response.conflicts[0].platform_code == "five_verst"
    assert response.default_strategy == "union"
