"""Старт сообщества — не площадка

5 вёрст держит отдельный раздел /starti-soobshchestv/: разовые старты вроде
«Зелёные 5 км» и «День физкультурника. Тула». В личный счётчик финишей сайт их
засчитывает, поэтому без них наши числа расходятся с источником ровно на
единицу у каждого, кто там бежал (522 человека на 07.09.2026, 683 финиша).

Собирать их надо, но площадками они не являются: у них нет расписания,
координат и второго старта. Флаг держит их вне каталога, карты, туризма и
рейтингов по локациям, оставляя в личных итогах и в протоколах.

Revision ID: 084_location_community_event
Revises: 083_start_weather
Create Date: 2026-09-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "084_location_community_event"
down_revision = "083_start_weather"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "locations",
        sa.Column(
            "is_community_event",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("locations", "is_community_event")
