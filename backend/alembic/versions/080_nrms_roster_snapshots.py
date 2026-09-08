"""Снимки состава волонтёров из NRMS

Когда сайт под сессией организатора читает или пишет состав даты в NRMS,
актуальный список ложится сюда тем же проходом (решение Дмитрия 04.09.2026:
«одним ходить, а другим скриптом выкачивать — глупо»). Снимок — источник
занятости ролей для формы «Хочу волонтёрить» и сверки заявок; открытая запись
5verst.ru остаётся запасным источником и даёт структуру ролей (число мест).

Revision ID: 080_nrms_roster_snapshots
Revises: 079_volunteer_signup_requests
Create Date: 2026-09-04
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "080_nrms_roster_snapshots"
down_revision = "079_volunteer_signup_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nrms_roster_snapshots",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("five_verst_slug", sa.String(length=255), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("nrms_event_id", sa.Integer(), nullable=True),
        sa.Column("status_id", sa.Integer(), nullable=True),
        sa.Column("upload_status_id", sa.Integer(), nullable=True),
        # [{verst_id, role_id, role_name, full_name}] в порядке NRMS.
        sa.Column("entries", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fetched_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["fetched_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("five_verst_slug", "event_date", name="uq_nrms_roster_snapshots_slug_date"),
    )


def downgrade() -> None:
    op.drop_table("nrms_roster_snapshots")
