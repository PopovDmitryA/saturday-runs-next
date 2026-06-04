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
    assert results[0].age_category == "М30-34 (1)"
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
