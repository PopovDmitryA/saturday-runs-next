"""Схемы публичного поиска по сайту и журнала поисковых запросов."""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class SearchLocationResponse(BaseModel):
    slug: str
    name: str
    city: str | None = None
    platform_codes: list[str] = Field(default_factory=list)
    href: str


class SearchRegisteredPersonResponse(BaseModel):
    """Человек с открытым профилем на сайте — ведёт на наш /users/{хендл}."""

    kind: Literal["registered"] = "registered"
    display_name: str
    href: str
    avatar_url: str | None = None
    total_runs: int = 0
    total_volunteering: int = 0
    # Последний старт — в строке выдачи: по нему упорядочены однофамильцы, и
    # без даты на экране порядок выглядел случайным.
    last_run_date: dt.date | None = None
    top_location_name: str | None = None
    platform_codes: list[str] = Field(default_factory=list)
    # Слово запроса нашлось только в середине слова имени («лев» в
    # «Михалевском»): такие строки идут после всех совпадений с начала слова,
    # и окно показывает их отдельной группой.
    partial: bool = False


class SearchParticipantResponse(BaseModel):
    """Участник без открытого профиля на сайте: ни ссылки, ни внешнего адреса.

    Поля profile_url здесь нет намеренно — на чужие профили в системах мы не
    ссылаемся (согласия нет), а схема не даст ему просочиться случайно.
    """

    kind: Literal["participant"] = "participant"
    display_name: str
    total_runs: int = 0
    total_volunteering: int = 0
    last_run_date: dt.date | None = None
    top_location_name: str | None = None
    top_location_city: str | None = None
    platform_codes: list[str] = Field(default_factory=list)
    partial: bool = False
    # «Это вы?»: непрозрачный токен строки (search_claim_service) — по нему
    # окно показывает карточку, а после входа привязывается ровно этот
    # человек. Внутреннего id участника в выдаче нет.
    claim_token: str | None = None


SearchPersonResponse = Annotated[
    SearchRegisteredPersonResponse | SearchParticipantResponse,
    Field(discriminator="kind"),
]


class SearchLocationsAllLink(BaseModel):
    """«Все локации: Москва (41) →» — когда локаций места больше, чем в выдаче."""

    # Подпись места: «Москва», «Московская область».
    label: str
    # Что подставить в поиск каталога (/locations?q=…): как место записано в каталоге.
    query: str
    # Сколько строк покажет каталог с этим фильтром.
    count: int


class SiteSearchResponse(BaseModel):
    query: str
    # Запрос в другой раскладке, по которому и нашлось показанное; None — не переводили.
    corrected_query: str | None = None
    locations: list[SearchLocationResponse] = Field(default_factory=list)
    # Сколько локаций подошло всего (в выдаче — не больше восьми).
    locations_total: int = 0
    locations_all: SearchLocationsAllLink | None = None
    # Точных совпадений нет, показаны похожие по написанию («сокольнеки»).
    locations_similar: bool = False
    people: list[SearchPersonResponse] = Field(default_factory=list)
    people_truncated: bool = False
    # Людей нашли по имени и месту («Попов Дмитрий Королёв»): подпись места.
    people_place: str | None = None
    # Поиск людей этому запросу был нужен, но не сделан — все места поиска в
    # процессе заняты или запрос отменён по времени. Пустой people тогда
    # значит «не искали», а не «не нашли»: так и надо сказать человеку и не
    # писать этот поиск в журнал как пустой (ревью, SKEP-3).
    people_skipped: bool = False


ClaimViewerState = Literal["guest", "can_link", "already_yours", "platform_linked", "taken"]


class SearchClaimResponse(BaseModel):
    """Окно «Это вы?»: та же карточка, что строка выдачи, и что можно сделать.

    viewer_state гостю — всегда "guest": привязан ли человек к кому-то,
    гостю не сообщаем ни в каком виде.
    """

    display_name: str
    platform_code: str
    total_runs: int = 0
    total_volunteering: int = 0
    last_run_date: dt.date | None = None
    top_location_name: str | None = None
    top_location_city: str | None = None
    viewer_state: ClaimViewerState


class SearchLogTopQuery(BaseModel):
    query: str
    count: int
    zero_results_count: int
    clicks: int


class SearchLogZeroQuery(BaseModel):
    query: str
    count: int
    last_at: dt.datetime


class SearchLogNoClickQuery(BaseModel):
    """Искали и никуда не перешли — главный список недостающих синонимов."""

    query: str
    count: int
    # Из них выдача была совсем пустой.
    zero_results_count: int
    last_at: dt.datetime


class SearchLogClicksByKind(BaseModel):
    page: int = 0
    location: int = 0
    person: int = 0
    # Человек из протоколов — открыл «Это вы?».
    participant: int = 0
    none: int = 0


class SearchLogDaily(BaseModel):
    # Сутки по Москве.
    date: dt.date
    count: int


class SearchLogRecentItem(BaseModel):
    created_at: dt.datetime
    query: str
    corrected_query: str | None = None
    pages_found: int
    locations_found: int
    people_found: int
    clicked_kind: str | None = None
    clicked_target: str | None = None
    is_authed: bool
    is_mobile: bool


class SearchClaimFunnel(BaseModel):
    """Воронка «Это вы?» за период; единица — заход (ref токена), не нажатие."""

    # Нажали строку гостем → вошли → привязали.
    guest_opened: int = 0
    guest_logged_in: int = 0
    guest_linked: int = 0
    # Нажали уже вошедшими (гостем этот заход не открывали) → привязали.
    authed_opened: int = 0
    authed_linked: int = 0
    # «Это не я».
    declined: int = 0
    # Привязка не прошла: HTTP-код → заходов ("409": 3).
    failed_by_status: dict[str, int] = Field(default_factory=dict)


class AdminSearchLogResponse(BaseModel):
    period_days: int
    total: int
    zero_result_total: int
    no_click_total: int = 0
    no_click_queries: list[SearchLogNoClickQuery] = Field(default_factory=list)
    top_queries: list[SearchLogTopQuery] = Field(default_factory=list)
    zero_result_queries: list[SearchLogZeroQuery] = Field(default_factory=list)
    clicks_by_kind: SearchLogClicksByKind
    daily: list[SearchLogDaily] = Field(default_factory=list)
    recent: list[SearchLogRecentItem] = Field(default_factory=list)
    claim_funnel: SearchClaimFunnel = Field(default_factory=SearchClaimFunnel)
