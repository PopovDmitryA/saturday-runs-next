from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.redis_client import get_redis_client
from app.five_verst.errors import FiveVerstBanDetected
from app.platform_adapters.five_verst import bulk_parser
from app.sync.global_sync import LocationSyncOptions, sync_location

logger = logging.getLogger(__name__)

ROTATION_INDEX_KEY = "five_verst:location_rotation:index"


@dataclass
class LocationRotationSyncResult:
    location_slug: str
    rotation_index: int
    locations_total: int
    errors: list[str] = field(default_factory=list)
    # Все площадки прогона: у пачки из нескольких слагов первая строка остаётся
    # в location_slug (журналы и отчёты читают её), а полный состав — здесь.
    location_slugs: list[str] = field(default_factory=list)
    # Сводные цифры по всей пачке (у одиночного прогона совпадают с sync).
    summaries_total: int = 0
    summaries_upserted: int = 0
    summaries_unchanged: int = 0
    protocols_fetched: int = 0
    run_results_upserted: int = 0
    volunteer_results_upserted: int = 0
    fetched_protocols: list[str] = field(default_factory=list)
    changed_protocols: list[str] = field(default_factory=list)


def _next_rotation_slugs(slugs: list[str], count: int) -> tuple[list[str], int, int]:
    if not slugs:
        raise RuntimeError("No 5verst locations in registry")
    redis = get_redis_client()
    raw = redis.get(ROTATION_INDEX_KEY)
    index = int(raw) if raw is not None else 0
    total = len(slugs)
    count = max(1, min(count, total))
    batch = [slugs[(index + offset) % total] for offset in range(count)]
    redis.set(ROTATION_INDEX_KEY, str((index + count) % total))
    return batch, index % total, total


def sync_next_location_batch(db: Session) -> LocationRotationSyncResult:
    """
    Rotate through official locations: each run checks up to N summaries for a
    handful of slugs. Protocols are fetched only for changed or missing
    summaries (see sync_location).

    Размер пачки держит длину круга около недели: страница /results/all/ —
    единственное место, где видно позднюю правку прошедшего старта (доливка
    волонтёров, исправленное время). Пока круг шёл по одной площадке за
    прогон, при ~220 локациях и 6 прогонах в сутки он занимал больше месяца.
    Видное 15.08.2026 в эту дыру и провалилось: протокол выложили с одним
    волонтёром, остальных двенадцать долили следом, а сайт три недели держал
    сводку с нулём — и ждать своей очереди площадке оставалось ещё столько же.
    Сверка протоколов (five_verst_reconcile) тут не помощник: она сравнивает
    протокол с НАШЕЙ же сводкой и устаревания сводки не видит по устройству.

    Сверяется ВСЯ таблица площадки, а не последние N строк. Страница уже
    скачана, сравнение хэшей идёт в памяти — резать его незачем, а с окном в
    20 строк правка старта, скажем, тридцатинедельной давности не находилась
    никогда: он в окно не попадал ни при каком числе проходов.
    """
    settings = get_settings()
    slugs = bulk_parser.list_location_slugs()
    batch, index, total = _next_rotation_slugs(
        slugs, settings.five_verst_location_rotation_slugs_per_run
    )
    logger.info(
        "5verst location rotation: slugs=%s index=%s/%s", ",".join(batch), index, total
    )

    result = LocationRotationSyncResult(
        location_slug=batch[0],
        rotation_index=index,
        locations_total=total,
        location_slugs=batch,
    )
    for slug in batch:
        try:
            sync_result = sync_location(
                db,
                LocationSyncOptions(
                    location_slug=slug,
                    # Вся таблица, а не последние N строк: страница уже
                    # скачана, и сравнение хэшей ничего не стоит. Правку
                    # старого старта иначе не увидеть вовсе — он не попадал
                    # в окно ни при каком числе проходов.
                    summaries_limit=settings.five_verst_location_batch_summaries_limit,
                    # А вот перекачки протоколов ограничены: см. комментарий
                    # к настройке. Недокачанное остаётся долгом и уходит в
                    # приоритет сверки, а не ждёт следующего круга.
                    protocol_fetch_limit=settings.five_verst_location_protocol_fetch_limit,
                    fetch_all_protocols_on_change=False,
                    location_refresh_interval_days=settings.five_verst_location_refresh_interval_days,
                ),
            )
        except FiveVerstBanDetected as exc:
            # Кулдаун общий для всех фетчей: остаток пачки упал бы с той же
            # ошибкой. Индекс ротации уже сдвинут — недокачанные площадки
            # заберёт следующий круг.
            result.errors.append(f"{slug}: {exc}; остаток пачки отложен")
            break
        except Exception as exc:
            result.errors.append(f"{slug}: {exc}")
            continue

        result.errors.extend(sync_result.errors)
        result.summaries_total += sync_result.summaries_total
        result.summaries_upserted += sync_result.summaries_upserted
        result.summaries_unchanged += sync_result.summaries_unchanged
        result.protocols_fetched += sync_result.protocols_fetched
        result.run_results_upserted += sync_result.run_results_upserted
        result.volunteer_results_upserted += sync_result.volunteer_results_upserted
        result.fetched_protocols.extend(sync_result.fetched_protocols)
        result.changed_protocols.extend(sync_result.changed_protocols)

    return result
