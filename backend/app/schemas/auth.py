from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

MergeStrategy = Literal["union", "survivor_only"]
MergeConflictChoice = Literal["survivor", "merged"]


class LoginRequestResponse(BaseModel):
    request_token: str
    bot_url: str
    expires_in: int


class LoginRequestStatusResponse(BaseModel):
    # pending → confirmed (бот подтвердил, вкладка может забрать сессию) →
    # claimed; либо denied («Это не я»), linked / merge_required (привязка),
    # expired. link_sent — прежний статус, когда вход был только по ссылке.
    status: str
    merge_token: str | None = None
    # Бот перестал отмечаться — подтверждения не дождаться; вкладка предлагает
    # запасной путь через виджет, не дожидаясь истечения запроса.
    bot_alive: bool = True


class LoginRequestClaimResponse(BaseModel):
    redirect: str


class BotLoginContextRequest(BaseModel):
    request_token: str
    telegram_id: int


class BotLoginContextResponse(BaseModel):
    """Что бот показывает перед кнопкой «Подтвердить вход»."""

    status: str
    needs_consent: bool = False
    # Согласие уже поставлено галочкой на сайте — бот не переспрашивает.
    consent_given: bool = False
    link_mode: bool = False
    browser: str = ""
    os: str = ""
    city: str = ""
    requested_at_label: str = ""


class BotDenyRequest(BaseModel):
    request_token: str
    telegram_id: int


class BotConfirmRequest(BaseModel):
    request_token: str
    telegram_id: int
    telegram_username: str | None = None
    telegram_first_name: str | None = None
    telegram_last_name: str | None = None
    telegram_chat_id: int
    consent_accepted: bool = False


class BotConfirmResponse(BaseModel):
    magic_link: str


class BotLoginStatusRequest(BaseModel):
    telegram_id: int


class BotLoginStatusResponse(BaseModel):
    needs_consent: bool


class AuthIdentityResponse(BaseModel):
    provider: str
    external_id: str
    display_name: str | None = None
    email: str | None = None
    linked_at: datetime
    label: str


class DisplayNameSuggestion(BaseModel):
    """Алгоритм расходится с текущим выбором — предлагаем сменить источник."""

    name: str
    platform_code: str | None = None
    source_title: str


class NoAccountPlatformRequest(BaseModel):
    platform_code: str = Field(min_length=1, max_length=32)
    no_account: bool = True


class NoAccountPlatformsResponse(BaseModel):
    no_account_platforms: list[str] = Field(default_factory=list)


class UserResponse(BaseModel):
    id: UUID
    telegram_id: int | None = None
    telegram_username: str | None = None
    telegram_first_name: str | None = None
    telegram_last_name: str | None = None
    display_name: str | None = None
    # Как показывать имя: "auto" — полное, "initial" — «Иван П.». Свободного
    # ввода нет — имя считается из профилей беговых систем.
    display_name_style: str = "auto"
    # Прежнее имя для одноразовой плашки «имя теперь из профиля». NULL — не нужна.
    display_name_notice: str | None = None
    # Алгоритм расходится с зафиксированным источником: имя молча не меняем, а
    # показываем баннер со ссылкой в настройки. NULL — расхождения нет.
    display_name_suggestion: DisplayNameSuggestion | None = None
    consent_accepted: bool = False
    is_admin: bool = False
    # Есть ли доступ хоть к одной локации кабинета организатора (автодоступ по
    # волонтёрствам в роли организатора или ручной грант) — для пункта меню.
    is_organizer: bool = False
    avatar_url: str | None = None
    # Оригинал аватарки без пережатия — открывается по клику на аватарку.
    # NULL у аватарок, загруженных до появления оригиналов.
    avatar_full_url: str | None = None
    # Публичный адрес участника: /users/{public_slug или serial_id}. Нужен
    # фронту, чтобы кабинет жил на собственном публичном URL (26.07.2026).
    serial_id: int | None = None
    public_slug: str | None = None
    auth_identities: list[AuthIdentityResponse] = Field(default_factory=list)
    # Онбординг: системы, где человек отметил «у меня там нет аккаунта».
    onboarding_no_account_platforms: list[str] = Field(default_factory=list)

    @field_validator("onboarding_no_account_platforms", mode="before")
    @classmethod
    def _no_account_none_as_empty(cls, value: object) -> object:
        # У неперсистнутого User (в тестах) JSONB-поле ещё None: server_default
        # применяется только при INSERT.
        return [] if value is None else value

    model_config = {"from_attributes": True}

    @field_validator("display_name_style", mode="before")
    @classmethod
    def _default_style(cls, value: object) -> object:
        # У ещё не записанного в БД пользователя server_default не сработал, и
        # в поле лежит None. Для ответа это то же самое, что «auto».
        return "auto" if value is None else value


class DisplayNamePreferencesUpdate(BaseModel):
    """Выбор из селектора имени: стиль и, при желании, система-источник."""

    style: str = Field(default="auto", max_length=16)
    # Код системы («five_verst», «s95», …), из профиля которой брать имя.
    # None — авто-приоритет: 5 вёрст → S95 → RunPark → parkrun.
    platform_code: str | None = Field(default=None, max_length=32)


class DisplayNameSourceItem(BaseModel):
    platform_code: str | None = None
    source_title: str
    name: str
    name_initial: str
    last_run: str | None = None


class DisplayNameOptionsResponse(BaseModel):
    current: str | None = None
    style: str = "auto"
    # Зафиксированная система-источник; None — выбирается автоматически.
    source: str | None = None
    # Источник выбран человеком, а не алгоритмом.
    source_manual: bool = False
    auto_name: str | None = None
    auto_source: str | None = None
    suggestion: DisplayNameSuggestion | None = None
    notice: str | None = None
    sources: list[DisplayNameSourceItem] = Field(default_factory=list)


class MessageResponse(BaseModel):
    message: str = Field(default="ok")


class PlatformLinkPreviewItem(BaseModel):
    platform_code: str
    external_user_id: str
    display_name: str | None = None


class MergeConflictItem(BaseModel):
    """Система привязана в обоих профилях: одна учётка системы на аккаунт,
    поэтому объединить эти две привязки нельзя — человек выбирает одну."""

    platform_code: str
    survivor: PlatformLinkPreviewItem
    merged: PlatformLinkPreviewItem


class MergePreviewResponse(BaseModel):
    merge_token: str
    merged_user_id: str
    survivor_links: list[PlatformLinkPreviewItem]
    merged_links: list[PlatformLinkPreviewItem]
    conflicts: list[MergeConflictItem]
    requires_choice: bool
    default_strategy: MergeStrategy
    warning: str


class MergeConfirmRequest(BaseModel):
    merge_token: str
    #: union — привязки обоих профилей; survivor_only — только текущего.
    #: Третьего варианта нет: забрать чужие привязки, выбросив свои, нельзя.
    strategy: MergeStrategy = "union"
    #: {код системы: survivor|merged} — ответы по конфликтным системам.
    conflict_choices: dict[str, MergeConflictChoice] = Field(default_factory=dict)


class OAuthFinishRequest(BaseModel):
    code: str = Field(min_length=1)
    state: str = Field(min_length=8)
    device_id: str | None = None
    payload: str | None = None


class OAuthFinishResponse(BaseModel):
    redirect: str
    merge_token: str | None = None


class EmailCodeRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    # Согласие на обработку данных — как и у OAuth, без него вход не начинается.
    consent: bool = False
    # Отдельное и необязательное: письма о крупных обновлениях. По закону о
    # рекламе такое согласие должно быть явным и отдельным от условий сервиса,
    # поэтому вторая галочка, а не одна на всё.
    news_consent: bool = False


class EmailCodeResponse(BaseModel):
    expires_in: int


class EmailVerifyRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    code: str = Field(min_length=4, max_length=12)


class EmailVerifyResponse(BaseModel):
    redirect: str


class TelegramLoginConfigResponse(BaseModel):
    enabled: bool
    # Бот жив (core/bot_heartbeat.py) — вход подтверждением в боте, а не
    # виджетом. Ложь — виджет, ему сервер с доступом к Telegram не нужен.
    bot_login: bool = False
    # Числовая часть токена — её ждёт виджет в браузере. Сам токен наружу
    # не отдаём никогда: он равносилен полному доступу к боту.
    bot_id: str = ""
    bot_username: str = ""


class TelegramWidgetLoginRequest(BaseModel):
    # Набор полей задаёт Telegram, а не мы: id, first_name, username, photo_url,
    # auth_date, hash. Принимаем как есть — подпись считается по всем полям,
    # и выкидывание «лишнего» её сломает.
    data: dict[str, str]
    # Метка сессии из /telegram/start: подтверждает, что это возврат человека,
    # который начинал вход у нас, и что согласие он дал до перехода.
    state: str = ""
    consent: bool = False


class TelegramWidgetLoginResponse(BaseModel):
    redirect: str
    # Не пуст, когда этим телеграмом владеет другой профиль: человек решает,
    # объединять ли их, — как при привязке VK или Яндекса.
    merge_token: str | None = None


class EmailLinkResponse(BaseModel):
    # merge_token не пуст, когда ящиком владеет другой профиль: тогда человек
    # решает, объединять ли их, — как при привязке VK или Яндекса.
    merge_token: str | None = None
