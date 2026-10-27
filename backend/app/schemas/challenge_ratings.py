from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel


class ToNextStep(BaseModel):
    """Шаг к индексу +1: сколько добрать на локации. location=None — новые
    локации, count — сколько таких нужно."""

    location: str | None
    add: int
    count: int | None = None


class ChallengeRatingRow(BaseModel):
    rank: int
    # Сдвиг места за неделю; None — неделю назад значения не было вовсе.
    rank_delta: int | None
    row_key: str
    display_name: str | None
    site_serial_id: int | None
    value: int
    # Прирост значения за последнюю неделю.
    value_delta: int
    # День, когда человек впервые набрал текущее значение. По нему делятся
    # места при равном значении: выше тот, кто дошёл раньше.
    reached_on: date
    # p- и V-индекс: сколько не хватает до индекса на единицу больше. Второй
    # ключ места — при равном индексе выше тот, кому меньше.
    to_next: int | None = None
    # «60 секунд» и «Круглый год»: чего не хватает (секунды числом, даты как
    # «MM-DD», ближайшие первыми) — первые до MISSING_LIMIT, и сколько всего.
    missing: list[int | str] | None = None
    missing_total: int | None = None
    # Уилсон: от какого до какого номера старта закрыта цепочка.
    chain_from: int | None = None
    chain_to: int | None = None
    # Серия: лучшая серия ещё идёт.
    ongoing: bool | None = None
    # «Ударный год»: пробежек в текущем году.
    this_year: int | None = None
    # Последний выход на старт (бег или волонтёрство) в этом срезе.
    last_seen: date | None = None
    # Значение в каждой системе — только в общем зачёте.
    platforms: dict[str, int] | None = None


class ChallengeVariantOption(BaseModel):
    key: str
    label: str
    # У варианта может не быть какой-то системы (волонтёрская серия без parkrun).
    platform_options: list[str]


class ChallengeRatingResponse(BaseModel):
    code: str
    slug: str
    title: str
    icon: str
    description: str
    ranking_note: str
    # Пояснение к срезу (архив parkrun) — None, если пояснять нечего.
    scope_note: str | None
    # Показывать ли недельные сдвиги (в архивном срезе parkrun — нет).
    show_deltas: bool
    value_label: str
    unit_forms: list[str] | None
    # Склонение колонки «До следующего»; None — колонки нет.
    to_next_forms: list[str] | None
    # date — день достижения; year — только год («Ударный год»).
    reached_format: str
    # seconds | calendar_days — есть колонка «Не хватает».
    missing_kind: str | None
    # Есть колонка «Старты» (№от–№до цепочки) — у Уилсона.
    shows_chain: bool
    variant: str
    variant_label: str | None
    # Пусто — у челленджа один вариант, переключателя нет.
    variants: list[ChallengeVariantOption]
    platform: str
    platform_options: list[str]
    platform_labels: dict[str, str]
    # all — все участники, active — выходившие на старт за active_days дней.
    activity: str
    active_days: int
    refresh_hours: int
    limit: int
    rows: list[ChallengeRatingRow]
    entrants: int
    latest_event_date: date | None
    built_at: datetime | None
    # False — снимков ещё нет (первый пересчёт после выката не прошёл).
    ready: bool


class MyChallengeRatingRow(ChallengeRatingRow):
    included: bool
    # p- и V-индекс: где добрать до индекса +1 — только в строке «Вы».
    to_next_plan: list[ToNextStep] | None = None


class MyChallengeRatingResponse(BaseModel):
    code: str
    variant: str
    platform: str
    activity: str
    row: MyChallengeRatingRow | None
    included: bool


class ChallengeHubCard(BaseModel):
    code: str
    slug: str
    title: str
    icon: str
    value_label: str
    unit_forms: list[str] | None
    # Срез, который реально показан: система, которой у челленджа нет
    # (parkrun у V-индекса), откатывается на общий зачёт.
    platform: str
    rows: list[ChallengeRatingRow]
    me: MyChallengeRatingRow | None


class ChallengeHubResponse(BaseModel):
    platform: str
    cards: list[ChallengeHubCard]


class ChallengePlace(BaseModel):
    slug: str
    # Вариант рейтинга для ссылки; None — вариант по умолчанию.
    variant: str | None
    platform: str
    # None — человека в этом рейтинге нет (значение 0) или снимков ещё нет.
    rank: int | None
    entrants: int | None
    # Значение челленджа у человека в этом рейтинге.
    value: int | None = None


class MyChallengePlacesResponse(BaseModel):
    platform: str
    # Ключ — код карточки челленджа в «Моих достижениях».
    places: dict[str, ChallengePlace]
