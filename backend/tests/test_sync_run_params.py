#!/usr/bin/env python3
from __future__ import annotations

from app.services.sync_run_params import (
    five_verst_latest_details,
    five_verst_reconcile_details,
    format_limit,
)


def test_format_limit() -> None:
    assert format_limit(None) == "без лимита"
    assert format_limit(20) == "20"


def test_five_verst_latest_details() -> None:
    text = five_verst_latest_details(
        update_limit=20,
        protocol_fetch_limit=None,
        fetch_all_protocols_on_change=True,
    )
    assert "20" in text
    assert "без лимита" in text


def test_five_verst_reconcile_details() -> None:
    text = five_verst_reconcile_details(
        limit=100,
        min_check_interval_days=0,
        location_slug=None,
    )
    assert "100" in text
    assert "давно не проверявшиеся" in text
