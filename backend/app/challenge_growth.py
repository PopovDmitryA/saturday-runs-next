"""Как растёт счётчик челленджа: дата каждого шага +1.

Общая основа карточки челленджа («Мои достижения») и рейтингов челленджей
(/ratings/p-index и соседи). Карточка считает одного человека, рейтинг — всех
участников протоколов разом, но правило подсчёта у них одно, и живёт оно здесь:
две копии логики рано или поздно разошлись бы, и человек видел бы в рейтинге
не то число, что у себя в кабинете.

Каждая функция возвращает «лестницу роста» — список дат, где k-я дата (с
единицы) — день, когда счётчик впервые достиг k. Из неё читается всё сразу:

* текущее значение — длина списка;
* дата достижения текущего значения — последний элемент;
* дата уровня с порогом N — элемент N-1 (см. achievements_service._level_dates);
* значение на любой день — сколько дат лестницы не позже него.

Индексы Хирша (p и V), цепочка Уилсона, серия суббот и лучший год растут не
быстрее чем на единицу за событие либо, как Уилсон, сразу на несколько —
тогда дата в лестнице повторяется.

Входные строки ДОЛЖНЫ идти по возрастанию даты: функции проигрывают историю.
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable
from datetime import date, timedelta

from app.saturday_week import week_saturday

# Финиш дольше часа в «60 секундах» не участвует: челленджи совпадений считают
# по MM:SS, а у времени за час на табло другой формат.
_SECONDS_LIMIT = 3600


def first_dates(keyed_dates: Iterable[tuple[Hashable | None, date]]) -> list[date]:
    """Лестница коллекции: дата первого закрытия каждой клетки, по возрастанию.

    Ключ None — строка в коллекцию не идёт (нет времени, места, номера…).
    """
    first: dict[Hashable, date] = {}
    for key, value in keyed_dates:
        if key is not None and key not in first:
            first[key] = value
    return sorted(first.values())


def seconds_key(finish_time_sec: int | None) -> int | None:
    """«60 секунд»: секунда финишного времени (до часа)."""
    if finish_time_sec is None or finish_time_sec <= 0 or finish_time_sec >= _SECONDS_LIMIT:
        return None
    return finish_time_sec % 60


def position_key(position: int | None) -> int | None:
    """«Бинго позиций»: две последние цифры места."""
    if position is None or position <= 0:
        return None
    return position % 100


def calendar_day_key(event_date: date) -> tuple[int, int]:
    """«Круглый год»: день календаря без года."""
    return event_date.month, event_date.day


def start_number_key(event_number: int | None, low: int, high: int) -> int | None:
    """«Нумератор»: номер старта в диапазоне, в любой системе."""
    if event_number is None or not low <= event_number <= high:
        return None
    return event_number


def hirsch_growth(visits: Iterable[tuple[date, Hashable]]) -> list[date]:
    """Индекс Хирша по локациям: X локаций, на каждой минимум X визитов.

    p-индекс — по финишам, V-индекс — по волонтёрствам. Один визит добавляет
    единицу одной локации и поднимает индекс максимум на единицу, поэтому
    лестница получается проходом, без пересортировки счётчиков на каждом шаге:
    at_least[k] — сколько локаций набрали k визитов и больше.
    """
    counts: dict[Hashable, int] = {}
    at_least: list[int] = [0]
    index = 0
    growth: list[date] = []
    for event_date, location in visits:
        count = counts.get(location, 0) + 1
        counts[location] = count
        if count == len(at_least):
            at_least.append(0)
        at_least[count] += 1
        if index + 1 < len(at_least) and at_least[index + 1] >= index + 1:
            index += 1
            growth.append(event_date)
    return growth


def wilson_growth(numbers: Iterable[tuple[date, int | None]]) -> list[date]:
    """Классический индекс Уилсона: цепочка номеров стартов строго с №1.

    Старт, закрывший дыру, удлиняет цепочку сразу на несколько номеров —
    тогда его дата стоит в лестнице столько же раз.
    """
    seen: set[int] = set()
    chain = 0
    growth: list[date] = []
    for event_date, number in numbers:
        if number is None or number <= 0:
            continue
        seen.add(number)
        while chain + 1 in seen:
            chain += 1
            growth.append(event_date)
    return growth


def wilson_floating_growth(numbers: Iterable[tuple[date, int | None]]) -> tuple[list[date], int | None]:
    """Плавающий индекс Уилсона: самая длинная цепочка номеров подряд с любого.

    Возвращает лестницу роста и первый номер цепочки, давшей текущую длину
    (при равной длине — той, что набрала её раньше: по ней и дата). Отрезки
    склеиваются по концам: номер между двумя цепочками сливает их в одну, и
    лестница прирастает сразу на несколько ступеней.
    """
    start_of: dict[int, int] = {}  # правый конец отрезка → его начало
    end_of: dict[int, int] = {}  # левый конец отрезка → его конец
    seen: set[int] = set()
    growth: list[date] = []
    best_start: int | None = None
    for event_date, number in numbers:
        if number is None or number <= 0 or number in seen:
            continue
        seen.add(number)
        left = start_of.pop(number - 1, number)
        right = end_of.pop(number + 1, number)
        end_of[left] = right
        start_of[right] = left
        length = right - left + 1
        if length > len(growth):
            growth.extend([event_date] * (length - len(growth)))
            best_start = left
    return growth, best_start


def streak_growth(activity_dates: Iterable[date]) -> list[date]:
    """Лучшая серия недель подряд — пробежкой или волонтёрством.

    Недели — как в календаре суббот (app.saturday_week.week_saturday): два
    старта одной недели серию не удлиняют. Шаг лестницы датируется первым
    стартом недели, а не её субботой: у будничного старта (1 января в среду)
    суббота недели ещё впереди, и «дата достижения» оказалась бы в будущем.
    """
    first_in_week: dict[date, date] = {}
    for value in activity_dates:
        week = week_saturday(value)
        known = first_in_week.get(week)
        if known is None or value < known:
            first_in_week[week] = value
    growth: list[date] = []
    current = 0
    previous: date | None = None
    for week in sorted(first_in_week):
        current = current + 1 if previous is not None and week - previous == timedelta(days=7) else 1
        previous = week
        if current > len(growth):
            growth.append(first_in_week[week])
    return growth


def current_streak_growth(activity_dates: Iterable[date], as_of: date) -> list[date]:
    """Серия, которая идёт сейчас: недели подряд до недели as_of или предыдущей.

    Предыдущая неделя тоже считается живой — иначе серия «сгорала» бы каждый
    понедельник до ближайшей субботы. Шаги датируются так же, как у лучшей
    серии (первым стартом недели). Серия оборвалась — пустой список.
    """
    first_in_week: dict[date, date] = {}
    for value in activity_dates:
        week = week_saturday(value)
        known = first_in_week.get(week)
        if known is None or value < known:
            first_in_week[week] = value
    if not first_in_week:
        return []
    week = max(first_in_week)
    current_week = week_saturday(as_of)
    if week != current_week and week != current_week - timedelta(days=7):
        return []
    run: list[date] = []
    while week in first_in_week:
        run.append(first_in_week[week])
        week -= timedelta(days=7)
    run.reverse()
    return run


def best_year_growth(run_dates: Iterable[date]) -> list[date]:
    """Лучший календарный год по числу пробежек.

    Рекорд растёт, когда счётчик ТЕКУЩЕГО года обгоняет все прежние годы, — по
    одной пробежке за шаг.
    """
    by_year: dict[int, int] = {}
    growth: list[date] = []
    for event_date in run_dates:
        count = by_year.get(event_date.year, 0) + 1
        by_year[event_date.year] = count
        if count > len(growth):
            growth.append(event_date)
    return growth


def hirsch_shortfall(counts: Iterable[int], target: int) -> int:
    """Сколько визитов не хватает до индекса Хирша target — дешевейшим путём.

    Добираем target самых обжитых локаций до target визитов каждую; локаций
    меньше target — недостающие стоят полные target визитов. Общая для
    подписи «ещё N» на карточке p- и V-индекса и колонки «До следующего» в их
    рейтингах.
    """
    top = sorted(counts, reverse=True)[:target]
    top += [0] * (target - len(top))
    return sum(max(0, target - count) for count in top)
