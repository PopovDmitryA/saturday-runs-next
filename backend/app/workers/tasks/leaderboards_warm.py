from __future__ import annotations

import logging

from app.db.session import get_session_factory
from app.services.fastest_rating_service import warm_fastest_rating
from app.services.leaderboard_service import (
    AMBIGUOUS_HOME_METRICS,
    GENDERED_METRICS,
    LEADERBOARD_METRICS,
    MAX_MIN_VISITS,
    MIN_VISITS_METRICS,
    TOURIST_MAP_METRICS,
    LeaderboardMetric,
    count_by_values,
    make_snapshot_source,
    platform_filter_values,
    refresh_leaderboard_cache,
    refresh_tourist_map_cache,
)
from app.services.location_records_rating_service import refresh_location_records_rating_cache
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# Ключ склейки: пока он жив, новый синк прогрев не планирует — иначе субботняя
# серия синков (5 вёрст ежечасно + S95 + RunPark) выстроила бы очередь из
# полных пересчётов сетки, и воркер занимался бы только ими.
_DEBOUNCE_KEY = "leaderboards:warm:scheduled"
# Ключ «прогрев прямо сейчас идёт»: беговой прогон по расписанию и прогон от
# синка не должны сойтись на одной сетке — вдвое больше работы за тот же
# результат. TTL — страховка от потерянного замка (воркер убит на полпути).
_RUNNING_KEY = "leaderboards:warm:running"
_RUNNING_TTL_SECONDS = 45 * 60


def schedule_leaderboards_warm() -> bool:
    """Разбудить пересчёт рейтингов после синка, записавшего результаты.

    Зовётся из каждого батч-синка рядом с прогревом дашбордов: до 16.08.2026
    таблицы рейтингов ждали расписания (раз в 2 часа) или TTL, и человек видел
    у себя в ЛК уже засчитанную пробежку, а в таблице — прежние цифры.

    Возвращает True, если задача поставлена в очередь; False — если пересчёт
    уже запланирован недавним синком (окно склейки).
    """
    from app.config import get_settings

    settings = get_settings()
    window = settings.leaderboards_warm_debounce_seconds
    try:
        from app.core.redis_client import get_redis_client

        claimed = get_redis_client().set(_DEBOUNCE_KEY, "1", nx=True, ex=window)
    except Exception:
        # Redis недоступен — склейка не работает, но пересчёт важнее: он же и
        # запишет свежий снапшот, когда Redis вернётся.
        logger.exception("leaderboards warm: debounce check failed, scheduling anyway")
        claimed = True
    if not claimed:
        return False
    warm_leaderboards_cache.apply_async(
        # Очередь — явно, как везде в репозитории: маршрут задачи не должен
        # зависеть от того, дотянулся ли до вызова task_routes.
        queue="runpark",
        countdown=settings.leaderboards_warm_delay_seconds,
        # Просроченный прогрев догонять незачем: следующий синк или расписание
        # всё равно пересчитают сетку целиком (тот же приём, что у portal_cache).
        expires=window,
    )
    return True


def _acquire_running_lock() -> bool:
    try:
        from app.core.redis_client import get_redis_client

        return bool(
            get_redis_client().set(_RUNNING_KEY, "1", nx=True, ex=_RUNNING_TTL_SECONDS)
        )
    except Exception:
        logger.exception("leaderboards warm: lock failed, running without it")
        return True


def _release_running_lock() -> None:
    try:
        from app.core.redis_client import get_redis_client

        get_redis_client().delete(_RUNNING_KEY)
    except Exception:
        logger.exception("leaderboards warm: lock release failed")


# Очередь runpark: её воркер самый свободный (5 коротких синков в день) и не
# обслуживает user-очереди, так что долгий пересчёт не задержит пользовательский sync.
@celery_app.task(name="leaderboards.warm_cache", queue="runpark")
def warm_leaderboards_cache() -> dict[str, object]:
    """Пересчитывает и перезаписывает кэш всех рейтингов, не дожидаясь TTL.

    Без прогрева первый посетитель раздела после протухания кэша ждал бы
    расчёт (30+ секунд на метрику) прямо в запросе.

    Запускается двумя путями — по расписанию и от синка, записавшего протоколы
    (см. schedule_leaderboards_warm), поэтому первым делом берёт замок: два
    одновременных прогона считали бы одну и ту же сетку.
    """
    if not _acquire_running_lock():
        logger.info("leaderboards warm skipped: another run in progress")
        return {"skipped": "already_running"}
    db = get_session_factory()()
    results: dict[str, object] = {}
    # Прогреваем всю сетку кнопок каждого рейтинга: зачёт (абсолют/женский) ×
    # порог визитов × система. Каждое сочетание — свой снапшот в кэше, и без
    # прогрева первый заход на него ждал бы полный пересчёт прямо в запросе
    # (31.07.2026 именно так и вышло: «2+» × «5 вёрст» у туризма не
    # прогревались, посетитель ждал больше минуты). Наборы кнопок замкнутые,
    # так что сетка конечная и предсказуемая.
    variants: list[tuple[LeaderboardMetric, str, int, str, str, bool]] = []
    for metric in LEADERBOARD_METRICS:
        genders = ("all", "female") if metric in GENDERED_METRICS else ("all",)
        visits_options = (
            range(1, MAX_MIN_VISITS + 1) if metric in MIN_VISITS_METRICS else range(1, 2)
        )
        # Единица зачёта (площадки/города/регионы) — ещё одно измерение сетки у
        # туристических рейтингов; у остальных вариант ровно один.
        units = count_by_values(metric) or ("locations",)
        for gender in genders:
            for visits in visits_options:
                for platform in platform_filter_values(metric):
                    for unit in units:
                        # Фильтр «только явный дом» — ещё одно измерение сетки у
                        # дальности: без прогрева первый клик по нему ждал бы
                        # полный пересчёт прямо в запросе, как было с «2+ × 5
                        # вёрст» у туризма 31.07.2026.
                        home_filters = (
                            (False, True) if metric in AMBIGUOUS_HOME_METRICS else (False,)
                        )
                        for hide_home in home_filters:
                            variants.append(
                                (metric, gender, visits, platform, unit, hide_home)
                            )

    # Один источник на всю задачу: сырые выборки и справочники читаются из базы
    # один раз на рейтинг, а не на каждое сочетание фильтров (фильтры
    # применяются в Python — см. _MetricSource). Иначе десятки вариантов
    # означали бы десятки полных сканирований протоколов.
    source = make_snapshot_source(db)
    current_metric: str | None = None
    try:
        for metric, gender, min_visits, platform, count_by, hide_home in variants:
            if source is not None and metric != current_metric:
                # Сырые строки прошлого рейтинга больше не нужны — отпускаем их,
                # чтобы пик памяти остался как при расчёте одного снапшота.
                source.release()
                current_metric = metric
            key = metric if gender == "all" else f"{metric}:{gender}"
            if min_visits > 1:
                key = f"{key}:v{min_visits}"
            if platform != "all":
                key = f"{key}:p{platform}"
            if count_by != "locations":
                key = f"{key}:c{count_by}"
            if hide_home:
                key = f"{key}:home-sure"
            try:
                snapshot = refresh_leaderboard_cache(
                    db,
                    metric,
                    gender,
                    min_visits,
                    platform,
                    count_by,
                    source,
                    hide_ambiguous_home=hide_home,
                )
                results[key] = snapshot.get("entrants", 0)
                # Карта туристов живёт при снапшоте и обесценивается вместе с
                # ним — прогреваем её базовый вариант тем же проходом. Своим
                # try: карта — приятное дополнение к таблице, и её неудача не
                # повод помечать ошибкой прогрев самого рейтинга.
                if (
                    metric in TOURIST_MAP_METRICS
                    and min_visits == 1
                    and platform == "all"
                    and count_by == "locations"
                ):
                    try:
                        results[f"{key}:tmap"] = refresh_tourist_map_cache(
                            db, metric, snapshot
                        )
                    except Exception:
                        logger.warning(
                            "tourist map warm failed for %s", key, exc_info=True
                        )
                # Закрываем транзакцию сразу после варианта. Сетка одного
                # рейтинга (до нескольких десятков сочетаний фильтров) считается
                # в Python над уже прочитанными строками — база в это время не
                # нужна, но соединение висело бы «idle in transaction» минутами,
                # а мы сами ставим idle_in_transaction_session_timeout=60s
                # (см. _engine_connect_args в app/db/session.py). Postgres такое
                # соединение убивал, и первый же запрос следующего рейтинга
                # падал: на проде это каждый прогон был volunteer_locations —
                # ровно тот, что идёт после большой сетки locations.
                # rollback, а не commit: задача только читает. На консистентность
                # это не влияет — уровень READ COMMITTED, каждый запрос и внутри
                # одной транзакции видел свой снимок.
                db.rollback()
            except Exception:
                logger.exception("leaderboards warm failed for %s", key)
                db.rollback()
                # Здесь чистим кэш источника не из-за самого отката, а из-за
                # ошибки: чтение оборвалось на полпути, и что успело осесть в
                # кэше — доверия не заслуживает. Следующий рейтинг начинаем с
                # чистого листа.
                if source is not None:
                    source.release()
                results[key] = "error"
        # Рекорды локаций живут отдельным снапшотом (строка там — площадка, а
        # не участник), но обесцениваются от тех же новых протоколов — греем их
        # тем же проходом. Итог идёт в лог, а не в results: там сводка по
        # метрикам лидербордов, и посторонний ключ ломал бы её читателей.
        # Своим try: рейтинг рекордов не должен ронять прогрев лидербордов людей.
        try:
            warmed = refresh_location_records_rating_cache(db)
            db.rollback()
            logger.info("location records rating warmed: %s локаций", warmed)
        except Exception:
            logger.exception("leaderboards warm failed for location_records")
            db.rollback()

        # Рейтинг быстрых — тем же проходом и по той же причине: свой снапшот,
        # свой try, итог в лог, а не в results.
        try:
            logger.info("fastest rating warmed: %s slices", warm_fastest_rating(db))
            db.rollback()
        except Exception:
            logger.exception("fastest rating warm failed")
            db.rollback()
    finally:
        # close() у SQLAlchemy делает ROLLBACK, и на убитом сервером соединении
        # он сам бросает исключение — уже посчитанный и разложенный по Redis
        # прогрев уходил в «Task raised unexpected» (05.08.2026 дважды). Кэш к
        # этому моменту уже записан, терять из-за прощания с базой нечего.
        try:
            db.close()
        except Exception:  # noqa: BLE001 — прощание с базой не должно ронять прогрев
            logger.warning("leaderboards warm: не удалось закрыть сессию", exc_info=True)
        _release_running_lock()
    logger.info("leaderboards cache warmed: %s", results)
    return results
