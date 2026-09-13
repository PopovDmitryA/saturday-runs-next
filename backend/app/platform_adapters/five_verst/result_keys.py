"""Ключи результатов 5 вёрст — один формат на оба источника.

Один и тот же финиш приходит к нам двумя путями: из протокола площадки
(bulk_parser) и из профиля участника (parser). Раньше каждый лепил ключ
по-своему — «slug:дата:участник» против «участник:дата:slug», — и один финиш
existed в базе под двумя именами.

Чем это кончалось. Синк профиля заводил отдельную строку: по ключу совпадения
нет, а запасной поиск по (событие, участник) не срабатывал, если в протоколе
человек ещё числился «НЕИЗВЕСТНЫМ» — там другой, синтетический участник. Дальше
протокольный синк (он авторитетный) такую строку удалял, она попадала в diff
журнала правок, и на ней падала запись правки. Плотинка №225 за 29.08.2026
залипла так намертво: каждый обход перекачивал площадку и откатывался
(Дмитрий 13.09.2026).

Поэтому формат тут ровно один, и оба парсера берут его отсюда.
"""

from __future__ import annotations

import re
from datetime import date

# Место старта в ключе: slug площадки, а когда его нет (в профиле ссылка на
# протокол не всегда есть) — нормализованное название.
UNKNOWN_LOCATION_KEY = "unknown"


def normalize_location_key(slug: str | None, location_name: str | None = None) -> str:
    if slug and slug.strip():
        return slug.strip()
    if location_name and location_name.strip():
        return location_name.strip().lower().replace(" ", "_")
    return UNKNOWN_LOCATION_KEY


def normalize_role_key(role: str) -> str:
    """Роль в ключе: строчными, без пунктуации. Пустая роль — просто волонтёр."""
    return re.sub(r"[^\w]+", "_", role.lower(), flags=re.UNICODE).strip("_") or "volunteer"


def run_result_key(location_key: str, event_date: date, external_user_id: str) -> str:
    """Финиш: площадка, дата, участник."""
    return f"{location_key}:{event_date.isoformat()}:{external_user_id}"


def volunteer_result_key(
    location_key: str,
    event_date: date,
    external_user_id: str,
    role: str,
) -> str:
    """Волонтёрство: то же плюс метка vol и роль — за старт их может быть несколько."""
    return f"{location_key}:{event_date.isoformat()}:vol:{external_user_id}:{normalize_role_key(role)}"


def unregistered_volunteer_result_key(
    location_key: str,
    event_date: date,
    who: str,
    role: str,
) -> str:
    """Волонтёр без профиля: вместо id — имя, а у безымянного порядковый номер.

    Бывает только в протоколе: в профиле человек по определению зарегистрирован.
    `who` приходит уже готовым (нормализованное имя или «nN») — второй раз его
    не трогаем, иначе поедут ключи у полумиллиона существующих строк.
    """
    return (
        f"{location_key}:{event_date.isoformat()}:vol:unregistered:"
        f"{who}:{normalize_role_key(role)}"
    )
