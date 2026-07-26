from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import (
    Participant,
    Platform,
    ProfileFetchPending,
    ProfileFetchPendingOperation,
    ProfileFetchPendingReason,
    ProfileFetchPendingStatus,
    User,
)
from app.parkrun.errors import ParkrunBanDetected, ParkrunProfileNotFound
from app.s95.errors import S95BanDetected
from app.services.profile_fetch_pending_service import (
    MAX_RETRY_ATTEMPTS,
    PERMANENT_ERROR_PREFIX,
    RUNPARK_SEED_NOTE_PREFIX,
    count_pending_rows,
    describe_processed_profile,
    enqueue_profile_fetch_pending,
    is_fetch_cooldown_error,
    list_pending_rows,
    process_pending_row,
    requeue_stuck_done_parkrun_pending,
    reset_failed_parkrun_pending,
    reset_failed_pending,
)
from app.services.profile_linking_service import ProfileLinkingError, preview_profile_link


def _sample_user(db_session: Session) -> User:
    user = User(telegram_id=int(uuid4().int % 10_000_000_000), consent_accepted=True)
    db_session.add(user)
    db_session.flush()
    return user


def test_is_fetch_cooldown_error() -> None:
    from app.parkrun.errors import ParkrunProfileParseError

    assert is_fetch_cooldown_error(ParkrunBanDetected("parkrun fetch in cooldown until 9999999999"))
    assert is_fetch_cooldown_error(Exception("cooldown until 123"))
    assert is_fetch_cooldown_error(
        ParkrunProfileParseError("Ban/protection page detected")
    )
    wrapped = ParkrunProfileParseError("wrapped")
    wrapped.__cause__ = ParkrunBanDetected("ban")
    assert is_fetch_cooldown_error(wrapped)


def test_enqueue_dedupes_by_external_id(db_session: Session) -> None:
    user = _sample_user(db_session)
    exc = ParkrunBanDetected("parkrun fetch in cooldown until 1780444000")
    first = enqueue_profile_fetch_pending(
        db_session,
        platform_code="parkrun",
        profile_input="3197430",
        user_id=user.id,
        exc=exc,
    )
    second = enqueue_profile_fetch_pending(
        db_session,
        platform_code="parkrun",
        profile_input="https://www.parkrun.org.uk/parkrunner/3197430/",
        user_id=user.id,
        exc=exc,
    )
    db_session.commit()
    assert first.id == second.id
    assert first.external_user_id == "3197430"
    pending = list_pending_rows(db_session, platform_code="parkrun")
    assert len(pending) == 1
    assert pending[0].status == ProfileFetchPendingStatus.pending


def test_preview_enqueues_on_cooldown(db_session: Session, monkeypatch) -> None:
    platform = db_session.query(Platform).filter(Platform.code == "parkrun").one()
    platform.is_active = True
    user = _sample_user(db_session)

    def _raise_cooldown(_db, _platform_code, _profile_url):
        raise ParkrunBanDetected("parkrun fetch in cooldown until 1780445000")

    # Подменять надо именно fetch_live_preview_bundle: при переданном user
    # preview_profile_link уходит в persist_live_profile_preview и до
    # adapter.fetch_profile_preview не доходит. С подменой адаптера тест
    # уходил в живой parkrun через Playwright и висел вечно.
    monkeypatch.setattr(
        "app.services.profile_preview_persist.fetch_live_preview_bundle",
        _raise_cooldown,
    )

    try:
        preview_profile_link(db_session, "parkrun", "3197430", user=user)
        raise AssertionError("expected ProfileLinkingError")
    except ProfileLinkingError as exc:
        assert exc.status_code == 503
        assert "добавлена в очередь" in exc.message

    pending = list_pending_rows(db_session, platform_code="parkrun")
    assert len(pending) == 1
    assert pending[0].external_user_id == "3197430"


def test_requeue_stuck_done_parkrun_without_platform_link(db_session: Session) -> None:
    user = _sample_user(db_session)
    row = ProfileFetchPending(
        user_id=user.id,
        platform_code="parkrun",
        profile_input="3197430",
        external_user_id="3197430",
        status=ProfileFetchPendingStatus.done,
    )
    db_session.add(row)
    db_session.commit()

    assert requeue_stuck_done_parkrun_pending(db_session) == 1
    db_session.refresh(row)
    assert row.status == ProfileFetchPendingStatus.pending
    assert row.attempts == 0


def test_list_pending_rows_prioritizes_user_requests_over_system_backlog(
    db_session: Session,
) -> None:
    """Строка без user_id (системный discovery/seed-backlog) не должна вставать
    впереди реального пользовательского запроса, даже если она старше.

    platform_code — приватный для теста, чтобы не смешиваться с реальным
    parkrun-backlog'ом, который уже лежит в БД."""
    platform_code = "test-priority-check"
    user = _sample_user(db_session)
    system_row = ProfileFetchPending(
        platform_code=platform_code,
        profile_input="old-system-row",
        operation=ProfileFetchPendingOperation.activity_import,
        reason=ProfileFetchPendingReason.cooldown,
        user_id=None,
    )
    db_session.add(system_row)
    db_session.flush()
    user_row = ProfileFetchPending(
        platform_code=platform_code,
        profile_input="new-user-row",
        operation=ProfileFetchPendingOperation.profile_preview,
        reason=ProfileFetchPendingReason.cooldown,
        user_id=user.id,
    )
    db_session.add(user_row)
    db_session.commit()

    pending = list_pending_rows(db_session, platform_code=platform_code, limit=10)
    assert [row.profile_input for row in pending] == ["new-user-row", "old-system-row"]


def test_list_pending_rows_prioritizes_runpark_seed_over_discovery_backlog(
    db_session: Session,
) -> None:
    """RunPark-архив гарантирует историю у атлета; discovery (s95-декаплинг
    и т.п.) — лишь гипотеза, что профиль вообще существует. Первое должно
    сортироваться раньше второго внутри безымянного системного backlog'а."""
    platform_code = "test-priority-check-runpark"
    discovery_row = ProfileFetchPending(
        platform_code=platform_code,
        profile_input="discovery-row",
        operation=ProfileFetchPendingOperation.activity_import,
        reason=ProfileFetchPendingReason.error,
        last_error="queued by s95 sync (parkrun decoupled from s95)",
        user_id=None,
    )
    db_session.add(discovery_row)
    db_session.flush()
    runpark_row = ProfileFetchPending(
        platform_code=platform_code,
        profile_input="runpark-row",
        operation=ProfileFetchPendingOperation.activity_import,
        reason=ProfileFetchPendingReason.error,
        last_error=RUNPARK_SEED_NOTE_PREFIX,
        user_id=None,
    )
    db_session.add(runpark_row)
    db_session.commit()

    pending = list_pending_rows(db_session, platform_code=platform_code, limit=10)
    assert [row.profile_input for row in pending] == ["runpark-row", "discovery-row"]


def test_permanent_not_found_is_marked_and_not_resurrected(
    db_session: Session, monkeypatch
) -> None:
    """Профиль, которого реально нет на parkrun (404), не должен пытаться
    загрузиться на КАЖДОМ старте демона — reset_failed_parkrun_pending
    раньше воскрешал такие строки безусловно (жалоба про 790115304)."""
    import app.services.profile_fetch_pending_service as svc

    row = ProfileFetchPending(
        platform_code="parkrun",
        profile_input="99999999",
        external_user_id="99999999",
        operation=ProfileFetchPendingOperation.activity_import,
        reason=ProfileFetchPendingReason.error,
    )
    db_session.add(row)
    db_session.commit()

    def _raise_not_found(*_args, **_kwargs):
        raise ParkrunProfileNotFound("Профиль parkrun не найден или недоступен")

    monkeypatch.setattr(svc, "_import_parkrun_activity", _raise_not_found)

    outcome = process_pending_row(db_session, row)
    assert outcome == "not_found"
    assert row.status == ProfileFetchPendingStatus.failed
    assert row.last_error.startswith(PERMANENT_ERROR_PREFIX)

    # Демон вызывает это на КАЖДОМ старте — не должно воскрешать permanent-404.
    reset_count = reset_failed_parkrun_pending(db_session)
    assert reset_count == 0
    assert row.status == ProfileFetchPendingStatus.failed


def test_reset_failed_parkrun_pending_still_retries_transient_failures(
    db_session: Session,
) -> None:
    """Обычная (не permanent) ошибка должна по-прежнему ретраиться при
    следующем старте демона — защита касается только permanent-404."""
    row = ProfileFetchPending(
        platform_code="parkrun",
        profile_input="1111111",
        external_user_id="1111111",
        operation=ProfileFetchPendingOperation.activity_import,
        status=ProfileFetchPendingStatus.failed,
        reason=ProfileFetchPendingReason.error,
        last_error="parkrun fetch in cooldown until 1780445000",
        attempts=5,
    )
    db_session.add(row)
    db_session.commit()

    reset_count = reset_failed_parkrun_pending(db_session)
    assert reset_count == 1
    assert row.status == ProfileFetchPendingStatus.pending
    assert row.attempts == 0


def test_enqueue_reopens_done_row_instead_of_duplicating(db_session: Session) -> None:
    """Дедуп раньше искал только среди 'pending'-строк — 'done'/'failed' не
    находились, и для одного атлета копилось несколько независимых строк
    (нашли на живых данных: s95-атлет 790269774 заведён дважды)."""
    row = ProfileFetchPending(
        platform_code="s95",
        profile_input="790269774",
        external_user_id="790269774",
        operation=ProfileFetchPendingOperation.activity_import,
        status=ProfileFetchPendingStatus.done,
        attempts=2,
        processed_at=datetime.now(timezone.utc),
    )
    db_session.add(row)
    db_session.commit()

    result = enqueue_profile_fetch_pending(
        db_session,
        platform_code="s95",
        profile_input="790269774",
        user_id=None,
        operation=ProfileFetchPendingOperation.activity_import,
        exc=S95BanDetected("HTTP 403 from S95 for https://s95.ru/athletes/790269774/"),
    )

    total = (
        db_session.query(ProfileFetchPending)
        .filter(
            ProfileFetchPending.platform_code == "s95",
            ProfileFetchPending.external_user_id == "790269774",
        )
        .count()
    )
    assert total == 1
    assert result.id == row.id
    assert result.status == ProfileFetchPendingStatus.pending
    assert result.attempts == 0
    assert result.processed_at is None


def test_enqueue_does_not_reset_status_of_row_being_processed(db_session: Session) -> None:
    row = ProfileFetchPending(
        platform_code="s95",
        profile_input="790269774",
        external_user_id="790269774",
        operation=ProfileFetchPendingOperation.activity_import,
        status=ProfileFetchPendingStatus.processing,
        attempts=1,
    )
    db_session.add(row)
    db_session.commit()

    result = enqueue_profile_fetch_pending(
        db_session,
        platform_code="s95",
        profile_input="790269774",
        user_id=None,
        exc=S95BanDetected("HTTP 403 from S95 for https://s95.ru/athletes/790269774/"),
    )

    assert result.id == row.id
    assert result.status == ProfileFetchPendingStatus.processing
    assert result.attempts == 1  # не сброшен — демон прямо сейчас держит строку


def test_cooldown_exhausted_after_max_attempts_gives_up_permanently(
    db_session: Session, monkeypatch
) -> None:
    """403 от S95 месяцами подряд не должен крутиться в pending вечно —
    после MAX_RETRY_ATTEMPTS строка сдаётся, и reset_failed_pending её
    больше не воскрешает (нашли на живых данных: s95-атлет 790269774)."""
    import app.services.profile_fetch_pending_service as svc

    row = ProfileFetchPending(
        platform_code="s95",
        profile_input="790269774",
        external_user_id="790269774",
        operation=ProfileFetchPendingOperation.activity_import,
        reason=ProfileFetchPendingReason.error,
    )
    db_session.add(row)
    db_session.commit()

    def _raise_ban(*_args, **_kwargs):
        raise S95BanDetected("HTTP 403 from S95 for https://s95.ru/athletes/790269774/")

    monkeypatch.setattr(svc, "_import_s95_activity", _raise_ban)

    for attempt in range(1, MAX_RETRY_ATTEMPTS):
        row.status = ProfileFetchPendingStatus.pending
        outcome = process_pending_row(db_session, row)
        assert outcome == "cooldown", f"attempt {attempt}"
        assert row.status == ProfileFetchPendingStatus.pending

    row.status = ProfileFetchPendingStatus.pending
    outcome = process_pending_row(db_session, row)
    assert outcome == "cooldown_exhausted"
    assert row.status == ProfileFetchPendingStatus.failed
    assert row.last_error.startswith(PERMANENT_ERROR_PREFIX)

    reset_count = reset_failed_pending(db_session, "s95")
    assert reset_count == 0
    assert row.status == ProfileFetchPendingStatus.failed


def test_describe_processed_profile_returns_url_and_name(db_session: Session) -> None:
    platform = db_session.query(Platform).filter(Platform.code == "parkrun").one()
    db_session.add(
        Participant(
            platform_id=platform.id,
            external_user_id="5003845",
            display_name="Иван ИВАНОВ",
            profile_url="https://www.parkrun.org.uk/parkrunner/5003845/",
        )
    )
    db_session.commit()

    # Формат «успех: Имя → ссылка» уходит в статусную строку демона
    # (parkrun_queue_daemon.session.show_status), см. 140b06f.
    description = describe_processed_profile(db_session, "parkrun", "5003845")
    assert description == "успех: Иван ИВАНОВ → https://www.parkrun.org.uk/parkrunner/5003845/"


def test_describe_processed_profile_none_for_unknown_or_missing_id(
    db_session: Session,
) -> None:
    assert describe_processed_profile(db_session, "parkrun", None) is None
    assert describe_processed_profile(db_session, "parkrun", "no-such-id") is None


def test_count_pending_rows_ignores_limit_of_list_pending_rows(
    db_session: Session,
) -> None:
    """count_pending_rows должен видеть ВЕСЬ backlog, а list_pending_rows —
    только limit строк из него (иначе демон печатает 'в очереди 300', хотя
    реально ждёт несколько тысяч)."""
    platform_code = "test-count-pending"
    for i in range(7):
        db_session.add(
            ProfileFetchPending(
                platform_code=platform_code,
                profile_input=f"row-{i}",
                operation=ProfileFetchPendingOperation.activity_import,
                reason=ProfileFetchPendingReason.error,
            )
        )
    db_session.commit()

    assert count_pending_rows(db_session, platform_code) == 7
    assert len(list_pending_rows(db_session, platform_code=platform_code, limit=3)) == 3
