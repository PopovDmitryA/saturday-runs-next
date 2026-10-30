"""Планировщик забегов: ответы и запросы /planner, /settings/planner,
/organizer/{slug}/calendar, /organizer/{slug}/guests и /admin/start-dates.

Контракт — frontend/src/features/planner/plannerApi.ts. План — только
«планирую пробежать»: волонтёров планировщик не ведёт (они записываются в
кабинетах систем), намерения нет ни в запросах, ни в ответах. Постоянный план
(«каждую субботу») — /planner/series; его вхождения приходят теми же PlanItem
без id (строки нет), с `series` и стабильным `ref`. Ссылок на профили
систем здесь нет и быть не должно (test_no_external_profile_links): человек
отдаётся только `handle` своего профиля на сайте.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

StartKind = Literal["regular", "special"]


class StartDateItem(BaseModel):
    date: dt.date
    kind: StartKind
    # Название особой даты («Новогодний старт»); у суббот — None.
    title: str | None = None
    platform_code: str
    number_forecast: int | None = None
    # «09:00»; None — время уточняйте (особая дата).
    start_time: str | None = None
    # Только у особых дат: был ли у локации старт в эту дату год назад.
    last_year: bool | None = None
    cancelled: bool = False
    cancel_reason: str | None = None


class PlanLocation(BaseModel):
    key: str
    slug: str
    name: str
    platform_code: str | None = None


class PlanFactLocation(BaseModel):
    name: str
    slug: str


class PlanFact(BaseModel):
    # volunteered — планировал пробежать, а в протоколе волонтёр на этой
    # локации: на старте был, это не «не получилось». У прошедшего дня с
    # «Не бегаю»: ran — всё-таки пробежал (где угодно), rested — не бегал.
    # elsewhere — только у вхождения постоянного плана: здесь не был, а
    # пробежал в другом месте (location); серых отметок у серии нет, и
    # missed у вхождения страница не красит.
    status: Literal["ran", "volunteered", "missed", "unknown", "rested", "elsewhere"]
    finish_time_sec: int | None = None
    event_number: int | None = None
    # Где пробежал — только у elsewhere.
    location: PlanFactLocation | None = None


class PlanStart(BaseModel):
    kind: StartKind
    title: str | None = None
    start_time: str | None = None
    number_forecast: int | None = None
    cancelled: bool = False
    cancel_reason: str | None = None


class PlanSeriesRef(BaseModel):
    id: UUID


class PlanDisplaces(BaseModel):
    """Постоянный план, чью субботу вытеснил этот разовый план в другой
    локации: убрать его — суббота вернётся (окно «Вернуть / Не бегаю»)."""

    id: UUID
    location: PlanLocation


class PlanItem(BaseModel):
    # Строка плана или строка дня; None — вхождение постоянного плана (строки
    # нет: убрать его — «Пропущу», то есть «Не бегаю» на этот день).
    id: UUID | None = None
    # Стабильный ключ: «plan:<uuid>» у строк, «series:<uuid>:<YYYY-MM-DD>» у
    # вхождений — по нему ключуются списки и факты.
    ref: str
    plan_date: dt.date
    # day — строка дня без локации: отметка «Не бегаю».
    kind: Literal["plan", "day"]
    # «Не бегаю» на этот день — только у строки дня и только владельцу.
    rest: bool = False
    # «Публично / Приватно» — выбор владельца. У плана — имя на странице
    # локации и план в календаре профиля; у вхождения постоянного и у плана
    # на его же точку (series ≠ null) — выбор постоянного плана (менять — PUT
    # /planner/series); у строки дня — для отметки «Не бегаю». Итог для
    # чужих глаз ещё зависит от профиля и возраста
    # (GET /settings/planner → forced).
    is_public: bool = False
    location: PlanLocation | None = None
    start: PlanStart | None = None
    past: bool
    fact: PlanFact | None = None
    # Вхождение постоянного плана или разовый план на его же точку
    # (поглотивший субботу): для чужих глаз — «↻ Каждую субботу».
    series: PlanSeriesRef | None = None
    # Разовый план в другой локации, вытеснивший субботу постоянного.
    displaces: PlanDisplaces | None = None


class SeriesAwayDate(BaseModel):
    date: dt.date
    location: PlanLocation


class PlanSeries(BaseModel):
    """Постоянный план «каждую субботу» на одной локации."""

    id: UUID
    location: PlanLocation
    starts_on: dt.date
    # None — «пока не остановите».
    until: dt.date | None = None
    # «Публично / Приватно» — один выбор на весь план.
    is_public: bool = False
    # Срок (V1 «до…») кончился: until < сегодня — «Продлить».
    expired: bool
    # Точка сейчас проводит старты; нет — будущих суббот у плана нет.
    plannable: bool
    # План на паузе («Ещё ходите?»): имени на будущих датах нет. С учётом
    # протоколов — пробежал снова, и план уже не спит, даже до задачи сна.
    # У кончившегося срока — False: там «Продлить», а не «Продолжить».
    sleeping: bool
    sleeping_since: dt.date | None = None
    # Протокол будит план (есть привязка к системам точки). Нет — пробежек
    # человека не видно: сон наступает так же, через три старта, карточка не
    # обещает «продолжится сам» и зовёт привязать профиль (/dashboard#profiles),
    # вернуть план сейчас — «Продолжить».
    wakes_by_presence: bool = True
    # Ближайшее вхождение (с учётом «Пропущу» и поездок); None — нет.
    next_date: dt.date | None = None
    # Будущие «Не бегаю» на датах плана (до 52).
    skipped: list[dt.date]
    # Будущие поездки, вытеснившие субботу плана.
    away: list[SeriesAwayDate]


class SeriesHint(BaseModel):
    """«Вы пробежали здесь 9 из 10 последних стартов» — предложить постоянный план."""

    location: PlanLocation
    runs: int
    of: int


class MySeriesResponse(BaseModel):
    series: PlanSeries | None = None
    hint: SeriesHint | None = None


class SeriesSaveRequest(BaseModel):
    key: str = Field(min_length=1, max_length=255)
    # None — ближайшая дата календаря.
    starts_on: dt.date | None = None
    # None — «пока не остановлю».
    until: dt.date | None = None
    # Окно V3: снятые галочки — «Не бегаю» на эти даты, возвращённые — снять.
    skip: list[dt.date] = Field(default_factory=list, max_length=60)
    unskip: list[dt.date] = Field(default_factory=list, max_length=60)
    # True — заменить постоянный план на другой локации (после 409).
    replace: bool = False
    # True — первая дата занята разовым планом в другой локации: убрать его.
    replace_first: bool = False
    # «Публично / Приватно»; поля нет или null — у нового плана как у профиля
    # (открыт — публично), у правки — без изменений. При закрытом профиле
    # сохраняется «Приватно».
    is_public: bool | None = None


class SeriesSaveResponse(BaseModel):
    series: PlanSeries
    # Постоянный план на другой локации, который этот заменил.
    replaced: PlanLocation | None = None
    # Даты skip, которые не отметились (не день старта, на дату есть план).
    skipped_failed: list[dt.date] = Field(default_factory=list)


class SeriesConflictResponse(BaseModel):
    """409 на PUT /planner/series: detail — готовый вопрос («Ваш постоянный план
    сейчас: Кузьминки. Заменить на Зеленоград?…»), series — текущий план;
    повтор с replace=true заменяет."""

    detail: str
    series: PlanSeries


class SeriesResumeResponse(BaseModel):
    series: PlanSeries


class SeriesPreviewDate(BaseModel):
    date: dt.date
    kind: StartKind
    title: str | None = None
    start_time: str | None = None
    number_forecast: int | None = None
    # on — встанет в план; skipped — «Не бегаю»; away — там разовый план в
    # другой локации (other); cancelled — старт отменён (вхождение есть, но
    # бежать не выйдет).
    state: Literal["on", "skipped", "away", "cancelled"]
    other: PlanLocation | None = None


class SeriesPreviewResponse(BaseModel):
    key: str
    starts_on: dt.date
    until: dt.date | None = None
    # Все даты правила в сроке (без срока — на год).
    total: int
    # Сколько из них встанет (state=on).
    on: int
    dates: list[SeriesPreviewDate]
    # Как план выйдет из паузы — как PlanSeries.wakes_by_presence: протоколом
    # (есть привязка к системе, что проводит здесь старты) или только
    # «Продолжить».
    wakes_by_presence: bool = True


class NextStartDay(BaseModel):
    """Ближайший день старта (эта суббота или особая дата раньше неё) для
    карточки «Ближайший план»."""

    date: dt.date
    kind: StartKind
    title: str | None = None
    # На этот день есть план пробежки.
    planned: bool
    # План дня — постоянный («↻ каждую субботу · Пропущу»).
    series: bool = False
    # На этот день стоит «Не бегаю».
    rest: bool


class ProfilePlanPlace(BaseModel):
    name: str
    slug: str
    # Старт отменён — план есть, но бежать там не выйдет.
    cancelled: bool = False
    # План — постоянный («↻» перед названием).
    regular: bool = False


class ProfilePlanLine(BaseModel):
    """Строка в шапке профиля: «📅 Ближайший план: сб 31 октября — Зеленоград ·
    все планы». Ближайший публичный план на любую дату (хозяину — ближайший
    любой); до трёх локаций на особую дату."""

    date: dt.date
    kind: StartKind
    title: str | None = None
    places: list[ProfilePlanPlace]
    # Сколько дат с публичными планами впереди, считая эту, — столько дней
    # покажет вкладка «Планы».
    upcoming_count: int = 0
    # Другим этот план не виден (приватный, закрытый профиль, ребёнок) — его
    # видит только хозяин профиля.
    only_me: bool


class ProfilePlansTab(BaseModel):
    """Вкладка «Планы» профиля: public — активна; empty — публичных планов
    нет («Публичных планов нет»); closed — профиль закрыт."""

    state: Literal["public", "empty", "closed"]


class ProfilePlanCalendarDay(BaseModel):
    """День календаря «Планов» в профиле: публичные планы или публичное «Не
    бегаю» (rest, places пустой)."""

    date: dt.date
    kind: StartKind
    title: str | None = None
    places: list[ProfilePlanPlace]
    rest: bool = False


class ProfilePlanCalendar(BaseModel):
    """GET /planner/profile/calendar?handle=&month=YYYY-MM — вкладка «Планы»
    профиля, только для чтения. Дни — с сегодняшнего; заметок нет ни у кого."""

    month: str
    today: dt.date
    # Смотрит хозяин: ему — ссылка «Все мои планы» на /plans. Видит он то же,
    # что другие.
    owner: bool
    days: list[ProfilePlanCalendarDay]


GuestMarkKind = Literal["first_ever", "jubilee", "jubilee_here", "jubilee_platform", "first_here"]


class GuestMark(BaseModel):
    """Знаковый старт — столбец таблицы «Кто у нас бежит»: «первая пробежка»,
    «250-я пробежка», «100-я у нас», «150-я в 5 вёрст», «впервые у нас».
    Нумерованные — только на три ближайших старта локации, «впервые у нас» —
    на любую дату."""

    kind: GuestMarkKind
    label: str


class PlanPerson(BaseModel):
    """Человек с публичным планом на странице локации: рядом с именем только
    всего пробежек — «впервые у нас» и юбилеи отмечаются лишь в оргкабинете,
    чтобы блок участников был компактным (решение Дмитрия 25.10.2026)."""

    name: str
    # /users/{handle}; None — без ссылки.
    handle: str | None = None
    # Всего пробежек по всем системам — как в профиле; None — не посчитать
    # (ни одной привязки с участником).
    runs: int | None = None
    # Блок локации: постоянный план сюда («↻» у имени — бегает здесь каждую
    # субботу). Разовые и постоянные там одним списком.
    regular: bool = False


class OrganizerPlanPerson(PlanPerson):
    """Человек с публичным планом в оргкалендаре — с метками, которых нет на
    странице локации."""

    # До этой даты ни пробежки, ни волонтёрства на этой локации, и это его
    # первый план сюда; на любую дату.
    first_here: bool = False
    # Юбилейный номер, которым станет запланированная пробежка (по правилу
    # «Календаря юбилеев»: 10, дальше кратные 25), — только на три ближайших
    # старта локации; иначе None.
    jubilee: int | None = None
    # Знаковый старт — те же метки и подписи, что в «Кто у нас бежит».
    marks: list[GuestMark] = Field(default_factory=list)


class LocationPlansDate(BaseModel):
    """Дата старта с планами: разовые и постоянные одним списком (у
    постоянных — regular), total = len(people) + private + children."""

    date: dt.date
    kind: StartKind
    title: str | None = None
    start_time: str | None = None
    number_forecast: int | None = None
    total: int
    # Публичные планы: имя как в профиле, все; порядок — больше пробежек выше,
    # без привязок в конце, при равенстве — кто раньше отметился.
    people: list[PlanPerson]
    # Приватный план или закрытый профиль — только числом.
    private: int
    # Публичный план и открытый профиль, но по протоколам дети — числом,
    # без имени.
    children: int


class PlansElsewhereDate(BaseModel):
    """Дата, где у зрителя разовые планы в других локациях."""

    date: dt.date
    locations: list[PlanLocation]
    # Лимит дня исчерпан — сюда план на эту дату не поставить.
    full: bool
    # Сколько планов можно на этот день. Планов больше лимита (админ убрал
    # особую дату, объединение профилей) — заменой одного не уложиться,
    # сервер на replace_key ответит 409 «уберите лишние».
    limit: int = 1


class LocationPlansBlock(BaseModel):
    identity_key: str
    plannable: bool
    reason: str | None = None
    # «Скоро открытие»: планировать можно, окно просит сверить дату открытия
    # на сайте системы (official_url — страница локации там).
    upcoming: bool = False
    official_url: str | None = None
    dates: list[LocationPlansDate]
    # Планы зрителя на этой локации: все его разовые здесь и
    # вхождения постоянного на даты вкладок; гостю — [].
    mine: list[PlanItem]
    # Его разовые планы в других локациях на год вперёд — окно плана
    # подписывает такие даты «бегу в …»; гостю — [].
    elsewhere: list[PlansElsewhereDate] = []
    # Постоянный план зрителя на ЭТОЙ точке; нет или он на другой — None.
    my_series: PlanSeries | None = None
    # «Вы пробежали здесь 9 из 10…» — только если точка подсказки эта, а
    # постоянного плана у зрителя нет.
    series_hint: SeriesHint | None = None


class PlanLocationOption(BaseModel):
    """Локация в «Где бежите?» окна плана от даты: на эту дату её можно
    запланировать (те же правила, что календарь локации)."""

    key: str
    slug: str
    name: str
    city: str | None = None
    # Системы точки, что проводят старт в эту дату (особая — только свои).
    platforms: list[str]
    number_forecast: int | None = None
    # Старт в эту дату отменён — запланировать можно, бежать там не выйдет.
    cancelled: bool = False
    # У человека уже план сюда на эту дату (разовый или постоянный).
    mine: bool = False
    # Точка постоянного плана человека.
    regular: bool = False
    # «Скоро открытие»: старт ещё не проводился, дату сверить на сайте системы.
    upcoming: bool = False


class PlanLocationsResponse(BaseModel):
    """GET /planner/locations?date=&q= — окно плана от даты."""

    date: dt.date
    # Вид дня: особая дата любой системы — special (у локации без её систем
    # это обычная суббота, а будняя — мимо списка).
    kind: StartKind
    title: str | None = None
    # Сколько планов можно на этот день: 1, на особую дату — 3.
    limit: int
    # На этот день у человека «Не бегаю».
    rest: bool
    # Разовые планы человека на эту дату — их считает limit (вхождение
    # постоянного не в счёт). len(planned) >= limit — другую локацию не выбрать.
    planned: list[PlanLocation] = []
    # Без запроса (q короче двух знаков): постоянная, домашняя, точки планов
    # за 90 дней, частые по пробежкам за год — до 8.
    suggested: list[PlanLocationOption]
    # С запросом: поиск по названию, городу, региону — до 20.
    results: list[PlanLocationOption]
    # Найдено больше, чем в results: окно просит уточнить запрос.
    truncated: bool = False


class LocationStartDates(BaseModel):
    identity_key: str
    plannable: bool
    reason: str | None = None
    dates: list[StartDateItem]


class MyPlansResponse(BaseModel):
    today: dt.date
    items: list[PlanItem]


class NextPlanResponse(BaseModel):
    # Ближайший будущий план (на любую дату) — разовый или вхождение постоянного.
    plan: PlanItem | None = None
    # Ближайший день старта и что на нём у человека.
    day: NextStartDay
    # Постоянный план (в т.ч. на паузе — «Продолжить»).
    series: PlanSeries | None = None
    # «Вы пробежали здесь 9 из 10 последних стартов».
    series_hint: SeriesHint | None = None


class PlanSaveRequest(BaseModel):
    """Лишние поля (в т.ч. прежние `intent` и `note`) молча отбрасываются —
    как во всех схемах проекта: план всегда «пробежать», заметок нет."""

    key: str = Field(min_length=1, max_length=255)
    plan_date: dt.date
    # «Публично / Приватно»; поля нет или null — у нового плана как у профиля
    # (а на точке своего постоянного — как он), у старого — без изменений.
    # При закрытом профиле сохраняется «Приватно».
    is_public: bool | None = None
    # На особую дату («+ ещё локация» 1 января): постоянный план этого дня не
    # вытеснять, а закрепить разовым планом на его точке. На обычную дату не
    # действует.
    keep_series: bool = False
    # День заполнен — заменить план этого дня на этой точке (ключ локации из
    # elsewhere / planned): тот убирается в той же транзакции.
    replace_key: str | None = Field(default=None, max_length=255)


class PlanSaveResponse(BaseModel):
    plan: PlanItem
    # Постоянный план, чью субботу вытеснил этот план («В Зеленограде 14
    # ноября не считаем — вернётся, если уберёте этот план»); иначе None.
    displaced: PlanLocation | None = None
    # План на точку своего постоянного, а тот на паузе — предложить «Продолжить».
    series_sleeping: bool = False


class RestRequest(BaseModel):
    # True — планы пробежки на этот день удалить и отметить «Не бегаю»;
    # False — при планах ответ 409 со списком планов.
    replace: bool = False
    # Видна ли отметка в календаре профиля; null — у новой как у профиля, у
    # стоящей — без изменений (повтор с полем — правка публичности).
    is_public: bool | None = None


class RestResponse(BaseModel):
    # Строка дня с rest=True.
    plan: PlanItem


class RestConflictResponse(BaseModel):
    """409 на «Не бегаю» поверх планов: detail — готовый вопрос для окна
    подтверждения, plans — что уйдёт при replace=true."""

    detail: str
    plans: list[PlanItem]


class PlannerSettingsResponse(BaseModel):
    """GET /settings/planner — переключатель «Публично / Приватно» в окне плана."""

    # Умолчание переключателя у нового плана — как профиль: открыт — публично.
    default_public: bool
    # Почему имя не видно при любом выборе: закрытый профиль (переключатель
    # выключен, «Профиль закрыт — планы видите только вы») или ребёнок
    # (переключатель есть, публичный план виден только числом «ребёнок»).
    forced: Literal["profile_private", "minor"] | None = None


class CalendarLocationBrief(BaseModel):
    slug: str
    name: str


class OrganizerPlanAudience(BaseModel):
    total: int
    people: list[OrganizerPlanPerson]
    private: int
    children: int


class OrganizerCalendarDay(BaseModel):
    date: dt.date
    kind: StartKind
    title: str | None = None
    # Номер прошедшего старта (из протокола).
    number: int | None = None
    number_forecast: int | None = None
    start_time: str | None = None
    past: bool
    # Всего планирующих: разовые и постоянные.
    planned: int
    # Разовые — только будущие даты (порядок — как у LocationPlansDate); у
    # прошедших — [] и 0: после старта важно, кто пришёл, а не кто собирался.
    people: list[OrganizerPlanPerson]
    private: int
    children: int
    # Постоянные: у будущих — как разовые, у прошедших — только total
    # (вхождения, а не «все, кто когда-то обещал»).
    regulars: OrganizerPlanAudience
    # Только прошедшие: сколько из планировавших разово были на старте
    # (пробежали или волонтёрили); None — протокола нет.
    came: int | None = None
    # То же для постоянных.
    regulars_came: int | None = None


class OrganizerCalendarResponse(BaseModel):
    location: CalendarLocationBrief
    month: str  # YYYY-MM
    days: list[OrganizerCalendarDay]


class GuestHome(BaseModel):
    """Домашняя локация человека — как её определяет сайт (выбранная руками,
    иначе по пробежкам, волонтёрствам и началу)."""

    name: str
    slug: str
    city: str | None = None
    # Дом — эта самая локация (по ключу идентичности: слаги у дома и у
    # страницы бывают разные).
    here: bool = False


class GuestGain(BaseModel):
    """Строка столбца «Челленджи»: label — пара слов («новая локация (31-я)»,
    «буква «К»», «Нумератор ПРО: 21 → 22», «🥈 Клуб Нельсона»), hint —
    полный текст подсказки (у номерных — по строке на челлендж)."""

    label: str
    hint: str


class GuestRow(BaseModel):
    name: str
    handle: str | None = None
    runs: int | None = None
    # Дни с пробежкой на этой локации (все системы точки); None — привязок
    # с участником нет, как у runs.
    runs_here: int | None = None
    first_here: bool
    # Номер «всего» из marks (kind=jubilee); столбец рисуется из marks.
    jubilee: int | None = None
    # Порядок: первая пробежка или юбилей всего, здесь, в системе, затем
    # «впервые у нас» (его поглощает «первая пробежка»).
    marks: list[GuestMark] = Field(default_factory=list)
    home: GuestHome | None = None
    # Что даст этот старт по челленджам — столбец «Челленджи» (если сбудутся
    # более ранние публичные планы человека); только на три ближайших старта,
    # как юбилеи; у состава постоянных пусто.
    gains: list[GuestGain] = Field(default_factory=list)


class GuestRegulars(BaseModel):
    """Постоянные на дату: кто (handles — строки regulars_people, в их порядке),
    сколько, приватные и дети; метки — только у дат горизонта юбилеев."""

    total: int
    private: int
    children: int
    handles: list[str]
    marks: dict[str, list[GuestMark]] = Field(default_factory=dict)
    # Челленджи постоянных на эту дату — по хендлу, как marks.
    gains: dict[str, list[GuestGain]] = Field(default_factory=dict)


class OrganizerGuestsDate(BaseModel):
    """Дата с планами: total = len(people) + private + children +
    regulars.total; people — разовые, больше пробежек выше, без привязок в конце."""

    date: dt.date
    kind: StartKind
    title: str | None = None
    total: int
    private: int
    children: int
    people: list[GuestRow]
    regulars: GuestRegulars


class OrganizerGuestsResponse(BaseModel):
    location: CalendarLocationBrief
    weeks: int
    dates: list[OrganizerGuestsDate]
    # Постоянные этой локации один раз — те же столбцы, без меток.
    regulars_people: list[GuestRow] = Field(default_factory=list)


class GuestPerson(BaseModel):
    name: str
    handle: str


class GuestPlannedDate(BaseModel):
    date: dt.date
    kind: StartKind
    title: str | None = None


class GuestStats(BaseModel):
    # Как в кабинете человека; None — привязок с участником нет.
    runs: int | None = None
    volunteering: int | None = None
    locations: int | None = None
    # Здесь — дни на всех системах точки; None — привязок с участником нет.
    runs_here: int | None = None
    volunteering_here: int | None = None
    last_here: dt.date | None = None
    best_here_sec: int | None = None
    # До ближайшего запланированного старта сюда ни пробежки, ни волонтёрства.
    first_here: bool


class GuestLabel(BaseModel):
    label: str


class GuestChallenge(BaseModel):
    title: str
    text: str


class GuestVisitChallenges(BaseModel):
    """Что даст визит в другую дату сюда (в горизонте юбилеев)."""

    date: dt.date
    challenges: list[GuestChallenge]


class GuestSeries(BaseModel):
    starts_on: dt.date
    until: dt.date | None = None


class OrganizerGuestCardResponse(BaseModel):
    location: CalendarLocationBrief
    person: GuestPerson
    # Разовые даты и три ближайших субботы постоянного плана.
    planned_dates: list[GuestPlannedDate]
    # Постоянный план сюда: «↻ Каждую субботу с 31 октября».
    series: GuestSeries | None = None
    stats: GuestStats
    home: GuestHome | None = None
    # «Будет 100-я пробежка», «Будет 10-я пробежка здесь» — только на три
    # ближайших старта локации.
    jubilees: list[GuestLabel]
    # «Юбилеи считаем на три ближайших старта» — есть план сюда дальше них;
    # иначе None.
    jubilees_note: str | None = None
    # Дата ближайшего визита, что ещё впереди (сегодняшний, уже лёгший в
    # протокол, и отменённый старт — не впереди); None — таких нет.
    challenges_date: dt.date | None = None
    # Что даст этот визит по челленджам сайта; нечего сказать — [].
    challenges: list[GuestChallenge]
    # «Номерные челленджи считаем на три ближайших старта» — визит дальше
    # них, и роста «Нумератора» и подобных у него не считаем; иначе None.
    challenges_note: str | None = None
    # Следующие даты сюда в горизонте юбилеев — то же, что их строки в
    # столбце «Челленджи»; даты, где визит ничего не даст, не приходят.
    later_visits: list[GuestVisitChallenges] = Field(default_factory=list)


class SpecialStartDateItem(BaseModel):
    id: UUID
    start_date: dt.date
    title: str
    platforms: list[str]


class SpecialStartDateDraft(BaseModel):
    start_date: dt.date
    title: str = Field(max_length=200)
    platforms: list[str] = Field(default_factory=list, max_length=10)


class SpecialStartDateListResponse(BaseModel):
    items: list[SpecialStartDateItem]


class PublicSpecialDate(BaseModel):
    """Особая дата для календаря кабинета: без id — правят её только в админке."""

    date: dt.date
    title: str
    platforms: list[str]


class PublicSpecialDatesResponse(BaseModel):
    items: list[PublicSpecialDate]
