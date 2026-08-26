from __future__ import annotations

from datetime import datetime, timezone

from app.models import Location, SyncStatus


def apply_location_registry_flags(
    row: Location,
    *,
    is_paused: bool,
    is_cancelled: bool,
    is_upcoming: bool = False,
) -> tuple[bool, bool, bool]:
    """Разложить статус из реестра системы по признакам строки.

    Возвращает (changed, pause_changed, cancel_changed). «Скоро» отдельного
    счётчика не имеет: это состояние новой площадки, и меняется оно ровно
    один раз — в день первого старта.

    Флаги пишутся и когда становятся False: реестр — источник правды, и снятая
    с сайта отметка обязана сниматься у нас. Иначе отмена одной субботы висела
    бы на площадке вечно.
    """
    changed = False
    pause_changed = False
    cancel_changed = False

    if row.is_upcoming != is_upcoming:
        row.is_upcoming = is_upcoming
        changed = True

    if row.is_cancelled != is_cancelled:
        row.is_cancelled = is_cancelled
        changed = True
        cancel_changed = True

    if row.is_paused != is_paused:
        row.is_paused = is_paused
        changed = True
        pause_changed = True

    if changed:
        row.fetched_at = datetime.now(timezone.utc)
        row.sync_status = SyncStatus.ok

    return changed, pause_changed, cancel_changed
