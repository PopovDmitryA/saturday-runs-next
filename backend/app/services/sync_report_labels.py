from __future__ import annotations

from typing import Any

PIPELINE_LABELS: dict[str, str] = {
    "5v registry /events/": "5verst: реестр /events/",
    "5v latest /results/latest/": "5verst: свежие результаты /results/latest/",
    "5v location rotation": "5verst: ротация локаций",
    "5v reconcile protocols": "5verst: сверка протоколов",
}

PIPELINE_PREFIX_LABELS: tuple[tuple[str, str], ...] = (
    ("5v location ", "5verst: локация "),
    ("5v summaries ", "5verst: своды "),
)

FIELD_LABELS: dict[str, str] = {
    "entries_total": "записей в реестре",
    "locations_updated": "локаций обновлено",
    "locations_created": "локаций создано",
    "locations_skipped_no_coords": "пропущено без координат",
    "coords_fetched": "координат загружено",
    "regions_backfilled": "регионов дополнено",
    "pause_status_changed": "изменений статуса «на паузе»",
    "cancel_status_changed": "изменений статуса «отмена»",
    "merge_requests_created": "заявок на слияние дублей",
    "merge_notifications_sent": "уведомлений о дублях отправлено",
    "summaries_total": "сводок всего",
    "summaries_upserted": "сводок записано",
    "summaries_unchanged": "сводок без изменений",
    "needs_update": "требуют обновления",
    "new_summaries": "новых сводок",
    "changed_summaries": "изменённых сводок",
    "missing_protocol": "без протокола в БД",
    "protocols_fetched": "протоколов загружено",
    "protocols_changed": "протоколов изменено",
    "run_results_upserted": "результатов пробежек записано",
    "volunteer_results_upserted": "результатов волонтёров записано",
    "planned_protocols": "протоколов в плане",
    "candidates_total": "кандидатов на сверку",
    "planned": "запланировано ключей",
    "location_slug": "локация",
    "rotation_index": "индекс ротации",
    "locations_total": "локаций в ротации",
    "location_upserted": "локация обновлена",
    "enqueued": "поставлено в очередь",
}


def pipeline_label(name: str) -> str:
    if name in PIPELINE_LABELS:
        return PIPELINE_LABELS[name]
    for prefix, label in PIPELINE_PREFIX_LABELS:
        if name.startswith(prefix):
            return label + name.removeprefix(prefix)
    return name


def field_label(key: str) -> str:
    return FIELD_LABELS.get(key, key)


def format_field_value(key: str, value: Any) -> str:
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, list):
        if key == "planned":
            return str(len(value))
        return ", ".join(str(item) for item in value[:5]) + ("…" if len(value) > 5 else "")
    return str(value)
