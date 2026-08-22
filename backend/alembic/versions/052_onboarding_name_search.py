"""Онбординг: поиск участников по ФИО (pg_trgm) + отметка прохождения онбординга

Revision ID: 052_onboarding_name_search
Revises: 051_blog_posts
Create Date: 2026-08-22
"""

import logging

import sqlalchemy as sa

from alembic import op

revision = "052_onboarding_name_search"
down_revision = "051_blog_posts"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("onboarding_completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # pg_trgm — trusted-расширение в PG13+, доступно владельцу БД без superuser.
    # Если прав всё же нет — поиск останется на seq scan, миграцию не валим.
    bind = op.get_bind()
    try:
        with bind.begin_nested():
            bind.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            bind.execute(
                sa.text(
                    "CREATE INDEX IF NOT EXISTS ix_participants_display_name_trgm "
                    "ON participants USING gin (lower(display_name) gin_trgm_ops)"
                )
            )
    except Exception as exc:  # pragma: no cover — только на нестандартных правах БД
        log.warning("pg_trgm index skipped (insufficient privileges?): %s", exc)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_participants_display_name_trgm")
    op.drop_column("users", "onboarding_completed_at")
