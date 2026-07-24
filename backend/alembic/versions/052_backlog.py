"""Add backlog_cards/backlog_votes/backlog_comments (страница «Бэклог»)

Revision ID: 052_backlog
Revises: 051_blog_posts
Create Date: 2026-07-24
"""

import sqlalchemy as sa

from alembic import op

revision = "052_backlog"
down_revision = "051_blog_posts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "backlog_cards",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("type", sa.Enum("bug", "feature", name="backlog_card_type_enum"), nullable=False),
        sa.Column("category", sa.String(length=64), server_default="other", nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "in_progress", "rejected", "done", name="backlog_card_status_enum"),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("is_anonymous", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "author_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("upvotes", sa.Integer(), server_default="0", nullable=False),
        sa.Column("downvotes", sa.Integer(), server_default="0", nullable=False),
        sa.Column("score", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_backlog_cards_status_score", "backlog_cards", ["status", "score"])

    op.create_table(
        "backlog_votes",
        sa.Column(
            "card_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("backlog_cards.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            primary_key=True,
        ),
        sa.Column("value", sa.SmallInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "backlog_comments",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "card_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("backlog_cards.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "author_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("is_anonymous", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_backlog_comments_card_created_at", "backlog_comments", ["card_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_backlog_comments_card_created_at", table_name="backlog_comments")
    op.drop_table("backlog_comments")
    op.drop_table("backlog_votes")
    op.drop_index("ix_backlog_cards_status_score", table_name="backlog_cards")
    op.drop_table("backlog_cards")

    op.execute("DROP TYPE IF EXISTS backlog_card_status_enum")
    op.execute("DROP TYPE IF EXISTS backlog_card_type_enum")
