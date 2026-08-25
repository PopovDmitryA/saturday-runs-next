"""Описание площадки 5 вёрст: страница «О трассе» плюс блок «Где и когда?».

Что берём и почему именно это. Страница `course/` состоит из двух частей:
уникальный текст про конкретный парк (маршрут, покрытие, место сбора, блок
«Как добраться») и общий для всех локаций хвост — «Внимание!», «Правила
безопасности на трассе», «И еще». Хвост одинаков на всех 200+ страницах, и
класть его в базу (а тем более выводить у себя) смысла нет: для поиска это
дубли, для читателя — вода. Поэтому парсер берёт всё до первого заголовка
верхнего уровня плюс блок «Как добраться» и на этом останавливается.

С главной страницы локации берём колонку «Где и когда?»: адрес старта и время.
Время там не всегда «каждую субботу в 9:00» — в Мариуполе, например, летом
старт в 8:00. Такое расписание больше нигде не лежит, а человеку нужно раньше
всего остального. Страница и так загружается тем же `fetch_location`, лишних
запросов это не добавляет.

Координаты со страниц уже разбирает bulk_parser.parse_course_coordinates —
здесь они только мешают тексту, поэтому вырезаются из абзацев.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

from app.platform_adapters.canonical import (
    CanonicalDescriptionSection,
    CanonicalLocationDescription,
)

# «55.791959:37.664957» — подпись кнопки-точки на карте. В вёрстке она лежит
# внутри обычного <p>, иногда вперемешку с текстом, поэтому вырезаем токеном.
COORD_TOKEN_RE = re.compile(r"\b\d{1,3}\.\d+\s*:\s*\d{1,3}\.\d+\b")
TRAVEL_BLOCK_MARKER = "добра"  # «Как добраться?» — заголовок блока knd-block-info
SCHEDULE_HEADING_RE = re.compile(r"Где и когда", re.I)
# «Читайте подробнее на странице Трасса.» — навигация по чужому сайту: у нас
# на неё уже стоит ссылка «Описание с сайта 5 вёрст», в тексте она мусор.
SCHEDULE_CROSSLINK_RE = re.compile(r"\s*Читайте подробнее на странице\s*Трасса\s*\.?", re.I)
# «Точка сбора участников:» без самих координат (их вырезает COORD_TOKEN_RE)
# оставляет висящее двоеточие — саму точку человек видит на карте рядом.
SCHEDULE_DANGLING_RE = re.compile(r"\s*Точка сбора участников\s*:?\s*$", re.I)


def _clean_text(node: Tag) -> str:
    text = node.get_text(" ", strip=True)
    text = COORD_TOKEN_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" ·—-").strip()


def _paragraphs(nodes: list[Tag]) -> list[str]:
    parts: list[str] = []
    for node in nodes:
        text = _clean_text(node)
        if text:
            parts.append(text)
    return parts


def _travel_sections(block: Tag) -> list[CanonicalDescriptionSection]:
    sections: list[CanonicalDescriptionSection] = []
    for column in block.select(".knd-block-info__content"):
        heading = column.find(["h3", "h4"])
        title = _clean_text(heading) if heading is not None else None
        body = column.find_all(["p", "ul", "ol"], recursive=False)
        text = "\n\n".join(_paragraphs(body))
        if not text:
            # «На автомобиле» без текста встречается (zelenograd) — пустая
            # колонка на нашей странице выглядела бы как потерянный контент.
            continue
        sections.append(CanonicalDescriptionSection(title=title, text=text))
    return sections


def parse_schedule_text(home_html: str) -> str | None:
    """Колонка «Где и когда?» с главной страницы локации: адрес и время старта."""

    soup = BeautifulSoup(home_html, "html.parser")
    for column in soup.select(".knd-block-info__content"):
        heading = column.find(["h3", "h4"])
        if heading is None or not SCHEDULE_HEADING_RE.search(heading.get_text(" ", strip=True)):
            continue
        parts = _paragraphs(column.find_all(["p", "ul", "ol"], recursive=False))
        text = " ".join(parts)
        # Кросслинк заменяем точкой, а не пробелом: у части площадок перед ним
        # предложение не закрыто («…Зеленограда Читайте подробнее…»), и после
        # простого выреза две фразы слипались в одну.
        text = SCHEDULE_CROSSLINK_RE.sub(". ", text)
        text = SCHEDULE_DANGLING_RE.sub("", text)
        text = re.sub(r"\s*\.(\s*\.)+", ".", text)  # «. .» после склейки
        text = re.sub(r"\s+([.,])", r"\1", text)  # «д. 26 .» — пробел перед точкой
        text = re.sub(r"\s+", " ", text).strip(" .·—-").strip()
        return f"{text}." if text and not text.endswith(".") else (text or None)
    return None


def parse_course_description(
    html: str,
    source_url: str,
    *,
    home_html: str | None = None,
) -> CanonicalLocationDescription:
    """Разбирает HTML страницы `/{slug}/course/` в канонический блок описания.

    `home_html` — главная страница локации, если она уже загружена: из неё
    берётся расписание («Где и когда?»).
    """

    schedule_text = parse_schedule_text(home_html) if home_html else None
    soup = BeautifulSoup(html, "html.parser")
    content = soup.select_one(".entry-content")
    if content is None:
        return CanonicalLocationDescription(schedule_text=schedule_text, source_url=source_url)

    course_nodes: list[Tag] = []
    travel_block: Tag | None = None
    for child in content.find_all(recursive=False):
        if not isinstance(child, Tag):
            continue
        classes = child.get("class") or []
        if "knd-block" in classes:
            if travel_block is None and TRAVEL_BLOCK_MARKER in _clean_text(child)[:80].lower():
                travel_block = child
            continue
        if child.name in {"h1", "h2"}:
            # Начался общий для всех локаций хвост — дальше уникального текста нет.
            break
        if child.name in {"p", "ul", "ol"}:
            course_nodes.append(child)

    course_text = "\n\n".join(_paragraphs(course_nodes)) or None

    travel_text: str | None = None
    travel_sections: list[CanonicalDescriptionSection] = []
    if travel_block is not None:
        intro = travel_block.select_one(".knd-block-info__text")
        travel_text = _clean_text(intro) if intro is not None else None
        travel_sections = _travel_sections(travel_block)

    return CanonicalLocationDescription(
        schedule_text=schedule_text,
        course_text=course_text,
        travel_text=travel_text or None,
        travel_sections=travel_sections,
        source_url=source_url,
    )
