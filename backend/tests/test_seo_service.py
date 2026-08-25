from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import cast

import pytest

from app.core.abuse_protection import RouteTier, classify_route
from app.services.release_service import ReleasesPage
from app.services.seo_service import (
    DEFAULT_DESCRIPTION,
    DEFAULT_TITLE,
    DESCRIPTION_BUDGET,
    PLATFORM_LABELS,
    STATIC_PAGE_META,
    TITLE_BUDGET,
    PageMeta,
    _catalog_body,
    _location_body,
    _og_image_tags,
    _page_number,
    _releases_body,
    build_location_meta,
    build_robots_txt,
    catalog_json_ld,
    is_known_path,
    location_json_ld,
    location_lead_sentences,
    normalize_path,
    resolve_page_meta,
)

# Фронт лежит рядом с бэкендом в репозитории и примонтирован в /frontend-src
# внутри контейнера — так же, как у сторожа роутов в test_page_analytics_service.
_FRONTEND_SRC_CANDIDATES = (
    Path("/frontend-src"),
    Path(__file__).resolve().parents[2] / "frontend" / "src",
)


def _frontend_src() -> Path:
    for candidate in _FRONTEND_SRC_CANDIDATES:
        if (candidate / "App.tsx").exists():
            return candidate
    raise AssertionError(
        "Не найден App.tsx — сторож мета-тегов не может работать. "
        f"Искали в: {', '.join(str(c) for c in _FRONTEND_SRC_CANDIDATES)}"
    )


def _static_routes_from_app() -> list[str]:
    """Ключи STATIC_ROUTES из App.tsx (литералы + константы portalRoutes.ts)."""
    src = _frontend_src()
    app_src = (src / "App.tsx").read_text(encoding="utf-8")
    block_start = app_src.index("const STATIC_ROUTES")
    block_end = app_src.index("\n};", block_start)
    block = app_src[block_start:block_end]

    consts = dict(
        re.findall(
            r'export const ([A-Z0-9_]+)\s*=\s*"([^"]+)"',
            (src / "lib" / "portalRoutes.ts").read_text(encoding="utf-8"),
        )
    )

    routes = re.findall(r'^\s*"([^"]+)"\s*:', block, re.M)
    for name in re.findall(r"^\s*\[([A-Z0-9_]+)\]\s*:", block, re.M):
        value = consts.get(name)
        assert value is not None, f"Константа {name} не найдена в portalRoutes.ts"
        routes.append(value)
    assert len(routes) > 20, "Разбор STATIC_ROUTES сломался — роутов подозрительно мало"
    return sorted(set(routes))


APP_ROUTES = _static_routes_from_app()


@pytest.mark.parametrize("path", APP_ROUTES)
def test_every_app_route_has_meta(path: str) -> None:
    """Новый роут обязан получить свой заголовок вкладки.

    Иначе страница молча уедет на дефолтный «run5k.run — статистика субботних
    пробежек», и во вкладке браузера все разделы станут одинаковыми — ровно то
    состояние, из которого эта фича и вытаскивает сайт.
    """
    meta = resolve_page_meta(path)
    # Корень законно совпадает с дефолтом — он и есть главная страница.
    if path == "/":
        return
    assert meta.title != DEFAULT_TITLE, (
        f"Роут {path} есть в App.tsx, но своих мета-тегов в seo_service.py у него "
        "нет — страница получит дефолтный заголовок главной"
    )


def _ts_static_paths() -> set[str]:
    """Ключи STATIC_PAGE_META из frontend/src/lib/pageMeta.ts."""
    src = (_frontend_src() / "lib" / "pageMeta.ts").read_text(encoding="utf-8")
    block_start = src.index("export const STATIC_PAGE_META")
    block_end = src.index("\n};", block_start)
    literals = set(re.findall(r'^\s*"([^"]+)":\s*\{', src[block_start:block_end], re.M))

    redirects_start = src.index("const REDIRECT_PATHS")
    redirects_end = src.index("];", redirects_start)
    literals |= set(re.findall(r'"([^"]+)"', src[redirects_start:redirects_end]))
    assert len(literals) > 20, "Разбор pageMeta.ts сломался — адресов подозрительно мало"
    return literals


def test_frontend_mirror_covers_same_paths() -> None:
    """Клиентское зеркало и серверный канон описывают один и тот же набор.

    Мета-теги живут в двух местах не от хорошей жизни: роботу их отдаёт
    пререндер (Python), человеку — SPA (TypeScript). Разъедутся — у робота и
    у пользователя будут разные заголовки одной страницы.
    """
    assert _ts_static_paths() == set(STATIC_PAGE_META), (
        "Набор адресов в frontend/src/lib/pageMeta.ts разошёлся с "
        "STATIC_PAGE_META в backend/app/services/seo_service.py"
    )


def test_frontend_mirror_keeps_location_wording_in_sync() -> None:
    """Формулировки и лимиты локаций совпадают на бэкенде и на клиенте.

    Полноценно сверить две реализации тестом нельзя — они на разных языках.
    Но всё, что реально разъезжается при правках (названия систем, бюджеты
    длины, шаблоны фраз), — это литералы, и их сверить можно. Разъедутся —
    робот и человек увидят разные страницы, а это для поисковика подмена.
    """
    src = (_frontend_src() / "lib" / "pageMeta.ts").read_text(encoding="utf-8")

    for code, label in PLATFORM_LABELS.items():
        assert f'{code}: "{label}"' in src, f"Название системы {code} разошлось с бэкендом"

    assert f"TITLE_BUDGET = {TITLE_BUDGET}" in src
    assert f"DESCRIPTION_BUDGET = {DESCRIPTION_BUDGET}" in src

    for phrase in (
        "площадка субботних пробежек",
        "Здесь прошло",
        "старты здесь проводили",
        "журнал протоколов",
        " — результаты и статистика",
        ". Результаты субботних забегов, посещаемость и рейтинги участников.",
    ):
        assert phrase in src, f"Формулировка {phrase!r} есть на бэкенде, но не на клиенте"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/", "/"),
        ("/about/", "/about"),
        ("/locations/kuzminki?tab=1", "/locations/kuzminki"),
        ("/locations/kuzminki#records", "/locations/kuzminki"),
        ("locations", "/locations"),
    ],
)
def test_normalize_path(raw: str, expected: str) -> None:
    assert normalize_path(raw) == expected


@pytest.mark.parametrize(
    ("path", "indexable"),
    [
        ("/", True),
        ("/about", True),
        ("/locations", True),
        ("/locations/kuzminki", True),
        ("/locations/kuzminki/events", True),
        ("/ratings/wins", True),
        # Карточка участника индексируется с 15.08.2026: под noindex ВК и
        # Telegram строят превью без картинки. Вкладки — по-прежнему нет.
        ("/users/ivan", True),
        ("/users/ivan/runs", False),
        ("/world", False),
        ("/hq/hq-2kl5kfrlzmnvn8sc", False),
        # /login индексируется с 06.08.2026 — посадочная под «личный кабинет».
        ("/login", True),
        ("/backlog", False),
        ("/admin/users", False),
        ("/settings", False),
    ],
)
def test_indexable_flags(path: str, indexable: bool) -> None:
    assert resolve_page_meta(path).indexable is indexable


def test_unknown_path_falls_back_to_default() -> None:
    meta = resolve_page_meta("/something-strange")
    assert meta == PageMeta(title=DEFAULT_TITLE, description=DEFAULT_DESCRIPTION)
    assert meta.indexable is False


def test_every_page_title_is_distinct_enough() -> None:
    """Заголовки разделов не должны совпадать друг с другом.

    Исключения перечислены явно: оба OAuth-колбэка — один экран, а заглушки
    кабинета законно делят «Личный кабинет».
    """
    shared = {"Вход — run5k.run", "Личный кабинет — run5k.run"}
    titles = [m.title for p, m in STATIC_PAGE_META.items() if m.title not in shared]
    assert len(titles) == len(set(titles)), "Два разных раздела делят один заголовок вкладки"


def _location_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "slug": "kuzminki",
        "name": "Кузьминки",
        "city": "Москва",
        "platforms": [
            {"platform_code": "five_verst", "is_active": True, "events_count": 271},
        ],
        "stats": {
            "events_count": 271,
            "finishers_total": 40123,
            "course_records": {
                "male": {"finish_time_display": "00:15:42"},
                "female": {"finish_time_display": "00:18:03"},
            },
        },
    }
    payload.update(overrides)
    return payload


def test_location_meta_uses_name_and_numbers() -> None:
    meta = build_location_meta(_location_payload())
    # Система впереди названия: в поиске набирают «5 вёрст кузьминки», а не
    # «кузьминки 5 вёрст», и первые слова заголовка весят больше.
    assert meta.title.startswith("5 вёрст Кузьминки, Москва")
    assert "271 старт" in meta.description
    # Разряды в тысячах разделены неразрывным пробелом: «40123» читается сплошняком.
    assert "40\u00a0123 финиша" in meta.description
    assert "15:42 / 18:03" in meta.description
    assert len(meta.description) <= DESCRIPTION_BUDGET
    assert meta.indexable is True

    log_meta = build_location_meta(_location_payload(), events_log=True)
    assert "журнал протоколов" in log_meta.title
    # Заголовок журнала обязан отличаться от заголовка самой локации: два
    # разных адреса с одним title поисковик считает дублем.
    assert log_meta.title != meta.title


@pytest.mark.parametrize(
    ("code", "label"),
    [("five_verst", "5 вёрст"), ("s95", "С95"), ("parkrun", "parkrun"), ("runpark", "RunPark")],
)
def test_location_title_names_the_active_system(code: str, label: str) -> None:
    meta = build_location_meta(
        _location_payload(
            platforms=[{"platform_code": code, "is_active": True, "events_count": 10}]
        )
    )
    assert meta.title.startswith(f"{label} Кузьминки")


def test_location_title_names_last_system_when_none_active() -> None:
    """Закрытая локация: называем систему, при которой она работала последней."""
    meta = build_location_meta(
        _location_payload(
            platforms=[
                {
                    "platform_code": "parkrun",
                    "is_active": False,
                    "events_count": 200,
                    "last_event_date": "2022-02-26",
                },
                {
                    "platform_code": "runpark",
                    "is_active": False,
                    "events_count": 11,
                    "last_event_date": "2022-06-04",
                },
            ]
        )
    )
    assert meta.title.startswith("RunPark Кузьминки")


def test_location_title_fits_the_budget_and_keeps_the_place() -> None:
    """Длинное название жертвует описательным хвостом, но не городом."""
    meta = build_location_meta(
        _location_payload(name="Чертаново Кировоградские пруды", city="Москва")
    )
    assert len(meta.title) <= TITLE_BUDGET
    assert meta.title.startswith("5 вёрст Чертаново Кировоградские пруды, Москва")


def test_location_title_does_not_repeat_city_inside_name() -> None:
    meta = build_location_meta(_location_payload(name="Томск Сосновый Бор", city="Томск"))
    assert meta.title.count("Томск") == 1


def test_location_lead_reads_as_sentences() -> None:
    sentences = location_lead_sentences(_location_payload())
    assert sentences[0] == "«Кузьминки» (Москва) — площадка субботних пробежек 5 вёрст."
    assert "271 старт" in sentences[1]
    # Число финишёров во вводном абзаце — с разбивкой по разрядам: репорт
    # Дмитрия 14.08.2026, «21581 участник» на странице читался сплошняком.
    assert "финишировали 40 123 участника" in sentences[1]
    # Прошлых систем нет — третьего предложения быть не должно.
    assert len(sentences) == 2


def test_location_lead_names_previous_systems() -> None:
    sentences = location_lead_sentences(
        _location_payload(
            platforms=[
                {"platform_code": "parkrun", "is_active": False, "events_count": 281},
                {"platform_code": "runpark", "is_active": False, "events_count": 11},
                {"platform_code": "five_verst", "is_active": True, "events_count": 226},
            ]
        )
    )
    assert sentences[-1] == "До 5 вёрст старты здесь проводили parkrun и RunPark."


def test_location_lead_skips_systems_without_events() -> None:
    """Связка в каталоге без единого протокола — не «эпоха» локации."""
    sentences = location_lead_sentences(
        _location_payload(
            platforms=[
                {"platform_code": "parkrun", "is_active": False, "events_count": 0},
                {"platform_code": "five_verst", "is_active": True, "events_count": 226},
            ]
        )
    )
    assert not any("До 5 вёрст" in s for s in sentences)


def test_location_meta_survives_empty_stats() -> None:
    """Локация без единого старта не должна ронять сборку мета-тегов."""
    meta = build_location_meta({"slug": "new-place", "name": "Новая", "city": None, "stats": {}})
    assert "Новая" in meta.title
    # Систем нет вовсе — заголовок просто без приставки, без «None» в тексте.
    assert "None" not in meta.title
    assert meta.description


@pytest.mark.parametrize(
    ("count", "expected"),
    [(1, "1 старт"), (2, "2 старта"), (5, "5 стартов"), (11, "11 стартов"), (21, "21 старт")],
)
def test_location_meta_pluralizes_starts(count: int, expected: str) -> None:
    meta = build_location_meta({"name": "X", "stats": {"events_count": count}})
    assert expected in meta.description


_DESCRIPTION_PAYLOAD = {
    "platform_code": "five_verst",
    "schedule_text": "Старт проходит по адресу: Москва, Сокольнический Вал, 1с1. Каждую субботу с 9:00.",
    "course_text": "Маршрут проходит по дорожкам парка.\n\nСтарт у центрального входа.",
    "travel_text": "Москва, Сокольнический Вал, 1с1",
    "travel_sections": [
        {"title": "Общественным транспортом", "text": "Ближайшая станция метро — Сокольники."},
        {"title": "На автомобиле", "text": "Парковка в 200 метрах от старта."},
        # У S95 врезка называется как наш заголовок — сервис отдаёт её без title.
        {"title": None, "text": "Ориентир — беседка-купол у катка."},
    ],
    "links": [{"title": "Карта и схема проезда", "url": "https://yandex.ru/maps/-/CDHUrYj8"}],
    "source_url": "https://5verst.ru/sokolniki/course/",
}


def test_location_body_shows_course_and_travel_text() -> None:
    """Робот получает то же описание трассы, что человек видит на странице."""
    body = _location_body(_location_payload(description=_DESCRIPTION_PAYLOAD), events_log=False)

    # Заголовок блока сразу называет источник — так же, как подблок-цитата на
    # самой странице; чужой текст обязан быть подписан ссылкой.
    assert "Описание с официального сайта 5 вёрст" in body
    assert 'href="https://5verst.ru/sokolniki/course/"' in body

    assert "<h3>Где и когда</h3>" in body
    assert "Каждую субботу с 9:00" in body
    assert "<h3>Трасса</h3>" in body
    assert "<p>Маршрут проходит по дорожкам парка.</p>" in body
    assert "<p>Старт у центрального входа.</p>" in body
    assert "<h3>Как добраться</h3>" in body
    assert "<h4>Общественным транспортом</h4>" in body
    assert "Парковка в 200 метрах" in body
    # Секция без заголовка выводится просто абзацем, без пустого <h4>.
    assert "Ориентир — беседка-купол у катка." in body
    assert body.count("Как добраться") == 1

    # Порядок как на странице: сначала наши данные, потом цитата с чужого сайта.
    assert body.index("<h2>История систем</h2>") < body.index("Описание с официального сайта")


def test_location_body_without_description() -> None:
    body = _location_body(_location_payload(), events_log=False)
    assert "Описание с официального сайта" not in body


def test_events_log_body_does_not_repeat_description() -> None:
    """Журнал протоколов — отдельный адрес; тот же текст там был бы дублем."""
    body = _location_body(_location_payload(description=_DESCRIPTION_PAYLOAD), events_log=True)
    assert "Маршрут проходит по дорожкам парка" not in body


def test_catalog_body_lists_locations_with_links() -> None:
    """Робот на /locations получает список площадок, а не два служебных предложения."""
    items = [
        {"slug": "a", "name": "Бутово", "city": "Москва", "platform_codes": ["five_verst"]},
        {"slug": "b", "name": "Кузьминки", "city": "Москва", "platform_codes": ["s95"]},
        {"slug": "c", "name": "Закрытая", "city": "Тверь", "platform_codes": ["parkrun"], "is_cancelled": True},
    ]
    body = _catalog_body(items)
    assert "<h1>Локации 5 вёрст, С95, parkrun и RunPark</h1>" in body
    assert "2 локации в 1 городе" in body
    assert '<a href="/locations/a">Бутово</a> — Москва' in body
    # Отменённые площадки в каталоге робота не участвуют.
    assert "Закрытая" not in body
    assert "5 вёрст — 1 площадка" in body


@pytest.mark.parametrize(
    ("path", "known"),
    [
        ("/", True),
        ("/locations", True),
        ("/ratings/wins", True),
        ("/locations/kuzminki", True),
        ("/locations/kuzminki/events", True),
        ("/users/ivan", True),
        ("/admin/users", True),
        ("/world", True),
        ("/protocol", True),
        ("/protocol/2026-08-15", True),
        # Форму даты регулярка пропускает, а недели такой нет — это 404, а не 500.
        ("/protocol/2026-08-99", False),
        ("/protocol/2026-02-30", False),
        ("/protocol/latest", False),
        # Мусор обязан быть неизвестен: SPA отвечал 200 на что угодно, и Яндекс
        # отметил это диагностикой «некорректно настроен возврат 404».
        ("/something-strange", False),
        ("/locations/kuzminki/events/extra", False),
        ("/ratings/fake-metric", False),
        ("/wp-admin", False),
    ],
)
def test_is_known_path(path: str, known: bool) -> None:
    assert is_known_path(path) is known


def test_unified_protocol_meta_carries_the_week() -> None:
    """Дата недели едет в заголовок — превью ссылки в чате должно её называть."""
    meta = resolve_page_meta("/protocol/2026-08-15")
    assert "15.08.2026" in meta.title
    assert meta.indexable is True


def test_robots_closes_user_pages_from_crawl() -> None:
    """Страницы участников не должны жечь краулинговый бюджет.

    Они и так noindex, но робот их скачивал сотнями — особенно после
    включения обхода по счётчикам Метрики.
    """
    robots = build_robots_txt()
    # Закрыты вкладки (/users/12/maps), сама карточка участника открыта —
    # иначе превью ссылки в ВК и Telegram остаётся без картинки.
    assert "Disallow: /users/*/" in robots
    # /world закрывать не за что: он один, а noindex в странице сохраняет вес.
    assert "Disallow: /world" not in robots


def test_last_event_block_reaches_the_robot() -> None:
    """«5 вёрст X результаты» — самый частый интент, робот обязан их видеть."""
    payload = _location_payload()
    stats = cast("dict[str, object]", payload["stats"])
    stats["last_event"] = {
        "event_date": "2026-08-08",
        "platform_code": "five_verst",
        "finishers": 62,
        "volunteers": 20,
        "best_male_time_display": "00:18:09",
        "best_female_time_display": "00:19:08",
        "avg_time_display": "00:27:28",
        "debutants": 2,
        "first_at_location": 2,
        "prs": 2,
    }
    body = _location_body(payload, events_log=False)
    assert "Последний старт: 2026-08-08 (5 вёрст)" in body
    assert "Финишировали: 62" in body
    # Часы отрезаются здесь так же, как везде: «18:09», а не «00:18:09».
    assert "Лучшее время, мужчины: 18:09" in body
    assert "Впервые здесь: 4" in body


def test_location_json_ld_describes_place_and_event() -> None:
    payload = _location_payload(
        latitude=55.667123,
        longitude=37.404714,
        region="Московская",
        country="Россия",
        platforms=[
            {
                "platform_code": "five_verst",
                "is_active": True,
                "events_count": 271,
                "url": "https://5verst.ru/kuzminki/",
            }
        ],
    )
    stats = cast("dict[str, object]", payload["stats"])
    stats["last_event"] = {"event_date": "2026-08-08", "finishers": 62}

    objects = location_json_ld(payload)
    types = [o["@type"] for o in objects]
    assert types == ["SportsActivityLocation", "SportsEvent", "BreadcrumbList"]

    place = objects[0]
    assert place["name"] == "5 вёрст Кузьминки"
    assert place["address"]["addressLocality"] == "Москва"
    assert place["geo"]["latitude"] == 55.667123
    # sameAs связывает нас с первоисточником, а не выдаёт за него.
    assert place["sameAs"] == ["https://5verst.ru/kuzminki/"]

    event = objects[1]
    assert event["startDate"] == "2026-08-08"
    # Финишёры — в description: maximumAttendeeCapacity означает вместимость.
    assert "62" in event["description"]
    assert "maximumAttendeeCapacity" not in event

    crumbs = objects[2]["itemListElement"]
    assert [c["name"] for c in crumbs] == ["Главная", "Локации", "Кузьминки"]


def test_location_json_ld_skips_event_without_date() -> None:
    """Нет данных о старте — нет и SportsEvent: разметка не выдумывает."""
    objects = location_json_ld(_location_payload())
    assert [o["@type"] for o in objects] == ["SportsActivityLocation", "BreadcrumbList"]


def test_catalog_json_ld_lists_live_locations() -> None:
    objects = catalog_json_ld(
        [
            {"slug": "b", "name": "Бутово", "city": "Москва"},
            {"slug": "a", "name": "Алёшкинский", "city": "Москва"},
            {"slug": "x", "name": "Закрытая", "is_cancelled": True},
        ]
    )
    listing = objects[0]
    assert listing["numberOfItems"] == 2
    # По алфавиту, как и на самой странице.
    assert [i["name"] for i in listing["itemListElement"]] == ["Алёшкинский", "Бутово"]


def test_og_image_tags_are_complete_for_previews() -> None:
    """Полный набор подтегов og:image — иначе ВК показывает миниатюру.

    secure_url ищут отдельные парсеры, type снимает угадывание формата,
    alt часть площадок берёт подписью к карточке.
    """
    tags = _og_image_tags("https://run5k.run/og/locations/butovo.png", alt="5 вёрст Бутово")
    joined = "\n".join(tags)
    for needed in (
        'property="og:image"',
        'property="og:image:secure_url"',
        'property="og:image:type" content="image/png"',
        'property="og:image:width" content="1200"',
        'property="og:image:height" content="630"',
        'property="og:image:alt" content="5 вёрст Бутово"',
        'name="twitter:card" content="summary_large_image"',
    ):
        assert needed in joined, needed


def test_og_image_falls_back_to_default_without_alt() -> None:
    tags = _og_image_tags(None)
    joined = "\n".join(tags)
    assert "/og/default.png" in joined
    # Без подписи тега alt быть не должно: пустой alt хуже отсутствующего.
    assert "og:image:alt" not in joined


def test_robots_lists_sitemap_and_closes_service_paths() -> None:
    robots = build_robots_txt()
    assert "Sitemap: " in robots
    assert "/sitemap.xml" in robots
    for closed in ("/api/", "/admin", "/hq/", "/settings"):
        assert f"Disallow: {closed}" in robots
    # /login — посадочная под «5 верст личный кабинет», закрывать её нельзя.
    assert "Disallow: /login" not in robots


@pytest.mark.parametrize("path", ["/sitemap.xml", "/robots.txt", "/__prerender/locations/kuzminki"])
def test_seo_paths_are_exempt_from_rate_limit(path: str) -> None:
    """429 в адрес поисковика останавливает обход — эти адреса вне тарифов."""
    assert classify_route(path, "GET") is RouteTier.exempt


def test_page_number_reads_query_and_ignores_garbage() -> None:
    assert _page_number("/updates") == 1
    assert _page_number("/updates?page=4") == 4
    assert _page_number("/updates?page=0") == 1
    assert _page_number("/updates?page=-2") == 1
    assert _page_number("/updates?page=abc") == 1
    assert _page_number("/updates?other=7") == 1


def test_releases_body_renders_history_with_neighbour_links() -> None:
    """Робот должен видеть текст релизов и путь к соседним страницам."""

    class _Release:
        def __init__(self, version: str, title: str, body: str) -> None:
            self.version = version
            self.title = title
            self.body = body
            self.released_at = date(2026, 8, 1)

    page = ReleasesPage(
        items=[
            _Release("2.4.0", "Таблицы на телефоне", "Вступление.\n\n- Первый пункт\n- Второй"),
        ],
        total=25,
        page=2,
        page_size=10,
        pages=3,
        latest_version="2.9.0",
    )
    html = _releases_body(page)  # type: ignore[arg-type]
    assert "v2.4.0 — Таблицы на телефоне" in html
    assert "<li>Первый пункт</li>" in html
    assert "<p>Вступление.</p>" in html
    assert "страница 2 из 3" in html
    # Со второй страницы «назад» ведёт на чистый /updates, а не на ?page=1.
    assert 'href="/updates" rel="prev"' in html
    assert 'href="/updates?page=3" rel="next"' in html
