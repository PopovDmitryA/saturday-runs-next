"""Add blocked_profile_slugs (ручной резерв vanity-ссылок профиля)

Редактируемый список slug'ов, которые нельзя занять под публичный профиль
(/users/{slug}). Отличается от статического RESERVED_SLUGS в коде: сюда попадают
конкретные ники, которые держим свободными по просьбе. Slug хранится в нижнем
регистре — сравнение с users.public_slug регистронезависимо.

Сидируем три адреса по просьбе Димы Петрова.

Revision ID: 041_blocked_profile_slugs
Revises: 040_clubs
Create Date: 2026-07-10
"""

import sqlalchemy as sa
from alembic import op

revision = "041_blocked_profile_slugs"
down_revision = "040_clubs"
branch_labels = None
depends_on = None


_SEED_COMMENT = "Дима Петров попросил не занимать эти адреса"
_SEED_SLUGS = ("engirunners", "dmitripetrov", "engirunner")


def upgrade() -> None:
    op.create_table(
        "blocked_profile_slugs",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_blocked_profile_slugs_slug"),
    )

    blocked = sa.table(
        "blocked_profile_slugs",
        sa.column("slug", sa.String),
        sa.column("comment", sa.Text),
    )
    op.bulk_insert(
        blocked,
        [{"slug": slug, "comment": _SEED_COMMENT} for slug in _SEED_SLUGS],
    )


def downgrade() -> None:
    op.drop_table("blocked_profile_slugs")
