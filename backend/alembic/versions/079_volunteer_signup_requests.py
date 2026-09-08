"""Заявки на волонтёрство

Участник на странице локации выбирает дату и роль — заявка ложится сюда и
уходит организатору в Telegram. Организатор подтверждает или отклоняет её в
кабинете; при подтверждении сайт вносит человека в NRMS (систему записи
волонтёров 5 вёрст) под учёткой самого организатора — у участника доступа
туда нет, а у оргкоманды есть. NRMS остаётся учётной системой, у нас —
«приёмная» заявок и журнал решений.

Локация задаётся каноническим identity key каталога (как в
location_organizer_access), verst ID участника снимается в момент заявки —
организатор ищет человека в NRMS по нему, а не по имени: у «Дмитрия Попова»
в системе больше восьми тёзок.

Revision ID: 079_volunteer_signup_requests
Revises: 078_protocol_fact_db_sighting
Create Date: 2026-09-04
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "079_volunteer_signup_requests"
down_revision = "078_protocol_fact_db_sighting"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "volunteer_signup_requests",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("location_key", sa.String(length=255), nullable=False),
        sa.Column("five_verst_slug", sa.String(length=255), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("role_name", sa.String(length=128), nullable=False),
        sa.Column("verst_id", sa.String(length=32), nullable=False),
        sa.Column("participant_name", sa.String(length=256), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        # pending → confirmed | declined | cancelled (участник отозвал сам).
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        # Запись в NRMS: none — не пробовали, saved — внесён, failed — ошибка,
        # manual — организатор внёс руками (сайт увидел имя в открытой записи).
        sa.Column("nrms_status", sa.String(length=16), nullable=False, server_default="none"),
        sa.Column("nrms_error", sa.Text(), nullable=True),
        sa.Column("nrms_saved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("organizer_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["decided_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_volunteer_signup_requests_location_status",
        "volunteer_signup_requests",
        ["location_key", "status", "event_date"],
    )
    op.create_index(
        "ix_volunteer_signup_requests_user_id",
        "volunteer_signup_requests",
        ["user_id"],
    )
    # Одна живая заявка на человека, дату и роль: повторный клик не плодит дубли.
    op.create_index(
        "uq_volunteer_signup_requests_open",
        "volunteer_signup_requests",
        ["user_id", "location_key", "event_date", "role_name"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'confirmed')"),
    )


def downgrade() -> None:
    op.drop_index("uq_volunteer_signup_requests_open", table_name="volunteer_signup_requests")
    op.drop_index("ix_volunteer_signup_requests_user_id", table_name="volunteer_signup_requests")
    op.drop_index("ix_volunteer_signup_requests_location_status", table_name="volunteer_signup_requests")
    op.drop_table("volunteer_signup_requests")
