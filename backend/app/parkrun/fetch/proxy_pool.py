"""Пул исходящих прокси для фетча parkrun.

Зачем. Очередь профилей (`profile_fetch_pending`) до сих пор разбиралась вручную
на машине разработчика: один IP, и первый же отказ WAF останавливал всю пачку.
На домашнем сервере поднято полтора десятка VPN-выходов, и если ходить через
них по кругу, очередь разбирается сама — упёрлись в защиту на одном выходе,
взяли следующий.

Список задаётся переменной PARKRUN_FETCH_PROXIES через запятую:

    PARKRUN_FETCH_PROXIES=socks5://127.0.0.1:10865,socks5://127.0.0.1:10866

Пусто — прежнее поведение, ходим со своего адреса. Никакой магии по умолчанию:
на проде прокси нет, и там всё должно работать как раньше.

Важно: и httpx, и браузер решателя капчи обязаны ходить через ОДИН выход.
Токен AWS WAF привязан к клиенту, и добытый с другого адреса он бесполезен.
"""

from __future__ import annotations

import logging
import os
import random

logger = logging.getLogger(__name__)

ENV_VAR = "PARKRUN_FETCH_PROXIES"


def load_proxies(raw: str | None = None) -> list[str]:
    """Разбирает список прокси. Пустые элементы и пробелы отбрасываем."""
    source = raw if raw is not None else os.environ.get(ENV_VAR, "")
    return [item.strip() for item in source.split(",") if item.strip()]


class ProxyPool:
    """Прокси по кругу, со случайной начальной точкой.

    Случайной — чтобы каждый прогон не начинался с одного и того же выхода:
    иначе первый в списке собирает все капчи, а остальные простаивают.
    """

    def __init__(self, proxies: list[str] | None = None, *, shuffle: bool = True) -> None:
        self._proxies = list(proxies) if proxies is not None else load_proxies()
        self._index = 0
        if self._proxies and shuffle:
            self._index = random.randrange(len(self._proxies))
        # сколько раз сменили выход за прогон — для лога и для предела попыток
        self.rotations = 0

    def __len__(self) -> int:
        return len(self._proxies)

    @property
    def enabled(self) -> bool:
        return bool(self._proxies)

    def current(self) -> str | None:
        if not self._proxies:
            return None
        return self._proxies[self._index % len(self._proxies)]

    def rotate(self) -> str | None:
        """Следующий выход. None, если пул пуст."""
        if not self._proxies:
            return None
        self._index = (self._index + 1) % len(self._proxies)
        self.rotations += 1
        nxt = self.current()
        logger.info("parkrun: переключаюсь на прокси %s (смена №%d)", nxt, self.rotations)
        return nxt

    def describe(self) -> str:
        if not self._proxies:
            return "прямое подключение (прокси не заданы)"
        return f"{len(self._proxies)} выходов, текущий {self.current()}"
