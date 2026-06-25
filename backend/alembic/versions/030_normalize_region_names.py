"""Normalize region names: strip trailing ' область' and ' край' suffixes

Nominatim geocoding returns full forms like "Краснодарский край",
"Московская область" while legacy data used short forms.
This migration normalizes all existing values to the short form.

Revision ID: 030_normalize_region_names
Revises: 029_event_crosslinks
Create Date: 2026-06-25
"""

from alembic import op

revision = "030_normalize_region_names"
down_revision = "029_event_crosslinks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE locations "
        "SET region = regexp_replace(region, ' (область|край)$', '', 'g') "
        "WHERE region ~ ' (область|край)$'"
    )


def downgrade() -> None:
    pass
