"""Имя пользователя берётся из профилей беговых систем.

Кейсы — не выдуманные: все взяты из разбора прод-БД 25.08.2026, на котором и
принималось решение отказаться от свободного ввода имени.
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import Participant, Platform, PlatformLink, User
from app.services.user_display_name_service import (
    STYLE_AUTO,
    STYLE_INITIAL,
    NameCandidate,
    apply_style,
    compute_display_name,
    dismiss_display_name_notice,
    display_name_options,
    display_name_suggestion,
    is_valid_person_name,
    name_candidates,
    prettify_name,
    rebind_display_name_source,
    refresh_user_display_name,
    resolve_display_name,
    selected_source_code,
    set_display_name_preferences,
)

# ---------------------------------------------------------------- чистые функции


def test_valid_name_accepts_real_names() -> None:
    assert is_valid_person_name("Дмитрий ПОПОВ")
    assert is_valid_person_name("Яна КРЖИЖАНОВСКАЯ-КУКАСОВА")
    assert is_valid_person_name("Sergey MISYUKOV")
    assert is_valid_person_name("Жан Марк ПОПОВ")


def test_valid_name_rejects_logins_and_junk() -> None:
    # Логины и почты от VK/Яндекса — из-за них всё и затевалось.
    assert not is_valid_person_name("a.kor90")
    assert not is_valid_person_name("pele1985")
    assert not is_valid_person_name("leo1973@spartak.ru")
    assert not is_valid_person_name("790194133")
    # Одно слово — не ФИО.
    assert not is_valid_person_name("Андрей")
    # Реальный мусор из прод-БД: артефакт парсера 5 вёрст и штрихкод parkrun.
    assert not is_valid_person_name("| 5 вёрст")
    assert not is_valid_person_name("A790221818")
    assert not is_valid_person_name(None)
    assert not is_valid_person_name("   ")
    # Заглушки платформ.
    assert not is_valid_person_name("Unknown #123")


def test_prettify_lowers_caps_surname() -> None:
    # 5 вёрст, parkrun и S95 пишут фамилию капсом — приводим к общему виду.
    assert prettify_name("Дмитрий ПОПОВ") == "Дмитрий Попов"
    assert prettify_name("Sergey MISYUKOV") == "Sergey Misyukov"
    assert prettify_name("Илья НАЙДЁНОВ") == "Илья Найдёнов"
    # Двойная фамилия через дефис — заглавная в обеих частях.
    assert prettify_name("Яна КРЖИЖАНОВСКАЯ-КУКАСОВА") == "Яна Кржижановская-Кукасова"


def test_apply_style_initial() -> None:
    assert apply_style("Иван Петров", STYLE_AUTO) == "Иван Петров"
    assert apply_style("Иван Петров", STYLE_INITIAL) == "Иван П."
    # Тройное имя: инициал берётся от последнего слова, а не от второго.
    assert apply_style("Жан Марк Попов", STYLE_INITIAL) == "Жан П."
    # Одно слово прятать нечего.
    assert apply_style("Иван", STYLE_INITIAL) == "Иван"


def _candidate(code: str, name: str, last_run: date | None = None) -> NameCandidate:
    return NameCandidate(
        platform_code=code,
        source_title=code,
        raw=name,
        value=prettify_name(name),
        last_run=last_run,
    )


def _resolve(*candidates: NameCandidate, style: str = STYLE_AUTO, platform_code: str | None = None) -> str | None:
    from app.services.user_display_name_service import _candidate_sort_key

    ordered = sorted(candidates, key=_candidate_sort_key)
    name, _chosen = resolve_display_name(ordered, style=style, platform_code=platform_code)
    return name


def test_live_system_wins_over_frozen_parkrun() -> None:
    """Смена фамилии: parkrun в РФ заморожен на феврале 2022, верить ему нельзя.

    Настоящий случай с прода: в parkrun лежит девичья фамилия с последним
    забегом 2022-02-26, в 5 вёрстах — текущая, с забегом за эту неделю.
    """
    assert (
        _resolve(
            _candidate("parkrun", "Юлия БЕДРЕТДИНОВА", date(2022, 2, 26)),
            _candidate("five_verst", "Юлия МИСЬКОВА", date(2026, 8, 22)),
        )
        == "Юлия Миськова"
    )


def test_cyrillic_wins_over_latin() -> None:
    assert (
        _resolve(
            _candidate("parkrun", "Maria KOSTENKO"),
            _candidate("five_verst", "Мария КОСТЕНКО"),
        )
        == "Мария Костенко"
    )
    # Кириллица важнее даже приоритета системы: латиница в 5 вёрстах проигрывает
    # кириллице в parkrun.
    assert (
        _resolve(
            _candidate("five_verst", "Serge BELYCHEV"),
            _candidate("parkrun", "Сергей БЕЛЫЧЕВ"),
        )
        == "Сергей Белычев"
    )


def test_mixed_script_name_loses_to_clean_one() -> None:
    """`Дмитрий KОЗЫРЕВ` с латинской K — опечатка в 5 вёрстах, а не имя."""
    assert (
        _resolve(
            _candidate("five_verst", "Дмитрий KОЗЫРЕВ"),
            _candidate("runpark", "Дмитрий Козырев"),
        )
        == "Дмитрий Козырев"
    )


def test_fresher_run_breaks_tie_within_same_priority() -> None:
    assert (
        _resolve(
            _candidate("five_verst", "Евгения БОРОВЧЕНКО", date(2025, 2, 15)),
            _candidate("five_verst", "Евгения ТЕН", date(2026, 8, 22)),
        )
        == "Евгения Тен"
    )


def test_forced_source_overrides_priority() -> None:
    candidates = [
        _candidate("five_verst", "Юлия МИСЬКОВА", date(2026, 8, 22)),
        _candidate("parkrun", "Юлия БЕДРЕТДИНОВА", date(2022, 2, 26)),
    ]
    assert _resolve(*candidates, platform_code="parkrun") == "Юлия Бедретдинова"
    # Систему отвязали — молча падаем на авто, а не показываем пустое имя.
    assert _resolve(*candidates, platform_code="s95") == "Юлия Миськова"


def test_resolve_without_candidates() -> None:
    assert resolve_display_name([], style=STYLE_AUTO) == (None, None)


# ------------------------------------------------------------------- через БД


def _platform(db: Session, code: str) -> Platform:
    platform = db.query(Platform).filter(Platform.code == code).one_or_none()
    if platform is None:
        platform = Platform(code=code, name=code)
        db.add(platform)
        db.commit()
        db.refresh(platform)
    return platform


def _user(db: Session, display_name: str | None = None) -> User:
    user = User(display_name=display_name)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _link(db: Session, user: User, code: str, name: str) -> Participant:
    platform = _platform(db, code)
    external_id = str(uuid4().int % 1_000_000_000)
    participant = Participant(platform_id=platform.id, external_user_id=external_id, display_name=name)
    db.add(participant)
    db.commit()
    db.refresh(participant)
    db.add(
        PlatformLink(
            user_id=user.id,
            platform_id=platform.id,
            participant_id=participant.id,
            external_user_id=external_id,
            external_url="https://example.test/profile",
        )
    )
    db.commit()
    return participant


def test_refresh_replaces_login_with_profile_name(db_session: Session) -> None:
    user = _user(db_session, display_name="pele1985")
    _link(db_session, user, "five_verst", "Дмитрий ПЕЛЕЗНЕВ")

    # Источник ещё не зафиксирован — пересчёт его и зафиксирует.
    assert refresh_user_display_name(db_session, user) is True
    assert user.display_name == "Дмитрий Пелезнев"
    # Идемпотентность: второй прогон ничего не меняет.
    assert refresh_user_display_name(db_session, user) is False


def test_refresh_keeps_provider_name_without_links(db_session: Session) -> None:
    """Непривязанному менять нечего: имя от провайдера остаётся как было."""
    user = _user(db_session, display_name="a.kor90")
    name, chosen = compute_display_name(db_session, user)
    assert chosen is None
    assert name == "a.kor90"


def test_junk_profile_name_is_skipped(db_session: Session) -> None:
    user = _user(db_session, display_name="trenihina")
    _link(db_session, user, "parkrun", "A790221818")
    _link(db_session, user, "five_verst", "Татьяна ТРЕНИХИНА")

    refresh_user_display_name(db_session, user)
    assert user.display_name == "Татьяна Тренихина"


def test_candidates_are_deduped_across_systems(db_session: Session) -> None:
    user = _user(db_session)
    for code in ("five_verst", "parkrun", "s95"):
        _link(db_session, user, code, "Алексей ДОРОФЕЕВ")

    candidates = name_candidates(db_session, user)
    assert [item.value for item in candidates] == ["Алексей Дорофеев"]

    rebind_display_name_source(db_session, user)
    # Одно имя на все системы — расходиться не с чем, предлагать нечего.
    assert display_name_options(db_session, user)["suggestion"] is None


def test_preferences_apply_style_and_source(db_session: Session) -> None:
    user = _user(db_session)
    _link(db_session, user, "five_verst", "Юлия МИСЬКОВА")
    _link(db_session, user, "parkrun", "Юлия БЕДРЕТДИНОВА")
    rebind_display_name_source(db_session, user)
    assert user.display_name == "Юлия Миськова"

    set_display_name_preferences(db_session, user, style=STYLE_INITIAL, platform_code="parkrun")
    assert user.display_name == "Юлия Б."
    assert user.display_name_style == STYLE_INITIAL
    assert user.display_name_source_manual is True

    set_display_name_preferences(db_session, user, style=STYLE_AUTO, platform_code=None)
    assert user.display_name == "Юлия Миськова"
    assert user.display_name_source_manual is False


# ------------------------------------------- источник фиксируется, а не гуляет


def test_source_is_fixed_at_link_time_and_survives_background_refresh(db_session: Session) -> None:
    """Имя не должно меняться само по себе: фоновый пересчёт источник не трогает."""
    user = _user(db_session)
    _link(db_session, user, "parkrun", "Виктор РОЖКОВ")
    rebind_display_name_source(db_session, user)
    assert selected_source_code(db_session, user) == "parkrun"
    assert user.display_name == "Виктор Рожков"

    # В 5 вёрстах человек записан иначе, и алгоритм предпочёл бы их. Но профиль
    # появился не через привязку (например, доехал синком), значит фоновый
    # пересчёт обязан оставить всё как есть.
    _link(db_session, user, "five_verst", "Виктор РОЖКОВ-СТАРШИЙ")
    assert refresh_user_display_name(db_session, user) is False
    assert user.display_name == "Виктор Рожков"
    assert selected_source_code(db_session, user) == "parkrun"

    # Зато человеку показывается предложение.
    suggestion = display_name_suggestion(db_session, user)
    assert suggestion is not None
    assert suggestion["name"] == "Виктор Рожков-Старший"
    assert suggestion["platform_code"] == "five_verst"


def test_name_change_inside_chosen_source_applies_immediately(db_session: Session) -> None:
    """А вот если имя поменялось в САМОЙ выбранной системе — подтягиваем молча."""
    user = _user(db_session)
    participant = _link(db_session, user, "five_verst", "Юлия МИСЬКОВА")
    rebind_display_name_source(db_session, user)
    assert user.display_name == "Юлия Миськова"

    participant.display_name = "Юлия БЕДРЕТДИНОВА"
    db_session.commit()

    assert refresh_user_display_name(db_session, user) is True
    assert user.display_name == "Юлия Бедретдинова"
    assert display_name_suggestion(db_session, user) is None


def test_link_rebinds_source_but_manual_choice_is_kept(db_session: Session) -> None:
    user = _user(db_session)
    _link(db_session, user, "parkrun", "Виктор РОЖКОВ")
    rebind_display_name_source(db_session, user)

    # Человек ничего не выбирал — новая привязка вправе сменить источник.
    _link(db_session, user, "five_verst", "Виктор ПЕТРОВ")
    rebind_display_name_source(db_session, user)
    assert selected_source_code(db_session, user) == "five_verst"
    assert user.display_name == "Виктор Петров"

    # Выбор руками новая привязка уже не перебивает.
    set_display_name_preferences(db_session, user, style=STYLE_AUTO, platform_code="parkrun")
    _link(db_session, user, "s95", "Виктор СИДОРОВ")
    rebind_display_name_source(db_session, user)
    assert selected_source_code(db_session, user) == "parkrun"
    assert user.display_name == "Виктор Рожков"


def test_suggestion_is_silenced_after_keep(db_session: Session) -> None:
    user = _user(db_session)
    _link(db_session, user, "parkrun", "Виктор РОЖКОВ")
    rebind_display_name_source(db_session, user)
    _link(db_session, user, "five_verst", "Виктор ПЕТРОВ")

    assert display_name_suggestion(db_session, user) is not None
    dismiss_display_name_notice(db_session, user)
    assert user.display_name_dismissed_name == "Виктор Петров"
    assert display_name_suggestion(db_session, user) is None
    # Имя при этом осталось прежним — «оставить как есть» ничего не меняет.
    assert user.display_name == "Виктор Рожков"


def test_new_suggestion_appears_after_dismissing_an_old_one(db_session: Session) -> None:
    user = _user(db_session)
    _link(db_session, user, "parkrun", "Виктор РОЖКОВ")
    rebind_display_name_source(db_session, user)
    participant = _link(db_session, user, "five_verst", "Виктор ПЕТРОВ")
    dismiss_display_name_notice(db_session, user)
    assert display_name_suggestion(db_session, user) is None

    participant.display_name = "Виктор СИДОРОВ"
    db_session.commit()
    suggestion = display_name_suggestion(db_session, user)
    assert suggestion is not None
    assert suggestion["name"] == "Виктор Сидоров"


def test_unlinking_chosen_source_falls_back(db_session: Session) -> None:
    """Выбранную систему отвязали — выбирать не из чего, берём следующую."""
    user = _user(db_session)
    _link(db_session, user, "five_verst", "Юлия МИСЬКОВА")
    _link(db_session, user, "parkrun", "Юлия БЕДРЕТДИНОВА")
    set_display_name_preferences(db_session, user, style=STYLE_AUTO, platform_code="parkrun")
    assert user.display_name == "Юлия Бедретдинова"

    link = (
        db_session.query(PlatformLink)
        .join(Platform, Platform.id == PlatformLink.platform_id)
        .filter(PlatformLink.user_id == user.id, Platform.code == "parkrun")
        .one()
    )
    db_session.delete(link)
    db_session.flush()
    user.display_name_source_manual = False
    rebind_display_name_source(db_session, user)
    assert selected_source_code(db_session, user) == "five_verst"
    assert user.display_name == "Юлия Миськова"


def test_preferences_reject_unlinked_platform(db_session: Session) -> None:
    import pytest

    user = _user(db_session)
    _link(db_session, user, "five_verst", "Юлия МИСЬКОВА")

    with pytest.raises(ValueError):
        set_display_name_preferences(db_session, user, style=STYLE_AUTO, platform_code="parkrun")
    with pytest.raises(ValueError):
        set_display_name_preferences(db_session, user, style="nonsense", platform_code=None)


def test_setting_preferences_clears_notice(db_session: Session) -> None:
    user = _user(db_session, display_name="pele1985")
    user.display_name_notice = "pele1985"
    _link(db_session, user, "five_verst", "Дмитрий ПЕЛЕЗНЕВ")

    set_display_name_preferences(db_session, user, style=STYLE_AUTO, platform_code=None)
    assert user.display_name_notice is None


def test_word_order_change_is_not_suggested(db_session: Session) -> None:
    """Перестановка имени и фамилии проходит молча, без баннера.

    «Попов Дмитрий» и «Дмитрий Попов» — одно и то же имя: спрашивать о таком
    человека незачем, порядок просто берётся как в беговой системе
    (просьба Дмитрия 02.09.2026).
    """
    from app.services.user_display_name_service import _same_words

    assert _same_words("Попов Дмитрий", "Дмитрий Попов")
    assert _same_words("ПОПОВ дмитрий", "Дмитрий Попов")
    assert _same_words("Семён Фёдоров", "Федоров Семен")
    # Разные имена перестановкой не становятся.
    assert not _same_words("Дмитрий Попов", "Дмитрий Петров")
    assert not _same_words("Дмитрий Попов", None)
    assert not _same_words("", "Дмитрий Попов")
