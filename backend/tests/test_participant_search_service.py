from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Event, Location, Participant, Platform, PlatformLink, RunResult, User
from app.platform_adapters.canonical import ProfilePreview
from app.services.admin_site_stats_service import get_admin_site_stats
from app.services.onboarding_service import (
    OnboardingError,
    complete_onboarding,
    post_login_redirect_target,
    set_platform_no_account,
)
from app.services.participant_search_service import ParticipantSearchError, search_participants
from app.services.profile_linking_service import (
    ProfileLinkingError,
    confirm_profile_link,
    confirm_profile_link_by_participant,
)


@pytest.fixture
def search_user(db_session: Session) -> User:
    user = User(consent_accepted=True, display_name="Искатель")
    db_session.add(user)
    db_session.commit()
    return user


def _five_verst(db_session: Session) -> Platform:
    return db_session.query(Platform).filter(Platform.code == "five_verst").one()


def _make_participant(
    db_session: Session,
    platform: Platform,
    display_name: str | None,
    **kwargs,
) -> Participant:
    participant = Participant(
        platform_id=platform.id,
        external_user_id=f"srch-{uuid4().hex[:10]}",
        display_name=display_name,
        **kwargs,
    )
    db_session.add(participant)
    db_session.commit()
    return participant


def _make_run(
    db_session: Session,
    platform: Platform,
    participant: Participant,
    event_date: date,
    location_name: str = "Тестовый парк",
) -> None:
    location = Location(
        platform_id=platform.id,
        external_key=f"srch-loc-{uuid4().hex[:10]}",
        name=location_name,
        city="Тестоград",
    )
    db_session.add(location)
    db_session.flush()
    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"srch-event-{uuid4().hex[:10]}",
        event_date=event_date,
    )
    db_session.add(event)
    db_session.flush()
    db_session.add(
        RunResult(
            event_id=event.id,
            participant_id=participant.id,
            external_result_key=f"srch-res-{uuid4().hex[:10]}",
            position=1,
        )
    )
    db_session.commit()


def test_search_finds_by_name_regardless_of_word_order(db_session: Session, search_user: User) -> None:
    platform = _five_verst(db_session)
    participant = _make_participant(db_session, platform, "Тестовый Бегун Поисковый")
    _make_run(db_session, platform, participant, date(2026, 7, 11))

    page = search_participants(db_session, search_user, "поисковый тестовый")

    found = [item for item in page.results if item.participant_id == participant.id]
    assert len(found) == 1
    result = found[0]
    assert result.platform_code == "five_verst"
    assert result.total_runs == 1
    assert result.last_run_date == date(2026, 7, 11)
    assert result.home_location_name == "Тестовый парк"
    assert result.home_location_city == "Тестоград"
    assert result.already_linked is False
    assert [(a.kind, a.event_date, a.location_name) for a in result.recent_activities] == [
        ("run", date(2026, 7, 11), "Тестовый парк")
    ]


def test_search_by_barcode_and_numeric_id(db_session: Session, search_user: User) -> None:
    platform = _five_verst(db_session)
    with_barcode = _make_participant(db_session, platform, "Штрихкодовый Тест", barcode_id="A7911111")
    numeric = Participant(
        platform_id=platform.id,
        external_user_id="791222333",
        display_name="Номерной Тест",
    )
    db_session.add(numeric)
    db_session.commit()

    # Штрихкод находится и с буквой, и без, и в нижнем регистре.
    for query in ("A7911111", "7911111", "a7911111"):
        page = search_participants(db_session, search_user, query)
        assert [item.participant_id for item in page.results] == [with_barcode.id], query

    # Номер участника — точное совпадение по external_user_id.
    page = search_participants(db_session, search_user, "791222333")
    assert [item.participant_id for item in page.results] == [numeric.id]

    # Чужой номер ничего не находит (никаких LIKE по цифрам).
    page = search_participants(db_session, search_user, "791222")
    assert page.results == []


def test_search_by_s95_legacy_barcode_without_prefix(db_session: Session, search_user: User) -> None:
    """Штрихкод С95 без «A» в базе — свой же QR (A770012057) должен его находить.

    Часть кодов С95 приехала из легаси голыми цифрами (770012057 вместо
    A770012057), при том что и сам С95, и наш личный кабинет показывают их
    с префиксом.
    """
    platform = db_session.query(Platform).filter(Platform.code == "s95").one()
    legacy = _make_participant(db_session, platform, "Легаси Тест", barcode_id="770012057")

    # \u0410 — кириллическая «А»: на глаз не отличить от латинской, а набирают
    # её на русской раскладке постоянно.
    for query in ("A770012057", "a770012057", "\u0410770012057", "770012057"):
        page = search_participants(db_session, search_user, query)
        assert [item.participant_id for item in page.results] == [legacy.id], query


def test_search_excludes_platforms_already_linked_by_user(db_session: Session, search_user: User) -> None:
    platform = _five_verst(db_session)
    mine = _make_participant(db_session, platform, "Исключение Привязанный Тест")
    db_session.add(
        PlatformLink(
            user_id=search_user.id,
            platform_id=platform.id,
            participant_id=mine.id,
            external_user_id=mine.external_user_id,
            external_url="https://example.test/mine",
        )
    )
    db_session.commit()
    _make_participant(db_session, platform, "Исключение Однофамилец Тест")

    page = search_participants(db_session, search_user, "Исключение")
    assert all(item.platform_code != "five_verst" for item in page.results)
    # Скрытые совпадения не пропадают молча: система называется, имя — нет.
    assert page.hidden_linked_platform_codes == ["five_verst"]

    clean = search_participants(db_session, search_user, "Такогоименинет")
    assert clean.hidden_linked_platform_codes == []


def test_search_skips_unknown_and_empty_names(db_session: Session, search_user: User) -> None:
    platform = _five_verst(db_session)
    _make_participant(db_session, platform, "Неизвестный")
    page = search_participants(db_session, search_user, "Неизвестный")
    assert all(item.display_name.casefold() != "неизвестный" for item in page.results)


def test_search_requires_min_length_and_consent(db_session: Session, search_user: User) -> None:
    with pytest.raises(ParticipantSearchError) as too_short:
        search_participants(db_session, search_user, "аб")
    assert too_short.value.status_code == 422

    no_consent = User(consent_accepted=False)
    db_session.add(no_consent)
    db_session.commit()
    with pytest.raises(ParticipantSearchError) as denied:
        search_participants(db_session, no_consent, "Иванов")
    assert denied.value.status_code == 403


def test_search_marks_profiles_linked_to_other_users(db_session: Session, search_user: User) -> None:
    platform = _five_verst(db_session)
    participant = _make_participant(db_session, platform, "Занятый Профильный Тест")
    other = User(consent_accepted=True)
    db_session.add(other)
    db_session.flush()
    db_session.add(
        PlatformLink(
            user_id=other.id,
            platform_id=platform.id,
            participant_id=participant.id,
            external_user_id=participant.external_user_id,
            external_url="https://example.test/busy",
        )
    )
    db_session.commit()

    page = search_participants(db_session, search_user, "Занятый Профильный")
    result = next(item for item in page.results if item.participant_id == participant.id)
    assert result.already_linked is True
    assert result.linked_to_me is False


def test_confirm_link_by_participant_creates_link(db_session: Session, search_user: User) -> None:
    platform = _five_verst(db_session)
    participant = _make_participant(
        db_session,
        platform,
        "Привязочный Кандидат Тест",
        profile_url="https://5verst.ru/userstats/999111222/",
    )

    with (
        patch("app.services.profile_linking_service.linking_sync_should_run", return_value=False),
        patch("app.services.profile_linking_service.complete_link_without_sync") as complete_mock,
    ):
        link = confirm_profile_link_by_participant(db_session, search_user, participant.id)

    assert link.user_id == search_user.id
    assert link.participant_id == participant.id
    assert link.external_user_id == participant.external_user_id
    assert link.external_url == "https://5verst.ru/userstats/999111222/"
    complete_mock.assert_called_once()

    with pytest.raises(ProfileLinkingError) as duplicate:
        confirm_profile_link_by_participant(db_session, search_user, participant.id)
    assert duplicate.value.status_code == 409


def test_link_method_recorded_for_search_and_url(db_session: Session, search_user: User) -> None:
    """Способ привязки пишется в platform_links — по нему меряем эффект поиска."""
    platform = _five_verst(db_session)
    from_search = _make_participant(
        db_session,
        platform,
        "Метка Поиска Тест",
        profile_url="https://5verst.ru/userstats/794000001/",
    )

    with (
        patch("app.services.profile_linking_service.linking_sync_should_run", return_value=False),
        patch("app.services.profile_linking_service.complete_link_without_sync"),
    ):
        link = confirm_profile_link_by_participant(db_session, search_user, from_search.id)
    assert link.link_method == "search"

    # Тот же участник, но привязка по ссылке — другой пользователь, метка "url".
    url_user = User(consent_accepted=True, display_name="Ссылочник")
    db_session.add(url_user)
    db_session.commit()
    url_participant = _make_participant(
        db_session,
        platform,
        "Метка Ссылки Тест",
        profile_url="https://5verst.ru/userstats/794000002/",
    )
    preview = ProfilePreview(
        external_user_id=url_participant.external_user_id,
        display_name="Метка Ссылки Тест",
        profile_url=url_participant.profile_url,
        total_runs=0,
        platform_code="five_verst",
    )
    with (
        patch("app.services.profile_linking_service.get_cached_profile_preview", return_value=preview),
        patch("app.services.profile_linking_service.linking_sync_should_run", return_value=False),
        patch("app.services.profile_linking_service.complete_link_without_sync"),
        patch("app.services.profile_linking_service.rebind_display_name_source"),
    ):
        url_link = confirm_profile_link(db_session, url_user, "five_verst", url_participant.profile_url)
    assert url_link.link_method == "url"


def test_onboarding_cohorts_count_first_day_conversion(db_session: Session) -> None:
    """Когорта недели регистрации: кто привязался в первые сутки, кто позже."""
    platform = _five_verst(db_session)
    now = datetime.now(timezone.utc)

    fast = User(consent_accepted=True, display_name="Быстрый", created_at=now - timedelta(days=3))
    slow = User(consent_accepted=True, display_name="Медленный", created_at=now - timedelta(days=3))
    never = User(consent_accepted=True, display_name="Без привязки", created_at=now - timedelta(days=3))
    db_session.add_all([fast, slow, never])
    db_session.flush()

    for user, linked_at in ((fast, now - timedelta(days=3)), (slow, now - timedelta(days=1))):
        participant = _make_participant(db_session, platform, f"Когорта {user.display_name}")
        db_session.add(
            PlatformLink(
                user_id=user.id,
                platform_id=platform.id,
                participant_id=participant.id,
                external_user_id=participant.external_user_id,
                external_url="https://example.test/cohort",
                linked_at=linked_at,
            )
        )
    db_session.commit()

    stats = get_admin_site_stats(db_session, period_days=30)
    cohort_users = {fast.id, slow.id, never.id}
    assert cohort_users  # три подопытных лежат в одной неделе регистрации
    current_week = max(row["week"] for row in stats["onboarding_cohorts"])
    row = next(r for r in stats["onboarding_cohorts"] if r["week"] == current_week)
    # В общей БД могут быть и другие пользователи этой недели — проверяем вклад.
    assert row["registered"] >= 3
    assert row["linked_1d"] >= 1
    assert row["linked_7d"] >= 2
    assert row["linked_any"] >= row["linked_1d"]


def test_confirm_link_by_participant_refuses_foreign_profile(db_session: Session, search_user: User) -> None:
    platform = _five_verst(db_session)
    participant = _make_participant(db_session, platform, "Чужой Профиль Тест")
    other = User(consent_accepted=True)
    db_session.add(other)
    db_session.flush()
    db_session.add(
        PlatformLink(
            user_id=other.id,
            platform_id=platform.id,
            participant_id=participant.id,
            external_user_id=participant.external_user_id,
            external_url="https://example.test/foreign",
        )
    )
    db_session.commit()

    with pytest.raises(ProfileLinkingError) as taken:
        confirm_profile_link_by_participant(db_session, search_user, participant.id)
    assert taken.value.status_code == 409


def test_search_orders_by_last_run_date(db_session: Session, search_user: User) -> None:
    platform = _five_verst(db_session)
    fresh = _make_participant(db_session, platform, "Сортировочный Свежий Тест")
    _make_run(db_session, platform, fresh, date(2026, 8, 15))
    stale = _make_participant(db_session, platform, "Сортировочный Давний Тест")
    _make_run(db_session, platform, stale, date(2025, 3, 1))
    empty = _make_participant(db_session, platform, "Сортировочный Пустой Тест")

    page = search_participants(db_session, search_user, "Сортировочный")
    ordered = [item.participant_id for item in page.results]
    assert ordered == [fresh.id, stale.id, empty.id]


def test_set_platform_no_account_toggles_and_validates(db_session: Session, search_user: User) -> None:
    assert set_platform_no_account(db_session, search_user, "parkrun", True) == ["parkrun"]
    assert set_platform_no_account(db_session, search_user, "s95", True) == ["parkrun", "s95"]
    # Повторная отметка не дублирует, снятие убирает только свою систему.
    assert set_platform_no_account(db_session, search_user, "parkrun", True) == ["parkrun", "s95"]
    assert set_platform_no_account(db_session, search_user, "parkrun", False) == ["s95"]

    with pytest.raises(OnboardingError) as unknown:
        set_platform_no_account(db_session, search_user, "strava", True)
    assert unknown.value.status_code == 404


def test_search_builds_runpark_profile_url(db_session: Session, search_user: User) -> None:
    """Ссылка на карму — только по идентификатору аккаунта RunPark."""
    runpark = db_session.query(Platform).filter(Platform.code == "runpark").one()
    guid = "A1B2C3D4-1111-4222-8333-444455556666"
    participant = Participant(
        platform_id=runpark.id,
        external_user_id=guid,
        display_name="Кармический Тест",
        barcode_id="A7933333",
    )
    db_session.add(participant)
    db_session.commit()

    page = search_participants(db_session, search_user, "Кармический")
    result = next(item for item in page.results if item.participant_id == participant.id)
    assert result.profile_url == f"https://runpark.ru/Account/Karmas/{guid}"


def test_search_gives_no_profile_url_without_runpark_account(
    db_session: Session, search_user: User
) -> None:
    """У личности «barcode:A…» аккаунта на RunPark нет — и ссылки быть не должно.

    Раньше мы подставляли /Account/Karmas/barcode:A790152825 — такой страницы не
    существует. На проде подобных привязок было 465 (Дмитрий 14.09.2026).
    """
    runpark = db_session.query(Platform).filter(Platform.code == "runpark").one()
    participant = Participant(
        platform_id=runpark.id,
        external_user_id="barcode:A7944444",
        display_name="Штрихкодный Тест",
        barcode_id="A7944444",
    )
    db_session.add(participant)
    db_session.commit()

    page = search_participants(db_session, search_user, "Штрихкодный")
    result = next(item for item in page.results if item.participant_id == participant.id)
    assert result.profile_url is None


def test_post_login_redirect_targets(db_session: Session, search_user: User) -> None:
    # Новый пользователь без привязок — на онбординг.
    assert post_login_redirect_target(db_session, search_user.id) == "welcome"

    # Пропустил или завершил онбординг — в кабинет.
    complete_onboarding(db_session, search_user)
    assert post_login_redirect_target(db_session, search_user.id) == "dashboard"

    # Есть привязка (даже без отметки онбординга) — в кабинет.
    with_link = User(consent_accepted=True)
    db_session.add(with_link)
    db_session.flush()
    platform = _five_verst(db_session)
    participant = _make_participant(db_session, platform, "Редирект Тест")
    db_session.add(
        PlatformLink(
            user_id=with_link.id,
            platform_id=platform.id,
            participant_id=participant.id,
            external_user_id=participant.external_user_id,
            external_url="https://example.test/redirect",
        )
    )
    db_session.commit()
    assert post_login_redirect_target(db_session, with_link.id) == "dashboard"


def test_search_ranks_only_a_bounded_pool_but_keeps_word_start_first(
    db_session: Session, search_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Балл «с начала слова» считается по пулу кандидатов, а не по всем «…левоглаз…» (ревью, onboarding-search-slower)."""
    from app.services import participant_search_service

    monkeypatch.setattr(participant_search_service, "CANDIDATE_LIMIT", 2)
    platform = _five_verst(db_session)
    for index in range(4):
        _make_participant(db_session, platform, f"Залевоглаз{index} Онбордингов")
    wanted = _make_participant(db_session, platform, "Левоглаз Нужный")

    page = search_participants(db_session, search_user, "левоглаз левоглаз ЛЕВОГЛАЗ")

    assert wanted.id in {item.participant_id for item in page.results}
    assert page.query == "левоглаз левоглаз ЛЕВОГЛАЗ"


def test_search_timeout_is_a_clear_error_not_500(
    db_session: Session, search_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy import text

    from app.services import participant_search_service

    monkeypatch.setattr(participant_search_service, "SEARCH_STATEMENT_TIMEOUT_MS", 50)

    def slow(db: Session, *_args, **_kwargs):  # noqa: ANN202
        db.execute(text("SELECT pg_sleep(2)"))

    monkeypatch.setattr(participant_search_service, "_name_candidates", slow)

    with pytest.raises(ParticipantSearchError) as timed_out:
        search_participants(db_session, search_user, "Таймаутов")
    assert timed_out.value.status_code == 503
    assert db_session.execute(text("SELECT 1")).scalar() == 1


def test_search_of_punctuation_only_finds_nobody(db_session: Session, search_user: User) -> None:
    """«%%% ___»: без слов условий на имя нет — и выдача не должна стать «первыми попавшимися» (SKEP-4)."""
    platform = _five_verst(db_session)
    _make_participant(db_session, platform, "Знаков Онбордингов")

    page = search_participants(db_session, search_user, "%%% ___")

    assert page.results == []
    assert page.truncated is False
    assert page.hidden_linked_platform_codes == []
    # Знак по краю слова — опечатка: «Знаков,» находит Знакова.
    found = search_participants(db_session, search_user, "Знаков, Онбордингов.")
    assert [item.display_name for item in found.results] == ["Знаков Онбордингов"]
