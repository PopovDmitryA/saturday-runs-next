"""Add is_global_pr and is_location_pr to run_results.

Личный рекорд теперь трёхуровневый: is_pr (внутри одной платформы, было),
is_global_pr (среди всех платформ — раньше вычислялся на лету, теперь
хранимое поле), is_location_pr (внутри одной физической локации across
платформ — новый уровень). Оба новых поля пересчитываются батчем через
personal_record_service.recalculate_cross_platform_personal_records и
требуют backfill (scripts/recalculate_cross_platform_personal_records.py)
после применения миграции.

Revision ID: 046_run_result_pr_levels
Revises: 045_user_goals
Create Date: 2026-07-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "046_run_result_pr_levels"
down_revision = "045_user_goals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "run_results",
        sa.Column("is_global_pr", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "run_results",
        sa.Column("is_location_pr", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_index("ix_platform_links_participant_id", "platform_links", ["participant_id"])


def downgrade() -> None:
    op.drop_index("ix_platform_links_participant_id", table_name="platform_links")
    op.drop_column("run_results", "is_location_pr")
    op.drop_column("run_results", "is_global_pr")
