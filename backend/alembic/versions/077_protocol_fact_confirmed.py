"""Момент первой выгрузки протокола: подтверждён наблюдением или нет

Наблюдатель писал «протокол замечен сейчас» для каждой пары (локация, дата),
которой ещё нет в таблице, — не различая «увидел, как протокол появился» и
«увидел протокол уже лежащим». На первом же прогоне 02.09.2026 в 21:00 он
разом записал 20 площадок, и все они получили выгрузку задним числом. У
Ставрополя Комсомольского пруда протокол лежал с 29.08 около 9 утра (наш же
синк забрал его в 09:00), а кабинет обвинял организатора в задержке 109 часов.

Флаг отделяет подтверждённое наблюдение от «увидели уже лежащим». Второе для
кабинета равно отсутствию данных: лучше прочерк, чем ложное опоздание.

Revision ID: 077_protocol_fact_confirmed
Revises: 076_user_last_seen
Create Date: 2026-09-03
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "077_protocol_fact_confirmed"
down_revision = "076_user_last_seen"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "protocol_upload_facts",
        sa.Column(
            "first_seen_confirmed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    # Легаси-факты (16 300 строк с 08.2024) писал root-крон, работавший
    # непрерывно два года, — им верим. А все факты источника site на момент
    # этой миграции — это ровно те 20 строк одного холодного старта.
    op.execute(
        "UPDATE protocol_upload_facts SET first_seen_confirmed = false WHERE source = 'site'"
    )


def downgrade() -> None:
    op.drop_column("protocol_upload_facts", "first_seen_confirmed")
