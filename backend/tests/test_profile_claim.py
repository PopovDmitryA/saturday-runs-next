"""Привязка по ID из тизера главной (Т1): разбор входа, отказы, идемпотентность."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Participant, Platform, PlatformLink, User
from app.services.profile_linking_service import (
    ProfileLinkingError,
    claim_profile_by_athlete_id,
    resolve_claim_input,
)


def _platform(db: Session, code: str) -> Platform:
    platform = db.query(Platform).filter(Platform.code == code).one_or_none()
    if platform is None:
        platform = Platform(code=code, name=code)
        db.add(platform)
        db.commit()
        db.refresh(platform)
    return platform


def _participant(db: Session, code: str, external_id: str, **kwargs) -> Participant:
    participant = Participant(
        platform_id=_platform(db, code).id,
        external_user_id=external_id,
        display_name="Тестовый Бегун",
        **kwargs,
    )
    db.add(participant)
    db.commit()
    db.refresh(participant)
    return participant


def _user(db: Session) -> User:
    user = User(display_name="Тест", consent_accepted=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_resolve_claim_input_uses_profile_url(db_session: Session) -> None:
    """Для 5 вёрст и S95 в preview/confirm уходит сохранённый адрес профиля."""
    external_id = str(uuid4().int % 1_000_000)
    url = f"https://5verst.ru/userstats/{external_id}/"
    _participant(db_session, "five_verst", external_id, profile_url=url)

    assert resolve_claim_input(db_session, "five_verst", external_id) == url


def test_resolve_claim_input_normalizes_parkrun_id(db_session: Session) -> None:
    """parkrun-ID люди пишут как «A331» — тизер и привязка нормализуют одинаково."""
    external_id = str(uuid4().int % 1_000_000)
    _participant(db_session, "parkrun", external_id, barcode_id=f"A{external_id}")

    assert resolve_claim_input(db_session, "parkrun", f"A{external_id}") == f"A{external_id}"


def test_resolve_claim_input_rejects_unknown_platform(db_session: Session) -> None:
    with pytest.raises(ProfileLinkingError) as exc:
        resolve_claim_input(db_session, "strava", "123")
    assert exc.value.status_code == 400


def test_resolve_claim_input_rejects_non_numeric_id(db_session: Session) -> None:
    with pytest.raises(ProfileLinkingError) as exc:
        resolve_claim_input(db_session, "five_verst", "иванов")
    assert exc.value.status_code == 400


def test_resolve_claim_input_missing_participant(db_session: Session) -> None:
    """ID, которого нет в нашей БД: тизер бы его тоже не показал."""
    with pytest.raises(ProfileLinkingError) as exc:
        resolve_claim_input(db_session, "five_verst", "999999999999")
    assert exc.value.status_code == 404


def test_resolve_claim_input_without_profile_url(db_session: Session) -> None:
    """Участник есть, адреса профиля нет — привязать нечем, но не 500."""
    external_id = str(uuid4().int % 1_000_000)
    _participant(db_session, "five_verst", external_id)

    with pytest.raises(ProfileLinkingError) as exc:
        resolve_claim_input(db_session, "five_verst", external_id)
    assert exc.value.status_code == 422


def test_claim_returns_already_linked(db_session: Session) -> None:
    """Профиль этой системы уже привязан — для сквозного пути это успех.

    Второй заход по той же ссылке (обновил страницу, вернулся назад) не должен
    показывать человеку ошибку.
    """
    external_id = str(uuid4().int % 1_000_000)
    participant = _participant(
        db_session, "five_verst", external_id, profile_url=f"https://5verst.ru/userstats/{external_id}/"
    )
    user = _user(db_session)
    db_session.add(
        PlatformLink(
            user_id=user.id,
            platform_id=participant.platform_id,
            participant_id=participant.id,
            external_user_id=external_id,
            external_url=participant.profile_url or "",
        )
    )
    db_session.commit()

    status, link = claim_profile_by_athlete_id(db_session, user, "five_verst", external_id)

    assert status == "already_linked"
    assert link is None


def test_claim_requires_consent(db_session: Session) -> None:
    """Без согласия на обработку данных привязка не проходит — как и вручную."""
    external_id = str(uuid4().int % 1_000_000)
    _participant(
        db_session, "five_verst", external_id, profile_url=f"https://5verst.ru/userstats/{external_id}/"
    )
    user = User(display_name="Без согласия", consent_accepted=False)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    with pytest.raises(ProfileLinkingError) as exc:
        claim_profile_by_athlete_id(db_session, user, "five_verst", external_id)
    assert exc.value.status_code == 403
