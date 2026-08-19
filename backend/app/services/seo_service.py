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

import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from html import escape
from pathlib import Path
from typing import Any, cast
from xml.sax.saxutils import escape as xml_escape

from sqlalchemy.orm import Session

from app.config import get_settings
from app.services.location_page_service import build_location_page, build_locations_index

logger = logging.getLogger(__name__)

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
    # «5 верст личный кабинет» — 2472 запроса/мес (Вордстат, июль 2026).
    # Решение Дмитрия 06.08.2026: за этот запрос боремся — сайт и есть личный
    # кабинет участника субботних стартов. Поэтому /login индексируется и
    # называет себя «личный кабинет участника», а не просто «вход».
    "/login": _meta(
        "Личный кабинет участника — вход — run5k.run",
        "Личный кабинет участника субботних пробежек: история стартов и волонтёрств "
        "5 вёрст, С95, parkrun и RunPark, рекорды и достижения. Вход через VK или Яндекс.",
        indexable=True,
    ),
    # Каталог ловит запросы без названия парка — «5 вёрст карта», «5 вёрст
    # результаты»: перечисляем системы, иначе страница не связывается с ними.
    "/locations": _meta(
        "Локации 5 вёрст, С95, parkrun и RunPark — каталог площадок — run5k.run",
        "Все площадки субботних пробежек на одной карте: сколько было стартов и "
        "финишей, когда прошёл первый забег, в каких системах живёт локация.",
        indexable=True,
    ),
    # Посадочная под запросы «5 вёрст результаты» (решение Дмитрия 06.08.2026):
    # свежие протоколы всех площадок разом, а не по одной локации.
    "/results": _meta(
        "Результаты 5 вёрст, С95 и RunPark — последняя суббота — run5k.run",
        "Результаты последней субботы по всем паркам: сколько финишёров и волонтёров "
        "было на каждой площадке, лучшие времена, новички и дата последнего старта.",
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
        "Беговой туризм в цифрах: кто пробежал на наибольшем числе разных локаций.",
        indexable=True,
    ),
    "/ratings/volunteer-locations": _meta(
        "Рейтинг волонтёрского туризма — run5k.run",
        "Кто волонтёрил на наибольшем числе разных локаций субботних пробежек.",
        indexable=True,
    ),
    "/ratings/openings": _meta(
        "Рейтинг открытий локаций — run5k.run",
        "Первопроходцы субботних пробежек: кто чаще всех бывал на торжественном "
        "открытии новых локаций 5 вёрст, С95, parkrun и RunPark.",
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
        "по уникальным локациям.",
        indexable=True,
    ),
    "/ratings/win-locations": _meta(
        "Рейтинг побед по локациям — run5k.run",
        "На скольких разных локациях участники успевали финишировать первыми.",
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
    profile = _PROFILE_RE.match(path)
    if profile:
        return _meta(
            "Участник — run5k.run",
            "Страница участника субботних пробежек: пробежки, волонтёрство, "
            "достижения и посещённые локации.",
            # Карточка участника индексируется с 15.08.2026 (иначе ВК и Telegram
            # показывают превью без картинки); вкладки — срезы той же страницы.
            indexable=profile.group(2) is None,
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


def _num(value: int) -> str:
    """21581 → «21 581» неразрывным пробелом.

    Сплошная цифра в описании читается как одно длинное число и на странице,
    и в выдаче поисковика. Разделитель неразрывный, чтобы разряды не
    разъезжались по строкам. Зеркало num из frontend/src/lib/pageMeta.ts.
    """
    return f"{value:,}".replace(",", " ")


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


# Как система называется в заголовке страницы. Зеркало platformCodeLabel из
# frontend/src/lib/format.ts.
PLATFORM_LABELS = {
    "five_verst": "5 вёрст",
    "s95": "С95",
    "parkrun": "parkrun",
    "runpark": "RunPark",
}

# Потолок заголовка. Яндекс и Google обрезают примерно здесь, а обрезанный
# заголовок в выдаче выглядит как ошибка. Медиана наших локаций укладывается,
# длинные («Чертаново Кировоградские пруды, Москва») ужимаются — см.
# _location_title: сначала уходит описательный хвост, город держим до конца,
# потому что его ищут («5 вёрст владивосток»).
TITLE_BUDGET = 70

# Потолок описания — та же логика, что у заголовка.
DESCRIPTION_BUDGET = 160


def _active_platform_label(payload: dict[str, Any]) -> str | None:
    """Название текущей системы локации: «5 вёрст», «parkrun», …

    Именно эту связку набирают в поиске — «5 вёрст мещерский», «5 вёрст
    геленджик». У локации может быть несколько эпох (parkrun → RunPark →
    5 вёрст); берём действующую, а у закрытых — последнюю по дате.
    """
    platforms = payload.get("platforms") or []
    if not platforms:
        return None

    active = [p for p in platforms if p.get("is_active")]
    if active:
        return PLATFORM_LABELS.get(str(active[0].get("platform_code") or ""))

    # Действующей нет (локация закрыта) — называем ту систему, при которой она
    # работала последней: «parkrun Ekaterinburg» ищут и после закрытия.
    def _last_date(platform: dict[str, Any]) -> str:
        return str(platform.get("last_event_date") or "")

    newest = max(platforms, key=_last_date)
    return PLATFORM_LABELS.get(str(newest.get("platform_code") or ""))


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
    platform = _active_platform_label(payload)
    where = _location_headline(name, city, platform)

    stats = payload.get("stats") or {}
    events_count = int(stats.get("events_count") or 0)
    finishers_total = int(stats.get("finishers_total") or 0)

    parts: list[str] = []
    if events_count:
        parts.append(
            f"{_num(events_count)} {_plural(events_count, 'старт', 'старта', 'стартов')}"
        )
    if finishers_total:
        parts.append(
            f"{_num(finishers_total)} {_plural(finishers_total, 'финиш', 'финиша', 'финишей')}"
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
        # «журнал протоколов» здесь не хвост, а то, что отличает эту страницу
        # от основной, — поэтому он часть head и не отбрасывается никогда.
        title = _fit_title(f"{where}: журнал протоколов")
        description = _describe(
            f"Все старты локации «{name}»",
            numbers,
            ". Дата, номер события, финишёры, волонтёры и лучшее время дня.",
            ". Дата, номер, финишёры и лучшее время дня.",
        )
        return _meta(title, description, indexable=True)

    title = _fit_title(where, " — результаты и статистика", " — результаты")
    # Систему называем и в описании — но один раз и по-человечески, в роли
    # подлежащего, а не списком ключевых слов.
    lead = f"{platform}, «{name}»" if platform else f"Локация «{name}»"
    if city:
        lead += f" ({city})"
    description = _describe(
        lead,
        numbers,
        ". Результаты субботних забегов, посещаемость и рейтинги участников.",
        ". Результаты забегов и рейтинги участников.",
    )
    return _meta(title, description, indexable=True)


def build_profile_meta(user: Any, payload: dict[str, Any] | None) -> PageMeta:
    """Мета-теги публичного профиля участника — с его цифрами.

    Приватный профиль сюда не попадает (см. render_prerendered_page): для него
    остаётся родовая мета без единой цифры. Профиль всегда noindex — в поиске
    личным страницам делать нечего (решение Дмитрия 02.08.2026), но
    разворачивание ссылки в чате мета всё равно определяет.
    """
    name = (getattr(user, "display_name", None) or "").strip() or "Участник"
    stats = (payload or {}).get("stats") or {}
    analytics = stats.get("analytics") or {}

    total_runs = int(stats.get("total_runs") or 0)
    total_volunteering = int(stats.get("total_volunteering") or 0)
    unique_locations = int(analytics.get("unique_locations") or 0)

    parts: list[str] = []
    if total_runs:
        parts.append(f"{_num(total_runs)} {_plural(total_runs, 'пробежка', 'пробежки', 'пробежек')}")
    if total_volunteering:
        parts.append(
            f"{_num(total_volunteering)} "
            f"{_plural(total_volunteering, 'волонтёрство', 'волонтёрства', 'волонтёрств')}"
        )
    if unique_locations:
        parts.append(f"{_num(unique_locations)} {_plural(unique_locations, 'локация', 'локации', 'локаций')}")

    # head — само имя: его не режем никогда, хвост уходит первым при нехватке
    # бюджета (у длинных имён останется «Имя — run5k.run»).
    title = _fit_title(name, " — статистика субботних пробежек", " — статистика")
    description = _describe(
        name,
        ", ".join(parts),
        ". Пробежки, волонтёрство, личные рекорды и карта посещённых локаций.",
        ". Пробежки, волонтёрство и личные рекорды.",
    )
    # indexable=True с 15.08.2026 (решение Дмитрия): под noindex ВКонтакте и
    # Telegram строят урезанную карточку — заголовок и описание, но БЕЗ
    # картинки (проверено на живых ссылках: локация с index показывает постер,
    # профиль с noindex — нет). Превью профиля важнее экономии бюджета обхода;
    # сам обход вкладок по-прежнему закрыт в robots.txt.
    return _meta(title, description, indexable=True)


def _describe(lead: str, numbers: str, *tails: str) -> str:
    """Описание под 160 символов: цифры важнее хвоста, хвост укорачиваем.

    Раньше слишком длинный текст резался по границе предложения и хвост
    пропадал целиком, оставляя треть бюджета пустой. Теперь для хвоста есть
    короткий вариант, и место используется до конца.
    """
    head = f"{lead}: {numbers}" if numbers else lead
    for tail in tails:
        candidate = f"{head}{tail}"
        if len(candidate) <= DESCRIPTION_BUDGET:
            return candidate
    # Не влезли даже с коротким хвостом — отдаём то, что помещается, целыми
    # предложениями (обрыв на полуслове в выдаче выглядит хуже).
    return _fit_description(head if head.endswith(".") else f"{head}.")


def location_lead_sentences(payload: dict[str, Any]) -> list[str]:
    """Вводные предложения о локации, собранные из уже посчитанных данных.

    Не выдумка и не «текст ради поисковика»: каждое утверждение — пересказ
    цифр, которые и так есть на странице. Зеркало на клиенте —
    locationLeadSentences в frontend/src/lib/pageMeta.ts; текст обязан
    совпадать, иначе робот и человек видят разные страницы.
    """
    name = str(payload.get("name") or "Локация")
    city = str(payload.get("city") or "").strip()
    platform = _active_platform_label(payload)
    stats = payload.get("stats") or {}

    where = f"«{name}» ({city})" if city else f"«{name}»"
    if platform:
        first = f"{where} — площадка субботних пробежек {platform}."
    else:
        first = f"{where} — площадка субботних пробежек."
    sentences = [first]

    events_count = int(stats.get("events_count") or 0)
    finishers_total = int(stats.get("finishers_total") or 0)
    if events_count and finishers_total:
        sentences.append(
            f"Здесь прошло {_num(events_count)} "
            f"{_plural(events_count, 'старт', 'старта', 'стартов')}, "
            f"финишировали {_num(finishers_total)} "
            f"{_plural(finishers_total, 'участник', 'участника', 'участников')}."
        )

    # У локации может быть несколько эпох: parkrun → RunPark → 5 вёрст.
    # Прежние системы называем — их тоже ищут вместе с названием парка.
    platforms = payload.get("platforms") or []
    previous = [
        PLATFORM_LABELS.get(str(p.get("platform_code") or ""))
        for p in platforms
        if not p.get("is_active") and int(p.get("events_count") or 0) > 0
    ]
    previous_named = [label for label in previous if label]
    if platform and previous_named:
        joined = " и ".join(
            [", ".join(previous_named[:-1]), previous_named[-1]]
            if len(previous_named) > 1
            else previous_named
        )
        sentences.append(f"До {platform} старты здесь проводили {joined}.")

    return sentences


def _location_headline(name: str, city: Any, platform: str | None) -> str:
    """«5 вёрст Мещерское озеро, Нижний Новгород» — основа заголовка.

    Город не приписываем, если он уже внутри названия («Томск Сосновый Бор»):
    задвоение читается как опечатка и съедает бюджет длины.
    """
    head = f"{platform} {name}" if platform else name
    city_text = str(city).strip() if city else ""
    if city_text and city_text.casefold() not in head.casefold():
        head = f"{head}, {city_text}"
    return head


def _fit_title(head: str, *tails: str) -> str:
    """Собирает заголовок под TITLE_BUDGET, жертвуя хвостами слева направо.

    head (система + локация + город) не режем никогда: это и есть то, что
    ищут. Первым уходит описательный хвост, последним — бренд.
    """
    brand = f" — {SITE_NAME}"
    for tail in (*tails, ""):
        candidate = f"{head}{tail}{brand}"
        if len(candidate) <= TITLE_BUDGET:
            return candidate
    # Даже «локация + бренд» не влезли — бренд отбрасываем, домен и так виден
    # в выдаче строкой адреса.
    return head


def _fit_description(description: str) -> str:
    """Ужимает описание до 160 символов по границе предложения.

    Обрыв на полуслове в выдаче выглядит хуже, чем более короткий, но целый
    текст, поэтому отрезаем по последней точке, а не по символу.
    """
    limit = DESCRIPTION_BUDGET
    if len(description) <= limit:
        return description
    cut = description[:limit]
    dot = cut.rfind(". ")
    if dot > limit // 2:
        return cut[: dot + 1]
    return cut.rstrip(" ,;:—-") + "…"


# --------------------------------------------------------------------------
# sitemap.xml и robots.txt
# --------------------------------------------------------------------------

# Статические адреса в sitemap. Порядок = порядок в файле, приоритет — грубая
# подсказка роботу, что важнее. Локации добавляются отдельно, из каталога.
_SITEMAP_STATIC: tuple[tuple[str, str], ...] = (
    ("/", "1.0"),
    ("/locations", "0.9"),
    ("/results", "0.9"),
    ("/ratings", "0.8"),
    ("/ratings/runs", "0.7"),
    ("/ratings/volunteering", "0.7"),
    ("/ratings/volunteer-roles", "0.6"),
    ("/ratings/locations", "0.7"),
    ("/ratings/volunteer-locations", "0.6"),
    ("/ratings/openings", "0.6"),
    ("/ratings/wins", "0.7"),
    ("/ratings/win-locations", "0.6"),
    ("/ratings/home-distance", "0.6"),
    ("/blog", "0.7"),
    ("/about", "0.6"),
    ("/login", "0.4"),
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

    Вкладки профиля закрыты от обхода (13.08.2026): робот скачивал сотни
    адресов вида /users/768/maps, и каждый съедал краулинговый бюджет,
    которого не хватает страницам локаций. Приток усилился после включения
    «обхода по счётчикам» Метрики: люди ходят в свои кабинеты, робот идёт
    следом.

    Сами карточки участников (/users/12) с 15.08.2026 открыты: под запретом
    обхода ВКонтакте и Telegram показывают превью без картинки, а живое
    превью со статистикой — то, ради чего профилями делятся.

    /world остаётся открытым для обхода: он один, бюджета не жжёт, а noindex
    в самой странице сохраняет вес исходящих ссылок.
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
        # Вкладки профиля (/users/12/runs, /maps, …) — сотни адресов на
        # участника, они и жгли бюджет обхода. Сама карточка /users/12
        # открыта с 15.08.2026: под запретом обхода превью-краулеры ВК и
        # Telegram картинку не показывают.
        "Disallow: /users/*/",
        # /login сознательно НЕ закрыт: «5 верст личный кабинет» — 2472
        # запроса/мес, и страница входа — наша посадочная под них.
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


# Размер OG-картинок: стандарт превью Telegram/VK/Facebook.
OG_IMAGE_WIDTH = 1200
OG_IMAGE_HEIGHT = 630

# Дефолтная брендовая карточка: лежит в статике фронта (frontend/public/og/),
# показывается для всех страниц без собственной картинки.
DEFAULT_OG_IMAGE_PATH = "/og/default.png"


def location_og_image_url(payload: dict[str, Any]) -> str | None:
    """Адрес прегенерированной OG-картинки локации, если файл уже отрендерен.

    Картинки складывает celery-задача og_render (очередь parkrun — только там
    есть Chromium) в settings.og_image_dir; nginx раздаёт их как /og/locations/*.
    Версия в query — дата последнего старта: Telegram кэширует превью надолго,
    новый URL сбрасывает кэш после каждой субботы.
    """
    slug = str(payload.get("slug") or "")
    if not slug:
        return None
    image_path = Path(get_settings().og_image_dir) / "locations" / f"{slug}.png"
    if not image_path.is_file():
        return None
    stats = payload.get("stats") or {}
    last_event = stats.get("last_event_date")
    version = last_event.isoformat() if isinstance(last_event, date) else str(last_event or "")
    suffix = f"?v={version}" if version else ""
    return f"{site_base_url()}/og/locations/{slug}.png{suffix}"


def profile_handle(user: Any) -> str:
    """Хэндл для адресов профиля: vanity-slug, иначе номер участника."""
    slug = (getattr(user, "public_slug", None) or "").strip()
    return slug or str(getattr(user, "serial_id", "") or "")


def profile_og_image_url(user: Any) -> str | None:
    """Адрес прегенерированной OG-картинки участника, если файл отрендерен.

    Файлы именуются по serial_id (vanity-slug можно сменить, номер — нет),
    раздаются nginx как /og/users/*. Версия в query — дата последней
    активности: после каждой субботы адрес меняется, и Telegram перезабирает
    превью вместо показа прошлогодних цифр.
    """
    serial_id = getattr(user, "serial_id", None)
    if serial_id is None:
        return None
    image_path = Path(get_settings().og_image_dir) / "users" / f"{serial_id}.png"
    if not image_path.is_file():
        return None
    version = ""
    updated = getattr(user, "updated_at", None)
    if updated is not None:
        version = updated.date().isoformat() if hasattr(updated, "date") else str(updated)
    suffix = f"?v={version}" if version else ""
    return f"{site_base_url()}/og/users/{serial_id}.png{suffix}"


def _og_image_tags(og_image_url: str | None, *, alt: str | None = None) -> list[str]:
    """og:image страницы: своя картинка либо дефолтная брендовая.

    twitter:card=summary_large_image — иначе Telegram/X показывают картинку
    мелкой иконкой сбоку вместо полноширинного превью.

    Полный набор подтегов (19.08.2026, ВК показывал миниатюру вместо большой
    картинки): og:image:secure_url — часть парсеров ищет именно его и без
    него считает картинку небезопасной; og:image:type — снимает необходимость
    угадывать формат по ответу; og:image:alt — подпись, её же некоторые
    площадки используют как заголовок карточки. Формат сниппета в итоге
    выбирает сам ВК, но эти теги убирают все поводы показать миниатюру.
    """
    url = og_image_url or f"{site_base_url()}{DEFAULT_OG_IMAGE_PATH}"
    tags = [
        f'<meta property="og:image" content="{escape(url, quote=True)}">',
        f'<meta property="og:image:secure_url" content="{escape(url, quote=True)}">',
        '<meta property="og:image:type" content="image/png">',
        f'<meta property="og:image:width" content="{OG_IMAGE_WIDTH}">',
        f'<meta property="og:image:height" content="{OG_IMAGE_HEIGHT}">',
        '<meta name="twitter:card" content="summary_large_image">',
    ]
    if alt:
        tags.insert(5, f'<meta property="og:image:alt" content="{escape(alt, quote=True)}">')
    return tags


def _last_event_block(last_event: dict[str, Any]) -> str:
    """«Последний старт: дата, финишёры, лучшие времена дня» — списком."""
    when = escape(str(last_event.get("event_date") or ""))
    platform = PLATFORM_LABELS.get(str(last_event.get("platform_code") or ""), "")
    title = f"Последний старт: {when}"
    if platform:
        title += f" ({escape(platform)})"

    facts: list[tuple[str, str]] = []
    finishers = last_event.get("finishers")
    if finishers:
        facts.append(("Финишировали", _num(int(finishers))))
    volunteers = last_event.get("volunteers")
    if volunteers:
        facts.append(("Волонтёров", _num(int(volunteers))))
    for key, label in (
        ("best_male_time_display", "Лучшее время, мужчины"),
        ("best_female_time_display", "Лучшее время, женщины"),
        ("avg_time_display", "Среднее время"),
    ):
        value = _strip_leading_hours(last_event.get(key))
        if value:
            facts.append((label, value))
    newcomers = (last_event.get("debutants") or 0) + (last_event.get("first_at_location") or 0)
    if newcomers:
        facts.append(("Впервые здесь", _num(int(newcomers))))
    prs = last_event.get("prs")
    if prs:
        facts.append(("Личных рекордов", _num(int(prs))))

    items = "".join(f"      <li>{escape(k)}: {escape(v)}</li>\n" for k, v in facts)
    return f"    <h2>{title}</h2>\n    <ul>\n{items}    </ul>"


def _breadcrumbs(*crumbs: tuple[str, str]) -> dict[str, Any]:
    """Хлебные крошки для поисковика: «Главная › Локации › Бутово»."""
    base = site_base_url()
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i, "name": name, "item": base + path}
            for i, (name, path) in enumerate(crumbs, start=1)
        ],
    }


def location_json_ld(payload: dict[str, Any], *, events_log: bool = False) -> list[dict[str, Any]]:
    """Разметка страницы локации: площадка + последний старт + крошки.

    SportsActivityLocation — площадка (адрес, координаты, ссылка на страницу
    системы). SportsEvent добавляем только если последний старт действительно
    известен: разметка обязана описывать то, что есть на странице.
    """
    base = site_base_url()
    slug = str(payload.get("slug") or "")
    name = str(payload.get("name") or "Локация")
    platform = _active_platform_label(payload)
    city = str(payload.get("city") or "").strip()
    display_name = f"{platform} {name}" if platform else name

    place: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "SportsActivityLocation",
        "name": display_name,
        "url": f"{base}/locations/{slug}",
        "sport": "Running",
    }
    address: dict[str, Any] = {"@type": "PostalAddress"}
    if city:
        address["addressLocality"] = city
    region = str(payload.get("region") or "").strip()
    if region:
        address["addressRegion"] = region
    country = str(payload.get("country") or "").strip()
    if country:
        address["addressCountry"] = country
    if len(address) > 1:
        place["address"] = address
    latitude, longitude = payload.get("latitude"), payload.get("longitude")
    if latitude is not None and longitude is not None:
        place["geo"] = {
            "@type": "GeoCoordinates",
            "latitude": latitude,
            "longitude": longitude,
        }
    # Ссылка на официальную страницу системы: sameAs связывает нашу страницу
    # с первоисточником, а не выдаёт её за него.
    official = [
        str(item.get("url"))
        for item in payload.get("platforms") or []
        if item.get("is_active") and item.get("url")
    ]
    if official:
        place["sameAs"] = official

    objects: list[dict[str, Any]] = [place]

    last_event = (payload.get("stats") or {}).get("last_event") or {}
    when = last_event.get("event_date")
    if when:
        event: dict[str, Any] = {
            "@context": "https://schema.org",
            "@type": "SportsEvent",
            "name": f"{display_name}: старт {when}",
            "startDate": str(when),
            "eventStatus": "https://schema.org/EventScheduled",
            "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
            "location": {"@type": "Place", "name": display_name, **(
                {"address": place["address"]} if "address" in place else {}
            )},
            "url": f"{base}/locations/{slug}",
        }
        # Числа старта кладём в description, а не в maximumAttendeeCapacity:
        # то поле означает вместимость площадки, а у нас фактические финишёры.
        # Разметка, выдающая одно за другое, — прямой путь под фильтр.
        finishers = last_event.get("finishers")
        if finishers:
            summary = f"Финишировали: {finishers}"
            best = _strip_leading_hours(last_event.get("best_male_time_display"))
            if best:
                summary += f". Лучшее время дня: {best}"
            event["description"] = summary + "."
        objects.append(event)

    crumbs = [("Главная", "/"), ("Локации", "/locations"), (name, f"/locations/{slug}")]
    if events_log:
        crumbs.append(("Журнал протоколов", f"/locations/{slug}/events"))
    objects.append(_breadcrumbs(*crumbs))
    return objects


def catalog_json_ld(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Разметка каталога: список площадок + крошки."""
    base = site_base_url()
    live = [i for i in items if not i.get("is_cancelled")]
    listing = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": "Локации субботних пробежек",
        "numberOfItems": len(live),
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": position,
                "name": str(item.get("name") or ""),
                "url": f"{base}/locations/{item.get('slug')}",
            }
            for position, item in enumerate(
                sorted(live, key=lambda i: str(i.get("name") or "").casefold()), start=1
            )
        ],
    }
    return [listing, _breadcrumbs(("Главная", "/"), ("Локации", "/locations"))]


def _json_ld_scripts(objects: list[dict[str, Any]]) -> list[str]:
    """Микроразметка Schema.org отдельными <script> на каждый объект.

    Зачем: до 13.08.2026 разметки не было вовсе — Яндекс разбирал страницу
    локации как обычный текст и не знал, что перед ним спортивная площадка с
    адресом, координатами и регулярными стартами. Разметка описывает ровно то,
    что есть на странице: выдумывать данные ради красивого сниппета нельзя —
    за расхождение разметки и содержимого поисковики наказывают.

    ensure_ascii=False — кириллица остаётся читаемой; </ экранируем, иначе
    строка внутри JSON может закрыть сам тег script.
    """
    scripts = []
    for obj in objects:
        payload = json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")
        scripts.append(f'<script type="application/ld+json">{payload}</script>')
    return scripts


def _render_html(
    *,
    meta: PageMeta,
    canonical: str,
    body_html: str,
    og_image_url: str | None = None,
    json_ld: list[dict[str, Any]] | None = None,
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
        # alt описывает картинку, а не сайт, поэтому бренд из хвоста убираем:
        # «5 вёрст Мещерский, Одинцово — результаты и статистика».
        *_og_image_tags(og_image_url, alt=meta.title.removesuffix(f" — {SITE_NAME}")),
        *_json_ld_scripts(json_ld or []),
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


def _paragraph_rows(text: str, indent: str = "    ") -> list[str]:
    return [f"{indent}<p>{escape(part.strip())}</p>" for part in text.split("\n\n") if part.strip()]


def _location_description_rows(payload: dict[str, Any]) -> list[str]:
    """Описание площадки — цитата с сайта системы: где и когда, трасса, дорога.

    Зеркало подблока LocationDescriptionQuote в LocationPage.tsx: заголовок сразу
    называет источник, дальше те же подзаголовки в том же порядке. Текст не наш,
    и подписать его — и честно, и полезно (внешняя ссылка на первоисточник
    роботу тоже понятна).
    """

    description = payload.get("description") or {}
    if not description:
        return []

    schedule_text = str(description.get("schedule_text") or "").strip()
    course_text = str(description.get("course_text") or "").strip()
    travel_text = str(description.get("travel_text") or "").strip()
    travel_sections = [
        section for section in (description.get("travel_sections") or []) if str(section.get("text") or "").strip()
    ]
    if not (schedule_text or course_text or travel_text or travel_sections):
        return []

    platform = PLATFORM_LABELS.get(str(description.get("platform_code") or ""))
    source_url = str(description.get("source_url") or "").strip()
    heading = f"Описание с официального сайта {platform}" if platform else "Описание с официального сайта системы"
    if source_url:
        heading_html = (
            f'{escape(heading)} — <a href="{escape(source_url)}" rel="nofollow noopener">{escape(source_url)}</a>'
        )
    else:
        heading_html = escape(heading)
    rows: list[str] = [f"    <h2>{heading_html}</h2>"]

    if schedule_text:
        rows.append("    <h3>Где и когда</h3>")
        rows.extend(_paragraph_rows(schedule_text))
    if course_text:
        rows.append("    <h3>Трасса</h3>")
        rows.extend(_paragraph_rows(course_text))

    if travel_text or travel_sections:
        rows.append("    <h3>Как добраться</h3>")
        if travel_text:
            rows.extend(_paragraph_rows(travel_text))
        for section in travel_sections:
            title = str(section.get("title") or "").strip()
            if title:
                rows.append(f"    <h4>{escape(title)}</h4>")
            rows.extend(_paragraph_rows(str(section.get("text") or "").strip()))

    links = description.get("links") or []
    if links:
        items = "".join(
            '      <li><a href="{url}" rel="nofollow noopener">{title}</a></li>\n'.format(
                url=escape(str(link.get("url") or "")),
                title=escape(str(link.get("title") or "Ссылка")),
            )
            for link in links
            if link.get("url")
        )
        rows.append("    <ul>\n" + items + "    </ul>")

    return rows


def _location_body(payload: dict[str, Any], *, events_log: bool) -> str:
    name = escape(str(payload.get("name") or "Локация"))
    stats = payload.get("stats") or {}

    rows: list[str] = [f"    <h1>{name}</h1>"]
    # Вводный абзац вместо голого названия города. Роботу нужен связный текст:
    # по списку цифр он не понимает, о чём страница, а «площадка субботних
    # пробежек 5 вёрст» ставит название системы рядом с названием парка —
    # ровно так, как их набирают в поиске.
    lead = location_lead_sentences(payload)
    for sentence in lead:
        rows.append(f"    <p>{escape(sentence)}</p>")

    facts: list[tuple[str, str]] = []
    if stats.get("events_count"):
        facts.append(("Стартов", _num(int(stats["events_count"]))))
    if stats.get("finishers_total"):
        facts.append(("Финишей", _num(int(stats["finishers_total"]))))
    if stats.get("unique_participants"):
        facts.append(("Уникальных участников", _num(int(stats["unique_participants"]))))
    if stats.get("unique_volunteers"):
        facts.append(("Уникальных волонтёров", _num(int(stats["unique_volunteers"]))))
    avg_display = _strip_leading_hours(stats.get("avg_finish_time_display"))
    if avg_display:
        facts.append(("Среднее время", avg_display))
    median_display = _strip_leading_hours(stats.get("median_finish_time_display"))
    if median_display:
        facts.append(("Медианное время", median_display))

    attendance = stats.get("attendance_record") or {}
    if attendance.get("finishers"):
        value = _num(int(attendance["finishers"]))
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

    # Результаты последнего старта — то, ради чего чаще всего и приходят
    # («5 вёрст мещерский результаты»). Раньше робот их не видел вовсе:
    # данные были только в API, в HTML уходили одни агрегаты за всю историю.
    last_event = stats.get("last_event") or {}
    if last_event.get("event_date"):
        rows.append(_last_event_block(last_event))

    platforms = payload.get("platforms") or []
    if platforms:
        eras = "".join(
            "      <li>{code}: {first} — {last}, {count} стартов</li>\n".format(
                code=escape(str(p.get("platform_code") or "")),
                first=escape(str(p.get("first_event_date") or "")),
                last=escape(str(p.get("last_event_date") or "")),
                count=escape(_num(int(p.get("events_count") or 0))),
            )
            for p in platforms
        )
        rows.append("    <h2>История систем</h2>\n    <ul>\n" + eras + "    </ul>")

    # Описание трассы и дорога до старта — единственный на странице текст,
    # который человек ищет словами, а не цифрами («как добраться до 5 вёрст
    # <парк>»). Стоит после истории систем — там же, где его видит человек:
    # на странице это отдельный подблок-цитата в самом низу карточки
    # «О площадке» (LocationPage.tsx, LocationDescriptionQuote). В журнале
    # протоколов его нет намеренно: один и тот же текст на двух адресах —
    # дубль, а не польза.
    if not events_log:
        rows.extend(_location_description_rows(payload))

    # Кластер города: те же ссылки, что человек видит в блоке «Другие
    # площадки в …» — под запросы вида «5 вёрст тюмень».
    city_locations = payload.get("city_locations") or []
    city = payload.get("city")
    if city_locations and city:
        neighbours = "".join(
            '      <li><a href="/locations/{slug}">{name}</a></li>\n'.format(
                slug=escape(str(item.get("slug") or "")),
                name=escape(str(item.get("name") or "")),
            )
            for item in city_locations
        )
        rows.append(
            f"    <h2>{escape(str(city))}: другие площадки</h2>\n    <ul>\n"
            + neighbours
            + "    </ul>"
        )

    slug = escape(str(payload.get("slug") or ""))
    if events_log:
        rows.append(f'    <p><a href="/locations/{slug}">Страница локации</a></p>')
    else:
        rows.append(f'    <p><a href="/locations/{slug}/events">Журнал протоколов</a></p>')

    return "\n".join(rows)


def _generic_body(meta: PageMeta) -> str:
    return f"    <h1>{escape(meta.title)}</h1>\n    <p>{escape(meta.description)}</p>"


def _catalog_body(items: list[dict[str, Any]]) -> str:
    """Тело каталога для робота: вводные фразы и список площадок ссылками.

    «5 верст парки» — 1919 запросов/мес, «5 верст карта» — 302, «5 верст
    локации» — 102 (Вордстат, июль 2026). До этого робот получал на /locations
    два служебных предложения: сам список рисует JS, и главный контент раздела
    для поисковика не существовал. Список здесь — то же, что человек видит в
    таблице каталога, просто в статике; заодно каждое название города в тексте
    отвечает хвосту «5 вёрст [город]».
    """
    live = [i for i in items if not i.get("is_cancelled")]
    cities = {str(i.get("city")).strip() for i in live if i.get("city")}
    by_platform: dict[str, int] = {}
    for item in live:
        for code in item.get("platform_codes") or []:
            by_platform[str(code)] = by_platform.get(str(code), 0) + 1

    rows = [
        "    <h1>Локации 5 вёрст, С95, parkrun и RunPark</h1>",
        "    <p>Каталог площадок субботних пробежек: "
        f"{_num(len(live))} {_plural(len(live), 'локация', 'локации', 'локаций')} в "
        f"{_num(len(cities))} {_plural(len(cities), 'городе', 'городах', 'городах')}.</p>",
    ]
    platform_bits = [
        f"{PLATFORM_LABELS[code]} — {_num(count)} "
        f"{_plural(count, 'площадка', 'площадки', 'площадок')}"
        for code, count in sorted(by_platform.items(), key=lambda kv: -kv[1])
        if code in PLATFORM_LABELS
    ]
    if platform_bits:
        rows.append(f"    <p>{escape('; '.join(platform_bits))}.</p>")

    items_html = "".join(
        '      <li><a href="/locations/{slug}">{name}</a>{city}</li>\n'.format(
            slug=escape(str(i.get("slug") or "")),
            name=escape(str(i.get("name") or "")),
            city=escape(f" — {i['city']}") if i.get("city") else "",
        )
        for i in sorted(live, key=lambda i: str(i.get("name") or "").casefold())
    )
    rows.append("    <ul>\n" + items_html + "    </ul>")
    return "\n".join(rows)


def _profile_body(name: str, payload: dict[str, Any]) -> str:
    stats = payload.get("stats") or {}
    analytics = stats.get("analytics") or {}
    rows: list[str] = [f"    <h1>{escape(name)}</h1>"]

    facts: list[tuple[str, str]] = []
    if stats.get("total_runs"):
        facts.append(("Пробежек", _num(int(stats["total_runs"]))))
    if stats.get("total_volunteering"):
        facts.append(("Волонтёрств", _num(int(stats["total_volunteering"]))))
    if analytics.get("unique_locations"):
        facts.append(("Уникальных локаций", _num(int(analytics["unique_locations"]))))
    if analytics.get("unique_run_regions"):
        facts.append(("Регионов", _num(int(analytics["unique_run_regions"]))))
    best = _strip_leading_hours_sec(analytics.get("best_finish_time_sec"))
    if best:
        facts.append(("Лучшее время", best))
    if analytics.get("saturday_streak"):
        facts.append(("Суббот подряд", _num(int(analytics["saturday_streak"]))))

    if facts:
        items = "".join(
            f"      <li>{escape(label)}: {escape(value)}</li>\n" for label, value in facts
        )
        rows.append("    <ul>\n" + items + "    </ul>")
    return "\n".join(rows)


def _strip_leading_hours_sec(seconds: Any) -> str | None:
    """Секунды → «23:47» (часы у пятикилометровых времён почти всегда нули)."""
    if not isinstance(seconds, (int, float)) or seconds <= 0:
        return None
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _render_profile(db: Session, *, handle: str, canonical: str) -> str | None:
    """HTML профиля для робота: имя и цифры участника.

    None — такого участника нет; вызывающий отдаст общий ответ. Скрытый
    владельцем профиль рендерится здесь же, но без цифр и с noindex.
    """
    from app.services.profile_slug_service import resolve_profile_handle

    try:
        user = resolve_profile_handle(db, handle)
    except Exception:  # noqa: BLE001 — робот получит родовую страницу, не 500
        return None
    if user is None:
        return None
    if getattr(user, "profile_private", False):
        # Скрытый профиль: ни цифр, ни своей картинки — и явный noindex, чтобы
        # индексируемость публичных карточек его не зацепила.
        hidden = _meta(
            "Участник — run5k.run",
            "Участник субботних пробежек скрыл свою страницу.",
        )
        return _render_html(meta=hidden, canonical=canonical, body_html=_generic_body(hidden))

    try:
        from app.services.admin_users_service import get_admin_user_preview_dashboard

        payload = get_admin_user_preview_dashboard(db, user.id)
    except Exception:  # noqa: BLE001
        payload = None
    if payload is None:
        return None

    meta = build_profile_meta(user, payload)
    name = (getattr(user, "display_name", None) or "").strip() or "Участник"
    image_url = profile_og_image_url(user)
    if image_url is None:
        # Картинки ещё нет (новый участник, первый шеринг) — ставим рендер в
        # очередь: сейчас превью будет с дефолтной карточкой, со следующего
        # раза — со своими цифрами.
        _enqueue_profile_image(user)
    return _render_html(
        meta=meta,
        canonical=canonical,
        body_html=_profile_body(name, payload),
        og_image_url=image_url,
    )


def _enqueue_profile_image(user: Any) -> None:
    """Ставит одиночный рендер карточки участника в очередь parkrun.

    Молча проглатывает любые ошибки: недоступный брокер не должен ломать
    выдачу страницы роботу.
    """
    serial_id = getattr(user, "serial_id", None)
    if serial_id is None:
        return
    try:
        from app.workers.tasks.og_render import og_render_user_images_task

        og_render_user_images_task.delay([str(serial_id)])
    except Exception:  # noqa: BLE001 — превью важнее фонового рендера
        logger.debug("og_render: не удалось поставить задачу для профиля %s", serial_id)


def is_known_path(raw_path: str) -> bool:
    """Существует ли такой адрес на сайте (без похода в БД).

    Нужно для честного 404: SPA-сайт по умолчанию отдаёт 200 на любой мусор,
    и Яндекс справедливо ругается «некорректно настроен возврат 404» — из-за
    этого несуществующие адреса лезут в индекс и жгут краулинговый бюджет.
    Здесь только форма адреса; существование конкретной локации проверяется
    отдельно, по БД.
    """
    path = normalize_path(raw_path)
    if path in STATIC_PAGE_META:
        return True
    if path.startswith("/admin/") or path == "/world":
        return True
    for pattern in (_PROFILE_RE, _LOCATION_EVENTS_RE, _LOCATION_RE, _SWEEP_HQ_RE):
        if pattern.match(path):
            return True
    return False


def render_prerendered_page(db: Session, raw_path: str) -> tuple[str, int]:
    """(HTML, HTTP-код) для робота: мета-теги плюс настоящий текст страницы.

    Человек сюда не попадает — ветка по User-Agent живёт в nginx. Ошибку БД
    наверх не пускаем: лучше отдать роботу страницу с одними мета-тегами, чем
    500 (иначе неудачный запрос читается как «сайт сломан»).

    Несуществующий адрес и несуществующая локация отдают 404 — раньше здесь
    было безусловное 200, и Яндекс отметил это диагностикой «некорректно
    настроен возврат 404» (11.08.2026).
    """
    path = normalize_path(raw_path)
    canonical = site_base_url() + ("" if path == "/" else path)

    profile_match = _PROFILE_RE.match(path)
    if profile_match:
        html = _render_profile(db, handle=profile_match.group(1), canonical=canonical)
        if html is not None:
            return html, 200

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
            return (
                _render_html(
                    meta=meta,
                    canonical=canonical,
                    body_html=_location_body(payload, events_log=bool(events_match)),
                    og_image_url=location_og_image_url(payload),
                    json_ld=location_json_ld(payload, events_log=bool(events_match)),
                ),
                200,
            )
        # Слаг не резолвится — такой локации нет. Именно этот случай Яндекс и
        # ловил: /locations/что-угодно отвечал 200 с пустой карточкой.
        return _not_found_page(canonical), 404

    meta = resolve_page_meta(path)

    if path == "/locations":
        try:
            items = cast(
                "list[dict[str, Any]]", build_locations_index(db).get("items") or []
            )
        except Exception:  # noqa: BLE001 — робот получит родовую страницу, не 500
            items = []
        if items:
            return (
                _render_html(
                    meta=meta,
                    canonical=canonical,
                    body_html=_catalog_body(items),
                    json_ld=catalog_json_ld(items),
                ),
                200,
            )

    if not is_known_path(path):
        return _not_found_page(canonical), 404

    return _render_html(meta=meta, canonical=canonical, body_html=_generic_body(meta)), 200


def _not_found_page(canonical: str) -> str:
    """Страница 404 для робота: noindex и ссылка обратно в каталог."""
    meta = _meta(
        "Страница не найдена — run5k.run",
        "Такой страницы на run5k.run нет. Загляните в каталог площадок "
        "субботних пробежек или на главную.",
    )
    body = (
        "    <h1>Страница не найдена</h1>\n"
        "    <p>Такой страницы на run5k.run нет.</p>\n"
        '    <p><a href="/locations">Каталог локаций</a> · <a href="/">Главная</a></p>'
    )
    return _render_html(meta=meta, canonical=canonical, body_html=body)
