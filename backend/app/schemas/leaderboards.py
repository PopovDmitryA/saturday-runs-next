from __future__ import annotations

from pydantic import BaseModel


class LeaderboardCellResponse(BaseModel):
    value: int
    delta: int


class VolunteerRoleDetailResponse(BaseModel):
    """Одна освоенная роль в детализации строки: сколько волонтёрств и где."""

    role: str
    total: int
    # platform code -> число волонтёрств в этой системе (пустые не приходят).
    platforms: dict[str, int]


class WeekLocationResponse(BaseModel):
    """Где участник был за последнюю неделю (одна площадка колонки «Последняя
    неделя»): площадка и дата визита — тем же видом, что «Последняя победа»
    в победных рейтингах."""

    name: str
    slug: str | None = None
    date: str | None = None


class LeaderboardRowResponse(BaseModel):
    rank: int
    rank_delta: int
    display_name: str | None
    site_serial_id: int | None
    platforms: dict[str, LeaderboardCellResponse]
    total: int
    total_delta: int
    # Только у метрики wins: «топ-локация побед» — локация с максимумом побед.
    home_location: str | None = None
    home_location_wins: int | None = None
    # Только у победных рейтингов: глобальный рекорд участника и последняя
    # победа (у win_locations — последняя НОВАЯ локация с победой).
    best_time_sec: int | None = None
    best_time_display: str | None = None
    last_win_location: str | None = None
    last_win_location_slug: str | None = None
    last_win_date: str | None = None
    # Только у метрики volunteer_roles: любимая роль (чаще всего выходил) и
    # детализация «роль × система × волонтёрств» для разворачивания строки.
    top_role: str | None = None
    top_role_count: int | None = None
    role_details: list[VolunteerRoleDetailResponse] = []
    # Только у туристических рейтингов: площадки / города / регионы. Одно из
    # трёх совпадает с total — какое, решает фильтр count_by.
    locations_total: int | None = None
    cities_total: int | None = None
    regions_total: int | None = None
    # Колонка «Последняя неделя» (см. WEEK_LOCATIONS_METRICS).
    week_locations: list[WeekLocationResponse] = []


class LeaderboardResponse(BaseModel):
    metric: str
    gender: str = "all"
    # Порог визитов туристических рейтингов: локация идёт в зачёт от N посещений.
    min_visits: int = 1
    # Фильтр «по одной системе»: "all" или код платформы.
    platform: str = "all"
    # Единица зачёта туристических рейтингов: locations / cities / regions.
    count_by: str = "locations"
    # Кнопки фильтра «единица зачёта» (пусто — у рейтинга такого фильтра нет).
    count_by_options: list[str] = []
    # Есть ли у рейтинга колонка «Последняя неделя».
    has_week_locations: bool = False
    title: str
    description: str
    unit: str
    platform_columns: list[str]
    # Кнопки фильтра «по системе» для этого рейтинга и зачёта: "all" + коды систем.
    platform_options: list[str] = []
    rows: list[LeaderboardRowResponse]
    threshold: int
    median: int
    entrants: int
    latest_event_date: str | None
    week_start: str | None
    built_at: str | None


class MyLeaderboardRowResponse(BaseModel):
    metric: str
    min_visits: int = 1
    platform: str = "all"
    count_by: str = "locations"
    locations_total: int | None = None
    cities_total: int | None = None
    regions_total: int | None = None
    week_locations: list[WeekLocationResponse] = []
    display_name: str | None
    site_serial_id: int
    platforms: dict[str, LeaderboardCellResponse]
    total: int
    total_delta: int
    rank: int | None
    rank_delta: int | None
    included: bool
    threshold: int
    # True только в гендерном зачёте, когда пол участника (по истории финишей)
    # определённо не совпадает с выбранным — «появитесь после N» не показываем.
    gender_mismatch: bool = False
    home_location: str | None = None
    home_location_wins: int | None = None
    best_time_sec: int | None = None
    best_time_display: str | None = None
    last_win_location: str | None = None
    last_win_location_slug: str | None = None
    last_win_date: str | None = None
    top_role: str | None = None
    top_role_count: int | None = None
    role_details: list[VolunteerRoleDetailResponse] = []
