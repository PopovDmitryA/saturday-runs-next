"""Площадка объявлена, но ещё не стартовала: отдельный признак «скоро»

До этого «скоро» из реестра 5 вёрст попадало в is_paused и выглядело на карте
как «не действует» — то есть новая площадка, которая вот-вот откроется, стояла
рядом с закрытыми навсегда. Это разные вещи: у одной стартов ещё не было, у
другой они кончились.

Заодно признак закрывает дыру в правиле «нет стартов больше 100 дней»: у
новой площадки последнего старта нет вовсе, и по датам она осталась бы
«активной» — обещанием, которого никто не давал.

Revision ID: 064_location_upcoming
Revises: 063_location_openings
Create Date: 2026-08-20
"""

import sqlalchemy as sa

from alembic import op

revision = "064_location_upcoming"
down_revision = "063_location_openings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "locations",
        sa.Column(
            "is_upcoming",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("locations", "is_upcoming")
