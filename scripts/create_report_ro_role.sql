-- Idempotent creation of a read-only role for the internal reporting API
-- (/api/internal/reports/*). Безопасно запускать повторно.
--
-- Запускать под суперпользователем: на VPS это postgres, в контейнерной базе
-- (дом, dev) суперпользователь — сам saturday_runs. Файл подаётся на stdin,
-- чтобы его не нужно было класть туда, где работает psql.
--
-- Создать роль или сменить пароль — пароль НЕ хранится в файле, передаётся
-- psql-переменной (тот же, что в REPORT_DATABASE_URL):
--
--   docker compose exec -T postgres psql -U saturday_runs -d saturday_runs_lk \
--        -v ON_ERROR_STOP=1 -v report_password="$REPORT_RO_PASSWORD" \
--        -f - < scripts/create_report_ro_role.sql
--
-- Только досинхронизировать права (роль уже есть, пароль не трогаем):
--
--   sudo -u postgres psql -d saturday_runs_lk -v ON_ERROR_STOP=1 \
--        -f - < scripts/create_report_ro_role.sql

\set ON_ERROR_STOP on

-- Одной транзакцией: между выдачей SELECT на всё и отзывом с закрытых таблиц
-- (шаги 3 и 5) report_ro не должен успеть их прочитать.
BEGIN;

-- 1) Роль и пароль — только если пароль передан. Без него роль должна уже
-- существовать, иначе шаг 2 остановит скрипт.
\if :{?report_password}
\else
\set report_password ''
\endif
SELECT :'report_password' <> '' AS set_password \gset
\if :set_password
SELECT format('CREATE ROLE report_ro LOGIN PASSWORD %L', :'report_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'report_ro')
\gexec
ALTER ROLE report_ro PASSWORD :'report_password';
\else
\echo 'report_password не задан: роль и пароль не трогаю, только права.'
\endif

-- 2) Гарантировать отсутствие лишних атрибутов.
ALTER ROLE report_ro WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
     NOREPLICATION;

-- 3) Доступ к текущей БД и схеме public (read-only).
SELECT format('GRANT CONNECT ON DATABASE %I TO report_ro', current_database())
\gexec

GRANT USAGE ON SCHEMA public TO report_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO report_ro;

-- 4) SELECT и на будущие таблицы. Права по умолчанию действуют только на
-- объекты, которые создаёт роль из FOR ROLE (без него — тот, кто запустил
-- скрипт). Таблицы создают миграции, поэтому FOR ROLE — владелец
-- alembic_version (на пустой базе — владелец БД). 18.07.2026 скрипт
-- запустили под postgres, права по умолчанию достались ему, и все таблицы
-- миграций 051+ (26 штук к 24.09.2026) остались для report_ro закрыты.
SELECT coalesce(
         (SELECT tableowner FROM pg_tables
           WHERE schemaname = 'public' AND tablename = 'alembic_version'),
         (SELECT pg_get_userbyid(datdba) FROM pg_database
           WHERE datname = current_database())
       ) AS table_owner
\gset
SELECT format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public '
              'GRANT SELECT ON TABLES TO report_ro', :'table_owner')
\gexec

-- 5) Закрытые таблицы (решено 24.09.2026): персональные данные и
-- то, что работает как пароль. Новая таблица из миграции откроется сама
-- (шаг 4) — если в ней такие же данные, дописать её сюда и перезапустить.
SELECT format('REVOKE ALL ON TABLE public.%I FROM report_ro', tablename)
  FROM pg_tables
 WHERE schemaname = 'public'
   AND tablename IN (
         'login_events',                -- IP и браузер при входе
         'user_geo_pings',              -- координаты людей
         'email_login_requests',        -- хеш почты и IP
         'user_notification_channels',  -- chat id в Telegram, почта, VK
         'auth_login_requests'          -- токен входа через бота: им забирают вход
       )
\gexec

-- 6) Явно убрать любую возможность записи (страховка на случай прежних грантов).
-- CREATE в public, выданный всем (PUBLIC, умолчание PG14), не трогаем —
-- решение 24.09.2026.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
     ON ALL TABLES IN SCHEMA public FROM report_ro;
REVOKE CREATE ON SCHEMA public FROM report_ro;

COMMIT;

-- Итог: сколько таблиц report_ro читает и какие закрыты.
SELECT format('report_ro читает %s из %s таблиц; закрыты: %s',
              count(*) FILTER (WHERE has_table_privilege('report_ro', c.oid, 'SELECT')),
              count(*),
              coalesce(string_agg(c.relname, ', ' ORDER BY c.relname)
                         FILTER (WHERE NOT has_table_privilege('report_ro', c.oid, 'SELECT')),
                       'нет')) AS summary
  FROM pg_class c
 WHERE c.relnamespace = 'public'::regnamespace
   AND c.relkind IN ('r', 'p');

\echo 'report_ro ready (read-only).'
