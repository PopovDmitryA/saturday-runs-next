"""Расписание стартов локации из текста «Где и когда?».

Порт легаси-парсера date_load_protocol.py::extract_schedule на наш контур:
вход — location_descriptions.schedule_text (синк реестра 5 вёрст уже снимает
этот блок со страницы локации), на сайт парсер не ходит. Прогон по всем 212
описаниям прод-БД 23.08.2026: распарсились все — 178 «каждую субботу в 9:00»,
34 сезонных («в 9:00 (с сентября по май), в 8:00 (июнь — август)»).

Результат хранится в location_descriptions.schedule_parsed:
[{"from_month": 9, "to_month": 5, "time": "09:00"}] — месяцы 1..12, диапазон
может «переламываться» через декабрь (сентябрь→май).
"""

from __future__ import annotations

import re
from datetime import date, time

# Все падежи месяцев: «январь/января/январе». Стем в 4 символа надёжен для
# русских названий («мая» задана явно, потому что короче стема).
_MONTH_STEMS: list[tuple[str, int]] = [
    ("янва", 1),
    ("февр", 2),
    ("март", 3),
    ("апре", 4),
    ("мая", 5),
    ("май", 5),
    ("мае", 5),
    ("июн", 6),
    ("июл", 7),
    ("авгу", 8),
    ("сент", 9),
    ("октя", 10),
    ("ноя", 11),
    ("дека", 12),
]

_TIME_BLOCK_RE = re.compile(r"(?:в\s*)?(?P<time>\d{1,2}[:.]\d{2})\s*\((?P<inside>[^)]+)\)")
_TIME_ONLY_RE = re.compile(r"(\d{1,2})[:.](\d{2})")


def _norm_month(token: str) -> int | None:
    word = token.strip(" .,:;!?()\"'").lower().replace("ё", "е")
    for stem, number in _MONTH_STEMS:
        if word.startswith(stem):
            return number
    return None


def _fmt_time(raw: str) -> str | None:
    hh, mm = raw.replace(".", ":").split(":")
    hour, minute = int(hh), int(mm)
    # Старты живут утром; «5 км» или «2026 год» временем не считаем.
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


def parse_schedule_text(schedule_text: str | None) -> list[dict[str, object]]:
    """Текст «Где и когда?» → список сезонных окон со временем старта."""

    if not schedule_text:
        return []
    raw = schedule_text.replace("\xa0", " ")
    raw = re.sub(r"[-–—]", "-", raw)
    raw = re.sub(r"\s+", " ", raw).lower()

    results: list[dict[str, object]] = []
    for match in _TIME_BLOCK_RE.finditer(raw):
        time_str = _fmt_time(match.group("time"))
        if time_str is None:
            continue
        inside = re.sub(r"^в\s+", "", match.group("inside").strip())

        span = re.match(r"с\s+([а-яе]+)\s+по\s+([а-яе]+)", inside) or re.match(
            r"([а-яе]+)\s*-\s*([а-яе]+)", inside
        )
        if span:
            start, finish = _norm_month(span.group(1)), _norm_month(span.group(2))
            if start and finish:
                results.append({"from_month": start, "to_month": finish, "time": time_str})
            continue

        months = [m for token in re.split(r",|\s+и\s+", inside) if (m := _norm_month(token))]
        if months:
            results.append({"from_month": min(months), "to_month": max(months), "time": time_str})

    if not results:
        # Обычный случай (178 из 212 локаций): просто «каждую субботу в 9:00».
        plain = _TIME_ONLY_RE.search(raw)
        if plain:
            time_str = _fmt_time(f"{plain.group(1)}:{plain.group(2)}")
            # Одинокое время верим только утреннему окну — иначе рискуем принять
            # за старт что-то из текста («работает до 22:00»).
            if time_str and 5 <= int(time_str[:2]) <= 12:
                results.append({"from_month": 1, "to_month": 12, "time": time_str})

    return results


def start_time_for_date(schedule: list[dict[str, object]] | None, on_date: date) -> time | None:
    """Время старта на конкретную дату с учётом сезонных окон."""

    if not schedule:
        return None
    month = on_date.month
    for entry in schedule:
        try:
            start = int(entry["from_month"])  # type: ignore[arg-type]
            finish = int(entry["to_month"])  # type: ignore[arg-type]
            hh, mm = str(entry["time"]).split(":")
        except (KeyError, TypeError, ValueError):
            continue
        in_window = start <= month <= finish if start <= finish else (month >= start or month <= finish)
        if in_window:
            return time(int(hh), int(mm))
    return None


def current_start_time_label(schedule: list[dict[str, object]] | None, on_date: date) -> str | None:
    """«09:00» — действующее время старта на дату (для страницы локации)."""

    value = start_time_for_date(schedule, on_date)
    if value is None:
        return None
    return f"{value.hour:02d}:{value.minute:02d}"
