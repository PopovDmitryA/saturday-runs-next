"""Мета-теги страниц, sitemap и серверный пререндер для роботов.

Зачем всё это. Сайт — SPA: nginx на любой адрес отдаёт один и тот же
index.html, содержимое дорисовывает JavaScript уже в браузере. Робот
(Яндекс, Google, Telegram, VK) получает пустую болванку и не видит ни
заголовка страницы, ни текста. Поэтому:

* мета-теги (title/description/og) описаны ЗДЕСЬ и продублированы на клиенте
  в frontend/src/lib/pageMeta.ts — этот файл канон, зеркало сторожит тест
  backend/tests/test_seo_service.py;
* sitemap.xml перечисляет роботу публичные адреса, включая все страницы
  локаций (их нельзя найти обходом — каталог рисуется скриптом);
* пререндер отдаёт роботу настоящий HTML с заголовком и текстом. Человека он
  не касается: ветка по User-Agent живёт в nginx/conf.d/default.conf.

Мировой обход parkrun (/world, /hq/*) и личные страницы участников
(/users/*) в sitemap не попадают и помечены noindex — решение Дмитрия
02.08.2026.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from html import escape
from typing import Any, cast
from xml.sax.saxutils import escape as xml_escape

from sqlalchemy.orm import Session

from app.config import get_settings
from app.services.location_page_service import build_location_page, build_locations_index

SITE_NAME = "run5k.run"

DEFAULT_TITLE = "run5k.run — статистика субботних пробежек"
DEFAULT_DESCRIPTION = (
    "Единая статистика субботних парковых пробежек: 5 вёрст, S95, parkrun и RunPark "
    "в одном месте. Рекорды и рейтинги участников, страницы локаций, личный кабинет "
    "с историей стартов и волонтёрства."
)


@dataclass(frozen=True)
class PageMeta:
    """Что отдаём роботу и что показываем во вкладке браузера.

    indexable — попадает ли адрес в sitemap и получает ли `index,follow`.
    Всё, что закрыто логином, служебное или персональное, — False.
    """

    title: str
    description: str
    indexable: bool = False


def _meta(title: str, description: str, *, indexable: bool = False) -> PageMeta:
    return PageMeta(title=title, description=description, indexable=indexable)


# Канон статических адресов = STATIC_ROUTES в frontend/src/App.tsx. Роут без
# строки здесь уронит test_every_app_route_has_meta — так же, как забытый роут
# роняет сторож аналитики (см. page_analytics_service).
STATIC_PAGE_META: dict[str, PageMeta] = {
    "/": _meta(
        DEFAULT_TITLE,
        DEFAULT_DESCRIPTION,
        indexable=True,
    ),
    "/about": _meta(
        "О проекте — run5k.run",
        "Как устроен run5k.run: откуда берутся данные 5 вёрст, S95, parkrun и RunPark, "
        "что внутри личного кабинета, как проект относится к приватности участников.",
        indexable=True,
    ),
    "/blog": _meta(
        "Блог — run5k.run",
        "Разборы и находки из статистики субботних пробежек: рекорды, серии, история "
        "локаций, необычные достижения участников.",
        indexable=True,
    ),
    "/updates": _meta(
        "Обновления сайта — run5k.run",
        "История релизов run5k.run: что появилось на сайте и когда.",
        indexable=True,
    ),
    "/login": _meta(
        "Вход — run5k.run",
        "Вход в личный кабинет run5k.run через VK или Яндекс.",
    ),
    "/locations": _meta(
        "Локации — run5k.run",
        "Каталог площадок субботних пробежек: сколько было стартов и финишей, когда "
        "прошёл первый забег, в каких системах живёт локация.",
        indexable=True,
    ),
    "/ratings": _meta(
        "Рейтинги — run5k.run",
        "Сквозные рейтинги участников субботних пробежек по всем системам: пробежки, "
        "волонтёрство, победы, туризм по локациям.",
        indexable=True,
    ),
    "/ratings/runs": _meta(
        "Рейтинг по числу пробежек — run5k.run",
        "Кто пробежал больше всех субботних стартов: сводный рейтинг по 5 вёрстам, "
        "S95, parkrun и RunPark.",
        indexable=True,
    ),
    "/ratings/volunteering": _meta(
        "Рейтинг волонтёров — run5k.run",
        "Кто чаще всех выходил волонтёром на субботние старты — сводный рейтинг по "
        "всем системам.",
        indexable=True,
    ),
    "/ratings/volunteer-roles": _meta(
        "Рейтинг по волонтёрским ролям — run5k.run",
        "Сколько разных волонтёрских ролей освоили участники субботних пробежек.",
        indexable=True,
    ),
    "/ratings/locations": _meta(
        "Рейтинг по числу локаций — run5k.run",
        "Беговой туризм в цифрах: кто пробежал на наибольшем числе разных площадок.",
        indexable=True,
    ),
    "/ratings/volunteer-locations": _meta(
        "Рейтинг волонтёрского туризма — run5k.run",
        "Кто волонтёрил на наибольшем числе разных площадок субботних пробежек.",
        indexable=True,
    ),
    "/ratings/wins": _meta(
        "Рейтинг побед — run5k.run",
        "Кто чаще всех финишировал первым на субботних стартах, с разбивкой по полу.",
        indexable=True,
    ),
    "/ratings/home-distance": _meta(
        "Рейтинг дальности от дома — run5k.run",
        "Кто уезжает бегать дальше всех от своей домашней локации: сумма километров "
        "по уникальным площадкам.",
        indexable=True,
    ),
    "/ratings/win-locations": _meta(
        "Рейтинг побед по локациям — run5k.run",
        "На скольких разных площадках участники успевали финишировать первыми.",
        indexable=True,
    ),
    # Публичная доска предложений: смотреть может любой, но в поиске ей делать
    # нечего — это рабочая кухня, а не витрина.
    "/backlog": _meta(
        "Бэклог — run5k.run",
        "Что участники предлагают добавить на сайт и за что голосуют.",
    ),
    # Лаборатория карты — витрина дизайна, наружу не публикуется.
    "/new/map-lab": _meta("Карта (лаборатория) — run5k.run", "Экспериментальная карта локаций."),
    "/new/cabinet-preview": _meta(
        "Превью кабинета — run5k.run",
        "Как выглядит личный кабинет run5k.run — на демонстрационных данных.",
    ),
    "/share": _meta("Поделиться — run5k.run", "Картинки с личной статистикой для соцсетей."),
    "/settings": _meta("Настройки — run5k.run", "Настройки профиля и привязанных аккаунтов."),
    "/oauth/yandex/callback": _meta("Вход — run5k.run", "Завершаем вход через Яндекс."),
    "/oauth/vk/callback": _meta("Вход — run5k.run", "Завершаем вход через VK."),
}

# Адреса-заглушки: сами ничего не показывают, сразу уводят на другой адрес.
# Один заголовок на всех — робот сюда всё равно не должен попадать.
_REDIRECT_PATHS = (
    "/dashboard",
    "/profiles",
    "/runs",
    "/achievements",
    "/co-runners",
    "/volunteering",
    "/maps",
    "/history",
    "/new/dashboard",
    "/new/runs",
    "/new/volunteering",
    "/new/achievements",
    "/new/co-runners",
    "/new/maps",
    "/new/history",
    "/new/share",
    "/new/settings",
    "/sync",
    "/queue",
    "/admin",
)
for _path in _REDIRECT_PATHS:
    STATIC_PAGE_META[_path] = _meta("Личный кабинет — run5k.run", "Переход в личный кабинет.")

_ADMIN_META = _meta("Админка — run5k.run", "Служебный раздел run5k.run.")

_PROFILE_RE = re.compile(r"^/users/([^/]+)(?:/([^/]+))?$")
_LOCATION_EVENTS_RE = re.compile(r"^/locations/([^/]+)/events$")
_LOCATION_RE = re.compile(r"^/locations/([^/]+)$")
_SWEEP_HQ_RE = re.compile(r"^/hq/.+$")


def normalize_path(raw_path: str) -> str:
    """Отрезает query/hash и хвостовой слэш (кроме корня)."""
    path = raw_path.split("?", 1)[0].split("#", 1)[0]
    if not path.startswith("/"):
        path = f"/{path}"
    if len(path) > 1:
        path = path.rstrip("/") or "/"
    return path


def resolve_page_meta(raw_path: str) -> PageMeta:
    """Мета-теги адреса без обращения к БД.

    Для страниц с сущностью (локация, участник) отдаёт родовой заголовок:
    имя подставляет либо клиент после загрузки данных, либо пререндер, который
    сущность уже прочитал (см. build_location_meta).
    """
    path = normalize_path(raw_path)

    if path.startswith("/admin/"):
        return _ADMIN_META
    if _SWEEP_HQ_RE.match(path):
        return _meta("Обход parkrun — run5k.run", "Служебная витрина мирового обхода parkrun.")
    if path == "/world":
        # Публичное табло мирового обхода. В sitemap не идёт и закрыто от
        # индексации по решению Дмитрия 02.08.2026.
        return _meta(
            "Мировой parkrun — run5k.run",
            "Сколько площадок parkrun в мире и как идёт их обход.",
        )
    if _PROFILE_RE.match(path):
        return _meta(
            "Участник — run5k.run",
            "Страница участника субботних пробежек: пробежки, волонтёрство, "
            "достижения и посещённые локации.",
        )
    if _LOCATION_EVENTS_RE.match(path):
        return _meta(
            "Журнал протоколов локации — run5k.run",
            "Все старты локации: дата, номер события, финишёры, волонтёры и лучшие "
            "результаты дня.",
            indexable=True,
        )
    if _LOCATION_RE.match(path):
        return _meta(
            "Локация — run5k.run",
            "Страница площадки субботних пробежек: рекорды трассы, посещаемость, "
            "история систем и распределение финишных времён.",
            indexable=True,
        )

    return STATIC_PAGE_META.get(path, _meta(DEFAULT_TITLE, DEFAULT_DESCRIPTION))


def _plural(count: int, one: str, few: str, many: str) -> str:
    tail_100 = count % 100
    if 11 <= tail_100 <= 14:
        return many
    tail = count % 10
    if tail == 1:
        return one
    if 2 <= tail <= 4:
        return few
    return many


def _strip_leading_hours(display: str | None) -> str | None:
    """00:17:51 → 17:51.

    В БД время лежит как ЧЧ:ММ:СС, но на пятикилометровой дистанции час никому
    не нужен, а в описании для поисковика лишние символы съедают лимит строки.
    Зеркало stripLeadingHours из LocationPage.tsx.
    """
    if not display:
        return None
    return display[3:] if display.startswith("00:") else display


def build_location_meta(payload: dict[str, Any], *, events_log: bool = False) -> PageMeta:
    """Мета-теги конкретной локации — по данным её страницы.

    Зеркало этой же сборки на клиенте: locationPageMeta в
    frontend/src/lib/pageMeta.ts. Меняете формулировку — меняйте в обоих местах.
    """
    name = str(payload.get("name") or "Локация")
    city = payload.get("city")
    where = f"{name}, {city}" if city else name

    stats = payload.get("stats") or {}
    events_count = int(stats.get("events_count") or 0)
    finishers_total = int(stats.get("finishers_total") or 0)

    parts: list[str] = []
    if events_count:
        parts.append(
            f"{events_count} {_plural(events_count, 'старт', 'старта', 'стартов')}"
        )
    if finishers_total:
        parts.append(
            f"{finishers_total} {_plural(finishers_total, 'финиш', 'финиша', 'финишей')}"
        )

    records = stats.get("course_records") or {}
    male = _strip_leading_hours((records.get("male") or {}).get("finish_time_display"))
    female = _strip_leading_hours((records.get("female") or {}).get("finish_time_display"))
    if male or female:
        best = " / ".join(x for x in (male, female) if x)
        parts.append(f"рекорды трассы {best}")

    numbers = ", ".join(parts)
    # Описание держим в ~155 символах: длиннее поисковик обрежет многоточием.
    if events_log:
        title = f"{where}: журнал протоколов — {SITE_NAME}"
        description = f"Все старты локации «{name}»"
        if numbers:
            description += f": {numbers}"
        description += ". Дата, номер события, финишёры, волонтёры и лучшее время дня."
        return _meta(title, description, indexable=True)

    title = f"{where} — статистика субботних пробежек — {SITE_NAME}"
    description = f"Локация «{name}»"
    if city:
        description += f" ({city})"
    if numbers:
        description += f": {numbers}"
    description += ". Посещаемость, история систем и рейтинги участников."
    return _meta(title, description, indexable=True)


# --------------------------------------------------------------------------
# sitemap.xml и robots.txt
# --------------------------------------------------------------------------

# Статические адреса в sitemap. Порядок = порядок в файле, приоритет — грубая
# подсказка роботу, что важнее. Локации добавляются отдельно, из каталога.
_SITEMAP_STATIC: tuple[tuple[str, str], ...] = (
    ("/", "1.0"),
    ("/locations", "0.9"),
    ("/ratings", "0.8"),
    ("/ratings/runs", "0.7"),
    ("/ratings/volunteering", "0.7"),
    ("/ratings/volunteer-roles", "0.6"),
    ("/ratings/locations", "0.7"),
    ("/ratings/volunteer-locations", "0.6"),
    ("/ratings/wins", "0.7"),
    ("/ratings/win-locations", "0.6"),
    ("/ratings/home-distance", "0.6"),
    ("/blog", "0.7"),
    ("/about", "0.6"),
    ("/updates", "0.4"),
)


def site_base_url() -> str:
    return get_settings().app_base_url.rstrip("/")


def _sitemap_url(base: str, path: str, *, lastmod: date | None, priority: str) -> str:
    parts = [f"    <loc>{xml_escape(base + path)}</loc>"]
    if lastmod is not None:
        parts.append(f"    <lastmod>{lastmod.isoformat()}</lastmod>")
    parts.append(f"    <priority>{priority}</priority>")
    body = "\n".join(parts)
    return f"  <url>\n{body}\n  </url>"


def build_sitemap(db: Session) -> str:
    """sitemap.xml: публичные разделы + страница и журнал каждой локации.

    Сознательно НЕ включаем (решение Дмитрия 02.08.2026): мировой обход
    parkrun (/world, /hq/*) и личные страницы участников (/users/*). Плюс
    всё, что за логином, служебное и редиректы.
    """
    base = site_base_url()
    urls = [
        _sitemap_url(base, path, lastmod=None, priority=priority)
        for path, priority in _SITEMAP_STATIC
    ]

    index = build_locations_index(db)
    items: list[dict[str, Any]] = cast("list[dict[str, Any]]", index.get("items") or [])
    for item in items:
        slug = item.get("slug")
        if not slug:
            continue
        # Отменённые локации в выдаче только мешают: страница есть, но ехать
        # туда некуда. Приостановленные оставляем — они возвращаются.
        if item.get("is_cancelled"):
            continue
        last_event = item.get("last_event_date")
        lastmod = last_event if isinstance(last_event, date) else None
        urls.append(_sitemap_url(base, f"/locations/{slug}", lastmod=lastmod, priority="0.8"))
        urls.append(
            _sitemap_url(base, f"/locations/{slug}/events", lastmod=lastmod, priority="0.5")
        )

    body = "\n".join(urls)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{body}\n"
        "</urlset>\n"
    )


def build_robots_txt() -> str:
    """robots.txt: закрываем служебное и личное, показываем sitemap.

    /users/ и /world не в sitemap, но и Disallow им не ставим: пусть робот
    ходит по ссылкам и видит `noindex` в самой странице — так вес ссылок не
    теряется, а в индекс они не попадают.
    """
    base = site_base_url()
    lines = [
        "User-agent: *",
        "Disallow: /api/",
        "Disallow: /admin",
        "Disallow: /hq/",
        "Disallow: /new/",
        "Disallow: /settings",
        "Disallow: /share",
        "Disallow: /login",
        "Disallow: /oauth/",
        "Disallow: /dashboard",
        "Disallow: /profiles",
        "Disallow: /runs",
        "Disallow: /volunteering",
        "Disallow: /achievements",
        "Disallow: /co-runners",
        "Disallow: /maps",
        "Disallow: /history",
        "",
        f"Sitemap: {base}/sitemap.xml",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Пререндер для роботов
# --------------------------------------------------------------------------


def _og_image_tags() -> list[str]:
    """Место под og:image.

    Динамическая картинка-превью (Л19) делается в отдельной ветке; когда
    появится эндпоинт, тег добавляется здесь и подхватится всеми страницами.
    """
    return []


def _render_html(
    *,
    meta: PageMeta,
    canonical: str,
    body_html: str,
) -> str:
    robots = "index,follow" if meta.indexable else "noindex,follow"
    head = [
        '<meta charset="utf-8">',
        f"<title>{escape(meta.title)}</title>",
        f'<meta name="description" content="{escape(meta.description, quote=True)}">',
        f'<meta name="robots" content="{robots}">',
        f'<link rel="canonical" href="{escape(canonical, quote=True)}">',
        f'<meta property="og:site_name" content="{SITE_NAME}">',
        '<meta property="og:type" content="website">',
        f'<meta property="og:title" content="{escape(meta.title, quote=True)}">',
        f'<meta property="og:description" content="{escape(meta.description, quote=True)}">',
        f'<meta property="og:url" content="{escape(canonical, quote=True)}">',
        '<meta name="twitter:card" content="summary">',
        *_og_image_tags(),
    ]
    head_html = "\n    ".join(head)
    return (
        "<!doctype html>\n"
        '<html lang="ru">\n'
        "  <head>\n"
        f"    {head_html}\n"
        "  </head>\n"
        "  <body>\n"
        f"{body_html}\n"
        "  </body>\n"
        "</html>\n"
    )


def _location_body(payload: dict[str, Any], *, events_log: bool) -> str:
    name = escape(str(payload.get("name") or "Локация"))
    city = payload.get("city")
    stats = payload.get("stats") or {}

    rows: list[str] = [f"    <h1>{name}</h1>"]
    if city:
        rows.append(f"    <p>{escape(str(city))}</p>")

    facts: list[tuple[str, str]] = []
    if stats.get("events_count"):
        facts.append(("Стартов", str(stats["events_count"])))
    if stats.get("finishers_total"):
        facts.append(("Финишей", str(stats["finishers_total"])))
    if stats.get("unique_participants"):
        facts.append(("Уникальных участников", str(stats["unique_participants"])))
    if stats.get("unique_volunteers"):
        facts.append(("Уникальных волонтёров", str(stats["unique_volunteers"])))
    avg_display = _strip_leading_hours(stats.get("avg_finish_time_display"))
    if avg_display:
        facts.append(("Среднее время", avg_display))
    median_display = _strip_leading_hours(stats.get("median_finish_time_display"))
    if median_display:
        facts.append(("Медианное время", median_display))

    attendance = stats.get("attendance_record") or {}
    if attendance.get("finishers"):
        value = str(attendance["finishers"])
        when = attendance.get("event_date")
        if when:
            value = f"{value} ({when})"
        facts.append(("Рекорд посещаемости", value))

    records = stats.get("course_records") or {}
    for key, label in (("male", "Рекорд трассы, мужчины"), ("female", "Рекорд трассы, женщины")):
        record = records.get(key) or {}
        display = _strip_leading_hours(record.get("finish_time_display"))
        if not display:
            continue
        runner = record.get("runner_name")
        value = f"{display} — {runner}" if runner else display
        facts.append((label, value))

    if facts:
        items = "".join(
            f"      <li>{escape(label)}: {escape(value)}</li>\n" for label, value in facts
        )
        rows.append("    <ul>\n" + items + "    </ul>")

    platforms = payload.get("platforms") or []
    if platforms:
        eras = "".join(
            "      <li>{code}: {first} — {last}, {count} стартов</li>\n".format(
                code=escape(str(p.get("platform_code") or "")),
                first=escape(str(p.get("first_event_date") or "")),
                last=escape(str(p.get("last_event_date") or "")),
                count=escape(str(p.get("events_count") or 0)),
            )
            for p in platforms
        )
        rows.append("    <h2>История систем</h2>\n    <ul>\n" + eras + "    </ul>")

    slug = escape(str(payload.get("slug") or ""))
    if events_log:
        rows.append(f'    <p><a href="/locations/{slug}">Страница локации</a></p>')
    else:
        rows.append(f'    <p><a href="/locations/{slug}/events">Журнал протоколов</a></p>')

    return "\n".join(rows)


def _generic_body(meta: PageMeta) -> str:
    return f"    <h1>{escape(meta.title)}</h1>\n    <p>{escape(meta.description)}</p>"


def render_prerendered_page(db: Session, raw_path: str) -> str:
    """HTML для робота: мета-теги плюс настоящий текст страницы.

    Человек сюда не попадает — ветка по User-Agent живёт в nginx. Ошибку БД
    наверх не пускаем: лучше отдать роботу страницу с одними мета-тегами, чем
    500 (иначе неудачный запрос читается как «сайт сломан»).
    """
    path = normalize_path(raw_path)
    canonical = site_base_url() + ("" if path == "/" else path)

    events_match = _LOCATION_EVENTS_RE.match(path)
    location_match = _LOCATION_RE.match(path)
    if events_match or location_match:
        slug = (events_match or location_match).group(1)  # type: ignore[union-attr]
        try:
            payload = build_location_page(db, slug)
        except Exception:  # noqa: BLE001 — робот получит родовую страницу, не 500
            payload = None
        if payload is not None:
            meta = build_location_meta(payload, events_log=bool(events_match))
            return _render_html(
                meta=meta,
                canonical=canonical,
                body_html=_location_body(payload, events_log=bool(events_match)),
            )

    meta = resolve_page_meta(path)
    return _render_html(meta=meta, canonical=canonical, body_html=_generic_body(meta))
