"""Имя волонтёра без профиля на 5 вёрст

В протоколе 5 вёрст волонтёр может быть без личного кабинета: вместо ссылки
на /userstats/<id> стоит «Имя ФАМИЛИЯ (Нужна регистрация)», а иногда и просто
«НЕИЗВЕСТНЫЙ». Парсер такие строки выбрасывал целиком — роль (часто это
организатор) исчезала из протокола, а число волонтёров расходилось со сводкой
сайта ровно на число таких строк: 1033 старта из 32437 на 06.09.2026.

Привязать их к participants нельзя — идентификатора нет, а заводить
синтетических участников значит испортить рейтинги и карьерные счётчики.
Поэтому имя живёт прямо в строке волонтёрства.

Revision ID: 081_volunteer_display_name
Revises: 078_protocol_fact_db_sighting

Номер 081, а не 079: в ветке волонтёрских заявок
(claude/volunteer-signup-flow) уже заняты 079 и 080, и хотя alembic
различает ревизии по id, а не по имени файла, два файла «079_» в одной
папке читать невозможно. Цепочка идёт от 078 — головы прода на 06.09.2026;
той ветке при мерже вторым останется переставить свой down_revision.
Create Date: 2026-09-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "081_volunteer_display_name"
down_revision = "078_protocol_fact_db_sighting"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "volunteer_results",
        sa.Column("display_name", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("volunteer_results", "display_name")
