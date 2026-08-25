"""Имя пользователя на сайте — из профилей беговых систем, а не из провайдера входа.

До 25.08.2026 `users.display_name` заполнялся от VK/Яндекса/Telegram, и на разборе
прод-БД это оказались в основном логины: 121 из 489 привязанных пользователей
показывались как `m4rtynovadian`, `a.kor90`, `leo1973@spartak.ru`, причём 107 из них
имя ни разу не трогали. Имена же в профилях 5 вёрст / S95 / parkrun / RunPark почти
идеальны — на 980 привязок нашлось ровно два битых значения.

Поэтому `users.display_name` стал материализованным результатом: его считает этот
модуль, а все 20+ мест, которые имя читают (рейтинги, страницы локаций, публичный
профиль, OG-карточки, админка), продолжают читать то же самое поле.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import AuthIdentity, Event, Participant, Platform, PlatformLink, RunResult, User
from app.services.co_runners_service import _is_unknown_participant_name
from app.services.platform_titles import PLATFORM_TITLES

# Стили показа имени. Свободного ввода нет: человек выбирает из готовых вариантов,
# иначе поле снова заполнится логинами и шутками.
STYLE_AUTO = "auto"
STYLE_INITIAL = "initial"
DISPLAY_NAME_STYLES = (STYLE_AUTO, STYLE_INITIAL)

# Приоритет систем как источника имени. Ключевой пункт — parkrun в самом низу:
# в России он заморожен на феврале 2022, все его имена устарели на 4.5 года.
# Именно оттуда лезут девичьи фамилии: `Юлия БЕДРЕТДИНОВА` с последним забегом
# 2022-02-26 против `Юлия МИСЬКОВА` в 5 вёрстах с забегом 2026-08.
PLATFORM_PRIORITY = {"five_verst": 0, "s95": 1, "runpark": 2, "parkrun": 3}
_UNKNOWN_PLATFORM_PRIORITY = 90
# Имена из VK/Яндекса/Telegram — только запасной вариант, после всех беговых систем.
_IDENTITY_PRIORITY = 99

# Имя должно состоять минимум из двух слов, только из букв (плюс дефис и апостроф).
# Отсекает логины (`a.kor90`, `leo1973@spartak.ru`), штрихкод вместо имени
# (`A790221818` у parkrun) и артефакт парсера 5 вёрст (`| 5 вёрст`).
_NAME_WORD_RE = re.compile(r"^[А-Яа-яЁёA-Za-z][А-Яа-яЁёA-Za-z'’\-]*$")
_MAX_DISPLAY_NAME_LENGTH = 128


@dataclass(frozen=True)
class NameCandidate:
    """Один вариант имени для селектора в кабинете."""

    # Код системы («five_verst», «s95», …) либо None у имени из провайдера входа.
    platform_code: str | None
    # Как источник называется в интерфейсе: «5 вёрст», «VK».
    source_title: str
    # Имя как оно лежит в источнике («Дмитрий ПОПОВ»).
    raw: str
    # Имя после приведения регистра («Дмитрий Попов») — это и показывается.
    value: str
    # Последний забег в этой системе; None у имён из провайдера входа.
    last_run: date | None

    @property
    def source_key(self) -> str:
        return self.platform_code or "identity"


def is_valid_person_name(name: str | None) -> bool:
    """Похоже ли значение на имя живого человека, а не на логин или заглушку."""
    if _is_unknown_participant_name(name):
        return False
    cleaned = (name or "").strip()
    if not cleaned or len(cleaned) > _MAX_DISPLAY_NAME_LENGTH:
        return False
    words = cleaned.split()
    if len(words) < 2:
        return False
    return all(_NAME_WORD_RE.match(word) for word in words)


def prettify_name(name: str) -> str:
    """«Дмитрий ПОПОВ» → «Дмитрий Попов», сохраняя дефисы и ё.

    5 вёрст, parkrun и S95 пишут фамилию капсом (460 из 462, 224 из 225 и 243 из 245
    привязок соответственно), RunPark — как в паспорте. Приводим к одному виду.
    """
    words = []
    for word in name.split():
        parts = re.split(r"(-)", word)
        words.append("".join(part if part == "-" else part[:1].upper() + part[1:].lower() for part in parts))
    return " ".join(words)


def apply_style(name: str, style: str) -> str:
    """Полное имя либо «Иван П.» — вариант приватности вместо свободного ввода."""
    if style != STYLE_INITIAL:
        return name
    words = name.split()
    if len(words) < 2:
        return name
    return f"{words[0]} {words[-1][:1].upper()}."


def _platform_priority(code: str | None) -> int:
    if code is None:
        return _IDENTITY_PRIORITY
    return PLATFORM_PRIORITY.get(code, _UNKNOWN_PLATFORM_PRIORITY)


def _has_cyrillic(name: str) -> bool:
    return bool(re.search(r"[А-Яа-яЁё]", name))


def _has_mixed_script_word(name: str) -> bool:
    """Слово, где кириллица перемешана с латиницей.

    Это всегда опечатка в исходной системе: `Дмитрий KОЗЫРЕВ` с латинской K в
    5 вёрстах, `АнастеZZZия CAREVA` там же. У обоих в соседней системе лежит
    чистый вариант — его и надо предпочесть, иначе опечатка переедет на сайт.
    """
    for word in name.split():
        if re.search(r"[А-Яа-яЁё]", word) and re.search(r"[A-Za-z]", word):
            return True
    return False


def _candidate_sort_key(candidate: NameCandidate) -> tuple[bool, bool, int, str]:
    """Чистый алфавит важнее смешанного, кириллица важнее латиницы,
    живая система важнее замороженной, свежее важнее старого."""
    last_run = candidate.last_run.isoformat() if candidate.last_run else ""
    return (
        _has_mixed_script_word(candidate.value),
        not _has_cyrillic(candidate.value),
        _platform_priority(candidate.platform_code),
        _invert_date(last_run),
    )


def _invert_date(value: str) -> str:
    """Ключ, при котором более поздняя дата сортируется раньше (по возрастанию)."""
    if not value:
        return "~"  # пусто — в самый конец
    return "".join(chr(ord("9") - int(ch)) if ch.isdigit() else ch for ch in value)


def name_candidates(db: Session, user: User) -> list[NameCandidate]:
    """Все варианты имени для пользователя, от самого предпочтительного к запасным."""
    last_run_by_participant = _last_run_dates(db, user.id)

    rows = (
        db.query(Platform.code, Participant.display_name, PlatformLink.participant_id)
        .join(PlatformLink, PlatformLink.platform_id == Platform.id)
        .join(Participant, Participant.id == PlatformLink.participant_id)
        .filter(PlatformLink.user_id == user.id)
        .all()
    )

    candidates: list[NameCandidate] = []
    for code, raw_name, participant_id in rows:
        if not is_valid_person_name(raw_name):
            continue
        candidates.append(
            NameCandidate(
                platform_code=code,
                source_title=PLATFORM_TITLES.get(code, code),
                raw=raw_name.strip(),
                value=prettify_name(raw_name.strip()),
                last_run=last_run_by_participant.get(participant_id),
            )
        )

    for identity in db.query(AuthIdentity).filter(AuthIdentity.user_id == user.id).all():
        if not is_valid_person_name(identity.display_name):
            continue
        raw_name = (identity.display_name or "").strip()
        candidates.append(
            NameCandidate(
                platform_code=None,
                source_title=_identity_title(identity),
                raw=raw_name,
                value=prettify_name(raw_name),
                last_run=None,
            )
        )

    candidates.sort(key=_candidate_sort_key)
    return _dedupe_candidates(candidates)


def _identity_title(identity: AuthIdentity) -> str:
    from app.services.auth_identity_service import provider_label

    return provider_label(identity.provider)


def _dedupe_candidates(candidates: list[NameCandidate]) -> list[NameCandidate]:
    """Одно и то же имя в трёх системах — один вариант в селекторе (побеждает первый)."""
    seen: set[str] = set()
    result: list[NameCandidate] = []
    for candidate in candidates:
        key = candidate.value.casefold().replace("ё", "е")
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


def _last_run_dates(db: Session, user_id: UUID) -> dict[UUID, date]:
    """participant_id -> дата последнего финиша. Нужна как тай-брейк между системами."""
    rows = (
        db.query(RunResult.participant_id, func.max(Event.event_date))
        .join(Event, Event.id == RunResult.event_id)
        .join(PlatformLink, PlatformLink.participant_id == RunResult.participant_id)
        .filter(PlatformLink.user_id == user_id, RunResult.finish_time_sec.isnot(None))
        .group_by(RunResult.participant_id)
        .all()
    )
    return {row[0]: row[1] for row in rows if row[1] is not None}


def _fallback_name(db: Session, user: User) -> str:
    """Если валидного имени нигде нет — оставляем человека там же, где он был.

    Это путь 200 непривязанных пользователей: у них нет бегового профиля, зато есть
    имя (пусть и логин) от VK/Яндекса. Менять его на «Участник #123» было бы
    ухудшением, а в рейтингах они всё равно не показываются — там нужны результаты.
    """
    identities = (
        db.query(AuthIdentity)
        .filter(AuthIdentity.user_id == user.id)
        .order_by(AuthIdentity.linked_at, AuthIdentity.provider)
        .all()
    )
    for identity in identities:
        name = (identity.display_name or "").strip()
        if name:
            return name[:_MAX_DISPLAY_NAME_LENGTH]
    telegram_parts = [
        part.strip() for part in (user.telegram_first_name, user.telegram_last_name) if part and part.strip()
    ]
    if telegram_parts:
        return " ".join(telegram_parts)[:_MAX_DISPLAY_NAME_LENGTH]
    if user.telegram_username:
        return f"@{user.telegram_username.lstrip('@')}"[:_MAX_DISPLAY_NAME_LENGTH]
    # Уже показанное имя лучше безликого «Участник #123»: пересчёт имени не
    # должен ухудшать то, что человек видел раньше (например, если провайдер
    # входа вернул профиль без имени).
    current = (user.display_name or "").strip()
    if current:
        return current[:_MAX_DISPLAY_NAME_LENGTH]
    return f"Участник #{user.serial_id}"


def resolve_display_name(
    candidates: list[NameCandidate],
    *,
    style: str = STYLE_AUTO,
    platform_code: str | None = None,
) -> tuple[str | None, NameCandidate | None]:
    """Выбранное имя и вариант, из которого оно получено.

    `platform_code` — зафиксированная система-источник. Если она задана, но имени
    в ней больше нет (профиль отвязали, имя в системе стало мусорным), молча
    падаем на авто-приоритет: показать пустое имя хуже, чем имя из соседней
    системы.
    """
    if not candidates:
        return None, None
    chosen = None
    if platform_code:
        chosen = next((item for item in candidates if item.platform_code == platform_code), None)
    if chosen is None:
        chosen = candidates[0]
    return apply_style(chosen.value, style), chosen


def selected_source_code(db: Session, user: User) -> str | None:
    """Код зафиксированной системы-источника; None — источник не задан."""
    if user.display_name_platform_id is None:
        return None
    platform = db.query(Platform).filter(Platform.id == user.display_name_platform_id).one_or_none()
    return platform.code if platform is not None else None


def _platform_id_by_code(db: Session, code: str | None) -> UUID | None:
    if not code:
        return None
    platform = db.query(Platform).filter(Platform.code == code).one_or_none()
    return platform.id if platform is not None else None


def compute_display_name(db: Session, user: User) -> tuple[str, NameCandidate | None]:
    """Итоговое имя пользователя при его нынешних настройках (ничего не пишет)."""
    candidates = name_candidates(db, user)
    name, chosen = resolve_display_name(
        candidates,
        style=user.display_name_style,
        platform_code=selected_source_code(db, user),
    )
    if name is None:
        return _fallback_name(db, user), None
    return name[:_MAX_DISPLAY_NAME_LENGTH], chosen


def refresh_user_display_name(db: Session, user: User, *, commit: bool = False) -> bool:
    """Обновить имя из ЗАФИКСИРОВАННОГО источника. Возвращает True, если изменилось.

    Источник здесь не пересматривается: если человеку выбрано имя из 5 вёрст, а
    алгоритм считает, что теперь лучше подошёл бы S95, имя молча не меняется —
    про это человеку показывается баннер (см. display_name_suggestion). Иначе
    имя в рейтингах могло бы меняться само по себе хоть каждый день.

    Единственное исключение — источник пропал (профиль отвязан, имя в системе
    стало мусорным): тогда фиксируем то, что выбирает алгоритм, потому что
    выбора всё равно нет.
    """
    new_name, chosen = compute_display_name(db, user)
    changed = False

    chosen_code = chosen.platform_code if chosen else None
    if chosen_code != selected_source_code(db, user):
        user.display_name_platform_id = _platform_id_by_code(db, chosen_code)
        changed = True

    if user.display_name != new_name:
        user.display_name = new_name
        changed = True

    if changed:
        if commit:
            db.commit()
        else:
            db.flush()
    return changed


def rebind_display_name_source(db: Session, user: User, *, commit: bool = False) -> bool:
    """Пересмотреть источник имени заново — только при привязке или отвязке профиля.

    Ручной выбор из настроек не перебиваем: если человек сам указал систему, новая
    привязка не должна менять ему имя за спиной. Расхождение с алгоритмом он
    увидит баннером.
    """
    if not user.display_name_source_manual:
        user.display_name_platform_id = None
        # Набор привязок изменился — прежнее отклонённое предложение больше не
        # про эту ситуацию.
        user.display_name_dismissed_name = None
    return refresh_user_display_name(db, user, commit=commit)


def refresh_all_display_names(db: Session, *, commit: bool = True) -> int:
    """Фоновый пересчёт имён: подтянуть свежее имя из уже выбранного источника."""
    changed = 0
    for user in db.query(User).all():
        if refresh_user_display_name(db, user):
            changed += 1
    if commit:
        db.commit()
    return changed


def display_name_suggestion(db: Session, user: User) -> dict[str, object] | None:
    """Что предложил бы алгоритм, если он расходится с нынешним именем.

    Возвращает None, когда предлагать нечего: алгоритм согласен с текущим
    выбором либо человек это же предложение уже отклонил.
    """
    candidates = name_candidates(db, user)
    auto_name, auto_source = resolve_display_name(
        candidates, style=user.display_name_style, platform_code=None
    )
    if auto_name is None or auto_source is None:
        return None
    if auto_name == user.display_name:
        return None
    if auto_name == user.display_name_dismissed_name:
        return None
    return {
        "name": auto_name,
        "platform_code": auto_source.platform_code,
        "source_title": auto_source.source_title,
    }


def display_name_options(db: Session, user: User) -> dict[str, object]:
    """Данные для раздела «Имя на сайте» в настройках."""
    candidates = name_candidates(db, user)
    selected_platform = selected_source_code(db, user)
    auto_name, auto_source = resolve_display_name(
        candidates, style=user.display_name_style, platform_code=None
    )
    return {
        "current": user.display_name,
        "style": user.display_name_style,
        "source": selected_platform,
        "source_manual": user.display_name_source_manual,
        # Что выбрал бы алгоритм — показывается у варианта «автоматически».
        "auto_name": auto_name,
        "auto_source": auto_source.platform_code if auto_source else None,
        "suggestion": display_name_suggestion(db, user),
        "notice": user.display_name_notice,
        "sources": [
            {
                "platform_code": item.platform_code,
                "source_title": item.source_title,
                "name": item.value,
                "name_initial": apply_style(item.value, STYLE_INITIAL),
                "last_run": item.last_run.isoformat() if item.last_run else None,
            }
            for item in candidates
            if item.platform_code is not None
        ],
    }


def set_display_name_preferences(
    db: Session,
    user: User,
    *,
    style: str,
    platform_code: str | None,
) -> User:
    """Применить выбор из настроек. Свободного ввода имени нет.

    platform_code = None означает «выбирать автоматически»: источник снова
    пересматривается при каждой привязке.
    """
    if style not in DISPLAY_NAME_STYLES:
        raise ValueError("Неизвестный стиль имени.")

    platform_id: UUID | None = None
    if platform_code:
        platform = db.query(Platform).filter(Platform.code == platform_code).one_or_none()
        if platform is None:
            raise ValueError("Неизвестная система.")
        if not _user_has_platform(db, user, platform.id):
            raise ValueError("Эта система не привязана к вашему аккаунту.")
        platform_id = platform.id

    user.display_name_style = style
    user.display_name_platform_id = platform_id
    user.display_name_source_manual = platform_id is not None
    # Человек дошёл до настроек и всё решил — ни плашку, ни отклонённое
    # предложение хранить больше незачем.
    user.display_name_notice = None
    user.display_name_dismissed_name = None
    refresh_user_display_name(db, user)
    db.commit()
    db.refresh(user)
    return user


def _user_has_platform(db: Session, user: User, platform_id: UUID) -> bool:
    return (
        db.query(PlatformLink.id)
        .filter(PlatformLink.user_id == user.id, PlatformLink.platform_id == platform_id)
        .first()
        is not None
    )


def dismiss_display_name_notice(db: Session, user: User) -> User:
    """«Оставить как есть»: гасим и одноразовую плашку, и текущее предложение."""
    suggestion = display_name_suggestion(db, user)
    changed = False
    if user.display_name_notice is not None:
        user.display_name_notice = None
        changed = True
    if suggestion is not None:
        # Запоминаем именно имя: если алгоритм потом предложит другое, баннер
        # появится снова, а с тем же — промолчит.
        user.display_name_dismissed_name = str(suggestion["name"])
        changed = True
    if changed:
        db.commit()
        db.refresh(user)
    return user
