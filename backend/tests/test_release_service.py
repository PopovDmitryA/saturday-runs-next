"""Юнит-тесты релизов: нумерация X.Y.Z(-fixN), сортировка, скрытие, подсказка версии.

Тесты ходят в БД из DATABASE_URL, где могут лежать настоящие релизы (сид
ретро-истории). Поэтому все версии здесь — с «тестовым» мажором 987/988:
он заведомо больше реальных, а проверки списков фильтруются по этому префиксу.
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.services.release_service import (
    ReleaseError,
    create_release,
    delete_release,
    latest_published_version,
    latest_version,
    list_all_releases,
    list_published_releases,
    parse_version,
    suggest_next_versions,
    update_release,
)

_TEST_PREFIX = "987."


def _test_versions(releases) -> list[str]:
    return [r.version for r in releases if r.version.startswith(_TEST_PREFIX)]


def _make_release(
    db: Session,
    *,
    version: str,
    published: bool = True,
    released_at: date | None = None,
):
    return create_release(
        db,
        version=version,
        title=f"Релиз {version}",
        body=f"Подробности релиза {version}",
        released_at=released_at or date(2026, 8, 1),
        is_published=published,
    )


def test_parse_version_accepts_semver_and_fix_suffix() -> None:
    assert parse_version("2.3.0") == (2, 3, 0, 0)
    assert parse_version("2.3.0-fix2") == (2, 3, 0, 2)
    for bad in ("2.3", "v2.3.0", "2.3.0-fix", "2.3.0.1", "2.3.0-rc1"):
        with pytest.raises(ReleaseError):
            parse_version(bad)


def test_sort_is_semver_not_lexicographic(db_session: Session) -> None:
    # 987.10.0 должен стоять выше 987.9.0, а 987.2.0-fix1 — выше 987.2.0.
    for version in ("987.2.0", "987.9.0", "987.2.0-fix1", "987.10.0"):
        _make_release(db_session, version=version)

    versions = _test_versions(list_published_releases(db_session))
    assert versions == ["987.10.0", "987.9.0", "987.2.0-fix1", "987.2.0"]


def test_unpublished_hidden_from_public_but_counted_for_numbering(db_session: Session) -> None:
    _make_release(db_session, version="987.3.0")
    _make_release(db_session, version="987.4.0", published=False)

    assert _test_versions(list_published_releases(db_session)) == ["987.3.0"]
    # Тестовый мажор больше любого реального → наши записи возглавляют список.
    assert latest_published_version(db_session) == "987.3.0"
    # Скрытый релиз занимает номер: следующая версия считается от него.
    assert latest_version(db_session) == "987.4.0"
    assert _test_versions(list_all_releases(db_session)) == ["987.4.0", "987.3.0"]


def test_suggest_next_versions(db_session: Session) -> None:
    _make_release(db_session, version="987.3.0")
    suggestions = suggest_next_versions(db_session)
    assert suggestions == {
        "current": "987.3.0",
        "major": "988.0.0",
        "minor": "987.4.0",
        "patch": "987.3.1",
        "fix": "987.3.0-fix1",
    }

    _make_release(db_session, version="987.3.0-fix1", published=False)
    assert suggest_next_versions(db_session)["fix"] == "987.3.0-fix2"

    # После удаления следующая версия считается от последней оставшейся записи.
    releases = {release.version: release for release in list_all_releases(db_session)}
    delete_release(db_session, releases["987.3.0-fix1"].id)
    assert suggest_next_versions(db_session)["current"] == "987.3.0"


def test_version_must_be_unique(db_session: Session) -> None:
    release = _make_release(db_session, version="987.3.0")
    _make_release(db_session, version="987.4.0")
    with pytest.raises(ReleaseError):
        _make_release(db_session, version="987.3.0")
    with pytest.raises(ReleaseError):
        update_release(
            db_session,
            release.id,
            version="987.4.0",
            title="х",
            body="х",
            released_at=None,
            is_published=True,
        )
    # Смена версии у самого себя конфликтом не считается.
    updated = update_release(
        db_session,
        release.id,
        version="987.3.0",
        title="Новый заголовок",
        body="Новое описание",
        released_at=date(2026, 7, 30),
        is_published=False,
    )
    assert updated.title == "Новый заголовок"
    assert updated.released_at == date(2026, 7, 30)
    assert updated.is_published is False


def test_validation_and_missing_release(db_session: Session) -> None:
    with pytest.raises(ReleaseError):
        _make_release(db_session, version="кривая")
    with pytest.raises(ReleaseError):
        create_release(
            db_session,
            version="987.5.0",
            title="  ",
            body="текст",
            released_at=None,
            is_published=False,
        )
    with pytest.raises(ReleaseError):
        delete_release(db_session, uuid4())
