"""Queue profile fetches when platform cooldown/ban blocks live requests."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "026_profile_fetch_pending"
down_revision = "025_auth_identities"
branch_labels = None
depends_on = None

pending_status_enum = postgresql.ENUM(
    "pending",
    "processing",
    "done",
    "failed",
    name="profile_fetch_pending_status_enum",
    create_type=False,
)
pending_reason_enum = postgresql.ENUM(
    "cooldown",
    "ban",
    "timeout",
    "error",
    name="profile_fetch_pending_reason_enum",
    create_type=False,
)
pending_operation_enum = postgresql.ENUM(
    "profile_preview",
    "activity_import",
    name="profile_fetch_pending_operation_enum",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    pending_status_enum.create(bind, checkfirst=True)
    pending_reason_enum.create(bind, checkfirst=True)
    pending_operation_enum.create(bind, checkfirst=True)

    op.create_table(
        "profile_fetch_pending",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("platform_code", sa.String(length=32), nullable=False),
        sa.Column("profile_input", sa.Text(), nullable=False),
        sa.Column("external_user_id", sa.String(length=128), nullable=True),
        sa.Column("canonical_profile_url", sa.String(length=1024), nullable=True),
        sa.Column("operation", pending_operation_enum, nullable=False, server_default="profile_preview"),
        sa.Column("status", pending_status_enum, nullable=False, server_default="pending"),
        sa.Column("reason", pending_reason_enum, nullable=False, server_default="cooldown"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_profile_fetch_pending_status_created",
        "profile_fetch_pending",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_profile_fetch_pending_platform_external",
        "profile_fetch_pending",
        ["platform_code", "external_user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_profile_fetch_pending_platform_external", table_name="profile_fetch_pending")
    op.drop_index("ix_profile_fetch_pending_status_created", table_name="profile_fetch_pending")
    op.drop_table("profile_fetch_pending")
    bind = op.get_bind()
    pending_operation_enum.drop(bind, checkfirst=True)
    pending_reason_enum.drop(bind, checkfirst=True)
    pending_status_enum.drop(bind, checkfirst=True)
