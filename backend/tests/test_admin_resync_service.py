"""«Обновить по ссылке»: разбор ссылок, дифф протокола и прогон по 5 вёрст
с подменённым фетчем."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import AdminResyncRequest, Location, Platform
from app.platform_adapters.canonical import (
    CanonicalEventSummary,
    CanonicalParticipant,
    CanonicalRunResult,
    CanonicalVolunteerResult,
)
from app.services.admin_resync_service import (
    EventSnapshot,
    ResyncInputError,
    ResyncProgress,
    RunSnap,
    VolunteerSnap,
    _run_five_verst_location,
    _run_five_verst_profile,
    _run_five_verst_protocol,
    describe_diff,
    diff_snapshots,
    parse_resync_input,
    summarize_result,
)
from app.sync import upsert
from app.sync.five_verst_protocol import fetch_and_upsert_event_protocol

# ===== Разбор ссылок =====


@pytest.mark.parametrize(
    ("raw", "platform", "kind", "expected"),
    [
        ("https://5verst.ru/userstats/790107594/", "five_verst", "profile", {"external_user_id": "790107594"}),
        ("5verst.ru/userstats/790107594", "five_verst", "profile", {"external_user_id": "790107594"}),
        (
            "https://5verst.ru/serpukhovgorodskoybor/results/22.08.2026/",
            "five_verst",
            "protocol",
            {"slug": "serpukhovgorodskoybor", "event_date": date(2026, 8, 22)},
        ),
        ("https://5verst.ru/zil/results/all/", "five_verst", "location", {"slug": "zil"}),
        ("https://5verst.ru/zil/", "five_verst", "location", {"slug": "zil"}),
        ("https://5verst.ru/zil/results/", "five_verst", "location", {"slug": "zil"}),
        ("https://s95.ru/athletes/5207/", "s95", "profile", {"external_user_id": "5207", "domain": "s95.ru"}),
        ("https://s95.ru/activities/4124", "s95", "protocol", {"activity_id": "4124"}),
        ("https://s95.ru/activities/4124.json", "s95", "protocol", {"activity_id": "4124"}),
        ("https://s95.ru/events/troitsk", "s95", "location", {"slug": "troitsk", "domain": "s95.ru"}),
        ("https://s95.by/events/minsk.json", "s95", "location", {"slug": "minsk", "domain": "s95.by"}),
    ],
)
def test_parse_resync_input(raw: str, platform: str, kind: str, expected: dict) -> None:
    target = parse_resync_input(raw)
    assert target.platform_code == platform
    assert target.kind == kind
    for key, value in expected.items():
        assert getattr(target, key) == value


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "790107594",  # голое число: платформу не угадать
        "https://example.com/zil/results/all/",
        "https://5verst.ru/results/latest/",  # служебная страница, не локация
        "https://5verst.ru/zil/results/99.99.2026/",
        "https://s95.ru/clubs/",
    ],
)
def test_parse_resync_input_rejects(raw: str) -> None:
    with pytest.raises(ResyncInputError):
        parse_resync_input(raw)


# ===== Дифф =====


def _run(position: int, sec: int | None, name: str, *, unknown: bool = False) -> RunSnap:
    return RunSnap(
        position=position,
        finish_time_sec=sec,
        finish_time_display=f"00:{sec // 60:02d}:{sec % 60:02d}" if sec is not None else None,
        status="unknown" if unknown else "finished",
        name=name,
        external_user_id="unknown:x" if unknown else f"79{position:07d}",
    )


def test_diff_snapshots_categories() -> None:
    before = EventSnapshot(
        runs={
            "a": _run(1, 1200, "Первый"),
            "b": _run(2, 1300, "Второй"),
            "unk3": _run(3, None, "НЕИЗВЕСТНЫЙ", unknown=True),
            "d": _run(4, 1500, "Четвёртый"),
        },
        volunteers={"v1": VolunteerSnap(role="Организатор", name="Орг"), "v2": VolunteerSnap(role="Фото", name="Фото")},
    )
    after = EventSnapshot(
        runs={
            "a": _run(1, 1200, "Первый"),
            "b": _run(2, 1290, "Второй"),  # время поправили
            "c": _run(3, None, "Третий"),  # неизвестный опознан: та же позиция и время
            "e": _run(5, 1600, "Пятый"),  # добавлен
        },
        volunteers={
            "v1": VolunteerSnap(role="Организатор", name="Орг"),
            "v3": VolunteerSnap(role="Маршал", name="Новый"),
        },
    )
    diff = diff_snapshots(before, after)
    assert diff["changed"] is True
    runs = diff["runs"]
    assert [r["name"] for r in runs["added"]] == ["Пятый"]
    assert [r["name"] for r in runs["removed"]] == ["Четвёртый"]
    assert runs["changed"][0]["name"] == "Второй"
    assert runs["changed"][0]["time_before"] == "00:21:40"
    assert runs["changed"][0]["time_after"] == "00:21:30"
    assert [r["name"] for r in runs["identified"]] == ["Третий"]
    vols = diff["volunteers"]
    assert vols["added"] == [{"name": "Новый", "role": "Маршал"}]
    assert vols["removed"] == [{"name": "Фото", "role": "Фото"}]
    text = describe_diff(diff)
    assert "добавлено 1" in text and "удалено 1" in text and "поправлено 1" in text and "опознано 1" in text
    assert "волонтёры: +1, −1" in text


def test_diff_snapshots_no_changes() -> None:
    snap = EventSnapshot(runs={"a": _run(1, 1200, "Первый")}, volunteers={})
    diff = diff_snapshots(snap, snap)
    assert diff["changed"] is False
    assert describe_diff(diff) == "без изменений"


# ===== Прогон по 5 вёрст с подменённым фетчем =====


def _five_verst(db: Session) -> Platform:
    platform = db.query(Platform).filter(Platform.code == "five_verst").one_or_none()
    if platform is None:
        pytest.skip("five_verst platform not seeded")
    return platform


def _location(db: Session, platform: Platform) -> Location:
    slug = f"resync-{uuid4().hex[:8]}"
    location = Location(
        platform_id=platform.id,
        external_key=slug,
        name="Resync Park",
        source_url=f"https://5verst.ru/{slug}/",
    )
    db.add(location)
    db.flush()
    return location


def _summary(slug: str, event_date: date, number: int, *, hash_: str) -> CanonicalEventSummary:
    return CanonicalEventSummary(
        external_event_key=f"{slug}:{number}:{event_date.isoformat()}",
        event_date=event_date,
        event_number=number,
        location_external_key=slug,
        location_name="Resync Park",
        finishers_count=2,
        volunteers_count=1,
        source_url=f"https://5verst.ru/{slug}/results/{event_date.strftime('%d.%m.%Y')}/",
        summary_hash=hash_,
    )


def _result(
    slug: str, event_date: date, *, user: str, name: str, position: int, sec: int | None, unknown: bool = False
) -> CanonicalRunResult:
    return CanonicalRunResult(
        external_result_key=f"{slug}:{event_date.isoformat()}:{user}",
        event_date=event_date,
        external_user_id=user,
        participant_name=name,
        position=position,
        finish_time_sec=sec,
        finish_time_display=f"00:{sec // 60:02d}:{sec % 60:02d}" if sec is not None else None,
        status="unknown" if unknown else "finished",
        location_external_key=slug,
        location_name="Resync Park",
    )


def _volunteer(slug: str, event_date: date, *, user: str, name: str, role: str) -> CanonicalVolunteerResult:
    return CanonicalVolunteerResult(
        external_result_key=f"{slug}:{event_date.isoformat()}:vol:{user}",
        event_date=event_date,
        external_user_id=user,
        participant_name=name,
        role=role,
        location_external_key=slug,
        location_name="Resync Park",
    )


def _load_protocol(
    db: Session, platform: Platform, location: Location, summary: CanonicalEventSummary, runs, vols, *, hash_: str
) -> None:
    summary_row, _ = upsert.upsert_event_summary(db, platform, location, summary)
    with (
        patch("app.sync.five_verst_protocol.bulk_parser.fetch_event_protocol", return_value=(runs, vols, "<html/>")),
        patch("app.sync.five_verst_protocol.bulk_parser.source_hash", return_value=hash_),
    ):
        fetch_and_upsert_event_protocol(db, platform, location, summary, summary_row)
    db.commit()


def _request(db: Session, *, kind: str, target: dict) -> AdminResyncRequest:
    row = AdminResyncRequest(
        platform_code="five_verst",
        kind=kind,
        input_url=target["canonical_url"],
        target=target,
        status="running",
        steps=[],
    )
    db.add(row)
    db.commit()
    return row


def test_protocol_resync_reports_diff(db_session: Session) -> None:
    platform = _five_verst(db_session)
    location = _location(db_session, platform)
    slug = location.external_key
    event_date = date(2026, 8, 22)
    summary = _summary(slug, event_date, 219, hash_="sum-1")
    unknown_key = f"unknown:{slug}:{event_date.isoformat()}:3"
    _load_protocol(
        db_session,
        platform,
        location,
        summary,
        [
            _result(slug, event_date, user="790000001", name="Олег ДУБИНКИН", position=1, sec=1085),
            _result(slug, event_date, user="790000002", name="Дмитрий НАУМОВ", position=2, sec=1260),
            _result(slug, event_date, user=unknown_key, name="НЕИЗВЕСТНЫЙ", position=3, sec=None, unknown=True),
            _result(slug, event_date, user="790000004", name="Павел КАЛИНИН", position=4, sec=1377),
        ],
        [_volunteer(slug, event_date, user="790000009", name="Орг", role="Организатор")],
        hash_="proto-1",
    )

    row = _request(
        db_session,
        kind="protocol",
        target={"canonical_url": summary.source_url, "slug": slug, "event_date": event_date.isoformat()},
    )
    fresh_runs = [
        _result(slug, event_date, user="790000001", name="Олег ДУБИНКИН", position=1, sec=1085),
        _result(slug, event_date, user="790000002", name="Дмитрий НАУМОВ", position=2, sec=1255),  # поправили время
        _result(slug, event_date, user="790107594", name="Александр ЯКУШИН", position=3, sec=None),  # был неизвестным
        _result(slug, event_date, user="790000005", name="Новый ФИНИШЁР", position=5, sec=1500),  # добавили
    ]
    fresh_vols = [
        _volunteer(slug, event_date, user="790000009", name="Орг", role="Организатор"),
        _volunteer(slug, event_date, user="790000010", name="Маршал", role="Маршал"),
    ]
    with (
        patch(
            "app.sync.five_verst_protocol.bulk_parser.fetch_event_protocol",
            return_value=(fresh_runs, fresh_vols, "<html/>"),
        ),
        patch("app.sync.five_verst_protocol.bulk_parser.source_hash", return_value="proto-2"),
    ):
        result = _run_five_verst_protocol(db_session, row, ResyncProgress(db_session, row.id))

    assert result["protocols_checked"] == 1
    assert result["protocols_changed"] == 1
    outcome = result["protocols"][0]
    assert outcome["reason"] == "requested"
    assert outcome["changed"] is True
    runs = outcome["diff"]["runs"]
    assert [r["name"] for r in runs["added"]] == ["Новый ФИНИШЁР"]
    assert [r["name"] for r in runs["removed"]] == ["Павел КАЛИНИН"]
    assert [r["name"] for r in runs["changed"]] == ["Дмитрий НАУМОВ"]
    assert [r["name"] for r in runs["identified"]] == ["Александр ЯКУШИН"]
    assert outcome["diff"]["volunteers"]["added"] == [{"name": "Маршал", "role": "Маршал"}]

    db_session.refresh(row)
    codes = [step["code"] for step in row.steps]
    assert codes == ["fetch", "protocol_fetch", "protocol_done"]
    assert "добавлено 1" in row.steps[-1]["text"]

    row.status = "done"
    row.result = result
    assert "изменилось 1" in summarize_result(row)


def test_protocol_resync_without_changes(db_session: Session) -> None:
    platform = _five_verst(db_session)
    location = _location(db_session, platform)
    slug = location.external_key
    event_date = date(2026, 8, 29)
    summary = _summary(slug, event_date, 220, hash_="sum-1")
    runs = [_result(slug, event_date, user="790000001", name="Олег ДУБИНКИН", position=1, sec=1085)]
    _load_protocol(db_session, platform, location, summary, runs, [], hash_="proto-1")

    row = _request(
        db_session,
        kind="protocol",
        target={"canonical_url": summary.source_url, "slug": slug, "event_date": event_date.isoformat()},
    )
    with (
        patch("app.sync.five_verst_protocol.bulk_parser.fetch_event_protocol", return_value=(runs, [], "<html/>")),
        patch("app.sync.five_verst_protocol.bulk_parser.source_hash", return_value="proto-1"),
    ):
        result = _run_five_verst_protocol(db_session, row, ResyncProgress(db_session, row.id))

    assert result["protocols_changed"] == 0
    assert result["protocols_unchanged"] == 1
    db_session.refresh(row)
    assert row.steps[-1]["text"].endswith("без изменений")


def test_location_resync_refetches_only_diverged(db_session: Session) -> None:
    platform = _five_verst(db_session)
    location = _location(db_session, platform)
    slug = location.external_key
    old_date, new_date = date(2026, 8, 22), date(2026, 8, 29)
    old_summary = _summary(slug, old_date, 219, hash_="sum-219")
    _load_protocol(
        db_session,
        platform,
        location,
        old_summary,
        [_result(slug, old_date, user="790000001", name="Олег ДУБИНКИН", position=1, sec=1085)],
        [],
        hash_="proto-219",
    )
    new_summary = _summary(slug, new_date, 220, hash_="sum-220")

    row = _request(
        db_session, kind="location", target={"canonical_url": f"https://5verst.ru/{slug}/results/all/", "slug": slug}
    )
    new_runs = [_result(slug, new_date, user="790000002", name="Дмитрий НАУМОВ", position=1, sec=1260)]
    with (
        patch(
            "app.services.admin_resync_service.bulk_parser.fetch_event_summaries",
            return_value=([old_summary, new_summary], "<html/>"),
        ),
        patch(
            "app.sync.five_verst_protocol.bulk_parser.fetch_event_protocol", return_value=(new_runs, [], "<html/>")
        ) as fetch,
        patch("app.sync.five_verst_protocol.bulk_parser.source_hash", return_value="proto-220"),
    ):
        result = _run_five_verst_location(db_session, row, ResyncProgress(db_session, row.id))

    assert fetch.call_count == 1  # старый старт совпал со сводкой — не перекачивали
    assert result["summaries_total"] == 2
    assert result["summaries_unchanged"] == 1
    assert result["summaries_diverged"] == 1
    assert result["reasons"] == {"new_summary": 1}
    outcome = result["protocols"][0]
    assert outcome["event_date"] == new_date.isoformat()
    assert outcome["diff"]["runs"]["after"] == 1
    db_session.refresh(row)
    assert any("нашли расхождения в 1 стартах" in step["text"] for step in row.steps)


def test_profile_resync_refetches_missing_protocol(db_session: Session) -> None:
    platform = _five_verst(db_session)
    location = _location(db_session, platform)
    slug = location.external_key
    known_date, missing_date = date(2026, 8, 15), date(2026, 8, 22)
    # Уникальный id: dev-БД — копия прода, и у настоящего 790107594 там 150+ строк.
    user_id = f"79{uuid4().int % 10**8:08d}"
    _load_protocol(
        db_session,
        platform,
        location,
        _summary(slug, known_date, 218, hash_="sum-218"),
        [_result(slug, known_date, user=user_id, name="Александр ЯКУШИН", position=3, sec=1303)],
        [],
        hash_="proto-218",
    )
    # Протокол 22.08 у нас есть, но человека в нём нет — как в реальном Серпухове.
    _load_protocol(
        db_session,
        platform,
        location,
        _summary(slug, missing_date, 219, hash_="sum-219"),
        [
            _result(
                slug,
                missing_date,
                user=f"unknown:{slug}:{missing_date.isoformat()}:3",
                name="НЕИЗВЕСТНЫЙ",
                position=3,
                sec=None,
                unknown=True,
            )
        ],
        [],
        hash_="proto-219",
    )

    profile = CanonicalParticipant(
        external_user_id=user_id,
        display_name="Александр ЯКУШИН",
        profile_url=f"https://5verst.ru/userstats/{user_id}/",
        total_runs=2,
    )
    profile_runs = [
        _result(slug, known_date, user=user_id, name="Александр ЯКУШИН", position=None, sec=1303),
        _result(slug, missing_date, user=user_id, name="Александр ЯКУШИН", position=None, sec=1303),
    ]
    row = _request(
        db_session,
        kind="profile",
        target={"canonical_url": profile.profile_url, "external_user_id": user_id},
    )
    refetched = [_result(slug, missing_date, user=user_id, name="Александр ЯКУШИН", position=3, sec=1303)]
    with (
        patch("app.platform_adapters.five_verst.parser.fetch_userstats_html", return_value="<html/>"),
        patch("app.platform_adapters.five_verst.parser.parse_userstats_html", return_value=profile),
        patch("app.platform_adapters.five_verst.parser.parse_userstats_runs_html", return_value=profile_runs),
        patch("app.platform_adapters.five_verst.parser.parse_userstats_volunteering_html", return_value=[]),
        patch(
            "app.sync.five_verst_protocol.bulk_parser.fetch_event_protocol", return_value=(refetched, [], "<html/>")
        ) as fetch,
        patch("app.sync.five_verst_protocol.bulk_parser.source_hash", return_value="proto-219b"),
    ):
        result = _run_five_verst_profile(db_session, row, ResyncProgress(db_session, row.id))

    assert fetch.call_count == 1
    assert result["missing_total"] == 1
    assert result["db_runs_before"] == 1
    assert result["db_runs_after"] == 2
    outcome = result["protocols"][0]
    assert outcome["reason"] == "missing_run"
    assert [r["name"] for r in outcome["diff"]["runs"]["identified"]] == ["Александр ЯКУШИН"]
    db_session.refresh(row)
    assert any("нет в базе: 1" in step["text"] for step in row.steps)
