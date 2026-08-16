"""Открытие локации: какой старт считать торжественным открытием (ручная разметка)

Рейтинг открытий считает участие в торжественном открытии локации. У 5 вёрст,
parkrun и RunPark номер такого старта известен из протокола — это событие №1.
У С95 по номерам забегов открытие не опознать, поэтому там номер проставляется
руками через админку — до этого открытий у локации нет.

Строка на локацию платформы, а не на физическую точку: одна и та же локация
открывалась в parkrun и в 5 вёрст по своим номерам, и размечать их надо
отдельно. В зачёт рейтинга идёт только самое раннее из таких открытий — парк
открывается один раз (правило добавлено 16.08.2026, сама таблица не менялась).

`opening_event_number IS NULL` при существующей строке — не «не знаем», а
осознанное «открытия у этой локации нет»: так гасится ложное открытие там, где
система начала вести протоколы позже самой локации.

Revision ID: 063_location_openings
Revises: 062_location_descriptions
Create Date: 2026-08-14
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "063_location_openings"
down_revision = "062_location_descriptions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "location_openings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("opening_event_number", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("updated_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
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
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("location_id", name="uq_location_openings_location_id"),
    )


def downgrade() -> None:
    op.drop_table("location_openings")
