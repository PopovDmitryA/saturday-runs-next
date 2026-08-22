from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BacklogCategoryInfo:
    key: str
    label: str


# Канонический список категорий карточек бэклога — единственный источник
# правды для валидации на бэкенде и для выпадающего списка на фронтенде
# (frontend/src/features/backlog/BacklogPage.tsx дублирует эти же ключи и
# подписи константой). Не DB-enum — список можно расширять без миграции.
BACKLOG_CATEGORY_REGISTRY: tuple[BacklogCategoryInfo, ...] = (
    BacklogCategoryInfo("dashboard", "Личный кабинет"),
    BacklogCategoryInfo("runs", "Пробежки"),
    BacklogCategoryInfo("volunteering", "Волонтёрство"),
    BacklogCategoryInfo("achievements", "Достижения"),
    BacklogCategoryInfo("co_runners", "Встречи"),
    BacklogCategoryInfo("ratings", "Рейтинги"),
    BacklogCategoryInfo("maps", "Карта"),
    BacklogCategoryInfo("locations", "Локации"),
    BacklogCategoryInfo("blog", "Блог"),
    BacklogCategoryInfo("sync", "Синхронизация с системами"),
    BacklogCategoryInfo("account", "Профиль и настройки"),
    BacklogCategoryInfo("other", "Другое"),
)

BACKLOG_CATEGORY_KEYS: frozenset[str] = frozenset(info.key for info in BACKLOG_CATEGORY_REGISTRY)
BACKLOG_CATEGORY_LABELS: dict[str, str] = {info.key: info.label for info in BACKLOG_CATEGORY_REGISTRY}
