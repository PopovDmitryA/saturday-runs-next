from __future__ import annotations

from pydantic import BaseModel, Field


class UnifiedProtocolRow(BaseModel):
    """Строка единого протокола недели."""

    # Место в выбранном зачёте: система + пол + возрастная группа, что выбрано.
    # Пусто у строк без финишного времени — они уходят в хвост списка.
    place: int | None = None
    # Места по полу и по группе меряют результат по всей неделе своей СИСТЕМЫ
    # и от выбранного среза не зависят.
    gender_place: int | None = None
    gender_total: int | None = None
    age_group_place: int | None = None
    age_group_total: int | None = None

    name: str | None = None
    external_user_id: str | None = None
    serial_id: int | None = None
    is_unknown: bool = False

    gender: str | None = None
    age_category: str | None = None
    age_group: str | None = None
    age_grade: float | None = None

    finish_time_sec: int | None = None
    finish_time_display: str | None = None
    pace_display: str | None = None
    club_name: str | None = None

    platform_code: str
    location_slug: str | None = None
    location_name: str = ""
    city: str | None = None
    country: str | None = None
    event_date: str
    event_number: int | None = None
    # Место на своей площадке — то, что стоит в протоколе платформы.
    location_position: int | None = None

    is_pr: bool = False
    is_first_run: bool = False
    is_me: bool = False


class UnifiedProtocolPlatform(BaseModel):
    platform_code: str
    title: str
    finishers: int
    locations: int


class UnifiedProtocolBest(BaseModel):
    name: str | None = None
    time_display: str | None = None
    time_sec: int | None = None
    location_name: str = ""
    location_slug: str | None = None
    platform_code: str | None = None


class UnifiedProtocolSummary(BaseModel):
    # Плитка «финишёров»: цифра фасета пола — по системе и возрастной группе
    # сужается, по полу нет (она же показывает разбивку М/Ж).
    finishers: int
    # Строк в самом зачёте — знаменатель долей «N% финишёров».
    scope_finishers: int = 0
    male: int
    female: int
    unknown_gender: int
    locations: int
    # Волонтёры недели: записей (ролей) и людей. Считаются по зачёту СИСТЕМЫ и
    # не сужаются полом и возрастной группой — у волонтёрства их просто нет.
    volunteers: int = 0
    volunteer_people: int = 0
    avg_time_sec: int | None = None
    avg_time_display: str | None = None
    median_time_sec: int | None = None
    median_time_display: str | None = None
    best_male: UnifiedProtocolBest | None = None
    best_female: UnifiedProtocolBest | None = None
    debutants: int
    prs: int
    clubs_count: int
    # Сколько строк недели осталось за бортом: зарубежный parkrun (в БД по
    # таким площадкам не протокол, а результаты наших туристов, среди них —
    # junior parkrun на 2 км).
    skipped_foreign_parkrun: int = 0


class UnifiedProtocolGenderCounts(BaseModel):
    """Мужчины/женщины в зачёте СИСТЕМЫ — цифры на таблетках фильтра пола.

    Отдельно от summary: тот считается по выбранному срезу, и после выбора
    «женщины» таблетка «Мужчины» показывала бы 0.
    """

    male: int
    female: int
    unknown: int
    total: int


class UnifiedProtocolAgeGroup(BaseModel):
    age_group: str
    male: int
    female: int
    unknown: int
    total: int


class UnifiedProtocolWeek(BaseModel):
    saturday: str
    finishers: int
    events: int


class UnifiedProtocolResponse(BaseModel):
    week_start: str
    week_end: str
    saturday: str
    scope_platform: str | None = None
    gender: str | None = None
    age_group: str | None = None
    query: str | None = None
    platforms: list[UnifiedProtocolPlatform] = Field(default_factory=list)
    summary: UnifiedProtocolSummary
    gender_counts: UnifiedProtocolGenderCounts
    age_groups: list[UnifiedProtocolAgeGroup] = Field(default_factory=list)
    results: list[UnifiedProtocolRow] = Field(default_factory=list)
    # Свои строки недели целиком — чтобы не искать себя среди тысяч.
    my_results: list[UnifiedProtocolRow] = Field(default_factory=list)
    page: int
    pages: int
    per_page: int
    total: int
    previous_saturday: str | None = None
    next_saturday: str | None = None
    latest_saturday: str | None = None


class UnifiedProtocolWeeksResponse(BaseModel):
    """Все недели с протоколами — для выпадающего выбора недели."""

    weeks: list[UnifiedProtocolWeek] = Field(default_factory=list)
    latest_saturday: str | None = None
