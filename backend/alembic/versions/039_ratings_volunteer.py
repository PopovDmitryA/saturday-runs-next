"""Оценка стартов для волонтёров + тип участия (бегал / волонтёрил)

До этого оценку можно было поставить только на конкретную пробежку (run_result).
Волонтёр не бежит — у него нет run_result, поэтому он не мог оценить старт.

Теперь оценка привязана либо к run_result (бегун), либо к volunteer_result
(волонтёр), а тип фиксируется в `participation_type` ('run' | 'volunteer').
Ровно один из двух FK заполнен (CHECK), уникальность — двумя частичными
индексами.

Revision ID: 039_ratings_volunteer
Revises: 037_users_public_slug
Create Date: 2026-07-10
"""

import sqlalchemy as sa

from alembic import op

revision = "039_ratings_volunteer"
down_revision = "037_users_public_slug"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Тип участия: по умолчанию 'run' — все существующие оценки от бегунов.
    op.add_column(
        "location_ratings",
        sa.Column(
            "participation_type",
            sa.String(length=16),
            nullable=False,
            server_default="run",
        ),
    )
    op.add_column(
        "location_ratings",
        sa.Column(
            "volunteer_result_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_location_ratings_volunteer_result",
        "location_ratings",
        "volunteer_results",
        ["volunteer_result_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # run_result_id теперь может быть NULL (для волонтёрских оценок).
    op.alter_column("location_ratings", "run_result_id", nullable=True)

    # Старый общий unique заменяем двумя частичными (по типу участия).
    op.drop_constraint("uq_location_ratings_user_run", "location_ratings", type_="unique")
    op.create_index(
        "uq_location_ratings_user_run",
        "location_ratings",
        ["user_id", "run_result_id"],
        unique=True,
        postgresql_where=sa.text("run_result_id IS NOT NULL"),
    )
    op.create_index(
        "uq_location_ratings_user_volunteer",
        "location_ratings",
        ["user_id", "volunteer_result_id"],
        unique=True,
        postgresql_where=sa.text("volunteer_result_id IS NOT NULL"),
    )

    # Ровно один из двух FK заполнен.
    op.create_check_constraint(
        "ck_location_ratings_one_source",
        "location_ratings",
        "(run_result_id IS NOT NULL) <> (volunteer_result_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_location_ratings_one_source", "location_ratings", type_="check")
    op.drop_index("uq_location_ratings_user_volunteer", table_name="location_ratings")
    op.drop_index("uq_location_ratings_user_run", table_name="location_ratings")
    # Волонтёрские оценки несовместимы со старой NOT NULL схемой — удаляем их.
    op.execute("DELETE FROM location_ratings WHERE run_result_id IS NULL")
    op.alter_column("location_ratings", "run_result_id", nullable=False)
    op.create_unique_constraint(
        "uq_location_ratings_user_run",
        "location_ratings",
        ["user_id", "run_result_id"],
    )
    op.drop_constraint(
        "fk_location_ratings_volunteer_result", "location_ratings", type_="foreignkey"
    )
    op.drop_column("location_ratings", "volunteer_result_id")
    op.drop_column("location_ratings", "participation_type")
