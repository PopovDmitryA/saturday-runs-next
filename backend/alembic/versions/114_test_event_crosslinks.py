"""Тестовый старт не бывает первичной стороной кросслинка и не даёт флагов

Кросслинк связывает один физический старт в двух системах: первичное событие
считается, вторичное — дубль. Если первичное — тестовый старт 5 вёрст (№0),
его не считает никто, а вторичное RunPark везде выбрасывается как дубль — и
настоящий старт пропадает целиком. На проде так было с тремя стартами
RUNtonda Mikhalkovo (21.05, 28.05, 04.06.2022): 47 финишей и 18 волонтёрств
RunPark не попадали в рейтинги, кабинет, достижения и оргкабинет, хотя
единый протокол и главная их показывали (разбор 21.10.2026).

Синк RunPark к тестовому первичному связку уже не создаёт, а синк 5 вёрст с
этой миграции снимает её, если событие позже станет тестовым
(upsert.upsert_event_for_summary). Здесь — разовая уборка того, что есть.

Заодно снимаем флаги «первый финиш» / «первый финиш на локации» / «рекорд» и
метки достижений с результатов тестовых стартов: у 5 вёрст они приходят из
иконок протокола, и одна такая строка (Губайловский Экопарк, 18.06.2022)
засчитывала человеку дебют на тесте. Новые строки теперь пишутся без флагов
(upsert_run_results).

И возвращаем дебюты, которые тест «занял» у 5 вёрст в 2022–2024: 5 вёрст
ставили иконку первого финиша с учётом теста, и первый настоящий старт
оставался без отметки — 45 дебютов в системе и 14 на локации (на 21.10.2026,
с 2025 года таких нет). Правило то же, что у парсера 5 вёрст: дебют в
системе ставит только is_first_run, на локации — is_first_run_at_location.

Revision ID: 114_test_event_crosslinks
Revises: 113_cancellation_places
Create Date: 2026-10-21
"""

from __future__ import annotations

from alembic import op

revision = "114_test_event_crosslinks"
down_revision = "113_cancellation_places"
branch_labels = None
depends_on = None


# Первый настоящий старт 5 вёрст у тех, кто бежал тест раньше него, если
# отметки дебюта у человека (в системе / на этой локации) нет вовсе.
_DEBUT_FIX_CTE = """
WITH runs AS (
    SELECT rr.id, rr.participant_id, e.location_id, e.event_date, e.is_test_event,
           rr.is_first_run, rr.is_first_run_at_location
    FROM run_results rr
    JOIN events e ON e.id = rr.event_id
    JOIN platforms p ON p.id = e.platform_id
    WHERE p.code = 'five_verst' AND rr.finish_time_sec IS NOT NULL
),
with_test AS (SELECT DISTINCT participant_id FROM runs WHERE is_test_event),
real AS (
    SELECT r.*,
           row_number() OVER (PARTITION BY participant_id ORDER BY event_date, id) AS rn_sys,
           row_number() OVER (PARTITION BY participant_id, location_id ORDER BY event_date, id) AS rn_loc
    FROM runs r
    WHERE NOT is_test_event AND participant_id IN (SELECT participant_id FROM with_test)
),
sys_fix AS (
    SELECT r.id FROM real r
    WHERE r.rn_sys = 1 AND NOT r.is_first_run
      AND NOT EXISTS (SELECT 1 FROM real x WHERE x.participant_id = r.participant_id AND x.is_first_run)
      AND EXISTS (
          SELECT 1 FROM runs t
          WHERE t.participant_id = r.participant_id AND t.is_test_event AND t.event_date < r.event_date
      )
),
loc_fix AS (
    SELECT r.id FROM real r
    WHERE r.rn_loc = 1 AND r.rn_sys <> 1 AND NOT r.is_first_run_at_location
      AND NOT EXISTS (
          SELECT 1 FROM real x
          WHERE x.participant_id = r.participant_id AND x.location_id = r.location_id AND x.is_first_run_at_location
      )
      AND EXISTS (
          SELECT 1 FROM runs t
          WHERE t.participant_id = r.participant_id AND t.location_id = r.location_id
            AND t.is_test_event AND t.event_date < r.event_date
      )
)
"""


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM event_crosslinks ec
        USING events e
        WHERE e.id = ec.primary_event_id AND e.is_test_event
        """
    )
    op.execute(
        """
        UPDATE run_results rr
        SET is_pr = false, is_global_pr = false, is_first_run = false, is_first_run_at_location = false,
            achievement_labels = '[]'::jsonb
        FROM events e
        WHERE e.id = rr.event_id
          AND e.is_test_event
          AND (rr.is_pr OR rr.is_global_pr OR rr.is_first_run OR rr.is_first_run_at_location
               OR jsonb_array_length(rr.achievement_labels) > 0)
        """
    )
    op.execute(_DEBUT_FIX_CTE + "UPDATE run_results SET is_first_run = true WHERE id IN (SELECT id FROM sys_fix)")
    op.execute(
        _DEBUT_FIX_CTE + "UPDATE run_results SET is_first_run_at_location = true WHERE id IN (SELECT id FROM loc_fix)"
    )


def downgrade() -> None:
    # Данные не возвращаем: связки к тестовому первичному и флаги на тестах —
    # ошибка, а не состояние, к которому стоит откатываться.
    pass
