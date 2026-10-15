"""Ручной статус локации и раздельные источники паузы

Статус площадки считается сам: реестр системы (пауза, отмена, «скоро») и
правило молчания — без стартов дольше 100 дней значит «не действует». Обоим
бывает нечего сказать: 5 вёрст перенесли Томск Сосновый Бор в Михайловскую
рощу (октябрь 2026), реестр паузы не ставит, а до ста тихих дней ещё три
месяца. Ручной статус ставит админ, и он сильнее и реестра, и правила:
  * NULL — как раньше, статус считается сам;
  * 'inactive' — «не действует»;
  * 'active' — «действует», даже если правило молчания решило бы иначе.
status_override_at — когда статус поставили: «не действует» снимается само,
если после этого площадка провела старт (решение Дмитрия 16.10.2026).

registry_paused — пауза по мнению реестра системы, отдельно от итогового
is_paused. Раньше оба источника писали в один флаг: синк реестра 5 вёрст в
20:50 снимал паузу, которую правило молчания ставило в 21:10, и двадцать
минут каждый вечер долго молчащая площадка числилась «действующей». Теперь
is_paused = ручной статус, иначе (registry_paused или молчание > 100 дней).
Начальное значение: пауза строк 5 вёрст и s95, которую не объясняет молчание,
— то есть поставленная реестром.

Revision ID: 110_location_status_override
Revises: 109_backlog_author_set_null
Create Date: 2026-10-15
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "110_location_status_override"
down_revision = "109_backlog_author_set_null"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("locations", sa.Column("status_override", sa.String(length=16), nullable=True))
    op.add_column("locations", sa.Column("status_override_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "locations",
        sa.Column("registry_paused", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.execute(
        """
        UPDATE locations l
        SET registry_paused = true
        FROM platforms p
        WHERE p.id = l.platform_id
          AND p.code IN ('five_verst', 's95')
          AND l.is_paused
          AND COALESCE(
                (SELECT max(s.event_date) FROM event_summaries s WHERE s.location_id = l.id),
                current_date
              ) >= current_date - 100
        """
    )
    op.create_check_constraint(
        "ck_locations_status_override",
        "locations",
        "status_override IS NULL OR status_override IN ('inactive', 'active')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_locations_status_override", "locations", type_="check")
    op.drop_column("locations", "registry_paused")
    op.drop_column("locations", "status_override_at")
    op.drop_column("locations", "status_override")
