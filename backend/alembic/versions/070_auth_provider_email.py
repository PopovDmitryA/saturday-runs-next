"""Вход по почте: значение 'email' в auth_provider_enum

Отдельного хранилища для почтового входа не нужно — идентичность ложится в
auth_identities как у VK и Яндекса: provider='email', external_id — адрес в
нормализованном виде (см. app/core/email_address.py), email — как его ввёл
человек.

Значение enum в PostgreSQL удалить нельзя, поэтому downgrade оставляет тип
как есть: он лишь снимает почтовые идентичности, чтобы откат не оставил
записей с провайдером, которого код уже не знает.

Revision ID: 070_auth_provider_email
Revises: 069_onboarding_no_account
Create Date: 2026-08-26
"""

from __future__ import annotations

from alembic import op

revision = "070_auth_provider_email"
down_revision = "069_onboarding_no_account"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF NOT EXISTS — миграция должна переживать повторный прогон: значение
    # могло приехать из другой ветки, а ALTER TYPE в транзакции не откатишь.
    op.execute("ALTER TYPE auth_provider_enum ADD VALUE IF NOT EXISTS 'email'")


def downgrade() -> None:
    op.execute("DELETE FROM auth_identities WHERE provider = 'email'")
