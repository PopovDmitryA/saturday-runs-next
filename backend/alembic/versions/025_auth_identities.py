"""Add auth_identities for multi-provider login."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "025_auth_identities"
down_revision = "024_participant_planning_seen_at"
branch_labels = None
depends_on = None

auth_provider_enum = postgresql.ENUM(
    "telegram",
    "vk",
    "yandex",
    name="auth_provider_enum",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    auth_provider_enum.create(bind, checkfirst=True)

    op.create_table(
        "auth_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", auth_provider_enum, nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=256), nullable=True),
        sa.Column("email", sa.String(length=256), nullable=True),
        sa.Column("profile_json", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "external_id", name="uq_auth_identities_provider_external_id"),
    )
    op.create_index("ix_auth_identities_user_id", "auth_identities", ["user_id"])

    op.execute(
        """
        INSERT INTO auth_identities (user_id, provider, external_id, display_name, linked_at)
        SELECT id, 'telegram', telegram_id::text, display_name, created_at
        FROM users
        WHERE telegram_id IS NOT NULL
        """
    )

    op.drop_constraint("users_telegram_id_key", "users", type_="unique")
    op.alter_column("users", "telegram_id", existing_type=sa.BigInteger(), nullable=True)


def downgrade() -> None:
    op.execute(
        """
        UPDATE users AS u
        SET telegram_id = ai.external_id::bigint
        FROM auth_identities AS ai
        WHERE ai.user_id = u.id
          AND ai.provider = 'telegram'
          AND u.telegram_id IS NULL
        """
    )
    op.execute("DELETE FROM users WHERE telegram_id IS NULL")

    op.alter_column("users", "telegram_id", existing_type=sa.BigInteger(), nullable=False)
    op.create_unique_constraint("users_telegram_id_key", "users", ["telegram_id"])

    op.drop_index("ix_auth_identities_user_id", table_name="auth_identities")
    op.drop_table("auth_identities")

    bind = op.get_bind()
    auth_provider_enum.drop(bind, checkfirst=True)
