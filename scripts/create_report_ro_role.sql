-- Idempotent creation of a read-only role for the internal reporting API
-- (/api/internal/reports/*). Безопасно запускать повторно.
--
-- Пароль НЕ хранится в файле — передаётся psql-переменной:
--
--   export REPORT_RO_PASSWORD='<пароль из чата>'
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
--        -v report_password="$REPORT_RO_PASSWORD" \
--        -f scripts/create_report_ro_role.sql
--
-- ВАЖНО: запускать под ролью-владельцем таблиц (saturday_runs), тогда
-- ALTER DEFAULT PRIVILEGES ниже раздаст SELECT и на будущие таблицы,
-- которые создадут миграции.

\set ON_ERROR_STOP on

-- 1) Создать роль, если её ещё нет (LOGIN, без прав по умолчанию).
SELECT format('CREATE ROLE report_ro LOGIN PASSWORD %L', :'report_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'report_ro')
\gexec

-- 2) (Пере)установить пароль и гарантировать отсутствие лишних атрибутов.
ALTER ROLE report_ro WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
     NOREPLICATION PASSWORD :'report_password';

-- 3) Доступ к текущей БД и схеме public (read-only).
SELECT format('GRANT CONNECT ON DATABASE %I TO report_ro', current_database())
\gexec

GRANT USAGE ON SCHEMA public TO report_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO report_ro;

-- 4) SELECT и на будущие таблицы (создаваемые текущим владельцем).
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO report_ro;

-- 5) Явно убрать любую возможность записи (страховка на случай прежних грантов).
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
     ON ALL TABLES IN SCHEMA public FROM report_ro;
REVOKE CREATE ON SCHEMA public FROM report_ro;

\echo 'report_ro ready (read-only).'
