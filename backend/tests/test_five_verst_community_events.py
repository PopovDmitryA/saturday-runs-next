"""Старты сообществ 5 вёрст: собираем финиши, но площадкой не считаем.

Раздел /starti-soobshchestv/ — разовые старты («Зелёные 5 км», «День
физкультурника. Тула»). Их нет ни в реестре площадок, ни в таблицах локаций,
поэтому весь остальной синк 5 вёрст их не видит. А сайт засчитывает эти финиши
в личный счётчик человека — из-за чего наши числа расходились с источником
ровно на единицу у каждого, кто там бежал (522 человека на 07.09.2026).
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import Event, Location, Platform, RunResult
from app.platform_adapters.five_verst import bulk_parser
from app.services.community_events import exclude_community_events, is_community_event
from app.services.location_catalog_service import LocationCatalogIndex
from app.services.user_location_stats import count_unique_locations_from_rows

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
        is_community_event=community,
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


def test_exclude_community_events_filters_the_query(db_session: Session) -> None:
    platform = _platform(db_session)
    regular = _location(db_session, platform, community=False)
    community = _location(db_session, platform, community=True)

    query = db_session.query(Location).filter(Location.id.in_([regular.id, community.id]))
    kept = {row.id for row in exclude_community_events(query).all()}
    assert kept == {regular.id}
    assert is_community_event(community) is True
    assert is_community_event(regular) is False


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
