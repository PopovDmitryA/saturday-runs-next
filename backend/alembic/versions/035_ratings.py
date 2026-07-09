"""Add location_ratings (оценки стартов)

Оценка ставится на конкретный старт (run_result). Повторный визит — новая
оценка. Один критерий обязателен (score_overall), остальные — по желанию.
Комментарий один на всю оценку. Флаг is_public — показывать ли автора наружу
(в БД всегда не анонимно). location_key — canonical identity локации для
агрегации рейтинга по одной физической площадке.

Revision ID: 035_ratings
Revises: 034_users_home_location
Create Date: 2026-07-05
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import func

revision = "035_ratings"
down_revision = "034_users_home_location"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "location_ratings",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=func.gen_random_uuid(),
        ),
        sa.Column("user_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_result_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("location_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("location_key", sa.String(length=255), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("platform_code", sa.String(length=32), nullable=False),
        sa.Column("score_overall", sa.SmallInteger(), nullable=False),
        sa.Column("score_organization", sa.SmallInteger(), nullable=True),
        sa.Column("score_route", sa.SmallInteger(), nullable=True),
        sa.Column("score_community", sa.SmallInteger(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=func.now(),
            onupdate=func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_result_id"], ["run_results.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"]),
        sa.UniqueConstraint("user_id", "run_result_id", name="uq_location_ratings_user_run"),
        sa.CheckConstraint("score_overall BETWEEN 1 AND 5", name="ck_location_ratings_overall"),
        sa.CheckConstraint(
            "score_organization IS NULL OR score_organization BETWEEN 1 AND 5",
            name="ck_location_ratings_organization",
        ),
        sa.CheckConstraint(
            "score_route IS NULL OR score_route BETWEEN 1 AND 5",
            name="ck_location_ratings_route",
        ),
        sa.CheckConstraint(
            "score_community IS NULL OR score_community BETWEEN 1 AND 5",
            name="ck_location_ratings_community",
        ),
    )
    op.create_index("ix_location_ratings_location_key", "location_ratings", ["location_key"])
    op.create_index("ix_location_ratings_user", "location_ratings", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_location_ratings_user", table_name="location_ratings")
    op.drop_index("ix_location_ratings_location_key", table_name="location_ratings")
    op.drop_table("location_ratings")
