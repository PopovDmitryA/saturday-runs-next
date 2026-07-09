"""Add users.public_slug (vanity ссылка на публичный профиль)

Уникальная НЕцифровая ссылка на профиль (/users/{public_slug}). Хранится в
нижнем регистре, поэтому обычного UNIQUE достаточно для регистронезависимой
уникальности. NULL — ссылка не задана.

Revision ID: 037_users_public_slug
Revises: 036_protocol_source_updated_at
Create Date: 2026-07-09
"""

import sqlalchemy as sa
from alembic import op

revision = "037_users_public_slug"
down_revision = "036_protocol_source_updated_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("public_slug", sa.String(length=64), nullable=True))
    op.create_unique_constraint("uq_users_public_slug", "users", ["public_slug"])


def downgrade() -> None:
    op.drop_constraint("uq_users_public_slug", "users", type_="unique")
    op.drop_column("users", "public_slug")
