"""Переключатель «имена построчно» в постах кабинета организатора.

Организаторы попросили писать каждого человека с новой строки (через
Дмитрия 21.09.2026); Дмитрий решил сделать это переключателем на все
именные блоки. Тут — «Привет новичкам», «Юбилеи дня» и сводный пост;
«Герои старта» — в test_organizer_post_guests.py.
"""

from __future__ import annotations

from datetime import date

from app.services.admin_event_report_service import _build_post_text
from app.services.organizer_post_service import _milestones_post, _newcomers_post


def _svod() -> dict[str, object]:
    return {
        "event": {
            "location_name": "Мещерский",
            "event_number": 101,
            "event_date": date(2026, 9, 19),
            "finishers_count": 90,
            "volunteers_count": 10,
            "platform_name": "5 вёрст",
        },
        "runners": [
            {
                "name": "Иван ИВАНОВ",
                "first_in_system": True,
                "first_at_location": True,
                "location_milestone": None,
                "platform_milestone": None,
            },
            {
                "name": "Пётр ПЕТРОВ",
                "first_in_system": False,
                "first_at_location": True,
                "location_milestone": 50,
                "platform_milestone": None,
            },
            {
                "name": "Сидор СИДОРОВ",
                "first_in_system": False,
                "first_at_location": False,
                "location_milestone": 25,
                "platform_milestone": 100,
            },
        ],
        "volunteers": [
            {
                "name": "Волонтёр НОВЫЙ",
                "first_volunteering": True,
                "location_milestone": None,
                "platform_milestone": None,
            },
        ],
    }


def test_newcomers_post_lines_is_the_historical_format() -> None:
    lines = _newcomers_post(_svod(), names_layout="lines").splitlines()
    assert lines[lines.index("🏃 Первый финиш:") + 1] == "• Иван ИВАНОВ"
    assert lines[lines.index("🙋 Первое волонтёрство:") + 1] == "• Волонтёр НОВЫЙ"
    assert lines[lines.index("🧳 Впервые на нашей локации:") + 1] == "• Пётр ПЕТРОВ"


def test_newcomers_post_inline() -> None:
    text = _newcomers_post(_svod(), names_layout="inline")
    assert "🏃 Первый финиш: Иван ИВАНОВ." in text
    assert "🧳 Впервые на нашей локации: Пётр ПЕТРОВ." in text
    assert "• " not in text


def test_milestones_post_layouts() -> None:
    inline = _milestones_post(_svod(), names_layout="inline").splitlines()
    start = inline.index("🏃 Юбилейные финиши на нашей локации:")
    # В строку — по уровням, как раньше.
    assert inline[start + 1 : start + 3] == ["🔹 25-й: Сидор СИДОРОВ", "🔹 50-й: Пётр ПЕТРОВ"]
    assert "🔹 100-й: Сидор СИДОРОВ" in inline

    lines = _milestones_post(_svod(), names_layout="lines").splitlines()
    start = lines.index("🏃 Юбилейные финиши на нашей локации:")
    # Построчно — человек и его уровень, без строк-уровней.
    assert lines[start + 1 : start + 3] == ["• Сидор СИДОРОВ — 25-й", "• Пётр ПЕТРОВ — 50-й"]
    assert "• Сидор СИДОРОВ — 100-й" in lines
    assert not any(line.startswith("🔹") for line in lines)


def _report() -> dict[str, object]:
    return {
        "event": {"event_date": date(2026, 9, 19), "event_number": 101, "platform_name": "5 вёрст"},
        "header": {
            "finishers": 90,
            "previous_event_finishers": None,
            "volunteers": 10,
            "attendance_record": False,
            "prior_max_finishers": None,
        },
        "course_records": [],
        "top_finishes": [],
        "stats": {
            "newcomers": [],
            "guests": [],
            "personal_bests": [],
            "location_bests": [],
            "comebacks": [],
            "first_volunteers": [],
            "new_role_volunteers": [],
        },
        "location_milestones": {
            "runs": [{"name": "Иван ИВАНОВ", "count": 100}, {"name": "Пётр ПЕТРОВ", "count": 100}],
            "volunteering": [{"name": "Волонтёр СТАРЫЙ", "count": 10}],
        },
        "one_step": {"runs": [{"name": "Сидор СИДОРОВ", "next_milestone": 50}], "volunteering": []},
        "clubs": {"runs": [{"name": "Клубный КЛУБНЫЙ", "count": 50}], "volunteering": []},
        "global_run_jubilees": [{"name": "Юбиляр ЮБИЛЯРОВ", "count": 75}],
    }


def test_full_post_inline_is_the_old_wording() -> None:
    text = _build_post_text(_report(), "Мещерский", names_layout="inline")
    assert "100 пробежек в локации: Иван ИВАНОВ, Пётр ПЕТРОВ" in text
    assert "10 волонтёрств в локации: Волонтёр СТАРЫЙ" in text
    assert "1 пробежка до 50 в локации: Сидор СИДОРОВ" in text
    assert "🌟Клуб 50 пробежек в системе 5 вёрст: Клубный КЛУБНЫЙ" in text
    assert "🎖️**Юбилейные пробежки:**\nЮбиляр ЮБИЛЯРОВ (75 пробежек)" in text
    assert "• " not in text


def test_full_post_lines_puts_every_name_on_its_own_line() -> None:
    lines = _build_post_text(_report(), "Мещерский", names_layout="lines").splitlines()
    start = lines.index("100 пробежек в локации:")
    assert lines[start + 1 : start + 3] == ["• Иван ИВАНОВ", "• Пётр ПЕТРОВ"]
    assert lines[lines.index("1 пробежка до 50 в локации:") + 1] == "• Сидор СИДОРОВ"
    assert lines[lines.index("🌟Клуб 50 пробежек в системе 5 вёрст:") + 1] == "• Клубный КЛУБНЫЙ"
    assert lines[lines.index("🎖️**Юбилейные пробежки:**") + 1] == "• Юбиляр ЮБИЛЯРОВ (75 пробежек)"


def test_default_layout_keeps_old_text() -> None:
    """Без параметра — прежний вид: сводный пост в строку, «Привет новичкам» построчно."""
    assert _build_post_text(_report(), "Мещерский") == _build_post_text(_report(), "Мещерский", names_layout="inline")
    assert _newcomers_post(_svod()) == _newcomers_post(_svod(), names_layout="lines")
