"""Расписка «протокол соответствует вот такому саммари»

Триггер «саммари изменилось → перечитать протокол» жил ровно один прогон.
Синк сначала коммитил новый summary_hash по всем площадкам, и только потом
шёл качать протоколы. Не дошёл до элемента — упал, поймал бан-кулдаун,
упёрся в protocol_fetch_limit — и следующий прогон уже видит «unchanged»:
расхождение застывает навсегда и ничем не лечится.

Так 22.08.2026 Серов (parkdkm) три дня показывал победителя с 00:15:04,
хотя площадка поправила протокол в тот же день: прогон five_verst:latest
успел записать новое саммари в 17:00:03 и упал в 17:04:26 на
IdleInTransactionSessionTimeout, не дойдя до протокола Серова в очереди.

summary_hash_at_fetch — отметка, которую ставит только успешная закачка
протокола. Пока она не равна текущему EventSummary.summary_hash, за
площадкой висит долг, и его видит любой следующий прогон.

Бэкфилл: у существующих строк считаем протокол соответствующим саммари.
Сверка по всем 5 вёрстам на момент миграции нашла ровно 5 живых расхождений
времён (parkdkm 22.08.2026 и четыре старых gubernskypark), они лечатся
адресной перекачкой, а не бэкфиллом — иначе в долг разом ушли бы 32 тысячи
протоколов.

Revision ID: 067_protocol_debt
Revises: 066_display_name_from_profile
Create Date: 2026-08-25
"""

import sqlalchemy as sa

from alembic import op

revision = "067_protocol_debt"
down_revision = "066_display_name_from_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "protocol_sync_states",
        sa.Column("summary_hash_at_fetch", sa.String(length=64), nullable=True),
    )
    op.execute(
        """
        UPDATE protocol_sync_states AS ps
        SET summary_hash_at_fetch = es.summary_hash
        FROM event_summaries AS es
        WHERE es.event_id = ps.event_id
          AND ps.last_protocol_fetched_at IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_column("protocol_sync_states", "summary_hash_at_fetch")
