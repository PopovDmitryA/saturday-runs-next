from app.services.sync_report_labels import field_label, pipeline_label
from app.services.vk_admin_notify import format_sync_finished, format_sync_started


def test_pipeline_and_field_labels_russian() -> None:
    started = format_sync_started("5v registry /events/")
    assert "реестр /events/" in started

    finished = format_sync_finished(
        "5v registry /events/",
        {
            "entries_total": 210,
            "locations_updated": 99,
            "pause_status_changed": 2,
            "cancel_status_changed": 3,
            "errors": ["pirogovskiy: таймаут"],
        },
    )
    assert "записей в реестре: 210" in finished
    assert "локаций обновлено: 99" in finished
    assert "изменений статуса «на паузе»: 2" in finished
    assert "изменений статуса «отмена»: 3" in finished
    assert "ошибок: 1" in finished
    assert pipeline_label("5v location slug-x") == "5verst: локация slug-x"
    assert field_label("unknown_key") == "unknown_key"
