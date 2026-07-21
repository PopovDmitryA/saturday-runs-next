from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import (
    Participant,
    Platform,
    PlatformLink,
    PlatformLinkSyncStatus,
    SyncJob,
    SyncJobStatus,
    SyncJobTrigger,
    User,
)
from app.services.parkrun_queue_daemon import ParkrunWorkItem, build_parkrun_work_queue, run_parkrun_queue_daemon


def _user(db_session: Session) -> User:
    user = User(telegram_id=int(uuid4().int % 10_000_000_000), consent_accepted=True)
    db_session.add(user)
    db_session.flush()
    return user


def _parkrun_platform(db_session: Session) -> Platform:
    platform = db_session.query(Platform).filter(Platform.code == "parkrun").one_or_none()
    if platform is None:
        platform = Platform(code="parkrun", name="parkrun", base_url="https://www.parkrun.org.uk")
        db_session.add(platform)
        db_session.flush()
    return platform


def _errored_link(
    db_session: Session, platform: Platform, user: User, *, trigger: SyncJobTrigger
) -> PlatformLink:
    participant = Participant(
        platform_id=platform.id,
        external_user_id=f"runner-{uuid4().hex[:8]}",
        display_name="Runner",
    )
    db_session.add(participant)
    db_session.flush()
    link = PlatformLink(
        user_id=user.id,
        platform_id=platform.id,
        participant_id=participant.id,
        external_user_id=participant.external_user_id,
        external_url=f"https://parkrun.example/{participant.external_user_id}",
        sync_status=PlatformLinkSyncStatus.error,
    )
    db_session.add(link)
    db_session.flush()
    db_session.add(
        SyncJob(
            user_id=user.id,
            platform_link_id=link.id,
            trigger=trigger,
            status=SyncJobStatus.failed,
        )
    )
    db_session.commit()
    return link


def test_manual_refresh_click_jumps_ahead_of_pending_backlog(db_session: Session) -> None:
    platform = _parkrun_platform(db_session)
    manual_user = _user(db_session)
    manual_link = _errored_link(db_session, platform, manual_user, trigger=SyncJobTrigger.manual)

    login_user = _user(db_session)
    login_link = _errored_link(db_session, platform, login_user, trigger=SyncJobTrigger.login)

    items = build_parkrun_work_queue(db_session, limit_pending=50)

    kinds_and_users = [(item.kind, item.user_id) for item in items]
    assert kinds_and_users[0] == ("sync", manual_link.user_id)
    assert ("sync", login_link.user_id) == kinds_and_users[-1]


def test_only_the_latest_sync_job_decides_priority(db_session: Session) -> None:
    """Первый клик был login-триггером, повторный ручной клик поверх той же
    ошибки должен подтолкнуть линк вперёд — приоритет по ПОСЛЕДНЕЙ попытке."""
    platform = _parkrun_platform(db_session)
    user = _user(db_session)
    link = _errored_link(db_session, platform, user, trigger=SyncJobTrigger.login)
    db_session.add(
        SyncJob(
            user_id=user.id,
            platform_link_id=link.id,
            trigger=SyncJobTrigger.manual,
            status=SyncJobStatus.failed,
            # Postgres' now() is frozen for the whole transaction, so within a
            # single test transaction both jobs would otherwise tie on
            # created_at; a real manual click always lands in a later,
            # separate request/transaction than the original auto-sync job.
            created_at=datetime.now(timezone.utc) + timedelta(seconds=1),
        )
    )
    db_session.commit()

    other_user = _user(db_session)
    _errored_link(db_session, platform, other_user, trigger=SyncJobTrigger.login)

    items = build_parkrun_work_queue(db_session, limit_pending=50)
    assert items[0].user_id == user.id


class _FakeSession:
    """Двойник ParkrunDaemonSession для теста без реального фетча/браузера."""

    def __init__(self, *, log: list[str] | None = None) -> None:
        self.httpx_aborted = False
        self.status_calls: list[str] = []
        self._log = log

    def show_status(self, message: str) -> None:
        self.status_calls.append(message)

    def human_pause_between_jobs(self) -> None:
        if self._log is not None:
            self._log.append("pause")


def test_httpx_abort_stops_remaining_batch(db_session: Session, monkeypatch) -> None:
    """--no-browser: первый же признак защиты WAF должен остановить всю
    оставшуюся пачку, а не долбить её следующими элементами."""
    import app.services.parkrun_queue_daemon as daemon_module

    calls: list[str] = []
    session = _FakeSession()

    def _fake_sync(db, user_id, *, label, **kwargs):  # noqa: ARG001
        calls.append(label)
        if label == "first":
            session.httpx_aborted = True
        return "sync_ok: stub"

    monkeypatch.setattr(daemon_module, "sync_parkrun_runs_for_user", _fake_sync)

    items = [
        ParkrunWorkItem(kind="sync", label="first", user_id=uuid4()),
        ParkrunWorkItem(kind="sync", label="second", user_id=uuid4()),
        ParkrunWorkItem(kind="sync", label="third", user_id=uuid4()),
    ]

    run_parkrun_queue_daemon(db_session, session, items)

    assert calls == ["first"]


def test_pause_brackets_the_location_step_on_both_sides(db_session: Session, monkeypatch) -> None:
    """Раньше пауза стояла только ПОСЛЕ локации (перед следующим человеком) —
    между человеком и локацией её не было вовсе. Теперь human_pause_between_jobs
    должен вызываться и до, и после after_item()."""
    import app.services.parkrun_queue_daemon as daemon_module

    log: list[str] = []
    session = _FakeSession(log=log)

    monkeypatch.setattr(
        daemon_module,
        "sync_parkrun_runs_for_user",
        lambda db, user_id, *, label, **kw: log.append(f"person:{label}") or "sync_ok: stub",
    )

    def _after_item() -> str | None:
        log.append("location")
        return None

    items = [
        ParkrunWorkItem(kind="sync", label="a", user_id=uuid4()),
        ParkrunWorkItem(kind="sync", label="b", user_id=uuid4()),
    ]

    run_parkrun_queue_daemon(db_session, session, items, after_item=_after_item)

    assert log == [
        "person:a", "pause", "location", "pause",
        "person:b", "pause", "location",
    ]


def _db_error() -> Exception:
    from sqlalchemy.exc import OperationalError

    return OperationalError("stmt", {}, Exception("connection refused on port 5434"))


def test_stops_after_three_consecutive_db_connection_losses(db_session, monkeypatch) -> None:
    """Оборванный SSH-туннель к прод-БД: после 3 обрывов подряд прогон
    прерывается, а не молотит остаток очереди трейсбэками."""
    import app.services.parkrun_queue_daemon as daemon_module

    calls: list[str] = []

    def _boom(db, user_id, *, label, **kw):  # noqa: ARG001
        calls.append(label)
        raise _db_error()

    monkeypatch.setattr(daemon_module, "sync_parkrun_runs_for_user", _boom)

    session = _FakeSession()
    items = [ParkrunWorkItem(kind="sync", label=f"r{i}", user_id=uuid4()) for i in range(6)]

    result = run_parkrun_queue_daemon(db_session, session, items)

    assert calls == ["r0", "r1", "r2"]  # остановились на третьем, не дошли до r3..r5
    assert result["summary"].get("db_connection_lost") == 1


def test_normal_fetch_error_does_not_count_toward_db_stop(db_session, monkeypatch) -> None:
    """Обычная ошибка фетча parkrun (не обрыв БД) счётчик обрывов не
    накапливает — прогон идёт до конца."""
    import app.services.parkrun_queue_daemon as daemon_module

    calls: list[str] = []

    def _boom(db, user_id, *, label, **kw):  # noqa: ARG001
        calls.append(label)
        raise ValueError("parkrun parse failed")

    monkeypatch.setattr(daemon_module, "sync_parkrun_runs_for_user", _boom)

    session = _FakeSession()
    items = [ParkrunWorkItem(kind="sync", label=f"r{i}", user_id=uuid4()) for i in range(5)]

    result = run_parkrun_queue_daemon(db_session, session, items)

    assert len(calls) == 5  # ни разу не остановились
    assert "db_connection_lost" not in result["summary"]
