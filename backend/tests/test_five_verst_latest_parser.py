from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.platform_adapters.five_verst import bulk_parser

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "five_verst_latest.html"


def test_parse_latest_results_html_fixture() -> None:
    html = FIXTURE_PATH.read_text(encoding="utf-8")
    summaries = bulk_parser.parse_latest_results_html(html, limit=5)
    assert len(summaries) == 5
    first = summaries[0]
    assert first.location_external_key
    assert first.event_number is not None
    assert first.event_date >= date(2020, 1, 1)
    assert first.summary_hash
    assert first.source_url.endswith("/")


@pytest.mark.network
def test_fetch_latest_results_integration_smoke() -> None:
    """Живой запрос к 5 вёрстам. Запускать вручную: pytest -m network."""
    summaries, html = bulk_parser.fetch_latest_results(limit=3)
    assert len(summaries) == 3
    assert len(html) > 1000
