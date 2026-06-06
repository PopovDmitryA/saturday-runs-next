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

DETAIL_SECTION_LABELS: dict[str, str] = {
    "updated_locations": "Локации обновлены",
    "created_locations": "Локации созданы",
    "coords_fetched_locations": "Загружены координаты",
    "pause_changed_locations": "Статус «на паузе» изменён",
    "cancel_changed_locations": "Статус «отмена» изменён",
    "merge_request_locations": "Заявки на слияние дублей",
    "fetched_protocols": "Загружены протоколы",
    "changed_protocols": "Изменены протоколы",
}

DETAIL_LIST_KEYS = frozenset(DETAIL_SECTION_LABELS.keys())
DETAIL_LIST_LIMIT = 25


def location_detail_label(slug: str, name: str | None = None) -> str:
    if name and name.strip() and name.strip() != slug:
        return f"{slug} ({name.strip()})"
    return slug


def protocol_detail_label(
    location_slug: str,
    external_event_key: str,
    location_name: str | None = None,
) -> str:
    return f"{location_detail_label(location_slug, location_name)}: {external_event_key}"


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
        if key in DETAIL_LIST_KEYS:
            return str(len(value))
        if key == "planned":
            return str(len(value))
        return ", ".join(str(item) for item in value[:5]) + ("…" if len(value) > 5 else "")
    return str(value)


def format_detail_sections(payload: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for key, title in DETAIL_SECTION_LABELS.items():
        value = payload.get(key)
        if not isinstance(value, list) or not value:
            continue
        lines.append(f"{title}:")
        for item in value[:DETAIL_LIST_LIMIT]:
            lines.append(f"• {item}")
        if len(value) > DETAIL_LIST_LIMIT:
            lines.append(f"… и ещё {len(value) - DETAIL_LIST_LIMIT}")
    return lines
