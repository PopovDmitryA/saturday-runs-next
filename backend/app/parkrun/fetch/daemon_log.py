"""Логирование Mac-демона parkrun/s95.

Две цели вывода:

* **Терминал** — только справочные строки «в моменте» (прогресс очереди,
  какие люди/локации обработаны, итог), каждая с таймстампом. Трейсбэков и
  библиотечного шума здесь нет.
* **Файл** (`PARKRUN_DAEMON_LOG`, рядом со скриптом в `data/`) — всё
  подряд: те же справочные строки, библиотечный INFO и полные трейсбэки
  `logger.exception`, тоже с таймстампами. Туда смотрят, когда что-то
  пошло не так; терминал остаётся чистым.

Справочные строки шлём через `cline()`; трейсбэки — обычным
`logger.exception(...)` в модулях, они попадают только в файл.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Логгер справочных строк «в моменте». Свой stdout-хендлер (терминал) +
# propagate в root (файл). Библиотечные логгеры stdout-хендлера не имеют,
# поэтому их трейсбэки в терминал не попадают.
CONSOLE_LOGGER_NAME = "parkrun.daemon.console"
console = logging.getLogger(CONSOLE_LOGGER_NAME)


def cline(message: str) -> None:
    """Справочная строка в терминал (и в файл-лог) с таймстампом."""
    console.info(message)


class _DropLibraryInfo(logging.Filter):
    """Режет из файла только БИБЛИОТЕЧНЫЙ INFO (httpx, DB flush, page-load),
    но оставляет справочные строки демона и всё WARNING+ (включая трейсбэки).
    Без этого QUIET выпиливал из файла прогресс-контекст, и трейсбэки
    оставались без «кто/что обрабатывался» — файл становился бесполезным."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING:
            return True
        return record.name.startswith(CONSOLE_LOGGER_NAME)


def setup_daemon_logging(log_file: Path, *, quiet: bool = False) -> Path:
    """Настроить файл-лог (полный) и чистый терминал. Возвращает путь к логу."""
    log_file = log_file.expanduser()
    log_file.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    # Файл — всё: справочные строки демона + библиотечные логи + трейсбэки.
    # quiet НЕ трогает справочные строки и WARNING+ (иначе трейсбэки без
    # контекста), а лишь режет библиотечный INFO-шум.
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    if quiet:
        file_handler.addFilter(_DropLibraryInfo())
    root.addHandler(file_handler)

    # Терминал — только строки из cline(), коротким форматом с таймстампом.
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
    for handler in list(console.handlers):
        console.removeHandler(handler)
    console.addHandler(console_handler)
    console.setLevel(logging.INFO)
    console.propagate = True  # справочные строки заодно уходят в файл

    return log_file
