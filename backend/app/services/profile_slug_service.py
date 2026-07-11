from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.models import BlockedProfileSlug, User

# Сообщение, когда slug свободен по формату, но занят в ручном блок-листе.
# Причину (комментарий админа) конечному пользователю не раскрываем.
BLOCKED_SLUG_MESSAGE = "Эта ссылка недоступна, выберите другую"

# Длина slug.
SLUG_MIN_LEN = 3
SLUG_MAX_LEN = 30

# Разрешён: латиница/цифры/`-`/`_`, начинается и кончается буквой или цифрой.
_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$")

# Зарезервированные слова — совпадают с существующими роутами приложения и
# служебными путями, чтобы slug не притворялся системной страницей и не ломал
# резолвинг. Сравнение регистронезависимое (slug и так в нижнем регистре).
RESERVED_SLUGS: frozenset[str] = frozenset(
    {
        "admin", "api", "about", "auth", "co-runners", "corunners", "dashboard",
        "demo", "health", "internal", "login", "logout", "maps", "me", "oauth",
        "profile", "profiles", "ratings", "resolve", "runs", "settings", "share",
        "static", "support", "u", "user", "users", "volunteering", "www",
    }
)


class SlugError(ValueError):
    """Некорректный или занятый slug."""


def normalize_slug(raw: str) -> str:
    return raw.strip().lower()


def validate_slug(raw: str, *, allow_reserved: bool = False) -> str:
    """Проверяет формат slug и возвращает нормализованное значение.

    Бросает SlugError с человекочитаемым сообщением при нарушении правил.
    ``allow_reserved`` пропускает проверку RESERVED_SLUGS: нужен ручному
    блок-листу в админке (там цель — наоборот, занять/зарезервировать ник,
    в т.ч. системное слово), тогда как для пользовательской ссылки reserved
    остаётся запрещённым.
    """
    slug = normalize_slug(raw)
    if not slug:
        raise SlugError("Ссылка не может быть пустой")
    if slug.isdigit():
        # иначе конфликт с числовым serial_id в /users/{...}
        raise SlugError("Ссылка не может состоять только из цифр")
    if len(slug) < SLUG_MIN_LEN:
        raise SlugError(f"Минимум {SLUG_MIN_LEN} символа")
    if len(slug) > SLUG_MAX_LEN:
        raise SlugError(f"Максимум {SLUG_MAX_LEN} символов")
    if not _SLUG_RE.match(slug):
        raise SlugError(
            "Только латинские буквы, цифры, дефис и подчёркивание; "
            "начинаться и заканчиваться буквой или цифрой"
        )
    if not allow_reserved and slug in RESERVED_SLUGS:
        raise SlugError("Эта ссылка зарезервирована, выберите другую")
    return slug


def _owner_of_slug(db: Session, slug: str) -> User | None:
    return db.query(User).filter(User.public_slug == slug).one_or_none()


def is_slug_blocked(db: Session, slug: str) -> bool:
    """True, если slug (в нижнем регистре) есть в ручном блок-листе."""
    return (
        db.query(BlockedProfileSlug.id)
        .filter(BlockedProfileSlug.slug == slug)
        .first()
        is not None
    )


def check_slug_availability(db: Session, user: User, raw: str) -> tuple[bool, str | None, str]:
    """Возвращает (доступен, причина_если_нет, нормализованный_slug)."""
    try:
        slug = validate_slug(raw)
    except SlugError as exc:
        return False, str(exc), normalize_slug(raw)
    owner = _owner_of_slug(db, slug)
    if owner is not None and owner.id != user.id:
        return False, "Эта ссылка уже занята", slug
    # Ручной блок-лист: не даём занять чужой зарезервированный ник. Если slug уже
    # принадлежит этому пользователю (owner is user) — не мешаем его сохранить.
    if owner is None and is_slug_blocked(db, slug):
        return False, BLOCKED_SLUG_MESSAGE, slug
    return True, None, slug


def set_profile_slug(db: Session, user: User, raw: str | None) -> None:
    """Устанавливает или очищает (пустое/None) slug пользователя.

    Смена разрешена: прежний slug освобождается. Бросает SlugError при
    некорректном/занятом значении.
    """
    if raw is None or not raw.strip():
        user.public_slug = None
        return
    slug = validate_slug(raw)
    owner = _owner_of_slug(db, slug)
    if owner is not None and owner.id != user.id:
        raise SlugError("Эта ссылка уже занята")
    if owner is None and is_slug_blocked(db, slug):
        raise SlugError(BLOCKED_SLUG_MESSAGE)
    user.public_slug = slug


def resolve_profile_handle(db: Session, handle: str) -> User | None:
    """Резолвит хэндл из URL в пользователя: число → serial_id, иначе → slug."""
    value = handle.strip()
    if not value:
        return None
    if value.isdigit():
        return db.query(User).filter(User.serial_id == int(value)).one_or_none()
    return _owner_of_slug(db, normalize_slug(value))
