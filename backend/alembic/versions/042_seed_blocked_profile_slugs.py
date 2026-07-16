"""Сид антисквоттинг-адресов в blocked_profile_slugs

Пакет нессистемных ников, которые держим недоступными под публичный профиль:
варианты названий платформ, бренд проекта, «официальные»/властные роли и
служебные слова, которых нет в статическом RESERVED_SLUGS. Всё — обычные manual-
записи (админ может удалить через /admin/profile-slugs). INSERT ... ON CONFLICT
DO NOTHING: миграция не падает, если какой-то slug уже занесён вручную.

Системные слова (admin, api, settings, …) сюда НЕ входят — они и так блокируются
в коде (RESERVED_SLUGS) и показываются в админке отдельным read-only блоком.

Revision ID: 042_seed_blocked_profile_slugs
Revises: 041_blocked_profile_slugs
Create Date: 2026-07-11
"""

from alembic import op

revision = "042_seed_blocked_profile_slugs"
down_revision = "041_blocked_profile_slugs"
branch_labels = None
depends_on = None


_COMMENT = "Служебный резерв / антисквоттинг"

# Платформы (варианты), бренд, роли, служебные.
_SLUGS = (
    # платформы
    "fiveverst", "five-verst", "five_verst", "verst", "s-95",
    "park-run", "parkrunru", "runparkru",
    # бренд / проект
    "run5k", "run5krun", "run5k-run", "saturdayruns", "saturday-runs", "saturday_runs",
    # «официальные» / властные роли
    "official", "team", "staff", "administration", "administrator",
    "moderator", "moderators", "mod", "moder", "root", "superuser", "owner",
    "system", "service", "bot", "security", "abuse", "support-team",
    # служебные (нет в RESERVED_SLUGS)
    "help", "helpdesk", "info", "contact", "feedback", "noreply", "no-reply",
    "webmaster", "postmaster", "robots", "sitemap", "favicon", "manifest",
    "assets", "search", "news", "feed", "faq", "terms", "privacy", "legal",
    "docs", "blog", "status", "home", "index", "test",
    # типосквоттинг
    "adm1n", "0auth",
)


def upgrade() -> None:
    rows = ", ".join(f"('{slug}', '{_COMMENT}')" for slug in _SLUGS)
    op.execute(
        "INSERT INTO blocked_profile_slugs (slug, comment) "
        f"VALUES {rows} "
        "ON CONFLICT (slug) DO NOTHING"
    )


def downgrade() -> None:
    slugs = ", ".join(f"'{slug}'" for slug in _SLUGS)
    op.execute(f"DELETE FROM blocked_profile_slugs WHERE slug IN ({slugs})")
