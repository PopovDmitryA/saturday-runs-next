"""Хеш протокола по РАЗОБРАННОМУ содержимому, а не по сырому HTML.

До 21.09.2026 `protocol_source_hash` у 5 вёрст считался sha256 от страницы
целиком. Страница от запроса к запросу отличается (nonce, метки времени,
рекламные блоки), поэтому хеш «менялся» практически всегда: по журналу
scheduled_run_logs на dev — 37 014 «изменившихся» из 37 030 перечитанных.
Каждая такая перечитка переписывала все строки протокола (UPDATE на строку),
подрезала кэши локации и запускала прогревы дашбордов и рейтингов — ради
страницы, в которой ничего не поменялось (SYNC-5V-02).

Здесь хеш строится из того, что мы реально кладём в базу: отсортированный
список кортежей результатов и волонтёрств. Одинаковые данные → одинаковый
хеш независимо от порядка строк и любой обвязки страницы.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

from app.platform_adapters.canonical import CanonicalRunResult, CanonicalVolunteerResult

# Версия формата — в самом хеше: сменится состав кортежа, сменятся и хеши, и
# все протоколы один раз перечитаются по полному пути. Так и задумано.
_FORMAT_VERSION = "v1"


def _run_row(item: CanonicalRunResult) -> list[object]:
    # Поля — те, что попадают в run_results/participants из протокола. Имя и
    # id участника здесь тоже: замена «НЕИЗВЕСТНОГО» на человека у 5 вёрст
    # меняет ключ, но перестраховка дешёвая, а пропустить переименование в
    # других системах, если они начнут пользоваться этим хешем, не хотелось бы.
    return [
        item.external_result_key,
        item.external_user_id,
        item.participant_name,
        item.position,
        item.finish_time_sec,
        item.status,
        item.age_category,
        item.club_name,
        list(item.achievement_labels or ()),
    ]


def _volunteer_row(item: CanonicalVolunteerResult) -> list[object]:
    return [
        item.external_result_key,
        item.role,
        item.external_user_id,
        item.participant_name,
    ]


def protocol_content_hash(
    run_results: Iterable[CanonicalRunResult],
    volunteer_results: Iterable[CanonicalVolunteerResult],
) -> str:
    """sha256 разобранного протокола: строки результатов + волонтёрств.

    Сортируем по сериализованной строке, а не по кортежу: в кортежах есть
    None (позиция у строк из профиля, время у «НЕИЗВЕСТНЫХ»), и сравнение
    None с int роняло бы сортировку.
    """
    runs = sorted(json.dumps(_run_row(item), ensure_ascii=False) for item in run_results)
    volunteers = sorted(json.dumps(_volunteer_row(item), ensure_ascii=False) for item in volunteer_results)
    payload = json.dumps(
        {"format": _FORMAT_VERSION, "runs": runs, "volunteers": volunteers},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
