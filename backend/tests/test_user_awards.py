"""Особые награды: выдача в админке, показ в витрине, слияние и удаление профиля."""

from __future__ import annotations

from collections.abc import Generator
from datetime import date
from uuid import uuid4

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.award_kinds import AWARD_KINDS, AwardKind
from app.config import Settings, get_settings
from app.db.session import get_db
from app.main import app
from app.models import User, UserAward
from app.services.account_merge_service import delete_user_with_dependencies, merge_users
from app.services.user_award_service import AwardGrantError, grant_award, list_user_awards

ADMIN_TELEGRAM_ID = 9201
SINGLE = "site_user_1000"


@pytest.fixture
def auth_settings() -> Settings:
    return Settings(
        app_secret_key="test-secret-key",
        app_debug=True,
        telegram_bot_internal_secret="bot-secret",
        telegram_bot_username="TestBot",
        admin_telegram_id=ADMIN_TELEGRAM_ID,
        database_url=get_settings().database_url,
        redis_url="redis://localhost:6379/0",
    )


@pytest.fixture
def client(
    db_session: Session, fake_redis: fakeredis.FakeRedis, auth_settings: Settings
) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = lambda: auth_settings

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _free_single_award(db_session: Session) -> None:
    """Награда «одна на сайт» может уже лежать в базе стенда — тест работает
    внутри своей откатываемой транзакции, поэтому просто освобождаем её."""
    db_session.execute(delete(UserAward).where(UserAward.code == SINGLE))
    db_session.flush()


def _login(client: TestClient, telegram_id: int, username: str = "user") -> None:
    request_token = client.post("/api/auth/login-request").json()["request_token"]
    confirm = client.post(
        "/api/auth/bot/confirm",
        json={
            "request_token": request_token,
            "telegram_id": telegram_id,
            "telegram_username": username,
            "telegram_chat_id": telegram_id,
            "consent_accepted": True,
        },
        headers={"X-Bot-Secret": "bot-secret"},
    )
    token = confirm.json()["magic_link"].split("token=")[1]
    client.get("/api/auth/callback", params={"token": token}, follow_redirects=False)


def _user(db_session: Session, name: str = "Иван Петров") -> User:
    user = User(
        telegram_id=int(uuid4().int % 10_000_000_000),
        telegram_username=f"u{uuid4().int % 1_000_000}",
        display_name=name,
        consent_accepted=True,
    )
    db_session.add(user)
    db_session.commit()
    return user


def test_registry_codes_are_unique_and_short() -> None:
    codes = [kind.code for kind in AWARD_KINDS]
    assert len(codes) == len(set(codes))
    # user_awards.code — String(64)
    assert all(0 < len(code) <= 64 for code in codes)
    # Номер и подпись должны влезть в марку размером с медаль, цвет поля —
    # из палитры AwardStamp на фронте
    assert all(kind.emblem is None or 0 < len(kind.emblem) <= 6 for kind in AWARD_KINDS)
    assert all(kind.caption is None or 0 < len(kind.caption) <= 10 for kind in AWARD_KINDS)
    assert all(kind.tone in {"carmine", "ultramarine", "emerald"} for kind in AWARD_KINDS)


def test_grant_and_list_award(db_session: Session) -> None:
    user = _user(db_session)
    admin = _user(db_session, "Админ")
    grant_award(db_session, user.id, code=SINGLE, awarded_on=date(2026, 10, 16), note="  ", granted_by_user_id=admin.id)

    assert list_user_awards(db_session, user.id) == [
        {
            "code": SINGLE,
            "title": "Тысячный участник",
            "icon": "🎖️",
            "emblem": "1000",
            "caption": "УЧАСТНИК",
            "tone": "carmine",
            "description": "Тысячным привязал профиль к run5k.run.",
            "awarded_on": "2026-10-16",
        }
    ]
    row = db_session.query(UserAward).filter(UserAward.user_id == user.id).one()
    # Пустая заметка не хранится строкой из пробелов
    assert row.note is None
    assert row.granted_by_user_id == admin.id


def test_single_holder_award_goes_to_one_person_only(db_session: Session) -> None:
    first, second = _user(db_session), _user(db_session, "Анна Смирнова")
    grant_award(
        db_session, first.id, code=SINGLE, awarded_on=date(2026, 10, 16), note=None, granted_by_user_id=first.id
    )

    with pytest.raises(AwardGrantError) as excinfo:
        grant_award(
            db_session, second.id, code=SINGLE, awarded_on=date(2026, 10, 16), note=None, granted_by_user_id=first.id
        )
    assert excinfo.value.status_code == 409
    assert "Иван Петров" in excinfo.value.message


def test_same_award_twice_and_unknown_code_are_rejected(db_session: Session) -> None:
    user = _user(db_session)
    grant_award(db_session, user.id, code=SINGLE, awarded_on=date(2026, 10, 16), note=None, granted_by_user_id=user.id)

    with pytest.raises(AwardGrantError) as twice:
        grant_award(
            db_session, user.id, code=SINGLE, awarded_on=date(2026, 10, 17), note=None, granted_by_user_id=user.id
        )
    assert twice.value.status_code == 409

    with pytest.raises(AwardGrantError) as unknown:
        grant_award(
            db_session,
            user.id,
            code="no_such_award",
            awarded_on=date(2026, 10, 16),
            note=None,
            granted_by_user_id=user.id,
        )
    assert unknown.value.status_code == 404


def test_award_removed_from_registry_is_hidden_not_deleted(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _user(db_session)
    db_session.add(UserAward(user_id=user.id, code="retired_award", awarded_on=date(2026, 1, 1)))
    db_session.commit()

    assert list_user_awards(db_session, user.id) == []
    assert db_session.query(UserAward).filter(UserAward.user_id == user.id).count() == 1

    # Вид вернули в код — награда снова видна
    retired = AwardKind(code="retired_award", title="Старая", icon="⭐", description="Вернулась.")
    monkeypatch.setattr("app.services.user_award_service.AWARD_BY_CODE", {"retired_award": retired})
    assert [item["code"] for item in list_user_awards(db_session, user.id)] == ["retired_award"]


def test_admin_api_grants_and_revokes(client: TestClient, db_session: Session) -> None:
    target = _user(db_session)
    _login(client, ADMIN_TELEGRAM_ID, "admin_user")

    listing = client.get(f"/api/admin/users/{target.id}/awards")
    assert listing.status_code == 200
    assert listing.json()["awards"] == []
    assert SINGLE in [kind["code"] for kind in listing.json()["kinds"]]

    created = client.post(
        f"/api/admin/users/{target.id}/awards",
        json={"code": SINGLE, "awarded_on": "2026-10-16", "note": "по журналу привязок"},
    )
    assert created.status_code == 201
    [award] = created.json()["awards"]
    assert award["awarded_on"] == "2026-10-16"
    assert award["note"] == "по журналу привязок"

    again = client.post(f"/api/admin/users/{target.id}/awards", json={"code": SINGLE, "awarded_on": "2026-10-16"})
    assert again.status_code == 409

    revoked = client.delete(f"/api/admin/users/{target.id}/awards/{award['id']}")
    assert revoked.status_code == 200
    assert revoked.json()["awards"] == []
    assert client.delete(f"/api/admin/users/{target.id}/awards/{award['id']}").status_code == 404


def test_admin_api_shows_current_holder_to_other_users(client: TestClient, db_session: Session) -> None:
    holder, other = _user(db_session), _user(db_session, "Анна Смирнова")
    grant_award(
        db_session, holder.id, code=SINGLE, awarded_on=date(2026, 10, 16), note=None, granted_by_user_id=holder.id
    )
    _login(client, ADMIN_TELEGRAM_ID, "admin_user")

    kinds = {kind["code"]: kind for kind in client.get(f"/api/admin/users/{other.id}/awards").json()["kinds"]}
    assert kinds[SINGLE]["holder"] == f"Иван Петров (№{holder.serial_id})"
    own = {kind["code"]: kind for kind in client.get(f"/api/admin/users/{holder.id}/awards").json()["kinds"]}
    assert own[SINGLE]["holder"] is None


def test_admin_api_requires_admin(client: TestClient, db_session: Session) -> None:
    target = _user(db_session)
    assert client.get(f"/api/admin/users/{target.id}/awards").status_code == 401
    _login(client, int(uuid4().int % 10_000_000_000), "not_admin")
    assert (
        client.post(
            f"/api/admin/users/{target.id}/awards", json={"code": SINGLE, "awarded_on": "2026-10-16"}
        ).status_code
        == 403
    )


def test_public_profile_achievements_include_awards(client: TestClient, db_session: Session) -> None:
    user = _user(db_session)
    grant_award(
        db_session, user.id, code=SINGLE, awarded_on=date(2026, 10, 16), note="секрет", granted_by_user_id=user.id
    )

    response = client.get(f"/api/users/{user.serial_id}/profile/achievements")
    assert response.status_code == 200
    [award] = response.json()["awards"]
    assert award["code"] == SINGLE
    assert award["awarded_on"] == "2026-10-16"
    # Заметка админа наружу не уходит
    assert "note" not in award

    # Фильтр систем витрины на награды не действует
    scoped = client.get(f"/api/users/{user.serial_id}/profile/achievements", params={"platform": "s95"})
    assert [item["code"] for item in scoped.json()["awards"]] == [SINGLE]


def test_own_achievements_include_awards(client: TestClient, db_session: Session) -> None:
    telegram_id = int(uuid4().int % 10_000_000_000)
    _login(client, telegram_id, "runner")
    user = db_session.query(User).filter(User.telegram_id == telegram_id).one()
    grant_award(db_session, user.id, code=SINGLE, awarded_on=date(2026, 10, 16), note=None, granted_by_user_id=user.id)

    response = client.get("/api/achievements")
    assert response.status_code == 200
    assert [item["code"] for item in response.json()["awards"]] == [SINGLE]


def test_merge_moves_award_to_survivor(db_session: Session) -> None:
    """Награда — о человеке: объединение аккаунтов не должно её терять, в
    какую бы сторону оно ни прошло."""
    survivor, merged = _user(db_session, "Новый вход"), _user(db_session)
    grant_award(
        db_session, merged.id, code=SINGLE, awarded_on=date(2026, 10, 16), note=None, granted_by_user_id=merged.id
    )

    merge_users(db_session, survivor.id, merged.id)

    assert [item["code"] for item in list_user_awards(db_session, survivor.id)] == [SINGLE]
    assert db_session.query(User).filter(User.id == merged.id).one_or_none() is None


def test_merge_keeps_survivor_copy_of_same_award(db_session: Session) -> None:
    survivor, merged = _user(db_session, "Новый вход"), _user(db_session)
    db_session.add_all(
        [
            UserAward(user_id=survivor.id, code="retired_award", awarded_on=date(2026, 1, 1)),
            UserAward(user_id=merged.id, code="retired_award", awarded_on=date(2026, 2, 2)),
        ]
    )
    db_session.commit()

    merge_users(db_session, survivor.id, merged.id)

    [row] = db_session.query(UserAward).filter(UserAward.user_id == survivor.id).all()
    assert row.awarded_on == date(2026, 1, 1)


def test_award_goes_away_with_profile(db_session: Session) -> None:
    user = _user(db_session)
    grant_award(db_session, user.id, code=SINGLE, awarded_on=date(2026, 10, 16), note=None, granted_by_user_id=user.id)
    user_id = user.id
    db_session.expunge(user)

    db_session.execute(delete(User).where(User.id == user_id))
    db_session.flush()

    assert db_session.query(UserAward).filter(UserAward.user_id == user_id).count() == 0


def test_granter_deletion_keeps_award(db_session: Session) -> None:
    user, admin = _user(db_session), _user(db_session, "Админ")
    grant_award(db_session, user.id, code=SINGLE, awarded_on=date(2026, 10, 16), note=None, granted_by_user_id=admin.id)
    admin_id = admin.id
    db_session.expunge(admin)

    db_session.execute(delete(User).where(User.id == admin_id))
    db_session.flush()

    row = db_session.query(UserAward).filter(UserAward.user_id == user.id).one()
    db_session.refresh(row)
    assert row.granted_by_user_id is None


def test_oauth_orphan_fold_keeps_award(db_session: Session) -> None:
    """Привязка VK/Яндекса в настройках поглощает профиль-«сироту» мимо
    merge_users (oauth_service) — награда обязана переехать и там."""
    survivor, orphan = _user(db_session, "Новый вход"), _user(db_session)
    grant_award(
        db_session, orphan.id, code=SINGLE, awarded_on=date(2026, 10, 16), note=None, granted_by_user_id=orphan.id
    )

    delete_user_with_dependencies(db_session, orphan, reassign_to=survivor.id)
    db_session.commit()

    assert [item["code"] for item in list_user_awards(db_session, survivor.id)] == [SINGLE]
