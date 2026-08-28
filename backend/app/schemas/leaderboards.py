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
    """Последний старт участника за неделю: площадка и дата — тем же видом, что
    «Последняя победа» в победных рейтингах. Всегда ровно одна площадка: если
    стартов было несколько, берётся самый поздний."""

    name: str
    slug: str | None = None
    date: str | None = None


class LeaderboardRowResponse(BaseModel):
    rank: int
    rank_delta: int
    # Стабильный якорь строки: по нему карта туристов сопоставляет светофоры со
    # строками таблицы. Место дублируется при равных значениях, а site_serial_id
    # есть только у зарегистрированных — ни то, ни другое строку не опознаёт.
    row_key: str = ""
    display_name: str | None
    site_serial_id: int | None
    platforms: dict[str, LeaderboardCellResponse]
    total: int
    total_delta: int
    # Только у метрики wins: «топ-локация побед» — локация с максимумом побед.
    # У home_distance в этой же колонке домашняя локация, а вместо числа побед —
    # пометка о том, что выбор дома под вопросом (см. home_location_note).
    home_location: str | None = None
    home_location_slug: str | None = None
    home_location_wins: int | None = None
    # "ambiguous" — автовыбор шаткий, "manual_off_top" — выбрано руками вне тройки.
    home_location_note: str | None = None
    # Только у победных рейтингов: глобальный рекорд участника и последняя
    # победа (у win_locations — последняя НОВАЯ локация с победой). Тот же слот
    # «площадка + дата» занимает «Последнее открытие» у рейтинга открытий —
    # колонка одна и та же, подпись зависит от метрики.
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
    # Прогноз завершения туризма (только у FORECAST_METRICS): сколько
    # действующих единиц зачёта осталось и когда они закончатся, если брать по
    # новой каждый старт. Дата пустая, когда брать уже нечего.
    remaining_total: int | None = None
    forecast_date: str | None = None
    # Колонка «Последняя неделя» (см. WEEK_LOCATIONS_METRICS).
    week_location: WeekLocationResponse | None = None


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
    # Есть ли колонки «Осталось» и «Прогноз» (прогноз завершения туризма).
    has_forecast: bool = False
    # Фильтр «только очевидный дом» (есть у дальности от дома) и его состояние.
    has_home_filter: bool = False
    hide_ambiguous_home: bool = False
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
    # Как часто таблица пересчитывается по расписанию (страховка сверх
    # событийного прогрева от синка — см. REFRESH_INTERVAL_HOURS).
    refresh_hours: int = 2


class MyLeaderboardRowResponse(BaseModel):
    metric: str
    min_visits: int = 1
    platform: str = "all"
    count_by: str = "locations"
    locations_total: int | None = None
    cities_total: int | None = None
    regions_total: int | None = None
    remaining_total: int | None = None
    forecast_date: str | None = None
    week_location: WeekLocationResponse | None = None
    display_name: str | None
    site_serial_id: int
    platforms: dict[str, LeaderboardCellResponse]
    total: int
    total_delta: int
    rank: int | None
    # Место среди всех с ненулевой метрикой — приходит и до порога рейтинга,
    # в отличие от rank (тот только у прошедших порог).
    rank_overall: int | None = None
    rank_delta: int | None
    included: bool
    threshold: int
    # True только в гендерном зачёте, когда пол участника (по истории финишей)
    # определённо не совпадает с выбранным — «появитесь после N» не показываем.
    gender_mismatch: bool = False
    home_location: str | None = None
    home_location_slug: str | None = None
    home_location_wins: int | None = None
    home_location_note: str | None = None
    # Только у home_distance: когда участник менял домашнюю локацию руками.
    # Свежее built_at таблицы — значит в таблице ещё километры от прежнего дома.
    home_location_changed_at: str | None = None
    best_time_sec: int | None = None
    best_time_display: str | None = None
    last_win_location: str | None = None
    last_win_location_slug: str | None = None
    last_win_date: str | None = None
    top_role: str | None = None
    top_role_count: int | None = None
    role_details: list[VolunteerRoleDetailResponse] = []


class JournalItemResponse(BaseModel):
    """Одна отметка журнала посещаемости: дата и где это было."""

    date: str
    location: str | None = None
    slug: str | None = None
    platform: str
    # Только у журнала волонтёрств: роль (канонический ярлык).
    role: str | None = None
    # Только у журнала туризма: первый визит на площадку за всю историю.
    new: bool | None = None


class JournalRowResponse(BaseModel):
    row_key: str
    # Место в рейтинге (все годы, текущие фильтры) — как в таблице рядом.
    rank: int | None = None
    display_name: str | None = None
    site_serial_id: int | None = None
    # «Всего» рейтинга за всю историю — для сверки с таблицей.
    total: int | None = None
    # Счёт выбранного года в единицах метрики (у туризма — новые площадки года).
    year_total: int
    # Закрытый профиль: счёт года остаётся, отметки по датам не отдаются.
    private: bool = False
    items: list[JournalItemResponse] = []


class AttendanceJournalResponse(BaseModel):
    metric: str
    year: int
    years: list[int] = []
    platform: str = "all"
    offset: int = 0
    limit: int = 50
    # Сколько строк всего в рейтинге — для «показать ещё».
    total_rows: int = 0
    latest_event_date: str | None = None
    built_at: str | None = None
    rows: list[JournalRowResponse] = []
    # Строка зрителя — с его отметками, даже если он за пределами страницы.
    me: JournalRowResponse | None = None


class VolunteerRoleItem(BaseModel):
    """Роль в справочнике фильтра: ярлык и признаки для пресетов."""

    key: str
    label: str
    # Нужно ли быть на площадке и можно ли в этот же день пробежать.
    on_site: bool
    runnable: bool


class VolunteerRoleCatalogResponse(BaseModel):
    presets: list[str]
    # Рейтинги, где фильтр ролей вообще применяется.
    metrics: list[str]
    roles: list[VolunteerRoleItem]


class TouristMapLocationResponse(BaseModel):
    """Одна площадка на карте туристов."""

    # Тот же ключ идентичности площадки, что у точек карты локаций
    # (catalog_identity_key) — по нему витрина сводит число с точкой на карте.
    key: str
    name: str
    slug: str | None = None
    # Сколько человек из верхушки рейтинга здесь были — это и есть число у точки.
    visitors: int
    # Сколько всего визитов они сюда сделали.
    visits: int
    # То же число по ступеням фильтра «какой топ считать»: "10" -> 3, "50" -> 12.
    # Приходят все ступени разом, чтобы переключение не ходило на сервер.
    visitors_by_top: dict[str, int] = {}


class TouristMapPlatformVisitResponse(BaseModel):
    """Визиты одного участника на выбранную площадку в одной системе."""

    code: str
    visits: int
    first_date: str | None = None
    last_date: str | None = None


class TouristMapVisitResponse(BaseModel):
    """Светофор одной строки таблицы на выбранной площадке."""

    row_key: str
    visits: int
    first_date: str | None = None
    last_date: str | None = None
    platforms: list[TouristMapPlatformVisitResponse] = []


class TouristMapResponse(BaseModel):
    metric: str
    min_visits: int = 1
    platform: str = "all"
    # По скольким верхним строкам рейтинга посчитаны светофоры в таблице.
    limit: int
    # Ступени фильтра «какой топ считать на карте» — их набор задаёт бэкенд,
    # витрина не держит свою копию.
    top_steps: list[int] = []
    built_at: str | None = None
    # Строки рейтинга, попавшие в расчёт: у остальных светофор не горит вовсе.
    row_keys: list[str] = []
    locations: list[TouristMapLocationResponse] = []
    # Заполнены только при запросе конкретной площадки (location_key).
    location: TouristMapLocationResponse | None = None
    visits: list[TouristMapVisitResponse] = []
