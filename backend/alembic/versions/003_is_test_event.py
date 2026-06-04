"""Add is_test_event flag to events and event_summaries."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column("is_test_event", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column(
        "event_summaries",
        sa.Column("is_test_event", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("event_summaries", "is_test_event")
    op.drop_column("events", "is_test_event")
