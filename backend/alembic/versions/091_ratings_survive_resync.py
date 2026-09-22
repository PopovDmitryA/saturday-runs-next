"""Отзыв на локацию переживает удаление строки результата

Аудит 09.2026, находки SCHEMA-01 и SYNC-OTHER-01. Оценка и фото висели на
run_results.id / volunteer_results.id с ON DELETE CASCADE, а синк удаляет
строки результата штатно: перечитка протокола с другим external_result_key,
дедуп профиля, чистка легаси-ключей 5 вёрст, полная пересборка старта у
RunPark при каждом синке участника. Вместе со строкой молча уезжал отзыв
пользователя и его фотографии (файлы в хранилище при этом оставались
сиротами) — ни лога, ни метрики.

Отзыв самодостаточен: в нём уже есть user_id, location_id, event_date,
platform_code и participation_type. Поэтому ссылку на результат делаем
необязательной подсказкой, а не держателем жизни:
  * оба FK → ON DELETE SET NULL;
  * CHECK ослаблен до «не больше одного источника» (раньше требовал ровно один,
    и SET NULL просто уронил бы синк);
  * две частичные уникалки по (человек, строка результата) заменены на
    естественный ключ (человек, локация, дата, тип участия) — он переживает
    смену id строки и не даёт задвоить отзыв после пересинка.

Дубли по естественному ключу миграция НЕ чистит (решение Дмитрия 21.09.2026):
на проде их нет (547 отзывов, 0 групп), а молча удалять чужие отзывы и
переносить фото — не дело миграции. Если дубли всё же найдутся, upgrade
останавливается с понятным сообщением, и их разбирают руками до деплоя.

Revision ID: 091_ratings_survive_resync
Revises: 090_index_cleanup
Create Date: 2026-09-21
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "091_ratings_survive_resync"
down_revision = "090_index_cleanup"
branch_labels = None
depends_on = None

_FIND_DUPLICATES_SQL = """
SELECT user_id, location_id, event_date, participation_type, count(*) AS n
  FROM location_ratings
 GROUP BY user_id, location_id, event_date, participation_type
HAVING count(*) > 1
 ORDER BY n DESC, user_id
"""


def _stop_on_duplicates() -> None:
    """Уникалка по естественному ключу не встанет поверх дублей — лучше
    остановиться до любых правок схемы, чем упасть посреди миграции."""
    rows = op.get_bind().execute(sa.text(_FIND_DUPLICATES_SQL)).fetchall()
    if not rows:
        return
    sample = "; ".join(
        f"user={row.user_id} location={row.location_id} date={row.event_date} "
        f"type={row.participation_type} x{row.n}"
        for row in rows[:10]
    )
    raise RuntimeError(
        f"Миграция 091 остановлена: в location_ratings {len(rows)} групп(ы) дублей по "
        f"(user_id, location_id, event_date, participation_type). Разберите их руками "
        f"(какой отзыв оставить, куда перенести фото) и запустите upgrade снова. "
        f"Первые: {sample}"
    )


def upgrade() -> None:
    _stop_on_duplicates()

    # FK: CASCADE -> SET NULL. Имена разные, потому что волонтёрская ссылка
    # добавлялась отдельной миграцией (039) с явным именем.
    op.drop_constraint("location_ratings_run_result_id_fkey", "location_ratings", type_="foreignkey")
    op.create_foreign_key(
        "location_ratings_run_result_id_fkey",
        "location_ratings",
        "run_results",
        ["run_result_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_constraint("fk_location_ratings_volunteer_result", "location_ratings", type_="foreignkey")
    op.create_foreign_key(
        "fk_location_ratings_volunteer_result",
        "location_ratings",
        "volunteer_results",
        ["volunteer_result_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.drop_constraint("ck_location_ratings_one_source", "location_ratings", type_="check")
    op.create_check_constraint(
        "ck_location_ratings_one_source",
        "location_ratings",
        "NOT (run_result_id IS NOT NULL AND volunteer_result_id IS NOT NULL)",
    )

    op.drop_index("uq_location_ratings_user_run", table_name="location_ratings")
    op.drop_index("uq_location_ratings_user_volunteer", table_name="location_ratings")
    op.create_index(
        "uq_location_ratings_user_location_date_type",
        "location_ratings",
        ["user_id", "location_id", "event_date", "participation_type"],
        unique=True,
    )


def downgrade() -> None:
    # Старая схема отзыв без строки результата выразить не может (CHECK «ровно
    # один источник» + уникалка по строке результата): такие отзывы снимаем,
    # их фото уходят по CASCADE location_rating_photos.rating_id.
    op.execute(
        "DELETE FROM location_ratings "
        "WHERE run_result_id IS NULL AND volunteer_result_id IS NULL"
    )

    op.drop_index("uq_location_ratings_user_location_date_type", table_name="location_ratings")
    op.create_index(
        "uq_location_ratings_user_run",
        "location_ratings",
        ["user_id", "run_result_id"],
        unique=True,
        postgresql_where=sa.text("run_result_id IS NOT NULL"),
    )
    op.create_index(
        "uq_location_ratings_user_volunteer",
        "location_ratings",
        ["user_id", "volunteer_result_id"],
        unique=True,
        postgresql_where=sa.text("volunteer_result_id IS NOT NULL"),
    )

    op.drop_constraint("ck_location_ratings_one_source", "location_ratings", type_="check")
    op.create_check_constraint(
        "ck_location_ratings_one_source",
        "location_ratings",
        "(run_result_id IS NOT NULL) <> (volunteer_result_id IS NOT NULL)",
    )

    op.drop_constraint("fk_location_ratings_volunteer_result", "location_ratings", type_="foreignkey")
    op.create_foreign_key(
        "fk_location_ratings_volunteer_result",
        "location_ratings",
        "volunteer_results",
        ["volunteer_result_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint("location_ratings_run_result_id_fkey", "location_ratings", type_="foreignkey")
    op.create_foreign_key(
        "location_ratings_run_result_id_fkey",
        "location_ratings",
        "run_results",
        ["run_result_id"],
        ["id"],
        ondelete="CASCADE",
    )
