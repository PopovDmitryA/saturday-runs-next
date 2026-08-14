"""Страна локации хранится по-русски — одно название на одну страну.

Английские написания попадали в БД двумя путями: заглушкой профильного импорта
(parkrun.org.uk отдаёт «United Kingdom» для площадки в любой точке мира) и
легаси-миграцией. В выдаче такая строка выглядит отдельной страной: на 08.08.2026
в базе одновременно жили «Великобритания» (2143 строки) и «United Kingdom» (367),
и любая группировка по стране разъезжалась надвое.

Обратный геокод у нас ходит с accept-language=ru и почти всегда отдаёт русское
название сам; этот словарь — страховка на заглушки и легаси. Незнакомое значение
пропускаем как есть: выдумывать перевод хуже, чем сохранить исходник.
"""

from __future__ import annotations

RUSSIAN_COUNTRY_BY_ALIAS: dict[str, str] = {
    # Заглушка профильного импорта parkrun и её родня.
    "united kingdom": "Великобритания",
    "united kingdom of great britain and northern ireland": "Великобритания",
    "great britain": "Великобритания",
    "uk": "Великобритания",
    "england": "Великобритания",
    "scotland": "Великобритания",
    "wales": "Великобритания",
    "northern ireland": "Великобритания",
    # Легаси-написания наших же стран.
    "russia": "Россия",
    "russian federation": "Россия",
    "belarus": "Беларусь",
    "белоруссия": "Беларусь",
    "serbia": "Сербия",
    "republic of serbia": "Сербия",
    # Страны, где есть площадки наших систем (parkrun — мировой каталог).
    "australia": "Австралия",
    "austria": "Австрия",
    "belgium": "Бельгия",
    "canada": "Канада",
    "denmark": "Дания",
    "eswatini": "Эсватини",
    "finland": "Финляндия",
    "france": "Франция",
    "georgia": "Грузия",
    "germany": "Германия",
    "ireland": "Ирландия",
    "italy": "Италия",
    "japan": "Япония",
    "kazakhstan": "Казахстан",
    "lithuania": "Литва",
    "malaysia": "Малайзия",
    "mozambique": "Мозамбик",
    "namibia": "Намибия",
    "netherlands": "Нидерланды",
    "new zealand": "Новая Зеландия",
    "norway": "Норвегия",
    "poland": "Польша",
    "singapore": "Сингапур",
    "south africa": "ЮАР",
    "sweden": "Швеция",
    "switzerland": "Швейцария",
    "tajikistan": "Таджикистан",
    "turkey": "Турция",
    "türkiye": "Турция",
    "united states": "США",
    "united states of america": "США",
    "usa": "США",
    "argentina": "Аргентина",
}


def normalize_country_name(value: str | None) -> str | None:
    """Русское название страны для записи в locations.country.

    Пустую строку считаем отсутствием страны — иначе `country or row.country`
    в upsert_location принимает её за значение и затирает уже известное.
    """
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    return RUSSIAN_COUNTRY_BY_ALIAS.get(cleaned.lower(), cleaned)
