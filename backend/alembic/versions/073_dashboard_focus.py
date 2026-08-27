"""Профили участника: фокус дашборда (турист/скоростник/волонтёр/завсегдатай).

dashboard_focus — что человек выбрал (NULL = ещё не выбирал, показываем все
блоки). dashboard_focus_auto — автонабор на момент последнего подтверждения:
с ним сравнивается свежий автоподбор, чтобы после привязки нового аккаунта
предложить только действительно новые профили.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "073_dashboard_focus"
down_revision = "072_protocol_watch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("dashboard_focus", JSONB, nullable=True))
    op.add_column("users", sa.Column("dashboard_focus_auto", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("users", "dashboard_focus_auto")
    op.drop_column("users", "dashboard_focus")
