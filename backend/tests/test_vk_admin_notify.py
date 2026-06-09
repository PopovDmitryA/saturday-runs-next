from app.services.sync_report_labels import field_label, pipeline_label
from app.services.vk_admin_notify import format_sync_finished, format_sync_started


def test_pipeline_and_field_labels_russian() -> None:
    started = format_sync_started("5v registry /events/")
    assert "реестр /events/" in started

    started_with_params = format_sync_started(
        "5v latest /results/latest/",
        details="Лимит summary на обновление: 20\nЛимит загрузки протоколов: без лимита",
    )
    assert "Лимит summary на обновление: 20" in started_with_params

    finished = format_sync_finished(
        "5v latest /results/latest/",
        {
            "protocols_fetched": 3,
            "fetched_protocols": ["kopyttse (Копытse): kopyttse-123"],
            "changed_protocols": ["obninsk (Обнинск): obninsk-456"],
            "errors": [],
        },
    )
    assert "Загружены протоколы:" in finished
    assert "• kopyttse" in finished
    assert "Изменены протоколы:" in finished

    finished = format_sync_finished(
        "5v registry /events/",
        {
            "entries_total": 210,
            "locations_updated": 2,
            "updated_locations": ["kopyttse (Копытse)", "obninsk (Обнинск)"],
            "created_locations": ["newpark (Новый парк)"],
            "errors": [],
        },
    )
    assert "записей в реестре: 210" in finished
    assert "Локации обновлены:" in finished
    assert "• kopyttse" in finished
    assert "Локации созданы:" in finished
    assert pipeline_label("5v location slug-x") == "5verst: локация slug-x"
    assert pipeline_label("s95 latest /activities") == "s95: свежие /activities"
    assert field_label("participants_synced") == "профилей обновлено"
