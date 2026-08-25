"""Релизы сайта: страница «Обновления», админ-CRUD и подсказка следующей версии.

Нумерация — X.Y.Z с необязательным суффиксом -fixN (2.2.0-fix1 идёт ПОСЛЕ
2.2.0, в отличие от prerelease в классическом семвере). Протокол присвоения
версий при деплое — docs/release_management.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SiteRelease

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-fix(\d+))?$")


class ReleaseError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def parse_version(version: str) -> tuple[int, int, int, int]:
    """Версия → сортировочный ключ (major, minor, patch, fixN); fixN=0 без суффикса."""
    match = _VERSION_RE.match(version.strip())
    if not match:
        raise ReleaseError("Версия должна быть вида X.Y.Z или X.Y.Z-fixN, например 2.3.0 или 2.3.0-fix1")
    major, minor, patch, fixn = match.groups()
    return int(major), int(minor), int(patch), int(fixn or 0)


def _validate_payload(*, version: str, title: str, body: str) -> tuple[str, str, str]:
    version = version.strip()
    title = title.strip()
    body = body.strip()
    parse_version(version)
    if not title:
        raise ReleaseError("Заголовок не может быть пустым")
    if not body:
        raise ReleaseError("Описание релиза не может быть пустым")
    return version, title, body


def _sorted_desc(releases: list[SiteRelease]) -> list[SiteRelease]:
    """Новые сверху: сначала по номеру версии, дата — на равных не бывает
    (версия уникальна), но released_at участвует для наглядности."""
    return sorted(releases, key=lambda r: (parse_version(r.version), r.released_at), reverse=True)


def list_published_releases(db: Session) -> list[SiteRelease]:
    rows = db.execute(select(SiteRelease).where(SiteRelease.is_published.is_(True))).scalars().all()
    return _sorted_desc(list(rows))


# Сколько релизов показывать на одной странице «Обновлений». Записи длинные
# (заголовок + описание в несколько абзацев), поэтому десятка хватает, чтобы
# страница читалась целиком и не превращалась в бесконечную ленту.
RELEASES_PAGE_SIZE = 10
RELEASES_MAX_PAGE_SIZE = 100


@dataclass(frozen=True)
class ReleasesPage:
    """Одна страница истории релизов вместе с координатами в общем списке."""

    items: list[SiteRelease]
    total: int
    page: int
    page_size: int
    pages: int
    # Номер самого свежего релиза — он же версия сайта в футере. Отдаём здесь,
    # чтобы страница не выясняла его вторым запросом: на второй странице
    # первая запись среза уже не самая свежая.
    latest_version: str | None


def paginate_published_releases(
    db: Session,
    *,
    page: int = 1,
    page_size: int = RELEASES_PAGE_SIZE,
    version: str | None = None,
) -> ReleasesPage:
    """Страница опубликованных релизов, новые сверху.

    Порядок задаётся номером версии, а не датой, поэтому сортируем и режем на
    страницы в Python: релизов десятки, не миллионы.

    `version` — «открой страницу, на которой лежит эта версия». Нужен старым
    ссылкам-якорям вида /updates#v2.5.0: с появлением страниц такая версия
    вполне может оказаться не на первой из них, и без этого якорь вёл бы в
    никуда. Неизвестная версия просто игнорируется — отдаём запрошенную
    страницу.
    """
    releases = list_published_releases(db)
    total = len(releases)
    page_size = max(1, min(page_size, RELEASES_MAX_PAGE_SIZE))
    pages = max(1, -(-total // page_size))

    if version:
        wanted = version.strip()
        index = next((i for i, r in enumerate(releases) if r.version == wanted), None)
        if index is not None:
            page = index // page_size + 1

    page = max(1, min(page, pages))
    start = (page - 1) * page_size
    return ReleasesPage(
        items=releases[start : start + page_size],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
        latest_version=releases[0].version if releases else None,
    )


def latest_published_version(db: Session) -> str | None:
    releases = list_published_releases(db)
    return releases[0].version if releases else None


def list_all_releases(db: Session) -> list[SiteRelease]:
    rows = db.execute(select(SiteRelease)).scalars().all()
    return _sorted_desc(list(rows))


def latest_version(db: Session) -> str | None:
    """Последняя версия среди ВСЕХ записей (и скрытых): база для следующего номера."""
    releases = list_all_releases(db)
    return releases[0].version if releases else None


def suggest_next_versions(db: Session) -> dict[str, str]:
    """Кандидаты следующей версии от последнего релиза в таблице."""
    current = latest_version(db)
    if current is None:
        return {"current": "—", "major": "1.0.0", "minor": "1.0.0", "patch": "1.0.0", "fix": "1.0.0"}
    major, minor, patch, fixn = parse_version(current)
    return {
        "current": current,
        "major": f"{major + 1}.0.0",
        "minor": f"{major}.{minor + 1}.0",
        "patch": f"{major}.{minor}.{patch + 1}",
        "fix": f"{major}.{minor}.{patch}-fix{fixn + 1}",
    }


def _ensure_version_free(db: Session, version: str, *, exclude_id: UUID | None = None) -> None:
    query = select(SiteRelease.id).where(SiteRelease.version == version)
    if exclude_id is not None:
        query = query.where(SiteRelease.id != exclude_id)
    if db.execute(query).scalar_one_or_none() is not None:
        raise ReleaseError(f"Версия {version} уже занята другим релизом", status_code=409)


def create_release(
    db: Session,
    *,
    version: str,
    title: str,
    body: str,
    released_at: date | None,
    is_published: bool = False,
) -> SiteRelease:
    version, title, body = _validate_payload(version=version, title=title, body=body)
    _ensure_version_free(db, version)
    release = SiteRelease(
        version=version,
        title=title,
        body=body,
        released_at=released_at or date.today(),
        is_published=is_published,
    )
    db.add(release)
    db.commit()
    db.refresh(release)
    return release


def _get_release(db: Session, release_id: UUID) -> SiteRelease:
    release = db.get(SiteRelease, release_id)
    if release is None:
        raise ReleaseError("Релиз не найден", status_code=404)
    return release


def update_release(
    db: Session,
    release_id: UUID,
    *,
    version: str,
    title: str,
    body: str,
    released_at: date | None,
    is_published: bool,
) -> SiteRelease:
    release = _get_release(db, release_id)
    version, title, body = _validate_payload(version=version, title=title, body=body)
    _ensure_version_free(db, version, exclude_id=release.id)
    release.version = version
    release.title = title
    release.body = body
    release.is_published = is_published
    if released_at is not None:
        release.released_at = released_at
    db.commit()
    db.refresh(release)
    return release


def delete_release(db: Session, release_id: UUID) -> None:
    release = _get_release(db, release_id)
    db.delete(release)
    db.commit()
