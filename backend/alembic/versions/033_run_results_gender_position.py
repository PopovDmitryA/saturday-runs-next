"""Add gender_position to run_results

Позиция в протоколе среди финишёров того же пола. Считается при синке
протокола (5 вёрст, S95, RunPark); для parkrun и строк без данных о поле — NULL.

Revision ID: 033_run_results_gender_position
Revises: 032_sync_watermarks
Create Date: 2026-07-02
"""

import sqlalchemy as sa
from alembic import op

revision = "033_run_results_gender_position"
down_revision = "032_sync_watermarks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("run_results", sa.Column("gender_position", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("run_results", "gender_position")
