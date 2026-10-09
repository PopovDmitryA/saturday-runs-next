"""Адрес почты в имени участника: оставляем часть до «@»

Яндекс ID у аккаунтов с почтой на своём домене отдаёт логином весь адрес, и
он становился именем профиля. Публичная карточка показывала адрес в <title>:
на 03.10.2026 в поиске Яндекса было пять таких профилей. Источник закрыт в
app/auth/providers/yandex.py, здесь — чистка уже сохранённых имён тем же
правилом, что у входа по коду на почту (часть до «@»).

Обратной миграции нет: прежние адреса восстанавливать незачем.

Revision ID: 108_display_name_email
Revises: 107_notification_nudge_snooze
Create Date: 2026-10-09
"""

from __future__ import annotations

from alembic import op

revision = "108_display_name_email"
down_revision = "107_notification_nudge_snooze"
branch_labels = None
depends_on = None

# Тот же шаблон, что app/core/display_name._EMAIL_RE, в синтаксисе Postgres.
_EMAIL_RE = r"([A-Za-z0-9._+\-]+)@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE users
        SET display_name = NULLIF(btrim(regexp_replace(display_name, '{_EMAIL_RE}', '\\1', 'g')), '')
        WHERE display_name ~ '{_EMAIL_RE}'
        """
    )
    # Тот же адрес лежит в именах способов входа: из них имя берётся, когда в
    # беговых системах его нет (user_display_name_service._fallback_name).
    op.execute(
        f"""
        UPDATE auth_identities
        SET display_name = NULLIF(btrim(regexp_replace(display_name, '{_EMAIL_RE}', '\\1', 'g')), '')
        WHERE display_name ~ '{_EMAIL_RE}'
        """
    )


def downgrade() -> None:
    pass
