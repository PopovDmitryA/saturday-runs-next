from __future__ import annotations

from pydantic import BaseModel


class FastestRowResponse(BaseModel):
    """Одна строка рейтинга быстрых — всегда конкретный финиш.

    В зачёте участников это лучший забег человека, в зачёте результатов — просто
    очередной быстрый забег: форма строки одна и та же, различается только то,
    как отобрана таблица.
    """

    rank: int
    # Стабильный якорь человека: у одного участника в зачёте результатов
    # несколько строк, и по row_key витрина их подсвечивает вместе.
    row_key: str
    display_name: str | None
    site_serial_id: int | None
    finish_time_sec: int
    finish_time_display: str
    platform: str
    location_name: str | None
    location_slug: str | None
    event_date: str | None
    # Ссылка на наш протокол этого старта (если у площадки есть страница).
    protocol_url: str | None
    # Возрастная группа забега — только там, где система печатает диапазон
    # (5 вёрст, RunPark). У parkrun в том же поле лежит age grade.
    age_group: str | None
    age_category: str | None
    gender: str | None


class FastestRatingResponse(BaseModel):
    mode: str
    platform: str
    gender: str
    age_group: str
    year: str
    limit: int
    title: str
    description: str
    platform_options: list[str]
    platform_labels: dict[str, str]
    age_group_options: list[str]
    # Система, у которой единственной есть возрастные группы: только при ней
    # витрина даёт выбрать ступень.
    age_group_platform: str
    # Годы в разрезе систем (ключ "all" — объединение). Русский parkrun жил
    # 2014–2022, остальные системы стартовали в 2022-м, и общий список лет
    # предлагал бы заведомо пустые сочетания.
    year_options_by_platform: dict[str, list[int]]
    rows: list[FastestRowResponse]
    built_at: str | None
    refresh_hours: int


class MyFastestRowResponse(BaseModel):
    mode: str
    platform: str
    gender: str
    age_group: str
    year: str
    row: FastestRowResponse | None
    rank: int | None
    included: bool
