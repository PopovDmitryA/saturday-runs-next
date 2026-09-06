from __future__ import annotations

from datetime import date

from app.platform_adapters.five_verst import bulk_parser

LOCATION_HTML = """
<html><head><title>5 вёрст | Бабушкинский на Яузе | Москва | Главная</title></head><body></body></html>
"""

COURSE_HTML = """
<html><body>
<a href="https://yandex.ru/maps/?pt=37.659999,55.875747&z=15&l=map">Старт</a>
<a href="https://yandex.ru/maps/?pt=37.662572,55.875664&z=15&l=map">Финиш</a>
</body></html>
"""

SUMMARIES_HTML = """
<table>
<tr><th>##</th><th>Дата</th><th>Финишёров</th><th>Волонтёров</th><th>Среднее время</th><th>Лучшее "Ж"</th><th>Лучшее "М"</th></tr>
<tr><td>206</td><td>23.05.2026</td><td>48</td><td>22</td><td>00:28:48</td><td>00:23:32</td><td>00:18:59</td></tr>
<tr><td>205</td><td>16.05.2026</td><td>52</td><td>18</td><td>00:29:22</td><td>00:23:17</td><td>00:19:30</td></tr>
</table>
"""

PROTOCOL_HTML = """
<table>
<tr><th>##</th><th>Участник</th><th>Возрастной рейтинг</th><th>Время</th></tr>
<tr>
  <td class="table_gray__row_position">1</td>
  <td class="table_gray__row_name">
    <div class="userRegistered cell-label cell-label_name">
      <a href="https://5verst.ru/userstats/790087870">Ali PAPAKHOV</a>
    </div>
    <div class="userRegistered">
      <a href="https://5verst.ru/clubs/yauzarun">#яuzarun</a>
    </div>
  </td>
  <td class="table_gray__row_stats"><div class="table_gray__cell cell-label text-left">М30-34 (1)</div></td>
  <td class="table_gray__row">
    <div class="cell-label cell-label_time">
      <div class="table-achievments">
        <span title="Личный рекорд!"><img alt="Личный рекорд!" src="/kandinsky-child/assets/icons/beaker.svg"></span>
      </div>
      <div>00:18:59</div>
    </div>
  </td>
</tr>
<tr>
  <td class="table_gray__row_position">2</td>
  <td class="table_gray__row_name">
    <a href="https://5verst.ru/userstats/790274599">Михаил ИЛЬИН</a>
  </td>
  <td class="table_gray__row_stats">М35-39 (1)</td>
  <td class="table_gray__row">
    <div class="cell-label cell-label_time">
      <div class="table-achievments">
        <span title="Первый финиш на 5 вёрст"><img alt="Первый финиш на 5 вёрст" src="/icons/crown.svg"></span>
        <span title="Первый финиш на Академический"><img alt="Первый финиш на Академический" src="/icons/thumbs_up.svg"></span>
      </div>
      <div>00:19:30</div>
    </div>
  </td>
</tr>
</table>
"""


def test_parse_location_with_course_coordinates() -> None:
    location = bulk_parser.parse_location_home_html(
        LOCATION_HTML,
        "babushkinskynayauze",
        "https://5verst.ru/babushkinskynayauze/",
        course_html=COURSE_HTML,
        course_source_url="https://5verst.ru/babushkinskynayauze/course/",
    )
    assert location.external_key == "babushkinskynayauze"
    assert location.name == "Бабушкинский на Яузе"
    assert location.city == "Москва"
    assert location.latitude == 55.875747
    assert location.longitude == 37.659999
    assert location.course_source_url.endswith("/course/")


def test_parse_event_summaries_html() -> None:
    summaries = bulk_parser.parse_event_summaries_html(
        SUMMARIES_HTML,
        "babushkinskynayauze",
        "Бабушкинский на Яузе",
        limit=1,
    )
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.event_number == 206
    assert summary.event_date == date(2026, 5, 23)
    assert summary.finishers_count == 48
    assert summary.volunteers_count == 22
    assert summary.avg_time_display == "00:28:48"
    assert summary.best_female_time_display == "00:23:32"
    assert summary.best_male_time_display == "00:18:59"
    assert summary.best_male_time_sec == 18 * 60 + 59
    assert len(summary.summary_hash) == 64


def test_compute_summary_hash_changes_when_field_changes() -> None:
    base = bulk_parser.compute_summary_hash(
        event_number=206,
        event_date=date(2026, 5, 23),
        finishers_count=48,
        volunteers_count=22,
        avg_time_sec=1728,
        best_female_time_sec=1412,
        best_male_time_sec=1139,
    )
    changed = bulk_parser.compute_summary_hash(
        event_number=206,
        event_date=date(2026, 5, 23),
        finishers_count=49,
        volunteers_count=22,
        avg_time_sec=1728,
        best_female_time_sec=1412,
        best_male_time_sec=1139,
    )
    assert base != changed


def test_parse_volunteers_from_event_html() -> None:
    html = """
    <h2 class="results-title">Команда организаторов</h2>
    <table>
      <tr><th>Волонтёр</th><th>Роль</th></tr>
      <tr>
        <td><a href="https://5verst.ru/userstats/790115702">Елена МУХАНОВА</a></td>
        <td>Организатор</td>
      </tr>
      <tr>
        <td><a href="https://5verst.ru/userstats/790277595">Ольга ГРАЧЁВА</a></td>
        <td>Секундомер</td>
      </tr>
      <tr>
        <td><a href="https://5verst.ru/userstats/790115702">Елена МУХАНОВА</a></td>
        <td>Обработка результатов</td>
      </tr>
    </table>
    """
    volunteers = bulk_parser.parse_volunteers_from_event_html(
        html,
        slug="babushkinskynayauze",
        event_date=date(2026, 5, 23),
    )
    assert len(volunteers) == 3
    assert volunteers[0].external_user_id == "790115702"
    assert volunteers[0].participant_name == "Елена МУХАНОВА"
    assert volunteers[0].role == "Организатор"
    assert volunteers[0].event_date == date(2026, 5, 23)
    assert volunteers[0].location_external_key == "babushkinskynayauze"


def test_parse_volunteers_keeps_unregistered_rows() -> None:
    """Волонтёр без профиля («НЕИЗВЕСТНЫЙ») — тоже волонтёр.

    Такие строки выбрасывались, и роль исчезала из протокола целиком: у
    Видного 15.08.2026 в списке не было организатора. Заодно ровно на число
    таких строк расходилось наше число волонтёров со сводкой сайта — она их
    считает (проверено на живых протоколах 06.09.2026).
    """
    html = """
    <h2 class="results-title">Команда организаторов</h2>
    <table>
      <tr><th>Волонтёр</th><th>Роль</th></tr>
      <tr>
        <td><div class="notRegistered"><a href="https://5verst.ru/register/?utm_source=5verst">НЕИЗВЕСТНЫЙ</a></div>
            <div>(Нужна регистрация)</div></td>
        <td>Организатор</td>
      </tr>
      <tr>
        <td><a href="https://5verst.ru/userstats/790277595">Ольга ГРАЧЁВА</a></td>
        <td>Секундомер</td>
      </tr>
      <tr>
        <td><div class="notRegistered"><a href="https://5verst.ru/register/">Ольга ДЕРИЗЕМЛЯ</a></div>
            <div>(Нужна регистрация)</div></td>
        <td>Маршал</td>
      </tr>
    </table>
    """
    volunteers = bulk_parser.parse_volunteers_from_event_html(
        html,
        slug="vysota",
        event_date=date(2026, 8, 15),
    )
    assert len(volunteers) == 3
    unregistered = [item for item in volunteers if item.external_user_id is None]
    assert [item.role for item in unregistered] == ["Организатор", "Маршал"]
    # Имя в такой строке бывает («Ольга ДЕРИЗЕМЛЯ (Нужна регистрация)»,
    # Стрежевой 23.07.2022) — тогда сохраняем его; «НЕИЗВЕСТНЫЙ» именем не
    # считаем, у человека его для нас действительно нет.
    assert unregistered[0].participant_name is None
    assert unregistered[1].participant_name == "Ольга ДЕРИЗЕМЛЯ"
    # Ключи различаются, иначе вторая строка затёрла бы первую.
    assert len({item.external_result_key for item in volunteers}) == 3
    assert unregistered[0].external_result_key == "vysota:2026-08-15:vol:unregistered:n1:организатор"
    assert (
        unregistered[1].external_result_key
        == "vysota:2026-08-15:vol:unregistered:ольга_дериземля:маршал"
    )


def test_parse_volunteers_ignores_rows_without_id_or_marker() -> None:
    """Признак «не зарегистрирован» обязателен: иначе сюда попадёт любая
    строка со сломанной разметкой и превратится в фантомного волонтёра."""
    html = """
    <h2 class="results-title">Команда организаторов</h2>
    <table>
      <tr><th>Волонтёр</th><th>Роль</th></tr>
      <tr><td><span>Просто текст без ссылки</span></td><td>Маршал</td></tr>
    </table>
    """
    assert (
        bulk_parser.parse_volunteers_from_event_html(
            html, slug="vysota", event_date=date(2026, 8, 15)
        )
        == []
    )


def test_parse_run_protocol_html() -> None:
    results = bulk_parser.parse_run_protocol_html(
        PROTOCOL_HTML,
        slug="babushkinskynayauze",
        event_date=date(2026, 5, 23),
        event_number=206,
    )
    assert len(results) == 2
    assert results[0].external_user_id == "790087870"
    assert results[0].participant_name == "Ali PAPAKHOV"
    assert results[0].event_date == date(2026, 5, 23)
    assert results[0].location_external_key == "babushkinskynayauze"
    assert results[0].event_number == 206
    assert results[0].position == 1
    assert results[0].finish_time_sec == 18 * 60 + 59
    # «(1)» в протоколе — место в возрастной группе, в категорию не попадает.
    assert results[0].age_category == "М30-34"
    assert results[1].age_category == "М35-39"
    assert results[0].club_name == "#яuzarun"
    assert results[0].is_pr is True
    assert results[0].is_first_run is False
    assert results[0].is_first_run_at_location is False
    assert results[0].achievement_labels == ["Личный рекорд!"]
    assert results[0].status == "finished"
    assert results[1].is_pr is False
    assert results[1].is_first_run is True
    assert results[1].is_first_run_at_location is True
    assert results[1].achievement_labels == [
        "Первый финиш на 5 вёрст",
        "Первый финиш на Академический",
    ]


def test_parse_event_summary_marks_test_event() -> None:
    html = """
    <table>
    <tr><th>##</th><th>Дата</th><th>Финишёров</th></tr>
    <tr>
      <td><img alt="Тестовое мероприятие" src="/assets/icons/test.svg"></td>
      <td><a href="https://5verst.ru/testpark/results/04.06.2022/">04.06.2022</a></td>
      <td>28</td><td>11</td><td>00:27:06</td><td>00:21:42</td><td>00:18:37</td>
    </tr>
    <tr><td>1</td><td>11.06.2022</td><td>57</td></tr>
    </table>
    """
    summaries = bulk_parser.parse_event_summaries_html(html, "testpark", "Test Park")
    assert len(summaries) == 2
    assert summaries[0].event_number == 0
    assert summaries[0].is_test_event is True
    assert summaries[0].event_date == date(2022, 6, 4)
    assert summaries[1].is_test_event is False


def test_parse_unknown_participant_in_protocol() -> None:
    html = """
    <table>
    <tr><td>39</td>
      <td><div class="unknown cell-label cell-label_name">НЕИЗВЕСТНЫЙ </div></td>
      <td></td><td></td>
    </tr>
    </table>
    """
    results = bulk_parser.parse_run_protocol_html(
        html,
        slug="babushkinskynayauze",
        event_date=date(2026, 5, 16),
        event_number=205,
    )
    assert len(results) == 1
    assert results[0].position == 39
    assert results[0].participant_name == "НЕИЗВЕСТНЫЙ"
    assert results[0].status == "unknown"
    assert results[0].external_user_id.startswith("unknown:")
    assert results[0].finish_time_sec is None


def test_parse_unregistered_participant_is_kept() -> None:
    """«(Нужна регистрация)» — тоже финишёр, 5 вёрст считает его в численности.

    У него есть имя и позиция, но нет ссылки на профиль, времени и категории.
    Раньше такая строка выбрасывалась (сохранялись только «НЕИЗВЕСТНЫЙ»), и
    протокол приезжал короче: у Дружбы 29.11.2025 не хватало позиций 67 и 118,
    из-за чего рекорд посещаемости показывался как 193 вместо 195.
    """
    html = """
    <table>
    <tr><td>67</td>
      <td><div class="cell-label cell-label_name">Михаил МИХАЙЛОВ (Нужна регистрация)</div></td>
      <td></td><td>&nbsp;</td>
    </tr>
    </table>
    """
    results = bulk_parser.parse_run_protocol_html(
        html,
        slug="druzhba",
        event_date=date(2025, 11, 29),
        event_number=189,
    )
    assert len(results) == 1
    assert results[0].position == 67
    assert results[0].status == "unknown"
    assert results[0].external_user_id == "unknown:druzhba:2025-11-29:67"
    assert results[0].finish_time_sec is None


def test_parse_protocol_keeps_every_numbered_row() -> None:
    """В протоколе не должно быть дыр по позициям: каждая строка — финишёр."""
    html = """
    <table>
    <tr><td>1</td>
      <td><a href="/userstats/?id=100">Быстрый Бегун</a></td>
      <td>М30-34 (1)</td><td>00:17:00</td>
    </tr>
    <tr><td>2</td>
      <td><div class="cell-label cell-label_name">Без Профиля (Нужна регистрация)</div></td>
      <td></td><td>&nbsp;</td>
    </tr>
    <tr><td>3</td>
      <td><div class="unknown cell-label cell-label_name">НЕИЗВЕСТНЫЙ</div></td>
      <td></td><td></td>
    </tr>
    </table>
    """
    results = bulk_parser.parse_run_protocol_html(
        html, slug="druzhba", event_date=date(2025, 11, 29), event_number=189
    )
    assert [row.position for row in results] == [1, 2, 3]


def test_age_category_regex_keeps_three_digit_bands() -> None:
    """«М110-114» не должна обрезаться до «М11».

    У участника с незаполненной датой рождения 5 вёрст считает абсурдный
    возраст и печатает трёхзначную границу. Старая регулярка брала «М» и ровно
    две цифры, поэтому в базу уезжала несуществующая категория «М11» — 114
    таких строк накопилось на проде (Анатолий БАЕВ, Елена КРУЧИНИНА и др.).
    Живая ячейка: «М110-114 (1) 170.89% age grade 1-й в возрастной группе».
    """
    from app.platform_adapters.five_verst.bulk_parser import AGE_CATEGORY_RE

    def parse(text: str) -> str | None:
        match = AGE_CATEGORY_RE.search(text)
        return match.group(1) if match else None

    assert parse("М110-114") == "М110-114"
    assert parse("Ж110-114") == "Ж110-114"
    assert parse("М120-124") == "М120-124"
    # Тот же сорт данных одним числом — встречается чаще диапазона.
    assert parse("М120") == "М120"
    # Обычные группы и хвост с местом в группе разбираются как раньше.
    assert parse("М40-44") == "М40-44"
    assert parse("М40-44 (2)") == "М40-44"
    assert parse("М10") == "М10"
    assert parse("Ж65-69  (12)") == "Ж65-69"
