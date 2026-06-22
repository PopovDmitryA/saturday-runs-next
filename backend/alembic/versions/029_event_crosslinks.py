"""Add event_crosslinks table for cross-platform deduplication"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "029_event_crosslinks"
down_revision = "028_runpark_activate_platform"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "event_crosslinks",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("primary_event_id", sa.UUID(), nullable=False),
        sa.Column("secondary_event_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["primary_event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["secondary_event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("primary_event_id", "secondary_event_id"),
    )
    op.create_index("ix_event_crosslinks_primary", "event_crosslinks", ["primary_event_id"])
    op.create_index("ix_event_crosslinks_secondary", "event_crosslinks", ["secondary_event_id"])

    # Populate from existing dual_load locations
    op.execute("""
        INSERT INTO event_crosslinks (primary_event_id, secondary_event_id)
        SELECT pe.id, se.id
        FROM runpark_location_mappings rlm
        JOIN events pe ON pe.location_id = rlm.matched_location_id
        JOIN events se ON se.location_id = rlm.runpark_location_row_id
            AND se.event_date = pe.event_date
        JOIN platforms rp ON rp.id = se.platform_id AND rp.code = 'runpark'
        WHERE rlm.decision = 'dual_load'
          AND rlm.matched_location_id IS NOT NULL
          AND rlm.runpark_location_row_id IS NOT NULL
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.drop_index("ix_event_crosslinks_secondary", "event_crosslinks")
    op.drop_index("ix_event_crosslinks_primary", "event_crosslinks")
    op.drop_table("event_crosslinks")
