"""Разбор описаний площадок: 5 вёрст (`/course/`) и S95 (`/events/{slug}`).

Фикстуры — обрезанные настоящие страницы (Сокольники и ЗИЛ, август 2026):
у 5 вёрст оставлен блок `.entry-content`, у S95 — карточки «Общая информация»
и «Наши контакты». Именно на эту вёрстку опираются парсеры.
"""

from __future__ import annotations

from pathlib import Path

from app.platform_adapters.five_verst.location_description import (
    parse_course_description,
    parse_schedule_text,
)
from app.s95.parsers.location import parse_location_description

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_five_verst_course_description() -> None:
    description = parse_course_description(
        _read("five_verst_course.html"), "https://5verst.ru/sokolniki/course/"
    )

    assert not description.is_empty()
    assert description.course_text is not None
    assert description.course_text.startswith("Маршрут проходит по грунтовым дорожкам парка")
    assert "Участники собираются у скамеек спортивного городка" in description.course_text

    assert description.travel_text == (
        "Мероприятие проводится в парке Сокольники в Москве. Москва, Сокольнический Вал, 1с1"
    )
    titles = [section.title for section in description.travel_sections]
    assert titles == ["Общественным транспортом", "Пешком", "На автомобиле"]
    assert "Ближайшая станция метро" in description.travel_sections[0].text
    assert description.source_url == "https://5verst.ru/sokolniki/course/"


def test_five_verst_schedule_from_home_page() -> None:
    """«Где и когда?» с главной: адрес и время старта, без чужой навигации."""
    text = parse_schedule_text(_read("five_verst_location_home.html"))

    assert text == "Старт проходит по адресу: Москва, Сокольнический Вал, 1с1. Каждую субботу с 9:00."
    # «Читайте подробнее на странице Трасса» — ссылка на чужой сайт, не текст.
    assert "Читайте подробнее" not in text
    # Координаты точки сбора живут на карте, а не в предложении.
    assert "55.791959" not in text
    assert not text.endswith("Точка сбора участников:")


def test_five_verst_schedule_absent_on_paused_location() -> None:
    """У площадок «на паузе» блока нет — это не ошибка разбора."""
    assert parse_schedule_text("<html><body><p>Мы пока на паузе</p></body></html>") is None


def test_five_verst_description_merges_home_and_course() -> None:
    description = parse_course_description(
        _read("five_verst_course.html"),
        "https://5verst.ru/sokolniki/course/",
        home_html=_read("five_verst_location_home.html"),
    )
    assert description.schedule_text is not None
    assert "Каждую субботу с 9:00" in description.schedule_text
    assert description.course_text is not None


def test_five_verst_course_drops_boilerplate_tail() -> None:
    """Общий для всех площадок хвост в описание не попадает.

    «Правила безопасности на трассе» и «Внимание!» одинаковы на всех 200+
    страницах: в базе это мусор, на сайте — дубль текста между локациями.
    """

    description = parse_course_description(
        _read("five_verst_course.html"), "https://5verst.ru/sokolniki/course/"
    )
    joined = " ".join(
        [description.course_text or "", description.travel_text or ""]
        + [section.text for section in description.travel_sections]
    )
    assert "Правила безопасности" not in joined
    assert "уступайте им дорогу" not in joined


def test_five_verst_course_strips_coordinate_chips() -> None:
    """Подписи кнопок-точек («55.79:37.66») — не текст описания."""

    description = parse_course_description(
        _read("five_verst_course.html"), "https://5verst.ru/sokolniki/course/"
    )
    assert "55.791959:37.664957" not in (description.course_text or "")


def test_five_verst_course_without_content_block() -> None:
    description = parse_course_description("<html><body><p>ничего</p></body></html>", "https://5verst.ru/x/course/")
    assert description.is_empty()


def test_s95_location_description() -> None:
    description = parse_location_description("".join(_read("s95_event_page.html")), "https://s95.ru/events/zil")

    assert not description.is_empty()
    assert description.course_text is not None
    # «Длина трассы — 5 км. Замер произведён с помощью курвиметра.» — типовой
    # зачин, одинаковый на всех площадках; в базу и на страницу не идёт.
    assert description.course_text.startswith("Маршрут старта пролегает по дорожкам набережной Марка Шагала")
    assert "Длина трассы" not in description.course_text
    assert "Замер произведён" not in description.course_text
    assert "набережной Марка Шагала" in description.course_text
    # Строка «Забеги проходят под названием "S95 ЗИЛ"» — брендинг, не описание.
    assert "Забеги проходят под названием" not in description.course_text
    # Абзац про гранты мэра одинаков на всех московских площадках.
    assert "Грантов Мэра Москвы" not in description.course_text

    assert description.travel_text == (
        "Забег проходит вдоль набережной Марка Шагала. Старт и сбор рядом с амфитеатром."
    )
    link_titles = [link.title for link in description.links]
    assert link_titles == ["Карта и схема проезда", "Парковка"]
    # Соцсети и чаты в блок «как добраться» не идут.
    assert all("t.me" not in link.url and "vk.com" not in link.url for link in description.links)


def test_s95_track_length_boilerplate_stripped_with_parenthetical() -> None:
    """Полный вариант зачина — с пояснением про курвиметр в скобках — тоже срезается.

    Это самый частый вариант на живых страницах (Гольяново, Хабаровск,
    Орехово-Зуево, Ангарские пруды): в скобках уточняется, что курвиметр —
    «специальный прибор для измерения расстояний на неровной местности».
    """
    html = (
        '<html><body><div class="card"><div class="card-header">'
        '<section class="card-title">Общая информация</section></div>'
        '<div class="card-body"><div class="card-text">'
        "<p><strong>Трасса</strong></p>"
        "<p>Длина трассы — 5000 м (5 км). Замер произведён с помощью курвиметра "
        "(специального прибора для измерения расстояний на неровной местности). "
        "Маршрут старта пролегает по дорожкам парка вокруг пруда.</p>"
        "</div></div></div></body></html>"
    )
    description = parse_location_description(html, "https://s95.ru/events/golyanovo")
    assert description.course_text == "Маршрут старта пролегает по дорожкам парка вокруг пруда."


def test_s95_track_length_boilerplate_stripped_other_measurement_device() -> None:
    """Метод замера бывает разный (курвиметр, измерительное колесо) — регулярка не завязана на слово."""
    html = (
        '<html><body><div class="card"><div class="card-header">'
        '<section class="card-title">Общая информация</section></div>'
        '<div class="card-body"><div class="card-text">'
        "<p><strong>Трасса</strong></p>"
        "<p>Длина трассы — 5 км. Замер произведён специальным измерительным колесом. "
        "Маршрут пролегает в два круга по набережной.</p>"
        "</div></div></div></body></html>"
    )
    description = parse_location_description(html, "https://s95.ru/events/shchyolkovo")
    assert description.course_text == "Маршрут пролегает в два круга по набережной."


def test_s95_serbian_venue_prefix_is_stripped() -> None:
    """s95.rs подписывает место как «Mesto događaja» — подпись в текст не идёт.

    Без этого варианта в базу Белграда легло «Mesto događaja: The run takes
    place…», и на странице подпись читалась как часть описания.
    """
    html = (
        '<html><body><div class="card"><div class="card-header">'
        '<section class="card-title">Kontakti</section></div>'
        '<div class="card-body"><p class="card-text"><strong>Mesto događaja:</strong> '
        "The run takes place at the left bank of the river Sava.</p>"
        "</div></div></body></html>"
    )
    description = parse_location_description(html, "https://s95.rs/events/belgrade")
    assert description.travel_text == "The run takes place at the left bank of the river Sava."


def test_s95_location_description_empty_page() -> None:
    description = parse_location_description("<html><body><div>пусто</div></body></html>", "https://s95.ru/events/x")
    assert description.is_empty()
    assert description.links == []
