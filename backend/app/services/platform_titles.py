"""Человекочитаемые названия систем.

В platforms.name лежат исторические варианты («с95», «Runpark»), поэтому в
тексты для людей идёт этот словарь, а не поле из БД.
"""

from __future__ import annotations

PLATFORM_TITLES = {
    "five_verst": "5 вёрст",
    "s95": "S95",
    "parkrun": "parkrun",
    "runpark": "RunPark",
}
