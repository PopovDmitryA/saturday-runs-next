"""Снимок табло обхода: тяжёлое считаем по расписанию, страница берёт готовое.

Зачем. Каждый заход на /hq пересчитывал всё заново, и 5.5 из 6.7 секунд уходило
на `count(*) FROM runs` — перебор 124 млн строк. Ещё 2.7 с стоила история темпа
за весь период. Данные меняются медленно (боты добавляют тысячи строк в минуту
к сотням миллионов), поэтому считать их на каждый показ незачем.

Теперь разделы считаются раз в три минуты задачей Celery и лежат в таблице
`hq_snapshot` той же базы обхода. Роут делает один поиск по первичному ключу.
Если снимка ещё нет (первый запуск, пустая таблица) — считаем на месте, чтобы
страница работала и без прогретого расписания.

Запросы держим здесь, а не в роутах: задача и запасной путь обязаны считать
ровно одно и то же, иначе снимок и «живой» ответ разъедутся.

История темпа опирается на индекс в базе обхода:

    CREATE INDEX CONCURRENTLY ix_queue_fetched_at
        ON crawl_queue (fetched_at) WHERE fetched_at IS NOT NULL;

Он создан вручную 24.08.2026 (таблицей владеет парсер, не сайт, поэтому в
миграциях его нет). Без него группировка по часам перебирала все 1.5 ГБ
crawl_queue: 16 с против 2.9 с.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

SNAPSHOT_TABLE = "hq_snapshot"
QUEUE_TOTAL_FALLBACK = 6_693_994  # для прогноза, если запрос не отдал total


def world_dsn() -> str:
    return os.getenv("PM_WORLD_DSN", "")


def _rows(conn, sql: str) -> list[dict]:
    cur = conn.execute(sql)
    cols = [c.name for c in cur.description]
    return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


def _num(x: Any) -> float:
    return float(x) if x is not None else 0.0


def _forecast(remaining: int, rate_24h: int) -> dict:
    if rate_24h <= 0:
        return {"days": None, "date": None}
    days = remaining / rate_24h
    return {"days": round(days, 1),
            "date": (date.today() + timedelta(days=days)).isoformat()}


# --------------------------------------------------------------- разделы

def shared_values(conn) -> dict:
    """То, что нужно нескольким разделам сразу.

    `count(*) FROM runs` — самая дорогая часть снимка: 124 млн строк, около
    5.5 с. Разделы /hq и /world показывают одно и то же число, поэтому на
    прогон считаем его один раз и раздаём обоим.
    """
    return {"runs": int(_rows(conn, "SELECT count(*) AS n FROM runs")[0]["n"] or 0)}


def _runs_total(conn, shared: dict | None) -> int:
    if shared is not None and "runs" in shared:
        return int(shared["runs"])
    return int(shared_values(conn)["runs"])


def compute_hq(conn, shared: dict | None = None) -> dict:
    """Закрытое табло /hq: прогресс, VPN-выходы, бесплатные прокси."""
    prog = _rows(conn, """
        SELECT count(*) AS total,
               count(*) FILTER (WHERE status IN
                   ('collected','ok','not_found','registered_empty','unclassified')) AS done,
               count(*) FILTER (WHERE status='collected') AS in_processing,
               count(*) FILTER (WHERE fetched_at > now() - interval '24 hours') AS rate_24h,
               count(*) FILTER (WHERE fetched_at > now() - interval '1 hour') AS rate_1h,
               (SELECT count(*) FROM athletes WHERE source='crawl'
                   AND parsed_at > now() - interval '24 hours') AS parse_rate_24h,
               (SELECT count(*) FROM athletes WHERE source='crawl'
                   AND parsed_at > now() - interval '1 hour') AS parse_rate_1h
        FROM crawl_queue""")[0]
    vpn = _rows(conn, """
        SELECT name, account, collected_total, active_seconds, delay_sec,
               captcha_total, captcha_solved,
               to_char(last_captcha_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS last_captcha_at,
               CASE WHEN account='mac' AND (worker_heartbeat_at IS NULL
                         OR worker_heartbeat_at <= now() - interval '90 seconds')
                         THEN 'off'   -- локальный скрипт: нет heartbeat = не запущен
                    WHEN NOT enabled THEN 'off'
                    WHEN cooldown_until > now() THEN 'cooldown'
                    WHEN worker_heartbeat_at > now() - interval '90 seconds' THEN 'working'
                    ELSE 'queued' END AS status,
               GREATEST(0, EXTRACT(EPOCH FROM (cooldown_until - now())) / 3600) AS cooldown_hours,
               to_char(last_ok_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS last_ok_at,
               ban_level
        FROM sweep_exits WHERE account <> 'free'
        ORDER BY
            collected_total DESC,                   -- РЕЙТИНГ: сколько спарсил (главное)
            CASE WHEN account='mac' AND (worker_heartbeat_at IS NULL
                      OR worker_heartbeat_at <= now() - interval '90 seconds') THEN 3
                 WHEN NOT enabled THEN 3
                 WHEN cooldown_until > now() THEN 2
                 WHEN worker_heartbeat_at > now() - interval '90 seconds' THEN 0
                 ELSE 1 END,                        -- вторым: работает→очередь→отлёжка→выкл
            cooldown_until ASC NULLS FIRST, name""")
    free_sum = _rows(conn, """
        SELECT count(*) AS total,
               count(*) FILTER (WHERE last_ok_at IS NOT NULL
                   AND (cooldown_until IS NULL OR cooldown_until<=now())) AS active,
               count(*) FILTER (WHERE cooldown_until > now()) AS cooldown,
               COALESCE(sum(collected_total), 0) AS collected
        FROM free_proxies""")[0]
    free_top = _rows(conn, """
        SELECT proxy, collected_total, ban_level, delay_sec, active_seconds,
               CASE WHEN cooldown_until > now() THEN 'cooldown'
                    WHEN last_ok_at IS NOT NULL THEN 'working'
                    ELSE 'off' END AS status,
               GREATEST(0, EXTRACT(EPOCH FROM (cooldown_until - now())) / 3600) AS cooldown_hours,
               to_char(last_ok_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS last_ok_at
        FROM free_proxies
        ORDER BY collected_total DESC, last_ok_at DESC NULLS LAST LIMIT 300""")

    done = int(prog["done"] or 0)
    total = int(prog["total"] or 0) or QUEUE_TOTAL_FALLBACK
    remaining = max(0, total - done)
    rate_24h = int(prog["rate_24h"] or 0)
    runs_total = _runs_total(conn, shared)

    for r in vpn:
        r["captcha_total"] = int(r.get("captcha_total") or 0)
        r["captcha_solved"] = int(r.get("captcha_solved") or 0)
        r["collected_total"] = int(r["collected_total"] or 0)
        r["active_seconds"] = int(r["active_seconds"] or 0)
        r["delay_sec"] = round(_num(r["delay_sec"]), 1)
        r["cooldown_hours"] = round(_num(r["cooldown_hours"]), 1)
        r["ban_level"] = int(r["ban_level"] or 0)
    for r in free_top:
        r["collected_total"] = int(r["collected_total"] or 0)
        r["active_seconds"] = int(r["active_seconds"] or 0)
        r["delay_sec"] = round(_num(r["delay_sec"]), 1)
        r["cooldown_hours"] = round(_num(r["cooldown_hours"]), 1)
        r["ban_level"] = int(r["ban_level"] or 0)

    return {
        "progress": {
            "done": done, "total": total, "remaining": remaining,
            "pct": round(done / total * 100, 3) if total else 0.0,
            "collected": done,  # собрано = в папке (не распарсено) + распарсено
            "in_processing": int(prog["in_processing"] or 0),
            "runs": runs_total,
        },
        "rate_24h": rate_24h,
        "rate_1h": int(prog["rate_1h"] or 0),
        "parse_rate_24h": int(prog["parse_rate_24h"] or 0),
        "parse_rate_1h": int(prog["parse_rate_1h"] or 0),
        "forecast": _forecast(remaining, rate_24h),
        "vpn": vpn,
        "free": {
            "summary": {
                "total": int(free_sum["total"] or 0),
                "active": int(free_sum["active"] or 0),
                "cooldown": int(free_sum["cooldown"] or 0),
                "collected": int(free_sum["collected"] or 0),
            },
            "top": free_top,
        },
    }


def compute_public(conn, shared: dict | None = None) -> dict:
    """ПУБЛИЧНОЕ табло /world — только обезличенные агрегаты.

    Здесь НЕ должно появиться ничего из закрытого /hq: ни имён и ID атлетов,
    ни адресов прокси и имён VPN-выходов, ни счётчиков капч и уровней банов.
    Добавляя поле, спроси себя, готов ли показать его незнакомому человеку.
    """
    prog = _rows(conn, """
        SELECT count(*) AS total,
               count(*) FILTER (WHERE status IN
                   ('collected','ok','not_found','registered_empty','unclassified')) AS checked,
               count(*) FILTER (WHERE fetched_at > now() - interval '24 hours') AS rate_24h,
               count(*) FILTER (WHERE fetched_at > now() - interval '1 hour') AS rate_1h,
               (SELECT count(*) FROM athletes WHERE source='crawl' AND status='ok') AS profiles
        FROM crawl_queue""")[0]

    # "checked" — сколько ID проверено (включая пустые/несуществующие), это и есть
    # прогресс по диапазону. "profiles" — сколько из них оказались живыми бегунами.
    checked = int(prog["checked"] or 0)
    total = int(prog["total"] or 0) or QUEUE_TOTAL_FALLBACK
    remaining = max(0, total - checked)
    rate_24h = int(prog["rate_24h"] or 0)
    return {
        "progress": {
            "checked": checked, "total": total, "remaining": remaining,
            "pct": round(checked / total * 100, 3) if total else 0.0,
            "runs": _runs_total(conn, shared),
            "profiles": int(prog["profiles"] or 0),
        },
        "rate_1h": int(prog["rate_1h"] or 0),
        "rate_24h": rate_24h,
        "forecast": _forecast(remaining, rate_24h),
    }


def compute_rate_history(conn, shared: dict | None = None) -> dict:
    """Темп по часам за ВЕСЬ период. Окно на нужное число часов роут вырежет сам:
    группировка по всей crawl_queue стоит секунды, а нарезка готового списка —
    доли миллисекунды, и снимок один на любой запрошенный период.

    Метки — настоящий UTC. Раньше `to_char` рисовал время в поясе базы
    (Europe/Moscow) и просто дописывал «Z», а фронтенд читал это как UTC и
    переводил в Москву ещё раз: подписи оси уезжали на +3 часа, а отсечение
    незакрытого часа выбрасывало три последних. Отсюда `AT TIME ZONE 'UTC'`.

    Рядом кладём `now_hour` — тот же UTC-час на момент расчёта; по нему роут
    режет окно, не заглядывая в часы веб-сервера.
    """
    # Группируем по метке времени, а в текст переводим уже готовые сотни строк.
    # Если сгруппировать сразу по to_char, СУБД хэширует 10 млн строк текста —
    # замер: 16 с против 2.9 с.
    rows = _rows(conn, """
        SELECT to_char(h, 'YYYY-MM-DD"T"HH24:00:00"Z"') AS hour, collected
        FROM (
            SELECT date_trunc('hour', fetched_at AT TIME ZONE 'UTC') AS h, count(*) AS collected
            FROM crawl_queue
            WHERE fetched_at IS NOT NULL
            GROUP BY 1
        ) g
        ORDER BY h""")
    now_hour = _rows(conn, """
        SELECT to_char(date_trunc('hour', now() AT TIME ZONE 'UTC'),
                       'YYYY-MM-DD"T"HH24:00:00"Z"') AS now_hour
        """)[0]["now_hour"]
    return {"hours": [{"hour": r["hour"], "collected": int(r["collected"] or 0)} for r in rows],
            "now_hour": now_hour}


SECTIONS = {
    "hq": compute_hq,
    "public": compute_public,
    "rate_history": compute_rate_history,
}


def slice_hours(payload: dict, hours: int) -> dict:
    """Окно последних `hours` часов из полной истории. hours=0 — весь период.

    Отсчитываем от `now_hour` снимка, а не от часов этой машины: так окно не
    зависит от того, насколько разъехались часы прода и базы. Формат меток
    фиксирован, поэтому сравнение строк совпадает со сравнением времени.
    """
    rows = payload.get("hours") or []
    if not hours:
        return {"hours": rows}
    now_hour = payload.get("now_hour")
    if not now_hour:                      # снимок старого образца — отдаём как есть
        return {"hours": rows}
    edge = datetime.strptime(now_hour, "%Y-%m-%dT%H:00:00Z") - timedelta(hours=hours)
    edge_key = edge.strftime("%Y-%m-%dT%H:00:00Z")
    return {"hours": [r for r in rows if r["hour"] > edge_key]}


# ------------------------------------------------------- чтение и запись

def ensure_table(conn) -> None:
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {SNAPSHOT_TABLE} (
            key         text PRIMARY KEY,
            payload     jsonb NOT NULL,
            computed_at timestamptz NOT NULL DEFAULT now()
        )""")


def read_section(conn, key: str) -> tuple[dict, str] | None:
    """Готовый раздел и время расчёта, либо None — если снимка ещё нет."""
    try:
        cur = conn.execute(
            f"SELECT payload, to_char(computed_at AT TIME ZONE 'UTC', "
            f"                        'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') "
            f"FROM {SNAPSHOT_TABLE} WHERE key = %s", (key,))
    except Exception:            # таблицы ещё нет — первый запуск
        conn.rollback()
        return None
    row = cur.fetchone()
    return (row[0], row[1]) if row else None


def refresh(conn) -> dict[str, object]:
    """Пересчитать все разделы и уложить в таблицу. Возвращает сводку для лога."""
    ensure_table(conn)
    conn.commit()
    try:
        shared = shared_values(conn)
    except Exception as exc:  # noqa: BLE001 — посчитаем внутри разделов, каждый сам
        conn.rollback()
        logger.warning("снимок /hq: общие значения не посчитаны: %r", exc)
        shared = None
    done: list[str] = []
    failed: dict[str, str] = {}
    for key, fn in SECTIONS.items():
        try:
            payload = fn(conn, shared)
        except Exception as exc:  # noqa: BLE001 — один битый раздел не должен ронять остальные
            conn.rollback()
            failed[key] = repr(exc)[:200]
            logger.warning("снимок /hq: раздел %s не посчитан: %r", key, exc)
            continue
        conn.execute(
            f"INSERT INTO {SNAPSHOT_TABLE} (key, payload, computed_at) "
            f"VALUES (%s, %s::jsonb, now()) "
            f"ON CONFLICT (key) DO UPDATE SET payload = EXCLUDED.payload, "
            f"computed_at = EXCLUDED.computed_at",
            (key, json.dumps(payload, ensure_ascii=False)))
        conn.commit()
        done.append(key)

    # Разделы иногда уходят (так ушла вкладка «Атлеты»). Их строки в таблице
    # никто бы не удалил, и они висели бы вечно — подчищаем на каждом прогоне.
    if done:
        cur = conn.execute(
            f"DELETE FROM {SNAPSHOT_TABLE} WHERE key <> ALL(%s) RETURNING key",
            (list(SECTIONS),))
        dropped = [r[0] for r in cur.fetchall()]
        conn.commit()
        if dropped:
            logger.info("снимок /hq: убраны лишние разделы %s", dropped)
    return {"ok": not failed, "sections": done, "failed": failed}
