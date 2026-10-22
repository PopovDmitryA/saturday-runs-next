"""Журнал посещаемости локации: кто попадает в строки.

Карточка бэклога 16.10.2026 (Алмаз Мансуров), решения Дмитрия 21.10.2026: номер
последней строки должен значить «столько разных людей здесь бегало
(волонтёрило) за год». У Парка Кирова в 2026-м было 277 строк, из них 61 —
отдельные неопознанные (каждый такой финиш — своя личность), а во
вкладке «Волонтёры» у 237 строк стоял ноль. Теперь неопознанных нет в строках
ни одного среза (в итоговой строке по датам они учтены), нулей нет в
«Бегунах» и «Волонтёрах», а человек с обеими ролями есть в обоих срезах.
Фильтр месяца по-прежнему никого не прячет — только опускает вниз.
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import (
    Event,
    Location,
    Participant,
    Platform,
    PlatformLink,
    RunResult,
    User,
    VolunteerResult,
)
from app.services import location_page_service
from app.services.location_page_service import (
    _attendance_listed,
    _AttendancePerson,
    build_location_attendance,
)

AUG_29 = date(2026, 8, 29)
SEP_05 = date(2026, 9, 5)
SEP_12 = date(2026, 9, 12)


def _platform(db_session: Session) -> Platform:
    return db_session.query(Platform).filter(Platform.code == "five_verst").one()


def _participant(db_session: Session, name: str, external_user_id: str | None = None) -> Participant:
    row = Participant(
        platform_id=_platform(db_session).id,
        external_user_id=external_user_id or f"78{uuid4().int % 10**9:09d}",
        display_name=name,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _run(db_session: Session, event: Event, participant: Participant) -> None:
    db_session.add(
        RunResult(
            event_id=event.id,
            participant_id=participant.id,
            external_result_key=f"run-{uuid4()}",
            finish_time_sec=1500,
            finish_time_display="00:25:00",
            status="finished",
        )
    )


def _volunteer(db_session: Session, event: Event, participant: Participant) -> None:
    db_session.add(
        VolunteerResult(
            event_id=event.id,
            participant_id=participant.id,
            external_result_key=f"vol-{uuid4()}",
            role="Секундомер",
        )
    )


@pytest.fixture
def scene(db_session: Session) -> dict[str, object]:
    platform = _platform(db_session)
    location = Location(
        platform_id=platform.id,
        external_key=f"attn-{uuid4().hex[:8]}",
        name="Парк Журнальный",
    )
    db_session.add(location)
    db_session.flush()
    events: dict[date, Event] = {}
    for day in (AUG_29, SEP_05, SEP_12):
        event = Event(
            platform_id=platform.id,
            location_id=location.id,
            external_event_key=f"{location.external_key}:{day.isoformat()}",
            event_date=day,
        )
        db_session.add(event)
        events[day] = event
    db_session.flush()

    runner = _participant(db_session, "Бегун ТОЛЬКОБЕГОВ")
    volunteer = _participant(db_session, "Волонтёр ТОЛЬКОВОЛОВ")
    both = _participant(db_session, "Обе РОЛЕВА")
    for day in (AUG_29, SEP_05, SEP_12):
        _run(db_session, events[day], runner)
    _volunteer(db_session, events[SEP_05], volunteer)
    _run(db_session, events[SEP_05], both)
    _volunteer(db_session, events[SEP_12], both)

    # Неопознанные: у каждого финиша без штрихкода своя одноразовая личность.
    # Вторая — с именем из протокола, но по ключу всё та же заглушка.
    key = location.external_key
    for external_id, name, day in (
        (f"unknown:{key}:2026-09-05:10", "НЕИЗВЕСТНЫЙ", SEP_05),
        (f"unknown:{key}:2026-09-05:11", "Александр ИМЕНОВ", SEP_05),
        (f"unknown:{key}:2026-09-12:7", "НЕИЗВЕСТНЫЙ", SEP_12),
    ):
        _run(db_session, events[day], _participant(db_session, name, external_id))

    # Зритель: привязанный профиль, бежал один раз и ни разу не волонтёрил.
    viewer_participant = _participant(db_session, "Зритель ЗРИТЕЛЕВ")
    viewer = User(consent_accepted=True)
    db_session.add(viewer)
    db_session.flush()
    db_session.add(
        PlatformLink(
            user_id=viewer.id,
            platform_id=platform.id,
            participant_id=viewer_participant.id,
            external_user_id=viewer_participant.external_user_id,
            external_url="https://example.org",
        )
    )
    _run(db_session, events[AUG_29], viewer_participant)
    db_session.commit()
    return {"slug": location.external_key, "viewer": viewer, "events": events}


def _journal(db_session: Session, scene: dict[str, object], kind: str, **kwargs: object) -> dict[str, object]:
    kwargs.setdefault("use_cache", False)
    payload = build_location_attendance(
        db_session, str(scene["slug"]), year=2026, kind=kind, **kwargs  # type: ignore[arg-type]
    )
    assert payload is not None
    return payload


def _names(payload: dict[str, object]) -> list[str]:
    return [str(row["name"]) for row in payload["rows"]]  # type: ignore[union-attr]


def test_unidentified_are_not_rows_but_stay_in_date_totals(
    db_session: Session, scene: dict[str, object]
) -> None:
    payload = _journal(db_session, scene, "all")
    names = _names(payload)
    assert sorted(names) == sorted(
        ["Бегун ТОЛЬКОБЕГОВ", "Волонтёр ТОЛЬКОВОЛОВ", "Обе РОЛЕВА", "Зритель ЗРИТЕЛЕВ"]
    )
    assert payload["total_rows"] == 4

    # Итоговая строка — число людей на старте, неопознанные в ней учтены.
    totals = payload["date_totals"]
    assert totals["2026-09-05"] == {  # type: ignore[index]
        "runners": 4,
        "volunteers": 1,
        "people": 5,
        "unidentified_runners": 2,
        "unidentified_volunteers": 0,
        "unidentified_people": 2,
    }
    assert totals["2026-09-12"]["runners"] == 2  # type: ignore[index]
    assert totals["2026-09-12"]["unidentified_runners"] == 1  # type: ignore[index]
    assert totals["2026-08-29"]["unidentified_people"] == 0  # type: ignore[index]


def test_slices_drop_zero_rows_but_keep_people_with_both_roles(
    db_session: Session, scene: dict[str, object]
) -> None:
    runners = _journal(db_session, scene, "runners")
    assert sorted(_names(runners)) == sorted(["Бегун ТОЛЬКОБЕГОВ", "Обе РОЛЕВА", "Зритель ЗРИТЕЛЕВ"])
    assert runners["total_rows"] == 3

    volunteers = _journal(db_session, scene, "volunteers")
    assert sorted(_names(volunteers)) == sorted(["Волонтёр ТОЛЬКОВОЛОВ", "Обе РОЛЕВА"])
    assert volunteers["total_rows"] == 2

    # Обе роли — в обоих срезах, «чужая» роль не закрашена.
    both_in_runners = next(row for row in runners["rows"] if row["name"] == "Обе РОЛЕВА")  # type: ignore[union-attr]
    assert both_in_runners["year_total"] == 1
    assert [item["roles"] for item in both_in_runners["items"]] == [[]]
    both_in_volunteers = next(row for row in volunteers["rows"] if row["name"] == "Обе РОЛЕВА")  # type: ignore[union-attr]
    assert both_in_volunteers["year_total"] == 1
    assert [item["run"] for item in both_in_volunteers["items"]] == [False]


def test_month_moves_people_down_but_does_not_hide_them(
    db_session: Session, scene: dict[str, object]
) -> None:
    """Решение 06.10.2026 (релиз 3.8.5) в силе: месяц только меняет порядок."""
    august = _journal(db_session, scene, "runners", month="2026-08")
    assert august["total_rows"] == 3
    names = _names(august)
    # «Обе РОЛЕВА» в августе не бегала — она внизу, но в журнале.
    assert names[-1] == "Обе РОЛЕВА"

    # В августе никто не волонтёрил — волонтёры года всё равно на месте.
    august_volunteers = _journal(db_session, scene, "volunteers", month="2026-08")
    assert august_volunteers["total_rows"] == 2


@pytest.mark.parametrize("use_cache", [False, True])
def test_viewer_row_follows_the_slice(
    db_session: Session, scene: dict[str, object], use_cache: bool
) -> None:
    """Строка «(вы)» живёт по тем же правилам, что таблица: ноль в срезе — нет строки.

    С кэшем второй запрос идёт другим путём (строки протоколов только этого
    профиля) — ответ обязан совпасть.
    """
    viewer_id = scene["viewer"].id  # type: ignore[attr-defined]
    for _attempt in range(2 if use_cache else 1):
        runners = _journal(db_session, scene, "runners", viewer_user_id=viewer_id, use_cache=use_cache)
        assert runners["me"] is not None
        assert runners["me"]["year_total"] == 1  # type: ignore[index]

        volunteers = _journal(db_session, scene, "volunteers", viewer_user_id=viewer_id, use_cache=use_cache)
        assert volunteers["me"] is None


def _spy_viewer_collects(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Запоминает, для кого строка «(вы)» собиралась отдельно, — то есть что
    таблица пришла из кэша и сработал второй путь."""
    original = location_page_service._collect_attendance_people
    calls: list[object] = []

    def spy(db: Session, event_ids: list[object], user_id: object = None) -> object:
        if user_id is not None:
            calls.append(user_id)
        return original(db, event_ids, user_id=user_id)  # type: ignore[arg-type]

    monkeypatch.setattr(location_page_service, "_collect_attendance_people", spy)
    return calls


@pytest.mark.parametrize("private", [False, True])
def test_viewer_row_from_cache_matches_the_table(
    db_session: Session, scene: dict[str, object], monkeypatch: pytest.MonkeyPatch, private: bool
) -> None:
    """Ревью 21.10.2026: из кэша строка «(вы)» приходила без имени и хендла.
    Фронт узнаёт себя в таблице по handle, не находил — и ставил сверху
    «Участник (вы)», а сам человек стоял ниже ещё раз, без пометки. Два
    одинаковых запроса подряд (второй — из кэша) дают одну и ту же строку."""
    viewer: User = scene["viewer"]  # type: ignore[assignment]
    viewer.profile_private = private
    db_session.commit()
    db_session.refresh(viewer)
    calls = _spy_viewer_collects(monkeypatch)

    first = _journal(db_session, scene, "all", viewer_user_id=viewer.id, use_cache=True)
    second = _journal(db_session, scene, "all", viewer_user_id=viewer.id, use_cache=True)
    assert calls == [viewer.id]  # второй ответ — таблица из кэша
    assert first["me"] is not None
    assert second["me"] == first["me"]

    me: dict[str, object] = second["me"]  # type: ignore[assignment]
    assert me["name"] == "Зритель ЗРИТЕЛЕВ"
    assert me["private"] is private
    own = [row for row in second["rows"] if row["name"] == me["name"]]  # type: ignore[union-attr]
    assert len(own) == 1
    # Хендл — по тому же правилу, что в таблице: у закрытого профиля его нет.
    assert me["handle"] == own[0]["handle"]
    assert me["handle"] == (None if private else str(viewer.serial_id))
    # Клетки себе видны и при закрытом профиле — в таблице их нет.
    assert len(me["items"]) == 1  # type: ignore[arg-type]


def test_viewer_row_from_cache_finds_link_without_participant_id(
    db_session: Session, scene: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """platform_links.participant_id проставлен не всегда, а таблица склеивает
    по (platform_id, external_user_id) — _platform_link_join. Строка «(вы)»
    из кэша ищет так же, иначе её не было бы вовсе."""
    events: dict[date, Event] = scene["events"]  # type: ignore[assignment]
    participant = _participant(db_session, "Безссылочный БЕЗССЫЛОЧНЫЙ")
    user = User(consent_accepted=True, public_slug=f"bez-{uuid4().hex[:8]}")
    db_session.add(user)
    db_session.flush()
    db_session.add(
        PlatformLink(
            user_id=user.id,
            platform_id=_platform(db_session).id,
            participant_id=None,
            external_user_id=participant.external_user_id,
            external_url="https://example.org",
        )
    )
    _run(db_session, events[SEP_12], participant)
    db_session.commit()
    calls = _spy_viewer_collects(monkeypatch)

    first = _journal(db_session, scene, "runners", viewer_user_id=user.id, use_cache=True)
    second = _journal(db_session, scene, "runners", viewer_user_id=user.id, use_cache=True)
    assert calls == [user.id]
    assert second["me"] == first["me"]
    assert second["me"]["name"] == "Безссылочный БЕЗССЫЛОЧНЫЙ"  # type: ignore[index]
    assert second["me"]["handle"] == user.public_slug  # type: ignore[index]


@pytest.mark.parametrize("kind", ["all", "runners", "volunteers"])
def test_pages_walk_the_filtered_list(db_session: Session, scene: dict[str, object], kind: str) -> None:
    """offset и «Показать ещё» идут по уже отобранному списку: порции по одной
    строке складываются ровно в полный журнал, заглушек и нулей среза в них
    нет, а порция за total_rows пустая."""
    full = _journal(db_session, scene, kind)
    total = int(full["total_rows"])  # type: ignore[arg-type]
    assert total == len(full["rows"])  # type: ignore[arg-type]

    walked: list[str] = []
    for offset in range(total):
        page = _journal(db_session, scene, kind, offset=offset, limit=1)
        assert page["total_rows"] == total
        walked.extend(_names(page))
    assert walked == _names(full)
    assert not any(name.startswith(("НЕИЗВЕСТНЫЙ", "Александр ИМЕНОВ")) for name in walked)
    assert _journal(db_session, scene, kind, offset=total, limit=1)["rows"] == []


@pytest.mark.parametrize("use_cache", [False, True])
def test_linked_stub_follows_the_location_rule(
    db_session: Session, scene: dict[str, object], use_cache: bool
) -> None:
    """Привязка к профилю заглушку человеком не делает — правило то же, что у
    рейтингов и счётчика участников локации (_identified_participant_clause).
    На проде таких привязок нет (21.10.2026); тест держит журнал и страницу
    локации заодно, в том числе для строки «(вы)» из кэша."""
    events: dict[date, Event] = scene["events"]  # type: ignore[assignment]
    stub = _participant(db_session, "Unknown #67")
    user = User(consent_accepted=True)
    db_session.add(user)
    db_session.flush()
    db_session.add(
        PlatformLink(
            user_id=user.id,
            platform_id=_platform(db_session).id,
            participant_id=stub.id,
            external_user_id=stub.external_user_id,
            external_url="https://example.org",
        )
    )
    _run(db_session, events[AUG_29], stub)
    db_session.commit()

    for _attempt in range(2 if use_cache else 1):
        payload = _journal(db_session, scene, "runners", viewer_user_id=user.id, use_cache=use_cache)
        assert "Unknown #67" not in _names(payload)
        assert payload["me"] is None
        # На старте он был — итоговая строка его считает.
        assert payload["date_totals"]["2026-08-29"]["unidentified_runners"] == 1  # type: ignore[index]


def test_attendance_listed_rules() -> None:
    runner = _AttendancePerson(name="Иван ИВАНОВ")
    runner.run_dates.add(SEP_05)
    assert _attendance_listed(runner, "all")
    assert _attendance_listed(runner, "runners")
    assert not _attendance_listed(runner, "volunteers")

    stub = _AttendancePerson(name="НЕИЗВЕСТНЫЙ", identified=False)
    stub.run_dates.add(SEP_05)
    assert not any(_attendance_listed(stub, kind) for kind in ("all", "runners", "volunteers"))
