from datetime import date, datetime

from pydantic import BaseModel, Field


class VolunteerRoleCountResponse(BaseModel):
    role: str
    count: int


class MapLocationPlatformVisitResponse(BaseModel):
    platform_code: str
    location_name: str
    location_url: str | None = None
    run_dates: list[date] = Field(default_factory=list)
    volunteer_dates: list[date] = Field(default_factory=list)
    volunteer_roles: list[VolunteerRoleCountResponse] = Field(default_factory=list)


class MapLocationPointResponse(BaseModel):
    id: str
    catalog_identity_key: str | None = None
    location_slug: str | None = None
    name: str
    latitude: float
    longitude: float
    city: str | None = None
    region: str | None = None
    platform_codes: list[str] = Field(default_factory=list)
    active_platform: str | None = None
    location_url: str | None = None
    # «Не действует»: стартов нет дольше порога либо так сказал реестр системы.
    is_paused: bool = False
    # Отмена ближайшего старта — временно, площадка работает.
    is_cancelled: bool = False
    # Площадка объявлена, но ещё не стартовала.
    is_upcoming: bool = False
    run_count: int = 0
    volunteer_count: int = 0
    visit_count: int = 0
    last_visit_date: date | None = None
    run_dates: list[date] = Field(default_factory=list)
    volunteer_dates: list[date] = Field(default_factory=list)
    platform_visits: list[MapLocationPlatformVisitResponse] = Field(default_factory=list)


class MapLocationsResponse(BaseModel):
    points: list[MapLocationPointResponse] = Field(default_factory=list)
    total_locations: int = 0
    mapped_locations: int = 0
    unmapped_locations: int = 0


class UniqueLocationPlatformDetailResponse(BaseModel):
    platform_code: str
    location_name: str
    run_dates: list[date] = Field(default_factory=list)
    volunteer_dates: list[date] = Field(default_factory=list)
    volunteer_roles: list[VolunteerRoleCountResponse] = Field(default_factory=list)


class UniqueLocationDetailResponse(BaseModel):
    catalog_identity_key: str
    location_slug: str | None = None
    name: str
    city: str | None = None
    region: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    has_coordinates: bool = False
    is_paused: bool = False
    is_cancelled: bool = False
    is_foreign: bool = False
    run_count: int = 0
    volunteer_count: int = 0
    first_visit_date: date | None = None
    last_visit_date: date | None = None
    platforms: list[UniqueLocationPlatformDetailResponse] = Field(default_factory=list)


class UniqueLocationsPlatformSummaryResponse(BaseModel):
    platform_code: str
    location_count: int


class UniqueLocationsDetailResponse(BaseModel):
    locations: list[UniqueLocationDetailResponse] = Field(default_factory=list)
    total_locations: int = 0
    unique_run_locations: int = 0
    unique_volunteer_locations: int = 0
    mapped_locations: int = 0
    unmapped_locations: int = 0
    platform_summary: list[UniqueLocationsPlatformSummaryResponse] = Field(default_factory=list)


class LocationPagePlatformResponse(BaseModel):
    platform_code: str
    location_name: str
    external_key: str
    url: str | None = None
    first_event_date: date | None = None
    last_event_date: date | None = None
    events_count: int = 0
    is_active: bool | None = None


class LocationCourseRecordResponse(BaseModel):
    finish_time_sec: int
    finish_time_display: str
    runner_name: str | None = None
    runner_handle: str | None = None
    event_date: date | None = None
    platform_code: str | None = None


class LocationAttendanceRecordResponse(BaseModel):
    finishers: int
    event_date: date | None = None
    event_number: int | None = None
    platform_code: str | None = None


class LocationCourseRecordsResponse(BaseModel):
    male: LocationCourseRecordResponse | None = None
    female: LocationCourseRecordResponse | None = None


class LocationMilestoneResponse(BaseModel):
    """Участник, закрывший клубный порог на этом старте («25-й финиш»)."""

    name: str
    count: int


class LocationOneStepResponse(BaseModel):
    """Кому до клубного порога остался один финиш — жанр «в шаге до клуба»."""

    name: str
    next: int


class LocationLastEventResponse(BaseModel):
    event_date: date
    event_number: int | None = None
    platform_code: str
    finishers: int | None = None
    volunteers: int | None = None
    avg_time_sec: int | None = None
    avg_time_display: str | None = None
    best_male_time_sec: int | None = None
    best_male_time_display: str | None = None
    best_female_time_sec: int | None = None
    best_female_time_display: str | None = None
    # Те же метрики, что в журнале протоколов.
    debutants: int | None = None
    first_at_location: int | None = None
    prs: int | None = None
    male_finishers: int | None = None
    female_finishers: int | None = None
    best_male_name: str | None = None
    best_female_name: str | None = None
    milestones: list[LocationMilestoneResponse] = []
    one_step: list[LocationOneStepResponse] = []


class LocationPageStatsResponse(BaseModel):
    events_count: int = 0
    finishers_total: int = 0
    unique_participants: int = 0
    volunteers_total: int = 0
    unique_volunteers: int = 0
    avg_finish_time_sec: int | None = None
    avg_finish_time_display: str | None = None
    avg_finishers: int | None = None
    attendance_record: LocationAttendanceRecordResponse | None = None
    course_records: LocationCourseRecordsResponse = Field(default_factory=LocationCourseRecordsResponse)
    first_event_date: date | None = None
    last_event_date: date | None = None
    median_finish_time_sec: int | None = None
    median_finish_time_display: str | None = None
    last_event: LocationLastEventResponse | None = None
    # Насколько последний старт сдвинул агрегат по сравнению со всеми
    # предыдущими (avg_after - avg_before); отрицательное значение — быстрее.
    avg_finish_time_delta_sec: int | None = None
    median_finish_time_delta_sec: int | None = None


class LocationHistogramRowResponse(BaseModel):
    start_sec: int
    gender: str | None = None
    age_group: str | None = None
    count: int


class LocationHistogramResponse(BaseModel):
    bin_size_sec: int
    rows: list[LocationHistogramRowResponse] = Field(default_factory=list)


class LocationAgeGroupTopRowResponse(BaseModel):
    """Строка топ-5 внутри возрастной группы локации."""

    place: int
    name: str | None = None
    handle: str | None = None
    best_time_sec: int
    best_time_display: str


class LocationAgeGroupRecordResponse(BaseModel):
    """Рекорд локации в возрастной группе (parkrun исключён — другие категории)."""

    # Ключ строки: он же якорь, на который ссылается личная плитка «место в группе».
    key: str
    gender: str
    age_group: str
    finish_time_sec: int
    finish_time_display: str | None = None
    runner_name: str | None = None
    # Хендл профиля на сайте (slug или номер) — если участник привязал систему.
    runner_handle: str | None = None
    event_date: date | None = None
    platform_code: str | None = None
    # Топ-5 группы: раскрывается спойлером под строкой рекорда.
    top: list[LocationAgeGroupTopRowResponse] = Field(default_factory=list)
    # Размер группы — знаменатель для места: сколько всего участников бегало в
    # ней на этой локации и сколько у них финишей.
    runners_total: int = 0
    finishes_total: int = 0


class LocationCityNeighborResponse(BaseModel):
    """Площадка того же города в блоке «Другие площадки в …»."""

    slug: str
    name: str
    events_count: int = 0


class LocationDescriptionSectionResponse(BaseModel):
    """«Общественным транспортом», «Пешком», «На автомобиле» — по одной секции."""

    title: str | None = None
    text: str


class LocationDescriptionLinkResponse(BaseModel):
    title: str
    url: str


class LocationDescriptionResponse(BaseModel):
    """Описание площадки с сайта системы. Текст чужой — source_url обязателен."""

    platform_code: str
    schedule_text: str | None = None
    course_text: str | None = None
    travel_text: str | None = None
    travel_sections: list[LocationDescriptionSectionResponse] = Field(default_factory=list)
    links: list[LocationDescriptionLinkResponse] = Field(default_factory=list)
    source_url: str | None = None
    updated_at: datetime | None = None


class LocationPageResponse(BaseModel):
    slug: str
    identity_key: str
    name: str
    city: str | None = None
    region: str | None = None
    country: str | None = None
    is_paused: bool = False
    is_cancelled: bool = False
    latitude: float | None = None
    longitude: float | None = None
    map_url: str | None = None
    start_point_url: str | None = None
    platforms: list[LocationPagePlatformResponse] = Field(default_factory=list)
    description: LocationDescriptionResponse | None = None
    stats: LocationPageStatsResponse = Field(default_factory=LocationPageStatsResponse)
    histogram: LocationHistogramResponse = Field(default_factory=lambda: LocationHistogramResponse(bin_size_sec=10))
    age_group_records: list[LocationAgeGroupRecordResponse] = Field(default_factory=list)
    city_locations: list[LocationCityNeighborResponse] = Field(default_factory=list)


class LocationEventRowResponse(BaseModel):
    event_date: date
    platform_code: str
    event_number: int | None = None
    overall_number: int
    finishers: int | None = None
    volunteers: int | None = None
    best_male_time_sec: int | None = None
    best_male_time_display: str | None = None
    best_male_runner_name: str | None = None
    best_male_runner_serial_id: int | None = None
    best_female_time_sec: int | None = None
    best_female_time_display: str | None = None
    best_female_runner_name: str | None = None
    best_female_runner_serial_id: int | None = None
    avg_time_sec: int | None = None
    avg_time_display: str | None = None
    debutants: int | None = None
    first_at_location: int | None = None
    prs: int | None = None
    has_protocol: bool = False
    protocol_url: str | None = None
    is_attendance_record: bool = False
    is_course_record_male: bool = False
    is_course_record_female: bool = False
    is_platform_attendance_record: bool = False
    is_platform_course_record_male: bool = False
    is_platform_course_record_female: bool = False


class LocationEventsResponse(BaseModel):
    slug: str
    name: str
    total: int = 0
    items: list[LocationEventRowResponse] = Field(default_factory=list)


class LocationLeaderRunnerResponse(BaseModel):
    name: str | None = None
    handle: str | None = None
    runs_count: int
    best_time_sec: int | None = None
    best_time_display: str | None = None


class LocationLeaderVolunteerResponse(BaseModel):
    name: str | None = None
    handle: str | None = None
    count: int


class LocationLeadersResponse(BaseModel):
    slug: str
    name: str
    runners: list[LocationLeaderRunnerResponse] = Field(default_factory=list)
    volunteers: list[LocationLeaderVolunteerResponse] = Field(default_factory=list)


class LocationIndexItemResponse(BaseModel):
    slug: str
    identity_key: str
    name: str
    city: str | None = None
    region: str | None = None
    country: str | None = None
    platform_codes: list[str] = Field(default_factory=list)
    is_paused: bool = False
    is_cancelled: bool = False
    events_count: int = 0
    finishers_total: int = 0
    first_event_date: date | None = None
    last_event_date: date | None = None
    best_male_time_sec: int | None = None
    best_male_time_display: str | None = None
    best_female_time_sec: int | None = None
    best_female_time_display: str | None = None
    attendance_record_finishers: int | None = None
    attendance_record_date: date | None = None


class LocationsIndexResponse(BaseModel):
    items: list[LocationIndexItemResponse] = Field(default_factory=list)
    total: int = 0


class LastResultsItemResponse(BaseModel):
    slug: str
    identity_key: str
    name: str
    city: str | None = None
    region: str | None = None
    country: str | None = None
    platform_codes: list[str] = Field(default_factory=list)
    is_paused: bool = False
    is_cancelled: bool = False
    event_date: date
    event_platform_codes: list[str] = Field(default_factory=list)
    # Система первичного протокола — для адреса нашей страницы протокола.
    event_platform_code: str | None = None
    event_number: int | None = None
    is_last_saturday: bool = False
    finishers: int | None = None
    volunteers: int | None = None
    debutants: int | None = None
    prs: int | None = None
    best_male_time_sec: int | None = None
    best_male_time_display: str | None = None
    best_female_time_sec: int | None = None
    best_female_time_display: str | None = None
    avg_time_sec: int | None = None
    avg_time_display: str | None = None
    has_protocol: bool = False
    protocol_url: str | None = None


class LastResultsResponse(BaseModel):
    saturday_date: date | None = None
    items: list[LastResultsItemResponse] = Field(default_factory=list)
    total: int = 0


class CatalogLocationTableRowResponse(BaseModel):
    row_key: str
    catalog_identity_key: str
    location_id: str
    location_slug: str | None = None
    name: str
    city: str | None = None
    region: str | None = None
    country: str | None = None
    platform_code: str
    is_paused: bool = False
    is_cancelled: bool = False
    has_coordinates: bool = False
    location_url: str | None = None
    visited: bool = False
    first_visit_date: date | None = None
    # Система самого раннего визита — может отличаться от platform_code строки
    # (бегал в parkrun-эпоху, сейчас локация живёт в 5 вёрстах)
    first_visit_platform: str | None = None
    # {система: дата первого визита} — фронт пересчитывает отметку под фильтр систем
    visits_by_platform: dict[str, date] = Field(default_factory=dict)


class CatalogLocationsTableResponse(BaseModel):
    rows: list[CatalogLocationTableRowResponse] = Field(default_factory=list)
    total_rows: int = 0


class LocationAgeGroupStandingResponse(BaseModel):
    """Место пользователя в топе локации по одной его возрастной группе.

    Групп у человека столько, сколько он успел пройти на этой площадке:
    перешёл из «30–34» в «35–39» — будут обе, каждая со своими цифрами.
    Ключ `key` тот же, что у строки в «Рекордах по возрастным группам», —
    по нему плитка ссылается на топ-5 своей группы.
    """

    key: str
    gender: str
    age_group: str
    label: str
    runs_count: int
    best_time_sec: int
    best_time_display: str
    best_time_date: date | None = None
    last_run_date: date | None = None
    place: int | None = None
    total: int = 0


class LocationTopRoleResponse(BaseModel):
    role: str
    count: int


class LocationHomeDistanceResponse(BaseModel):
    """Плитка «сколько отсюда до дома» на странице локации."""

    # None — координат площадки или домашней локации нет; расстояние неизвестно.
    distance_km: float | None = None
    is_home: bool = False
    # Зелёная маркировка плитки — «здесь уже бегал», серая — «ещё не был».
    visited: bool = False
    run_count: int = 0
    home_name: str
    home_slug: str | None = None
    home_is_auto: bool = True


class MapPointNextStartResponse(BaseModel):
    """Прогноз ближайшего старта площадки в одной системе — для попапа карты."""

    platform_code: str
    number: int
    date: date
    # Сколько недель откручено от последнего известного старта: 1 — площадка
    # идёт вровень, больше — она пропускала субботы, и прогноз тем шатче.
    weeks_ahead: int = 1
    # Какой «Нумератор» закрывает этот номер. None — номер вне обоих диапазонов.
    challenge_code: str | None = None
    challenge_title: str | None = None
    # Даст ли старт +1: в сквозном зачёте челленджа и внутри своей системы.
    # None — аноним либо номер вне диапазонов: считать нечего.
    plus_one_overall: bool | None = None
    plus_one_platform: bool | None = None


class MapPointContextResponse(BaseModel):
    """Личный контекст точки карты: он грузится по клику, а не пачкой со всей
    картой, — на карте каталога три тысячи точек."""

    identity_key: str
    authenticated: bool = False
    next_starts: list[MapPointNextStartResponse] = Field(default_factory=list)
    home_distance: LocationHomeDistanceResponse | None = None


class MapGeoPingRequest(BaseModel):
    """Огрублённая отметка положения с карты. Координаты фронт присылает уже
    округлёнными до двух знаков — сервер округляет их ещё раз."""

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    # Погрешность определения в метрах, как её отдал браузер.
    accuracy_m: float | None = Field(default=None, ge=0)


class LocationPersonalStatsResponse(BaseModel):
    """Личная статистика пользователя на локации (блок «Вы на этой локации»)."""

    slug: str
    name: str
    runs_count: int = 0
    # Все пробежки пользователя по всем локациям — для строки «N% ваших стартов».
    total_runs: int = 0
    best_time_sec: int | None = None
    best_time_display: str | None = None
    best_time_date: date | None = None
    avg_time_sec: int | None = None
    avg_time_display: str | None = None
    first_run_date: date | None = None
    last_run_date: date | None = None
    volunteering_count: int = 0
    # Любимая роль на этой локации: чаще всего выходил (ярлыки систем схлопнуты
    # в канон, см. volunteer_role_taxonomy).
    top_volunteer_role: LocationTopRoleResponse | None = None
    # Место в топе локации по числу пробежек (та же группировка, что у лидеров).
    # Место в топе по пробежкам — внутри своего пола (пол материализован в
    # participants.gender). Общего места нет: см. build_location_personal_stats.
    gender: str | None = None
    rank_by_runs_gender: int | None = None
    runners_total_gender: int | None = None
    # Возрастные группы 5 вёрст, в которых пользователь здесь бегал.
    age_groups: list[LocationAgeGroupStandingResponse] = Field(default_factory=list)
    # Расстояние от домашней локации. None — дом не определился (нет пробежек),
    # плитку на странице тогда не показываем.
    home_distance: LocationHomeDistanceResponse | None = None


class ProtocolNeighbourResponse(BaseModel):
    """Соседний старт локации в сквозной хронологии (для стрелок «‹ ›»)."""

    platform_code: str
    event_date: date
    event_number: int | None = None
    overall_number: int | None = None
    # Цифры соседнего старта — для дельт «+37 к прошлому» на плитках.
    finishers: int | None = None
    volunteers: int | None = None
    avg_time_sec: int | None = None
    best_male_time_sec: int | None = None
    best_female_time_sec: int | None = None
    debutants: int | None = None
    first_at_location: int | None = None
    prs: int | None = None


class ProtocolClubResponse(BaseModel):
    name: str
    count: int


class ProtocolAgeGroupResponse(BaseModel):
    age_group: str
    male: int = 0
    female: int = 0
    unknown: int = 0
    total: int = 0


class ProtocolSummaryResponse(BaseModel):
    finishers: int = 0
    volunteers: int = 0
    male: int = 0
    female: int = 0
    unknown_gender: int = 0
    avg_time_sec: int | None = None
    avg_time_display: str | None = None
    median_time_sec: int | None = None
    median_time_display: str | None = None
    best_time_sec: int | None = None
    best_time_display: str | None = None
    last_time_sec: int | None = None
    last_time_display: str | None = None
    best_male_time_display: str | None = None
    best_male_runner_name: str | None = None
    best_female_time_display: str | None = None
    best_female_runner_name: str | None = None
    debutants: int = 0
    first_at_location: int = 0
    prs: int = 0
    location_prs: int = 0
    clubs_count: int = 0
    top_clubs: list[ProtocolClubResponse] = Field(default_factory=list)
    is_attendance_record: bool = False
    is_course_record_male: bool = False
    is_course_record_female: bool = False


class ProtocolResultResponse(BaseModel):
    position: int | None = None
    name: str | None = None
    external_user_id: str | None = None
    profile_url: str | None = None
    serial_id: int | None = None
    gender: str | None = None
    gender_position: int | None = None
    gender_total: int | None = None
    age_category: str | None = None
    age_group: str | None = None
    age_group_position: int | None = None
    age_group_total: int | None = None
    age_grade: float | None = None
    finish_time_sec: int | None = None
    finish_time_display: str | None = None
    pace_display: str | None = None
    club_name: str | None = None
    status: str | None = None
    # Финишёр без штрихкода («НЕИЗВЕСТНЫЙ») — витрина приглушает строку.
    is_unknown: bool = False
    is_pr: bool = False
    is_global_pr: bool = False
    is_location_pr: bool = False
    is_first_run: bool = False
    is_first_run_at_location: bool = False
    achievement_labels: list[str] = Field(default_factory=list)
    history_rank: int | None = None
    history_total: int | None = None
    # Какая это пробежка по счёту у участника: сквозная у связанных аккаунтов,
    # иначе в своей системе (см. run_number_all_systems).
    run_number: int | None = None
    run_number_all_systems: bool = False
    # Место времени в истории своей возрастной группы на площадке.
    age_group_history_rank: int | None = None
    age_group_history_total: int | None = None
    # Результат обновил рекорд своей возрастной группы на площадке (только 5в).
    is_age_group_record: bool = False
    is_me: bool = False


class ProtocolVolunteerResponse(BaseModel):
    name: str | None = None
    external_user_id: str | None = None
    profile_url: str | None = None
    serial_id: int | None = None
    roles: list[str] = Field(default_factory=list)
    # Роли, которые человек исполняет впервые в карьере.
    new_roles: list[str] = Field(default_factory=list)
    # Какое это волонтёрство по счёту в карьере (в своей системе).
    volunteer_number: int | None = None
    is_first_volunteering: bool = False
    is_first_here: bool = False
    is_me: bool = False


class LocationProtocolResponse(BaseModel):
    slug: str
    name: str
    city: str | None = None
    platform_code: str
    event_date: date
    event_number: int | None = None
    overall_number: int | None = None
    title: str | None = None
    source_url: str | None = None
    has_protocol: bool = False
    # Строк меньше, чем позиций или заявленных финишёров: так помечены
    # зарубежные parkrun-старты, где у нас только строки своих участников.
    is_partial: bool = False
    declared_finishers: int | None = None
    previous: ProtocolNeighbourResponse | None = None
    next: ProtocolNeighbourResponse | None = None
    summary: ProtocolSummaryResponse = Field(default_factory=ProtocolSummaryResponse)
    age_groups: list[ProtocolAgeGroupResponse] = Field(default_factory=list)
    results: list[ProtocolResultResponse] = Field(default_factory=list)
    volunteers: list[ProtocolVolunteerResponse] = Field(default_factory=list)
