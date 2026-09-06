from __future__ import annotations


def format_limit(value: int | None) -> str:
    return "без лимита" if value is None else str(value)


def five_verst_latest_details(
    *,
    update_limit: int | None,
    protocol_fetch_limit: int | None,
    fetch_all_protocols_on_change: bool,
) -> str:
    return (
        f"Лимит summary на обновление: {format_limit(update_limit)}\n"
        f"Лимит загрузки протоколов: {format_limit(protocol_fetch_limit)}\n"
        f"При изменении сводки: {'все протоколы' if fetch_all_protocols_on_change else 'только изменённые'}"
    )


def five_verst_reconcile_details(
    *,
    limit: int,
    min_check_interval_days: int,
    location_slug: str | None,
) -> str:
    lines = [
        f"Пакет: {limit} протоколов (сначала расхождения, добор — самые давно не проверявшиеся)",
        f"Мин. интервал повторной проверки: {min_check_interval_days} дн.",
    ]
    if location_slug:
        lines.append(f"Только локация: {location_slug}")
    return "\n".join(lines)


def five_verst_week_sweep_details(
    *,
    weeks_back: int,
    limit: int,
    week_start: str | None = None,
    week_end: str | None = None,
) -> str:
    week_label = {0: "последняя суббота", 1: "неделя W−1", 2: "неделя W−2"}.get(
        weeks_back, f"неделя W−{weeks_back}"
    )
    lines = [
        f"Неделя: {week_label}",
        f"Пакет: {limit} протоколов за заход (перекачиваем без оглядки на сводку)",
    ]
    if week_start and week_end:
        lines.append(f"Даты: {week_start} — {week_end}")
    return "\n".join(lines)


def five_verst_rotation_details(
    *,
    summaries_limit: int,
    slugs_per_run: int = 1,
    locations_total: int | None = None,
    rotation_index: int | None = None,
) -> str:
    lines = [
        f"Summary на локацию: {summaries_limit}",
        f"Ротация: {slugs_per_run} локаций из реестра за запуск"
        if slugs_per_run > 1
        else "Ротация: одна локация из реестра за запуск",
    ]
    if locations_total is not None and rotation_index is not None:
        lines.append(f"Позиция в ротации: {rotation_index + 1} из {locations_total}")
    return "\n".join(lines)


def five_verst_location_details(
    *,
    location_slug: str,
    summaries_limit: int | None,
    protocol_fetch_limit: int | None,
    fetch_all_protocols_on_change: bool,
) -> str:
    return (
        f"Локация: {location_slug}\n"
        f"Лимит summary: {format_limit(summaries_limit)}\n"
        f"Лимит загрузки протоколов: {format_limit(protocol_fetch_limit)}\n"
        f"При изменении сводки: {'все протоколы' if fetch_all_protocols_on_change else 'только изменённые'}"
    )


def five_verst_registry_details(*, limit: int | None) -> str:
    if limit is None:
        return "Обработка: весь реестр /events/"
    return f"Лимит записей реестра: {limit}"


def five_verst_clubs_registry_details(*, limit: int | None) -> str:
    if limit is None:
        return "Обработка: весь список /clubs/"
    return f"Лимит клубов из списка: {limit}"


def five_verst_clubs_details_details(*, limit: int) -> str:
    return f"Пакет: {limit} клубов (самые давно не обновлявшиеся, изменённые — вне очереди)"


def s95_registry_details(*, limit: int | None) -> str:
    if limit is None:
        return "Обработка: весь реестр /activities"
    return f"Лимит записей реестра: {limit}"
