from app.services.sync_report_labels import field_label, pipeline_label
from app.services.vk_admin_notify import format_daily_summary


def test_pipeline_and_field_labels_russian() -> None:
    assert pipeline_label("5v location slug-x") == "5verst: локация slug-x"
    assert pipeline_label("s95 latest /activities") == "s95: свежие /activities"
    assert field_label("participants_synced") == "профилей обновлено"


def _platform(**overrides: object) -> dict:
    payload = {
        "platform": "five_verst",
        "platform_label": "5 вёрст",
        "runs": 14,
        "ok": 14,
        "problems": 0,
        "skipped": 0,
        "errors_total": 0,
        "metrics": [
            {"key": "run_results_upserted", "label": "результатов пробежек записано", "value": 5120},
            {"key": "protocols_fetched", "label": "протоколов загружено", "value": 340},
        ],
        "pipelines": [],
    }
    payload.update(overrides)
    return payload


def test_daily_summary_counts_runs_and_updates() -> None:
    text = format_daily_summary(
        "18.07.2026",
        [_platform()],
        admin_url="https://run5k.run/admin/sync-runs",
    )

    assert "Автообновление за 18.07.2026" in text
    assert "✅ 5 вёрст: 14 запусков" in text
    assert "• результатов пробежек записано: 5 120" in text
    assert "https://run5k.run/admin/sync-runs" in text


def test_daily_summary_flags_problems() -> None:
    text = format_daily_summary(
        "18.07.2026",
        [_platform(platform="s95", platform_label="S95", runs=6, ok=4, problems=1, skipped=1)],
        problems=[
            {
                "pipeline_label": "s95: реестр /activities",
                "errors": ["HTTP 503 от s95.ru"],
            }
        ],
    )

    assert "⚠️ S95: 6 запусков, 1 с ошибками, пропущено 1" in text
    assert "Что болит:" in text
    assert "s95: реестр /activities: HTTP 503 от s95.ru" in text


def test_daily_summary_without_runs() -> None:
    text = format_daily_summary("18.07.2026", [])
    assert "Запусков не было." in text


def test_daily_summary_pluralizes_runs() -> None:
    assert "1 запуск" in format_daily_summary("18.07.2026", [_platform(runs=1, metrics=[])])
    assert "3 запуска" in format_daily_summary("18.07.2026", [_platform(runs=3, metrics=[])])
    assert "11 запусков" in format_daily_summary("18.07.2026", [_platform(runs=11, metrics=[])])
