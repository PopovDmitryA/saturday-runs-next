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


def test_daily_summary_collapses_repeated_problems() -> None:
    """Одна залипшая вещь не должна занимать собой весь раздел.

    Плотинка №225 падала в каждом обходе и 13.09.2026 дала пять одинаковых
    строк подряд — вторая, настоящая ошибка дня до сводки бы не доехала.
    """
    stuck = {
        "pipeline_label": "5v week sweep W-2",
        "errors": ["plotinka:225:2026-08-29: '<' not supported between 'NoneType' and 'int'"],
    }
    text = format_daily_summary(
        "13.09.2026",
        [_platform(problems=5)],
        problems=[stuck, stuck, stuck, stuck, stuck, {"pipeline_label": "s95: реестр", "errors": ["HTTP 503"]}],
    )

    assert text.count("plotinka:225") == 1
    assert "(× 5)" in text
    # Вторая поломка не вытеснена повторами первой.
    assert "s95: реестр: HTTP 503" in text


def test_daily_summary_caps_problem_list() -> None:
    problems = [
        {"pipeline_label": f"пайплайн {index}", "errors": [f"ошибка {index}"]}
        for index in range(8)
    ]
    text = format_daily_summary("13.09.2026", [_platform(problems=8)], problems=problems)

    assert "пайплайн 4: ошибка 4" in text
    assert "пайплайн 5: ошибка 5" not in text
    assert "…и ещё 3 поломки" in text


def test_daily_summary_problem_without_error_text() -> None:
    text = format_daily_summary(
        "13.09.2026",
        [_platform(problems=1)],
        problems=[{"pipeline_label": "5v latest", "errors": []}],
    )
    assert "5v latest: без текста ошибки" in text
