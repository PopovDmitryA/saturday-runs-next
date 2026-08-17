"""Ручные гранты доступа к кабинету организатора локации

Кабинет организатора открыт двум кругам людей: тем, кто по данным протоколов
хоть раз волонтёрил на локации в роли организатора (канонический ключ
run_director из volunteer_role_taxonomy — «Организатор» у 5 вёрст,
«Руководитель» у RunPark, «Run Director» у parkrun, «Директор» у С95), и тем,
кому админ выдал доступ руками. Автодоступ по волонтёрствам вычисляется на
лету и здесь НЕ материализуется — в таблице живут только ручные гранты.

Локация задаётся каноническим identity key каталога (та же строка, что в
users.home_location_key и location_ratings.location_key), а не location_id:
грант должен покрывать все платформы одной физической точки сразу.

Revision ID: 064_location_organizer_access
Revises: 063_location_openings
Create Date: 2026-08-16
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "064_location_organizer_access"
down_revision = "063_location_openings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "location_organizer_access",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("location_key", sa.String(length=255), nullable=False),
        sa.Column("granted_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["granted_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "location_key", name="uq_location_organizer_access_user_location"
        ),
    )
    op.create_index(
        "ix_location_organizer_access_user_id",
        "location_organizer_access",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_location_organizer_access_user_id", table_name="location_organizer_access"
    )
    op.drop_table("location_organizer_access")
