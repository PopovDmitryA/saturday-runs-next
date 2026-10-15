from __future__ import annotations

from datetime import datetime, timezone

from app.models import Location, SyncStatus
from app.services.location_activity_status import status_override_paused


def apply_location_registry_flags(
    row: Location,
    *,
    is_paused: bool | None,
    is_cancelled: bool,
    is_upcoming: bool = False,
    cancel_reason: str | None = None,
) -> tuple[bool, bool, bool]:
    """Разложить статус из реестра системы по признакам строки.

    Возвращает (changed, pause_changed, cancel_changed); pause_changed — реестр
    сменил своё мнение о паузе. «Скоро» отдельного
    счётчика не имеет: это состояние новой площадки, и меняется оно ровно
    один раз — в день первого старта.

    `cancel_reason` — текст организатора со страницы площадки (его пишет только
    s95). Он живёт ровно столько, сколько живёт отмена: снятая отметка стирает
    и причину, иначе на странице осталась бы прошлогодняя записка.

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

    reason = cancel_reason if is_cancelled else None
    if row.cancel_reason != reason:
        row.cancel_reason = reason
        changed = True

    # Пауза складывается из трёх источников (миграция 110): ручной статус
    # админа, мнение реестра (registry_paused) и правило молчания. Реестр
    # пишет только своё мнение и трогает итог, лишь когда это мнение
    # меняется: раньше он каждый вечер в 20:50 снимал паузу, которую правило
    # молчания ставило в 21:10. is_paused=None — источнику нечего сказать о
    # паузе (наблюдатель отмен s95).
    target = row.is_paused
    if is_paused is not None and row.registry_paused != is_paused:
        row.registry_paused = is_paused
        changed = True
        pause_changed = True
        # Сняв свою паузу, реестр возвращает площадку в строй; если она при
        # этом молчит дольше порога, ночное правило вернёт паузу само.
        target = is_paused
    elif row.registry_paused:
        target = True
    # Ручной статус сильнее реестра: реестр, не знающий о переезде площадки,
    # иначе возвращал бы её в «действует».
    manual = status_override_paused(row.status_override)
    if manual is not None:
        target = manual
    if row.is_paused != target:
        row.is_paused = target
        changed = True

    if changed:
        row.fetched_at = datetime.now(timezone.utc)
        row.sync_status = SyncStatus.ok

    return changed, pause_changed, cancel_changed
