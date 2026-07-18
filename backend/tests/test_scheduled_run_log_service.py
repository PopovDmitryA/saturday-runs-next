from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.services.scheduled_run_log_service import (
    extract_metrics,
    headline_count,
    platform_for_pipeline,
    resolve_period,
)


def test_platform_derived_from_pipeline_name() -> None:
    assert platform_for_pipeline("5v latest /results/latest/") == "five_verst"
    assert platform_for_pipeline("5v location kopyttse") == "five_verst"
    assert platform_for_pipeline("s95 registry /activities") == "s95"
    assert platform_for_pipeline("runpark latest") == "runpark"
    assert platform_for_pipeline("что-то новое") == "other"


def test_metrics_drop_location_details_and_errors() -> None:
    metrics = extract_metrics(
        {
            "protocols_fetched": 3,
            "fetched_protocols": ["kopyttse: kopyttse-123", "obninsk: obninsk-456"],
            "updated_locations": ["kopyttse (Копытце)"],
            "planned": ["kopyttse-123", "obninsk-456"],
            "location_slug": "kopyttse",
            "errors": ["сломалось"],
            "skipped": False,
            "empty": None,
        }
    )

    # Перечисления локаций и протоколов не хранятся — их итоги приходят
    # отдельными счётчиками (protocols_fetched). Прочие списки — длиной.
    assert metrics == {
        "protocols_fetched": 3,
        "planned": 2,
        "location_slug": "kopyttse",
    }


def test_headline_count_prefers_most_meaningful_key() -> None:
    assert headline_count({"protocols_fetched": 3, "run_results_upserted": 512}) == 512
    assert headline_count({"protocols_fetched": 3}) == 3
    assert headline_count({"locations_total": 200}) == 0


def test_resolve_period_defaults_to_last_week() -> None:
    start, end = resolve_period()
    assert (end - start) == timedelta(days=7)


def test_resolve_period_explicit_range_is_inclusive() -> None:
    start, end = resolve_period(date_from=date(2026, 7, 1), date_to=date(2026, 7, 3))
    assert start.date() == date(2026, 7, 1)
    # Верхняя граница — начало следующих суток, чтобы 3 июля попало целиком.
    assert end.date() == date(2026, 7, 4)


def test_resolve_period_swapped_dates_do_not_invert() -> None:
    start, end = resolve_period(date_from=date(2026, 7, 10), date_to=date(2026, 7, 1))
    assert start <= end


def test_utc_run_lands_in_moscow_day() -> None:
    """Запуск в 23:30 UTC — это уже следующие сутки по МСК."""
    start, end = resolve_period(date_from=date(2026, 7, 2), date_to=date(2026, 7, 2))
    run_at = datetime(2026, 7, 1, 23, 30, tzinfo=timezone.utc)
    assert start <= run_at < end
