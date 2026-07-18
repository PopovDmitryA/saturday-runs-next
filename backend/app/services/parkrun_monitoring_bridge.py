"""Мост к соседнему проекту parkrun-monitoring (~/Projects/parkrun-monitoring).

Mac-демон чередует очередь сайта с задачами мониторинга: обновление
недельной статистики стран (results-service закрыт WAF'ом с серверного IP,
поэтому ходим с Mac), проход по eventhistory-саммари локаций и push
собранного на сервер. Все вызовы — subprocess к CLI мониторинга; если
каталог не настроен (PARKRUN_MONITORING_DIR) или бинарь отсутствует,
каждая функция тихо возвращает None и демон работает как раньше.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def _monitoring_binary() -> Path | None:
    root = os.environ.get("PARKRUN_MONITORING_DIR", "").strip()
    if not root:
        return None
    binary = Path(root) / ".venv" / "bin" / "parkrun-monitoring"
    return binary if binary.exists() else None


def _run(args: list[str], timeout: int) -> str | None:
    binary = _monitoring_binary()
    if binary is None:
        return None
    try:
        result = subprocess.run(
            [str(binary), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=binary.parents[2],
        )
    except subprocess.TimeoutExpired:
        logger.warning("parkrun-monitoring %s: timeout %ss", args, timeout)
        return f"monitoring {' '.join(args)}: timeout"
    output = (result.stdout + result.stderr).strip().splitlines()
    tail = output[-1] if output else ""
    if result.returncode != 0:
        logger.warning("parkrun-monitoring %s failed: %s", args, tail)
        return f"monitoring {' '.join(args)}: exit {result.returncode} — {tail}"
    return f"monitoring: {tail}"


def refresh_monitoring_stats() -> str | None:
    """Обновить каталог и недельную статистику стран в локальной БД мониторинга."""
    return _run(["sync", "--no-notify"], timeout=900)


def monitoring_history_step(limit: int = 1) -> str | None:
    """Стянуть eventhistory-саммари следующих `limit` локаций (самых несвежих).

    `--push-each` пишет каждую локацию на сервер сразу после выкачки: если
    прогон прервётся (Ctrl+C, капча, закрытый ноутбук), собранное уже в
    канонической БД. Коннекты мультиплексируются, см. push_to_server.
    """
    return _run(
        ["fetch-history", "--limit", str(limit), "--push-each"],
        timeout=180 * limit + 120,
    )


def push_monitoring_to_server() -> str | None:
    """Отправить собранную статистику и историю в каноническую БД на сервере."""
    return _run(["push"], timeout=600)
