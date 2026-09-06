"""Ротация локаций 5 вёрст: пачка слагов за прогон и круг длиной в неделю.

Страница /results/all/ — единственное место, где видна ПОЗДНЯЯ правка уже
прошедшего старта: доливка волонтёров, исправленное время. Сверка протоколов
(five_verst_reconcile) такого не ловит по устройству: она сравнивает протокол
с НАШЕЙ же сводкой, а сводка при этом остаётся старой.

Пока ротация брала одну площадку за прогон (6 прогонов в сутки на ~220
локаций), круг занимал больше месяца. Видное 15.08.2026 в него и провалилось:
протокол выложили с одним волонтёром, остальных двенадцать долили следом, а
сайт держал старую сводку три недели.
"""

from __future__ import annotations

import fakeredis

from app.config import get_settings
from app.sync.five_verst_location_rotation import ROTATION_INDEX_KEY, _next_rotation_slugs
from app.sync.five_verst_reconcile import PRIORITY_REASONS, ReconcileReason
from app.sync.global_sync import _select_summaries_for_protocol_fetch

SLUGS = [f"loc{i}" for i in range(10)]


def test_batch_takes_several_slugs_and_moves_index(fake_redis: fakeredis.FakeRedis) -> None:
    batch, index, total = _next_rotation_slugs(SLUGS, 5)
    assert batch == ["loc0", "loc1", "loc2", "loc3", "loc4"]
    assert (index, total) == (0, 10)
    assert fake_redis.get(ROTATION_INDEX_KEY) == "5"

    batch, index, _ = _next_rotation_slugs(SLUGS, 5)
    assert batch == ["loc5", "loc6", "loc7", "loc8", "loc9"]
    assert index == 5
    # Круг замкнулся ровно, без пропусков и повторов.
    assert fake_redis.get(ROTATION_INDEX_KEY) == "0"


def test_batch_wraps_around_the_end(fake_redis: fakeredis.FakeRedis) -> None:
    fake_redis.set(ROTATION_INDEX_KEY, "8")
    batch, index, _ = _next_rotation_slugs(SLUGS, 5)
    assert batch == ["loc8", "loc9", "loc0", "loc1", "loc2"]
    assert index == 8
    assert fake_redis.get(ROTATION_INDEX_KEY) == "3"


def test_batch_never_exceeds_registry_size() -> None:
    """Пачка больше реестра не должна перечитывать одну площадку дважды."""
    batch, _, _ = _next_rotation_slugs(["only"], 5)
    assert batch == ["only"]


def test_batch_size_is_at_least_one(fake_redis: fakeredis.FakeRedis) -> None:
    batch, _, _ = _next_rotation_slugs(SLUGS, 0)
    assert batch == ["loc0"]
    assert fake_redis.get(ROTATION_INDEX_KEY) == "1"


def test_rotation_reads_the_whole_location_table() -> None:
    """Ротация сверяет ВСЮ таблицу /results/all/, а не последние N строк.

    Страница уже скачана, сравнение хэшей идёт в памяти — резать его незачем.
    С окном в 20 строк правка старта тридцатинедельной давности не находилась
    никогда: он в окно не попадал ни при каком числе проходов.
    """
    settings = get_settings()
    assert settings.five_verst_location_batch_summaries_limit is None


def test_unfetched_protocols_become_debt_not_loss() -> None:
    """Потолок перекачек за заход не теряет находки, а переводит их в долг.

    Сводку прогон записывает всегда, а протокол под ней остаётся от прошлой
    версии — то есть `summary_hash_at_fetch ≠ summary_hash`, и это
    приоритетная причина сверки. Иначе потолок означал бы «увидели правку и
    забыли до следующего круга».
    """
    settings = get_settings()
    assert settings.five_verst_location_protocol_fetch_limit > 0
    # Отбор режет очередь ровно по потолку — и только при выключенном
    # fetch_all_protocols_on_change, как его и вызывает ротация.
    queue = [(f"summary{i}", f"canonical{i}") for i in range(25)]
    selected = _select_summaries_for_protocol_fetch(
        queue,
        protocol_fetch_limit=settings.five_verst_location_protocol_fetch_limit,
        fetch_all_protocols_on_change=False,
    )
    assert len(selected) == settings.five_verst_location_protocol_fetch_limit
    # Свежие старты вперёд: таблица идёт от новых к старым.
    assert selected[0] == queue[0]
    assert ReconcileReason.protocol_debt in PRIORITY_REASONS


def test_weekly_cycle_at_default_settings() -> None:
    """Норма круга — около недели, иначе поздние правки снова потеряются."""
    settings = get_settings()
    runs_per_day = 6  # crontab(minute=30, hour="*/4")
    locations = 220  # официальный реестр 5 вёрст на 09.2026
    per_day = settings.five_verst_location_rotation_slugs_per_run * runs_per_day
    assert 5 <= locations / per_day <= 9
