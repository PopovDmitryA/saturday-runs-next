"""participants.profile_checked_at — дата актуальности профиля

Когда страницу профиля участника реально открывали и парсили. Отличается от
fetched_at: тот обновляется при любом касании строки (импорт результатов,
протоколы, миграции), поэтому не годится как «когда последний раз смотрели
профиль». Нужен для повторного обхода всех parkrun-профилей: обходить от
самых давно просмотренных.

Revision ID: 043_participants_profile_checked_at
Revises: 042_seed_blocked_profile_slugs
Create Date: 2026-07-12
"""


from alembic import op

revision = "043_participants_profile_checked_at"
down_revision = "042_seed_blocked_profile_slugs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF NOT EXISTS: колонку добавляют заранее прямым ALTER (до recreate контейнеров),
    # чтобы новый код не падал на UndefinedColumn в окне между up -d и alembic upgrade.
    op.execute(
        "ALTER TABLE participants ADD COLUMN IF NOT EXISTS profile_checked_at TIMESTAMP WITH TIME ZONE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE participants DROP COLUMN IF EXISTS profile_checked_at")
