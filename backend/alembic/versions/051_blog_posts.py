"""Add blog_posts (затравки постов Telegram-канала для блога на портале)

Revision ID: 051_blog_posts
Revises: 050_scheduled_run_logs
Create Date: 2026-07-19
"""

import sqlalchemy as sa

from alembic import op

revision = "051_blog_posts"
down_revision = "050_scheduled_run_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "blog_posts",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("teaser", sa.Text(), nullable=False),
        sa.Column("telegram_url", sa.String(length=512), nullable=False),
        sa.Column("topic", sa.String(length=64)),
        sa.Column(
            "published_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("is_published", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("clicks_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_blog_posts_published_at", "blog_posts", ["published_at"])


def downgrade() -> None:
    op.drop_index("ix_blog_posts_published_at", table_name="blog_posts")
    op.drop_table("blog_posts")
