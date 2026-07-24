"""participants.gender — материализация пола вместо разбора age_category на чтении

Агрегаты главной портала считали пол выражением
`CASE ... substr(participants.age_category, 1, 1) ...` по всем строкам джойна
(2.2 млн run_results). Выражение неиндексируемое → полный перебор + сортировки
мимо work_mem: один прогрев кэша главной стоил ~59 сек времени БД и ~300 МБ
временных файлов. Теперь пол пишется при апсерте участника
(gender_position_service.resolve_participant_gender).

Миграция намеренно делает только DDL и **не бэкфиллит**: alembic гоняет всю
ревизию одной транзакцией, а ADD COLUMN берёт ACCESS EXCLUSIVE на participants
и держит его до COMMIT. С бэкфиллом внутри (три UPDATE по 323к строк) любой
запрос к participants — то есть весь сайт — вставал бы на всё это время.
Проверено локально: SELECT воркера повис на Lock:relation.

Данные заливает отдельный батчевый скрипт (свои коммиты, сайт живой):

    docker compose exec api python scripts/backfill_participant_gender.py

До бэкфилла колонка пустая, и агрегаты главной покажут всех как «пол неизвестен»
— поэтому скрипт надо прогнать сразу после миграции.

Revision ID: 053_participant_gender
Revises: 052_backlog
Create Date: 2026-07-24
"""

import sqlalchemy as sa

from alembic import op

revision = "053_participant_gender"
down_revision = "052_backlog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ADD COLUMN без DEFAULT — в PG11+ это правка каталога, таблица не переписывается.
    op.add_column("participants", sa.Column("gender", sa.String(length=8), nullable=True))
    # Частичный индекс: NULL (пол неизвестен) в агрегатах не участвует.
    # На пустой колонке строится мгновенно — потому и создаём до бэкфилла.
    op.create_index(
        "ix_participants_gender",
        "participants",
        ["gender"],
        postgresql_where=sa.text("gender IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_participants_gender", table_name="participants")
    op.drop_column("participants", "gender")
