-- RunPark → Saturday Runs: контракт read-only view
-- Версия: 1.0 (2026-05-27)
--
-- RunPark: замените тело каждой view на SELECT из ваших таблиц,
-- сохранив имена и типы колонок. Файл database_views.md — описание полей.
--
-- Шаблоны ниже создают view с правильной схемой и нулём строк (LIMIT 0).
-- После подключения ваших таблиц удалите LIMIT 0 и подставьте реальные SELECT.

CREATE SCHEMA IF NOT EXISTS runpark_export;

COMMENT ON SCHEMA runpark_export IS
  'Read-only export views for Saturday Runs integration. Contract v1.0';


-- ---------------------------------------------------------------------------
-- 1. Локации
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW runpark_export.vw_locations AS
SELECT
    NULL::text              AS location_id,
    NULL::text              AS name,
    NULL::text              AS city,
    NULL::text              AS region,
    NULL::text              AS country,
    NULL::double precision  AS latitude,
    NULL::double precision  AS longitude,
    false                   AS is_active,
    false                   AS is_paused,
    NULL::text              AS public_url,
    NULL::text              AS map_url,
    NULL::text              AS legacy_parkrun_slug,
    NULL::text              AS legacy_five_verst_slug,
    NULL::text              AS legacy_s95_slug,
    NULL::timestamptz       AS updated_at,
    false                   AS is_deleted
WHERE false;

COMMENT ON VIEW runpark_export.vw_locations IS
  'Справочник площадок RunPark. PK: location_id';

COMMENT ON COLUMN runpark_export.vw_locations.location_id IS 'Стабильный slug или UUID (text). Не меняется при переименовании.';
COMMENT ON COLUMN runpark_export.vw_locations.name IS 'Название площадки для отображения.';
COMMENT ON COLUMN runpark_export.vw_locations.city IS 'Город.';
COMMENT ON COLUMN runpark_export.vw_locations.region IS 'Регион / субъект.';
COMMENT ON COLUMN runpark_export.vw_locations.country IS 'Страна, напр. RU.';
COMMENT ON COLUMN runpark_export.vw_locations.latitude IS 'WGS-84, широта.';
COMMENT ON COLUMN runpark_export.vw_locations.longitude IS 'WGS-84, долгота.';
COMMENT ON COLUMN runpark_export.vw_locations.is_active IS 'Локация активна в системе.';
COMMENT ON COLUMN runpark_export.vw_locations.is_paused IS 'Забеги временно не проводятся.';
COMMENT ON COLUMN runpark_export.vw_locations.public_url IS 'Публичная страница локации.';
COMMENT ON COLUMN runpark_export.vw_locations.map_url IS 'URL карты маршрута.';
COMMENT ON COLUMN runpark_export.vw_locations.legacy_parkrun_slug IS 'Slug parkrun для склейки с другими системами.';
COMMENT ON COLUMN runpark_export.vw_locations.legacy_five_verst_slug IS 'Slug 5 вёрст для склейки.';
COMMENT ON COLUMN runpark_export.vw_locations.legacy_s95_slug IS 'Slug С95 для склейки.';
COMMENT ON COLUMN runpark_export.vw_locations.updated_at IS 'Время последнего изменения (для инкрементального синка).';
COMMENT ON COLUMN runpark_export.vw_locations.is_deleted IS 'Мягкое удаление; true = не отдавать в Saturday Runs.';


-- ---------------------------------------------------------------------------
-- 2. Участники
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW runpark_export.vw_participants AS
SELECT
    NULL::text        AS participant_id,
    NULL::text        AS display_name,
    NULL::text        AS profile_url,
    NULL::integer     AS total_runs,
    NULL::integer     AS total_volunteering,
    NULL::text        AS club_name,
    NULL::text        AS barcode_id,
    NULL::text        AS planning_location_id,
    NULL::text        AS age_category,
    NULL::timestamptz AS updated_at,
    false             AS is_deleted
WHERE false;

COMMENT ON VIEW runpark_export.vw_participants IS
  'Профили участников. PK: participant_id. ID используется при привязке в Saturday Runs.';

COMMENT ON COLUMN runpark_export.vw_participants.participant_id IS 'Стабильный ID участника в RunPark.';
COMMENT ON COLUMN runpark_export.vw_participants.display_name IS 'Имя в протоколах.';
COMMENT ON COLUMN runpark_export.vw_participants.profile_url IS 'Публичный URL профиля.';
COMMENT ON COLUMN runpark_export.vw_participants.total_runs IS 'Суммарное число финишей (если есть в системе).';
COMMENT ON COLUMN runpark_export.vw_participants.total_volunteering IS 'Суммарное число волонтёрств.';
COMMENT ON COLUMN runpark_export.vw_participants.club_name IS 'Клуб / команда.';
COMMENT ON COLUMN runpark_export.vw_participants.barcode_id IS 'Штрихкод / номер.';
COMMENT ON COLUMN runpark_export.vw_participants.planning_location_id IS 'FK → vw_locations.location_id, домашняя площадка.';
COMMENT ON COLUMN runpark_export.vw_participants.age_category IS 'Возрастная группа.';
COMMENT ON COLUMN runpark_export.vw_participants.updated_at IS 'Время последнего изменения.';
COMMENT ON COLUMN runpark_export.vw_participants.is_deleted IS 'Мягкое удаление.';


-- ---------------------------------------------------------------------------
-- 3. События (забеги)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW runpark_export.vw_events AS
SELECT
    NULL::text        AS event_id,
    NULL::text        AS location_id,
    NULL::date        AS event_date,
    NULL::integer     AS event_number,
    NULL::text        AS title,
    false             AS is_test_event,
    NULL::integer     AS finishers_count,
    NULL::text        AS public_url,
    NULL::timestamptz AS updated_at,
    false             AS is_deleted
WHERE false;

COMMENT ON VIEW runpark_export.vw_events IS
  'События (забеги). PK: event_id. FK: location_id → vw_locations.';

COMMENT ON COLUMN runpark_export.vw_events.event_id IS 'Стабильный ID события.';
COMMENT ON COLUMN runpark_export.vw_events.location_id IS 'FK → vw_locations.location_id.';
COMMENT ON COLUMN runpark_export.vw_events.event_date IS 'Календарная дата забега.';
COMMENT ON COLUMN runpark_export.vw_events.event_number IS 'Номер забега на площадке (#N).';
COMMENT ON COLUMN runpark_export.vw_events.title IS 'Заголовок события.';
COMMENT ON COLUMN runpark_export.vw_events.is_test_event IS 'Тестовое мероприятие — скрывается в UI по умолчанию.';
COMMENT ON COLUMN runpark_export.vw_events.finishers_count IS 'Число финишировавших.';
COMMENT ON COLUMN runpark_export.vw_events.public_url IS 'URL протокола / страницы события.';
COMMENT ON COLUMN runpark_export.vw_events.updated_at IS 'Время последнего изменения.';
COMMENT ON COLUMN runpark_export.vw_events.is_deleted IS 'Мягкое удаление.';


-- ---------------------------------------------------------------------------
-- 4. Результаты пробежек
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW runpark_export.vw_run_results AS
SELECT
    NULL::text        AS result_id,
    NULL::text        AS event_id,
    NULL::text        AS participant_id,
    NULL::date        AS event_date,
    NULL::text        AS location_id,
    NULL::text        AS location_name,
    NULL::integer     AS event_number,
    NULL::integer     AS position,
    NULL::integer     AS finish_time_sec,
    NULL::text        AS finish_time_display,
    NULL::integer     AS pace_sec_per_km,
    NULL::text        AS age_category,
    NULL::text        AS status,
    false             AS is_pr,
    false             AS is_test_event,
    NULL::timestamptz AS updated_at,
    false             AS is_deleted
WHERE false;

COMMENT ON VIEW runpark_export.vw_run_results IS
  'Результаты пробежек. PK: result_id. Уникальность альтернатива: (event_id, participant_id).';

COMMENT ON COLUMN runpark_export.vw_run_results.result_id IS 'Уникальный ID результата.';
COMMENT ON COLUMN runpark_export.vw_run_results.event_id IS 'FK → vw_events.event_id.';
COMMENT ON COLUMN runpark_export.vw_run_results.participant_id IS 'FK → vw_participants.participant_id.';
COMMENT ON COLUMN runpark_export.vw_run_results.event_date IS 'Дата забега (денормализация для выборок по пользователю).';
COMMENT ON COLUMN runpark_export.vw_run_results.location_id IS 'FK → vw_locations.location_id.';
COMMENT ON COLUMN runpark_export.vw_run_results.location_name IS 'Название локации без JOIN.';
COMMENT ON COLUMN runpark_export.vw_run_results.event_number IS 'Номер забега на площадке.';
COMMENT ON COLUMN runpark_export.vw_run_results.position IS 'Место в протоколе.';
COMMENT ON COLUMN runpark_export.vw_run_results.finish_time_sec IS 'Время финиша в секундах (предпочтительно).';
COMMENT ON COLUMN runpark_export.vw_run_results.finish_time_display IS 'Время строкой MM:SS или HH:MM:SS.';
COMMENT ON COLUMN runpark_export.vw_run_results.pace_sec_per_km IS 'Темп, секунд на километр.';
COMMENT ON COLUMN runpark_export.vw_run_results.age_category IS 'Возрастная группа на этом забеге.';
COMMENT ON COLUMN runpark_export.vw_run_results.status IS 'finished | DNF | DNS | …';
COMMENT ON COLUMN runpark_export.vw_run_results.is_pr IS 'Личный рекорд.';
COMMENT ON COLUMN runpark_export.vw_run_results.is_test_event IS 'Признак тестового события.';
COMMENT ON COLUMN runpark_export.vw_run_results.updated_at IS 'Время последнего изменения.';
COMMENT ON COLUMN runpark_export.vw_run_results.is_deleted IS 'Мягкое удаление.';


-- ---------------------------------------------------------------------------
-- 5. Волонтёрство
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW runpark_export.vw_volunteer_results AS
SELECT
    NULL::text        AS volunteer_id,
    NULL::text        AS event_id,
    NULL::text        AS participant_id,
    NULL::text        AS participant_name,
    NULL::date        AS event_date,
    NULL::text        AS location_id,
    NULL::text        AS location_name,
    NULL::integer     AS event_number,
    NULL::text        AS role,
    false             AS is_test_event,
    NULL::timestamptz AS updated_at,
    false             AS is_deleted
WHERE false;

COMMENT ON VIEW runpark_export.vw_volunteer_results IS
  'Волонтёрство. PK: volunteer_id. Уникальность альтернатива: (event_id, participant_id, role).';

COMMENT ON COLUMN runpark_export.vw_volunteer_results.volunteer_id IS 'Уникальный ID записи волонтёра.';
COMMENT ON COLUMN runpark_export.vw_volunteer_results.event_id IS 'FK → vw_events.event_id.';
COMMENT ON COLUMN runpark_export.vw_volunteer_results.participant_id IS 'FK → participant; NULL если только имя.';
COMMENT ON COLUMN runpark_export.vw_volunteer_results.participant_name IS 'Имя, если participant_id отсутствует.';
COMMENT ON COLUMN runpark_export.vw_volunteer_results.event_date IS 'Дата события.';
COMMENT ON COLUMN runpark_export.vw_volunteer_results.location_id IS 'FK → vw_locations.location_id.';
COMMENT ON COLUMN runpark_export.vw_volunteer_results.location_name IS 'Название локации без JOIN.';
COMMENT ON COLUMN runpark_export.vw_volunteer_results.event_number IS 'Номер забега на площадке.';
COMMENT ON COLUMN runpark_export.vw_volunteer_results.role IS 'Роль как в UI RunPark (Судья, Organizer, …).';
COMMENT ON COLUMN runpark_export.vw_volunteer_results.is_test_event IS 'Признак тестового события.';
COMMENT ON COLUMN runpark_export.vw_volunteer_results.updated_at IS 'Время последнего изменения.';
COMMENT ON COLUMN runpark_export.vw_volunteer_results.is_deleted IS 'Мягкое удаление.';


-- ---------------------------------------------------------------------------
-- 6. Сводки по событиям (опционально)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW runpark_export.vw_event_summaries AS
SELECT
    NULL::text        AS event_id,
    NULL::integer     AS finishers_count,
    NULL::integer     AS volunteers_count,
    NULL::integer     AS avg_time_sec,
    NULL::text        AS avg_time_display,
    NULL::integer     AS best_male_time_sec,
    NULL::text        AS best_male_time_display,
    NULL::integer     AS best_female_time_sec,
    NULL::text        AS best_female_time_display,
    NULL::timestamptz AS updated_at
WHERE false;

COMMENT ON VIEW runpark_export.vw_event_summaries IS
  'Опционально: агрегаты по событию. PK: event_id. Можно не реализовывать на старте.';

COMMENT ON COLUMN runpark_export.vw_event_summaries.event_id IS 'FK → vw_events.event_id.';
COMMENT ON COLUMN runpark_export.vw_event_summaries.finishers_count IS 'Число финишировавших.';
COMMENT ON COLUMN runpark_export.vw_event_summaries.volunteers_count IS 'Число волонтёров.';
COMMENT ON COLUMN runpark_export.vw_event_summaries.avg_time_sec IS 'Среднее время, секунды.';
COMMENT ON COLUMN runpark_export.vw_event_summaries.avg_time_display IS 'Среднее время, строка.';
COMMENT ON COLUMN runpark_export.vw_event_summaries.best_male_time_sec IS 'Лучшее мужское время, секунды.';
COMMENT ON COLUMN runpark_export.vw_event_summaries.best_male_time_display IS 'Лучшее мужское время, строка.';
COMMENT ON COLUMN runpark_export.vw_event_summaries.best_female_time_sec IS 'Лучшее женское время, секунды.';
COMMENT ON COLUMN runpark_export.vw_event_summaries.best_female_time_display IS 'Лучшее женское время, строка.';
COMMENT ON COLUMN runpark_export.vw_event_summaries.updated_at IS 'Время последнего изменения.';


-- ---------------------------------------------------------------------------
-- Пример реализации (RunPark заполняет по аналогии)
-- ---------------------------------------------------------------------------
/*
CREATE OR REPLACE VIEW runpark_export.vw_run_results AS
SELECT
    r.id::text                    AS result_id,
    r.event_id::text              AS event_id,
    r.user_id::text               AS participant_id,
    e.event_date                  AS event_date,
    e.location_id::text           AS location_id,
    l.name                        AS location_name,
    e.number                      AS event_number,
    r.position                    AS position,
    r.finish_seconds              AS finish_time_sec,
    to_char(make_interval(secs => r.finish_seconds), 'MI:SS') AS finish_time_display,
    r.pace_sec_per_km             AS pace_sec_per_km,
    r.age_group                   AS age_category,
    r.status                      AS status,
    coalesce(r.is_personal_best, false) AS is_pr,
    coalesce(e.is_test, false)    AS is_test_event,
    r.updated_at                  AS updated_at,
    coalesce(r.deleted, false)    AS is_deleted
FROM runpark.results r
JOIN runpark.events e ON e.id = r.event_id
JOIN runpark.locations l ON l.id = e.location_id
WHERE e.published = true;
*/


-- ---------------------------------------------------------------------------
-- Права доступа (RunPark настраивает на своей стороне)
-- ---------------------------------------------------------------------------
/*
CREATE ROLE saturday_runs_reader LOGIN PASSWORD '…';
GRANT USAGE ON SCHEMA runpark_export TO saturday_runs_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA runpark_export TO saturday_runs_reader;
-- для будущих view:
ALTER DEFAULT PRIVILEGES IN SCHEMA runpark_export
  GRANT SELECT ON TABLES TO saturday_runs_reader;
*/
