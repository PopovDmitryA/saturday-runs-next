"""Тематические старты 5 вёрст: одна локация-серия, финиши в личном счёте.

Раздел /starti-soobshchestv/ — разовые старты («Зелёные 5 км», «День
физкультурника. Тула»). Их нет ни в реестре площадок, ни в таблицах локаций,
поэтому весь остальной синк 5 вёрст их не видит. А сайт засчитывает эти финиши
в личный счётчик человека — из-за чего наши числа расходились с источником
ровно на единицу у каждого, кто там бежал (522 человека на 07.09.2026).

Площадкой ни один из них не является, и своей локации у каждого больше нет:
все живут одной серией «Старты сообществ», а собственное имя старта — в
заголовке события.
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.activity_url import prefer_event_source_url, resolve_activity_url
from app.models import Event, Location, Platform, RunResult
from app.platform_adapters.five_verst import bulk_parser
from app.platform_adapters.five_verst.parser import (
    parse_userstats_runs_html,
    parse_userstats_volunteering_html,
)
from app.services.location_catalog_service import LocationCatalogIndex
from app.services.series_locations import exclude_series, is_series, start_title
from app.services.user_location_stats import count_unique_locations_from_rows
from app.sync import upsert

COMMUNITY_HTML = """
<h2>08 августа 2026 года. День физкультурника Тула</h2>
<table>
  <tr><th>##</th><th>Участник</th><th>Возрастной рейтинг</th><th>Время</th></tr>
  <tr>
    <td class="row_position">1</td>
    <td class="row_name"><a href="https://5verst.ru/userstats/790272446">Сергей НАУМОВ</a></td>
    <td class="row_stats">М40-44 (1) 81.8%</td>
    <td class="cell-label_time">00:16:55</td>
  </tr>
  <tr>
    <td class="row_position">2</td>
    <td class="row_name"><a href="https://5verst.ru/userstats/790129467">Алексей ВОЛКОВ</a></td>
    <td class="row_stats">М40-44 (2) 74.53%</td>
    <td class="cell-label_time">00:18:34</td>
  </tr>
</table>
<p>Волонтерство</p>
<table>
  <tr><th>Волонтёр</th><th>Роль</th></tr>
  <tr><td><a href="https://5verst.ru/userstats/790111111">Илья КОНОВ</a></td><td>Организатор</td></tr>
</table>
"""


def test_parse_community_event_reads_name_date_and_tables() -> None:
    """Дату и название берём из заголовка: другого источника у страницы нет."""
    page = bulk_parser.parse_community_event_html(COMMUNITY_HTML, "denfizkulturnikatula")
    assert page is not None
    assert page.name == "День физкультурника Тула"
    assert page.event_date == date(2026, 8, 8)
    assert len(page.run_results) == 2
    assert page.run_results[0].participant_name == "Сергей НАУМОВ"
    assert page.run_results[0].finish_time_display == "00:16:55"
    # Таблица волонтёров стоит под абзацем «Волонтерство», а не под заголовком
    # «Команда организаторов», как на обычной странице протокола.
    assert [v.role for v in page.volunteer_results] == ["Организатор"]
    assert page.volunteer_results[0].external_user_id == "790111111"


def test_parse_community_event_without_heading_is_skipped() -> None:
    """Без разобранной даты заводить событие не к чему."""
    assert bulk_parser.parse_community_event_html("<h2>Просто заголовок</h2>", "x") is None


SECTION_HTML = (
    '<a href="https://5verst.ru/starti-soobshchestv/zelenye5km">Зелёные 5 км</a>'
    '<a href="/starti-soobshchestv/denfizkulturnikatula/">День физкультурника. Тула</a>'
    '<a href="/starti-soobshchestv/">раздел</a>'
    '<a href="/vysota/results/all/">площадка</a>'
)


def test_community_slugs_collected_from_links() -> None:
    assert bulk_parser.parse_community_slugs_html(SECTION_HTML) == [
        "zelenye5km",
        "denfizkulturnikatula",
    ]


def test_community_name_comes_from_the_section_not_the_page_heading() -> None:
    """Имя должно совпадать с тем, что человек видит у себя в профиле.

    В разделе старт подписан «Зелёные 5 км» — ровно как в профиле, — а
    заголовок самой страницы называет его «Сбер Зелёный марафон». Люди сверяют
    свои числа по профилю, поэтому имя берём из раздела.
    """
    entries = bulk_parser.parse_community_entries_html(SECTION_HTML)
    assert entries["zelenye5km"] == "Зелёные 5 км"
    assert entries["denfizkulturnikatula"] == "День физкультурника. Тула"

    page = bulk_parser.parse_community_event_html(
        COMMUNITY_HTML, "denfizkulturnikatula", display_name="День физкультурника. Тула"
    )
    assert page is not None
    assert page.name == "День физкультурника. Тула"
    # Без имени из раздела остаётся заголовок страницы.
    fallback = bulk_parser.parse_community_event_html(COMMUNITY_HTML, "denfizkulturnikatula")
    assert fallback is not None
    assert fallback.name == "День физкультурника Тула"


def _platform(db: Session) -> Platform:
    platform = db.query(Platform).filter(Platform.code == "five_verst").one_or_none()
    if platform is None:
        platform = Platform(code="five_verst", name="5 вёрст")
        db.add(platform)
        db.flush()
    return platform


def _location(db: Session, platform: Platform, *, community: bool) -> Location:
    location = Location(
        platform_id=platform.id,
        external_key=f"comm-{uuid4().hex[:8]}",
        name="Тест",
        is_series=community,
    )
    db.add(location)
    db.flush()
    return location


def test_community_event_is_not_a_visited_location(db_session: Session) -> None:
    """Финиш со старта сообщества не добавляет человеку «уникальную локацию».

    Площадкой такой старт не является: ни расписания, ни координат, ни второго
    старта. В личный счётчик пробежек он идёт (иначе не сойдёмся с 5 вёрст), а
    в туризм — нет.
    """
    platform = _platform(db_session)
    regular = _location(db_session, platform, community=False)
    community = _location(db_session, platform, community=True)
    catalog_index = LocationCatalogIndex(db_session)

    counts = count_unique_locations_from_rows(
        catalog_index,
        [(regular, "five_verst"), (community, "five_verst")],
    )
    assert counts.unique_total == 1


def test_exclude_series_filters_the_query(db_session: Session) -> None:
    platform = _platform(db_session)
    regular = _location(db_session, platform, community=False)
    community = _location(db_session, platform, community=True)

    query = db_session.query(Location).filter(Location.id.in_([regular.id, community.id]))
    kept = {row.id for row in exclude_series(query).all()}
    assert kept == {regular.id}
    assert is_series(community) is True
    assert is_series(regular) is False


def test_community_runs_still_count_in_personal_total(db_session: Session) -> None:
    """Главное, ради чего всё затевалось: финиш попадает в личный счёт."""
    platform = _platform(db_session)
    community = _location(db_session, platform, community=True)
    event = Event(
        platform_id=platform.id,
        location_id=community.id,
        external_event_key=f"comm-event-{uuid4().hex[:8]}",
        event_date=date(2026, 8, 8),
        title="День физкультурника",
    )
    db_session.add(event)
    db_session.flush()
    db_session.add(
        RunResult(
            event_id=event.id,
            external_result_key=f"{event.external_event_key}:1",
            position=1,
            finish_time_sec=1015,
            status="finished",
        )
    )
    db_session.flush()

    total = (
        db_session.query(RunResult)
        .join(Event, RunResult.event_id == Event.id)
        .filter(Event.location_id == community.id)
        .count()
    )
    assert total == 1


PROFILE_RUNS_HTML = """
<table>
  <tr><th>Дата</th><th>Мероприятие</th><th>Время</th></tr>
  <tr>
    <td>30.05.2026</td>
    <td><a href="https://5verst.ru/starti-soobshchestv/zelenye5km">Зелёные 5 км #1</a></td>
    <td>00:18:28</td>
  </tr>
  <tr>
    <td>16.08.2026</td>
    <td><a href="https://5verst.ru/druzhba/results/16.08.2026/">Дружба #228</a></td>
    <td>00:21:03</td>
  </tr>
</table>
"""

PROFILE_VOLUNTEERING_HTML = """
<table>
  <tr><th>Дата</th><th>Мероприятие</th><th>Роль</th></tr>
  <tr>
    <td>08.08.2026</td>
    <td><a href="https://5verst.ru/starti-soobshchestv/denfizkulturnikatula">День физкультурника. Тула</a></td>
    <td>Хронометрист</td>
  </tr>
</table>
"""


def test_profile_run_at_thematic_start_keeps_the_real_slug() -> None:
    """Ссылка раздела должна давать слаг старта, а не имя из подписи.

    Профильный парсер знал только /{слаг}/results/{дата}/, поэтому строку
    «Зелёные 5 км #1» он заводил локацией с ключом «зелёные_5_км» — отдельной
    от той, что собрал синк раздела, с двумя битыми адресами и вторым
    экземпляром того же финиша.
    """
    runs = parse_userstats_runs_html(PROFILE_RUNS_HTML, "790113352", "Аркадий КОЛЕСНИЧЕНКО")
    by_date = {item.event_date: item for item in runs}

    thematic = by_date[date(2026, 5, 30)]
    assert thematic.location_external_key == "zelenye5km"
    assert thematic.is_community_event is True
    # Ключ результата — на слаге старта: ровно такой кладёт синк раздела,
    # иначе тот же финиш приехал бы вторым экземпляром.
    assert thematic.external_result_key.startswith("zelenye5km:2026-05-30:")
    # «#1» в профиле — номер этого тематического старта, а не порядковый в
    # серии: наш журнал считает сквозной номер сам.
    assert thematic.event_number is None

    regular = by_date[date(2026, 8, 16)]
    assert regular.location_external_key == "druzhba"
    assert regular.is_community_event is False
    assert regular.event_number == 228


def test_profile_volunteering_at_thematic_start_keeps_the_real_link() -> None:
    rows = parse_userstats_volunteering_html(PROFILE_VOLUNTEERING_HTML, "790111111", "Илья КОНОВ")
    assert len(rows) == 1
    assert rows[0].location_external_key == "denfizkulturnikatula"
    assert rows[0].is_community_event is True
    assert rows[0].source_url == "https://5verst.ru/starti-soobshchestv/denfizkulturnikatula"


def test_thematic_start_link_is_not_replaced_by_a_404() -> None:
    """Адрес протокола тематического старта — страница раздела, и только она.

    Общее правило 5 вёрст синтезирует /{слаг}/results/{дата}/. У тематического
    старта такой страницы нет: прод отдавал 404 и в «Источнике» на странице
    протокола, и в колонке журнала стартов.
    """
    stored = "https://5verst.ru/starti-soobshchestv/zelenye5km"
    assert (
        resolve_activity_url(
            platform_code="five_verst",
            event_date=date(2026, 5, 30),
            event_number=None,
            event_source_url=stored,
            location_external_key="starti-soobshchestv",
        )
        == stored
    )
    # И профильный импорт не должен затирать его синтетическим адресом.
    assert (
        prefer_event_source_url(
            "five_verst",
            stored,
            "https://5verst.ru/zelenye5km/results/30.05.2026/",
        )
        == stored
    )


def test_series_events_are_matched_by_key_not_by_date(db_session: Session) -> None:
    """Старт серии опознаётся ключом: дата у серии ничего не значит.

    У площадки дата однозначна — там событие ищется по паре «локация + дата».
    В серии в одну субботу может пройти больше одного старта («День
    физкультурника» бывает не только в Туле), и второй не должен молча
    перезаписать первый. Уникальный индекс из миграции 009 второго события в
    тот же день всё равно не даст, поэтому ждём внятную ошибку, а не тихую
    подмену протокола.
    """
    platform = _platform(db_session)
    series = _location(db_session, platform, community=True)
    same_day = date(2026, 5, 30)

    first = upsert.upsert_event_for_profile(
        db_session,
        platform,
        series,
        external_event_key=f"zelenye5km:{same_day.isoformat()}",
        event_date=same_day,
        event_number=None,
        location_name="Зелёные 5 км",
        location_slug="zelenye5km",
        source_url="https://5verst.ru/starti-soobshchestv/zelenye5km",
    )
    assert first.title == "Зелёные 5 км"

    # Тот же старт второй раз — та же строка, а не дубль.
    again = upsert.upsert_event_for_profile(
        db_session,
        platform,
        series,
        external_event_key=f"zelenye5km:{same_day.isoformat()}",
        event_date=same_day,
        event_number=None,
        location_name="Зелёные 5 км",
        location_slug="zelenye5km",
        source_url="https://5verst.ru/starti-soobshchestv/zelenye5km",
    )
    assert again.id == first.id

    with pytest.raises(ValueError, match="уже есть старт"):
        upsert.upsert_event_for_profile(
            db_session,
            platform,
            series,
            external_event_key=f"drugoystart:{same_day.isoformat()}",
            event_date=same_day,
            event_number=None,
            location_name="Другой старт",
            location_slug="drugoystart",
            source_url="https://5verst.ru/starti-soobshchestv/drugoystart",
        )


def test_series_start_title_drops_the_service_heading(db_session: Session) -> None:
    """«С95 и друзья #11» — не имя старта, а имя локации с номером.

    У 5 вёрст у тематического старта имя своё («Зелёные 5 км»), у s95 его нет:
    все выезды идут под одним именем, а заголовок события собирается из имени
    локации и номера. Подписывать таким «именем» строку под самой же локацией —
    шум, поэтому такие заголовки отсеиваем.
    """
    platform = _platform(db_session)
    series = _location(db_session, platform, community=True)
    series.name = "С95 и друзья"
    venue = _location(db_session, platform, community=False)
    venue.name = "Дружба"
    db_session.flush()

    assert start_title(series, "С95 и друзья #11") is None
    assert start_title(series, "С95 и друзья") is None
    assert start_title(series, "Зелёные 5 км") == "Зелёные 5 км"
    assert start_title(series, None) is None
    # У площадки имени старта не бывает вовсе: там заголовок всегда служебный.
    assert start_title(venue, "Дружба #228") is None
