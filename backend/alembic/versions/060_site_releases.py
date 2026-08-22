"""Add site_releases (публичная страница «Обновления» + админ-CRUD релизов)

Revision ID: 060_site_releases
Revises: 059_avatar_full
Create Date: 2026-08-02
"""

import sqlalchemy as sa

from alembic import op

revision = "060_site_releases"
down_revision = "059_avatar_full"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "site_releases",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("version", sa.String(length=32), nullable=False, unique=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("released_at", sa.Date(), nullable=False),
        sa.Column("is_published", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_site_releases_released_at", "site_releases", ["released_at"])


def downgrade() -> None:
    op.drop_index("ix_site_releases_released_at", table_name="site_releases")
    op.drop_table("site_releases")
