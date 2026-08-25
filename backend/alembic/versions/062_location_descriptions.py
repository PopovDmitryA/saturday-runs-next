"""Описания локаций: когда старт, трасса и как добраться (5 вёрст, S95)

Тексты со страниц систем («Где и когда?» и «О трассе» на 5verst.ru и карточки
«Общая информация» / «Наши контакты» на s95.ru/events/{slug}) хранятся у нас,
чтобы страница локации перестала быть голой таблицей цифр: человеку нужно
знать, где старт и как до него доехать, а поиску — связный текст про парк.

Одна строка на локацию платформы: у идентичности их бывает несколько
(5 вёрст и S95 на одной площадке), и описания у них разные.

fetched_at (когда смотрели) и content_updated_at + revision (когда и сколько
раз текст менялся) — разные вещи: по строке должно быть видно «проверяли час
назад, а поменялось в марте», а не просто «что-то происходило».

Revision ID: 062_location_descriptions
Revises: 061_home_loc_changed_at
Create Date: 2026-08-11
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "062_location_descriptions"
down_revision = "061_home_loc_changed_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "location_descriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("schedule_text", sa.Text(), nullable=True),
        sa.Column("course_text", sa.Text(), nullable=True),
        sa.Column("travel_text", sa.Text(), nullable=True),
        sa.Column(
            "travel_sections",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "links",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("source_url", sa.String(length=1024), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("location_id", name="uq_location_descriptions_location_id"),
    )


def downgrade() -> None:
    op.drop_table("location_descriptions")
