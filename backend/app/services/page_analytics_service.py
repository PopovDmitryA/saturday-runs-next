"""Собственная событийная аналитика страниц.

Дополняет грубые Redis-счётчики app.core.site_stats: каждое событие просмотра
пишется в Postgres (page_view_events) с разделом, сущностью (какой профиль,
какая локация) и длительностью, затем celery-задача page_stats.rollup
сворачивает события в дневные агрегаты page_stats_daily (день — московский).
Сырые события живут settings.page_events_retention_days, агрегаты — вечно.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import false, func, or_, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import Location, PageStatsDaily, PageViewEvent, Platform, User
from app.services.profile_slug_service import resolve_profile_handle

STATS_TIMEZONE = ZoneInfo("Europe/Moscow")

# Максимум, который принимаем от клиента как время на странице (защита от мусора).
MAX_DURATION_SEC = 8 * 3600

DEFAULT_PERIOD_DAYS = 30
# Эксперимент главной — единственный; зеркало HOME_EXPERIMENT из frontend/src/lib/abTest.ts.
HOME_EXPERIMENT = "home_v1"
# Нижняя граница для открытого «по такое-то число»: раньше аналитики не было.
EARLIEST_STATS_DATE = date(2026, 7, 1)

_PROFILE_RE = re.compile(r"^/users/([^/]+)$")
# Вкладка профиля: /users/{хендл}/{сегмент}. Считается обычным просмотром
# профиля — вкладка в отчёте не нужна, а хендл важен: по нему просмотр
# доресолвливается до user_id и попадает в «топ профилей».
_PROFILE_TAB_RE = re.compile(r"^/users/([^/]+)/[^/]+$")
# Служебная страница мирового обхода parkrun: /hq/{токен}. Токен в entity_key
# не кладём — он одноразовый и в отчёте бесполезен.
_SWEEP_HQ_RE = re.compile(r"^/hq/.+$")
_LOCATION_EVENTS_RE = re.compile(r"^/locations/([^/]+)/events$")
_LOCATION_PARTICIPANTS_RE = re.compile(r"^/locations/([^/]+)/participants$")
# Единый протокол недели: /protocol/{дата-субботы}. В entity_key едет дата —
# по ней видно, какие недели открывают (свежая суббота или архив).
_UNIFIED_PROTOCOL_RE = re.compile(r"^/protocol/(\d{4}-\d{2}-\d{2})$")
# Протокол старта: /locations/{slug}/protocol/{система}/{дата}. В entity_key
# едет slug — просмотры протоколов копятся к локации, как у журнала.
_LOCATION_PROTOCOL_RE = re.compile(r"^/locations/([^/]+)/protocol/[^/]+/\d{4}-\d{2}-\d{2}$")
_LOCATION_RE = re.compile(r"^/locations/([^/]+)$")

# Одна страница приложения = один page_type. Ключ — нормализованный путь;
# канон — STATIC_ROUTES в frontend/src/App.tsx (добавили роут — добавьте строку
# сюда и в PAGE_TYPE_LABELS на фронте, иначе страница уедет в "other").
_STATIC_PAGE_TYPES = {
    # С релиза портала (июль 2026) канонические пути отдают портальные страницы.
    # Типы "landing"/"login"/"about" остались только в исторических данных.
    "/": "portal_home",
    "/about": "portal_about",
    "/blog": "portal_blog",
    "/updates": "updates",
    "/login": "portal_login",
    "/new/map-lab": "portal_map_lab",
    "/welcome": "welcome",
    "/dashboard": "dashboard",
    "/profiles": "dashboard",  # тот же компонент, что и /dashboard
    "/runs": "runs",
    "/achievements": "achievements",
    "/co-runners": "co_runners",
    "/volunteering": "volunteering",
    "/maps": "maps",
    "/locations": "locations_index",
    "/results": "last_results",
    "/protocol": "unified_protocol",
    "/history": "history",
    "/ratings": "ratings_hub",
    "/ratings/runs": "ratings_runs",
    "/ratings/volunteering": "ratings_volunteering",
    "/ratings/volunteer-roles": "ratings_volunteer_roles",
    "/ratings/locations": "ratings_locations",
    "/ratings/volunteer-locations": "ratings_volunteer_locations",
    "/ratings/openings": "ratings_openings",
    "/ratings/wins": "ratings_wins",
    "/ratings/fastest": "ratings_fastest",
    "/ratings/win-locations": "ratings_win_locations",
    "/ratings/home-distance": "ratings_home_distance",
    "/ratings/location-records": "ratings_location_records",
    "/ratings/regions": "ratings_regions",
    "/backlog": "backlog",
    "/organizer": "organizer_index",
    # Превью кабинета на демо-данных — витрина дизайна, а не раздел сайта.
    "/new/cabinet-preview": "cabinet_preview",
    "/share": "share",
    "/settings": "settings",
}

# Страницы-заглушки: сразу редиректят на другой адрес, своего содержимого нет.
# Служебные адреса кабинета времён тёмного запуска: сами ничего не показывают,
# сразу уводят на /users/{хендл}/… Держим отдельно от «прочего», иначе выглядят
# как забытый раздел, хотя это просто старые ссылки.
_REDIRECT_PATHS = frozenset(
    {
        "/sync",
        "/queue",
        "/admin",
        "/new/dashboard",
        "/new/runs",
        "/new/volunteering",
        "/new/achievements",
        "/new/co-runners",
        "/new/maps",
        "/new/history",
        "/new/share",
        "/new/settings",
        "/dashboards",
    }
)


def _is_legacy_grafana_path(raw_path: str) -> bool:
    """Зеркало isLegacyGrafanaPath из frontend/src/lib/siteBrand.ts.

    Проверяется до нормализации: "/dashboard/" (со слэшем) — это Grafana,
    а "/dashboard" — наш личный кабинет.
    """
    return raw_path.startswith(("/d/", "/dashboard/", "/public/"))


def classify_page(path: str) -> tuple[str, str]:
    """Раскладывает путь страницы в (page_type, entity_key).

    Порядок проверок повторяет renderRoute в App.tsx — раскладка обязана
    совпадать с тем, что реально отрисовалось. Неизвестный путь не теряется:
    уходит в "other" вместе с путём в entity_key, чтобы забытый раздел был
    виден в админке, а не пропадал молча.

    entity_key — сырой ключ из URL (хэндл профиля, slug локации); для профилей
    он дорезолвливается до user_id в record_page_view.
    """
    raw = (path or "/").split("?", 1)[0].split("#", 1)[0]
    if _is_legacy_grafana_path(raw):
        return "legacy_grafana", raw[:128]

    normalized = raw.rstrip("/") or "/"

    static = _STATIC_PAGE_TYPES.get(normalized)
    if static is not None:
        return static, ""

    if normalized in _REDIRECT_PATHS:
        return "redirect", normalized

    profile = _PROFILE_RE.match(normalized)
    if profile:
        return "profile", profile.group(1)[:128]

    profile_tab = _PROFILE_TAB_RE.match(normalized)
    if profile_tab:
        return "profile", profile_tab.group(1)[:128]

    if _SWEEP_HQ_RE.match(normalized):
        return "sweep_hq", ""

    unified_protocol = _UNIFIED_PROTOCOL_RE.match(normalized)
    if unified_protocol:
        return "unified_protocol", unified_protocol.group(1)

    location_protocol = _LOCATION_PROTOCOL_RE.match(normalized)
    if location_protocol:
        return "location_protocol", location_protocol.group(1)[:128]

    location_events = _LOCATION_EVENTS_RE.match(normalized)
    if location_events:
        return "location_events", location_events.group(1)[:128]

    location_participants = _LOCATION_PARTICIPANTS_RE.match(normalized)
    if location_participants:
        return "location_participants", location_participants.group(1)[:128]

    location = _LOCATION_RE.match(normalized)
    if location:
        return "location", location.group(1)[:128]

    # Возврат из Telegram — своя страница со своим типом: у неё другой поток
    # (данные приезжают во фрагменте адреса) и другая воронка.
    if normalized == "/auth/telegram/return":
        return "telegram_return", ""
    if normalized.startswith("/oauth/"):
        return "oauth_callback", ""
    # Служебный рендер OG-картинок (Playwright): фронт такие просмотры не шлёт
    # вовсе, ветка — страховка, чтобы случайный заход не падал в "other".
    if normalized.startswith("/render/"):
        return "og_render", ""
    # Демо и админка — одной строкой каждая (разбивка по подстраницам не нужна:
    # демо — витрина целиком, админка внутренняя). Подстраница едет в entity_key,
    # так что при желании разложить их можно и задним числом, без потери данных.
    if normalized == "/demo" or normalized.startswith("/demo/"):
        return "demo", normalized[len("/demo") :].lstrip("/")[:128]
    if normalized.startswith("/admin/"):
        return "admin", normalized[len("/admin/") :][:128]
    # Кабинет организатора: одна строка на семейство /organizer/{slug}[/report|/absence],
    # слаг (и подстраница) — в entity_key.
    if normalized.startswith("/organizer/"):
        return "organizer_location", normalized[len("/organizer/") :][:128]
    return "other", normalized[:128]


def record_page_view(
    db: Session,
    *,
    view_id: UUID,
    path: str,
    visitor_key: str | None,
    viewer_user_id: UUID | None,
) -> None:
    """Пишет сырое событие просмотра; повторный view_id молча игнорируется."""
    page_type, entity_key = classify_page(path)
    is_self = False
    if page_type == "profile" and entity_key:
        owner = resolve_profile_handle(db, entity_key)
        if owner is not None:
            entity_key = str(owner.id)
            is_self = viewer_user_id is not None and owner.id == viewer_user_id

    stmt = (
        pg_insert(PageViewEvent)
        .values(
            view_id=view_id,
            path=(path or "/")[:256],
            page_type=page_type,
            entity_key=entity_key,
            visitor_key=visitor_key,
            viewer_user_id=viewer_user_id,
            is_self=is_self,
        )
        .on_conflict_do_nothing(index_elements=["view_id"])
    )
    db.execute(stmt)
    db.commit()


def record_blog_post_click(
    db: Session,
    *,
    path: str | None,
    visitor_key: str | None,
    viewer_user_id: UUID | None,
) -> None:
    """Клик по карточке блога (переход в Telegram) как событие аналитики.

    Это не просмотр страницы, а исходящий переход, поэтому пишется мимо
    classify_page: page_type фиксированный. По каким именно постам кликают,
    «Популярность» не различает — по-постовые клики уже есть в админке блога
    (clicks_count); здесь только сам факт перехода. В entity_key — страница,
    с которой кликнули ("/" или "/blog"): раскладка «с главной / из блога»
    сохраняется и в дневных агрегатах. Длительности у события нет и не будет.

    visitor_key от клиента всегда анонимный ("a:..."), т.к. карточки блога не
    знают состояния авторизации; для залогиненных приводим его к "u:<user_id>",
    как это делает buildVisitorKey на фронте для просмотров страниц.
    """
    if viewer_user_id is not None:
        visitor_key = f"u:{viewer_user_id}"
    source = ((path or "/blog").split("?", 1)[0] or "/blog")[:128]
    stmt = pg_insert(PageViewEvent).values(
        view_id=uuid4(),
        path=source[:256],
        page_type="blog_post_click",
        entity_key=source,
        visitor_key=visitor_key,
        viewer_user_id=viewer_user_id,
        is_self=False,
    )
    db.execute(stmt)
    db.commit()


def record_page_leave(db: Session, *, view_id: UUID, duration_sec: int) -> None:
    """Дозаполняет длительность просмотра (беконы могут приходить несколько раз)."""
    clamped = max(0, min(int(duration_sec), MAX_DURATION_SEC))
    db.query(PageViewEvent).filter(PageViewEvent.view_id == view_id).update(
        {
            PageViewEvent.duration_sec: func.greatest(
                func.coalesce(PageViewEvent.duration_sec, 0), clamped
            )
        },
        synchronize_session=False,
    )
    db.commit()


def _day_bounds_utc(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=STATS_TIMEZONE)
    return start, start + timedelta(days=1)


def local_today() -> date:
    return datetime.now(STATS_TIMEZONE).date()


def rollup_day(db: Session, day: date) -> int:
    """Пересобирает агрегаты за один день (delete + insert из сырых событий)."""
    start, end = _day_bounds_utc(day)
    rows = (
        db.query(
            PageViewEvent.page_type,
            PageViewEvent.entity_key,
            func.count().label("views"),
            func.count(func.distinct(PageViewEvent.visitor_key)).label("unique_viewers"),
            func.count(PageViewEvent.id).filter(PageViewEvent.is_self.is_(True)).label("self_views"),
            func.coalesce(func.sum(PageViewEvent.duration_sec), 0).label("total_duration_sec"),
            func.count(PageViewEvent.duration_sec).label("duration_views"),
        )
        .filter(PageViewEvent.ts >= start, PageViewEvent.ts < end)
        .group_by(PageViewEvent.page_type, PageViewEvent.entity_key)
        .all()
    )
    db.query(PageStatsDaily).filter(PageStatsDaily.date == day).delete(synchronize_session=False)
    for row in rows:
        db.add(
            PageStatsDaily(
                date=day,
                page_type=row.page_type,
                entity_key=row.entity_key,
                views=int(row.views),
                unique_viewers=int(row.unique_viewers),
                self_views=int(row.self_views),
                total_duration_sec=int(row.total_duration_sec),
                duration_views=int(row.duration_views),
            )
        )
    db.commit()
    return len(rows)


def rollup_recent_days(db: Session, *, days: int = 2) -> int:
    """Пересобирает последние N дней (сегодня и вчера по МСК) — идемпотентно."""
    today = local_today()
    total = 0
    for offset in range(days):
        total += rollup_day(db, today - timedelta(days=offset))
    return total


def cleanup_old_events(db: Session, *, retention_days: int) -> int:
    cutoff = datetime.now(STATS_TIMEZONE) - timedelta(days=retention_days)
    deleted = (
        db.query(PageViewEvent)
        .filter(PageViewEvent.ts < cutoff)
        .delete(synchronize_session=False)
    )
    db.commit()
    return int(deleted)


# --- Чтение для админки -----------------------------------------------------


def resolve_period(
    *,
    period_days: int | None,
    date_from: date | None,
    date_to: date | None,
) -> tuple[date, date]:
    """Диапазон отчёта: либо явные даты, либо последние N дней включая сегодня.

    Явные даты приоритетнее. Указана одна граница — вторая берётся открытой
    (от первого дня данных / по сегодня). Перепутанные местами — меняем.
    """
    today = local_today()
    if date_from is None and date_to is None:
        days = period_days or DEFAULT_PERIOD_DAYS
        return today - timedelta(days=days - 1), today
    start = date_from or EARLIEST_STATS_DATE
    end = date_to or today
    if start > end:
        start, end = end, start
    return start, end


def build_page_analytics(
    db: Session,
    *,
    start: date,
    end: date,
    top_limit: int = 20,
) -> dict[str, object]:
    """Сводка популярности по агрегатам page_stats_daily за период (включительно).

    unique_viewers — сумма дневных уников (один человек в разные дни
    учитывается многократно), той же природы, что на /admin/stats.
    """
    base = db.query(PageStatsDaily).filter(PageStatsDaily.date >= start, PageStatsDaily.date <= end)

    section_rows = (
        base.with_entities(
            PageStatsDaily.page_type,
            func.sum(PageStatsDaily.views).label("views"),
            func.sum(PageStatsDaily.unique_viewers).label("unique_viewers"),
            func.sum(PageStatsDaily.self_views).label("self_views"),
            func.sum(PageStatsDaily.total_duration_sec).label("total_duration_sec"),
            func.sum(PageStatsDaily.duration_views).label("duration_views"),
        )
        .group_by(PageStatsDaily.page_type)
        .order_by(func.sum(PageStatsDaily.views).desc())
        .all()
    )

    def entity_top(page_types: list[str]) -> list[Any]:
        return (
            base.with_entities(
                PageStatsDaily.entity_key,
                func.sum(PageStatsDaily.views).label("views"),
                func.sum(PageStatsDaily.unique_viewers).label("unique_viewers"),
                func.sum(PageStatsDaily.self_views).label("self_views"),
                func.sum(PageStatsDaily.total_duration_sec).label("total_duration_sec"),
                func.sum(PageStatsDaily.duration_views).label("duration_views"),
            )
            .filter(PageStatsDaily.page_type.in_(page_types), PageStatsDaily.entity_key != "")
            .group_by(PageStatsDaily.entity_key)
            .order_by(func.sum(PageStatsDaily.views).desc())
            .limit(top_limit)
            .all()
        )

    profile_rows = entity_top(["profile"])
    # Популярность локации — это обе её страницы: карточка и список забегов.
    location_rows = entity_top(["location", "location_events"])

    profile_labels = _profile_labels(db, [row.entity_key for row in profile_rows])
    location_labels = _location_labels(db, [row.entity_key for row in location_rows])

    def row_stats(row: Any) -> dict[str, object]:
        duration_views = int(row.duration_views or 0)
        total_duration = int(row.total_duration_sec or 0)
        return {
            "views": int(row.views or 0),
            "unique_viewers": int(row.unique_viewers or 0),
            "self_views": int(row.self_views or 0),
            "avg_duration_sec": round(total_duration / duration_views) if duration_views else None,
        }

    return {
        "date_from": start,
        "date_to": end,
        "sections": [{"page_type": row.page_type, **row_stats(row)} for row in section_rows],
        "top_profiles": [
            {
                "entity_key": row.entity_key,
                **profile_labels.get(row.entity_key, {"label": row.entity_key, "href": None}),
                **row_stats(row),
            }
            for row in profile_rows
        ],
        "top_locations": [
            {
                "entity_key": row.entity_key,
                **location_labels.get(row.entity_key, {"label": row.entity_key, "href": f"/locations/{row.entity_key}"}),
                **row_stats(row),
            }
            for row in location_rows
        ],
    }


def _profile_labels(db: Session, entity_keys: list[str]) -> dict[str, dict[str, object]]:
    """entity_key профиля — user_id (или нерезолвленный хэндл — оставляем как есть)."""
    user_ids: list[UUID] = []
    for key in entity_keys:
        try:
            user_ids.append(UUID(key))
        except ValueError:
            continue
    if not user_ids:
        return {}
    users = db.query(User).filter(User.id.in_(user_ids)).all()
    labels: dict[str, dict[str, object]] = {}
    for user in users:
        name = user.display_name or user.telegram_first_name or f"#{user.serial_id}"
        handle = user.public_slug or str(user.serial_id)
        labels[str(user.id)] = {"label": name, "href": f"/users/{handle}"}
    return labels


def _location_labels(db: Session, entity_keys: list[str]) -> dict[str, dict[str, object]]:
    """Имя локации по slug страницы: первый матч по external_key среди платформ."""
    if not entity_keys:
        return {}
    lowered = {key.lower(): key for key in entity_keys}
    rows = (
        db.query(Location.external_key, Location.name, Platform.code)
        .join(Platform, Location.platform_id == Platform.id)
        .filter(func.lower(Location.external_key).in_(list(lowered)))
        .all()
    )
    priority = {"five_verst": 0, "s95": 1, "runpark": 2, "parkrun": 3}
    best: dict[str, tuple[int, str]] = {}
    for external_key, name, platform_code in rows:
        key = lowered.get(external_key.strip().lower())
        if key is None:
            continue
        rank = priority.get(platform_code, 9)
        if key not in best or rank < best[key][0]:
            best[key] = (rank, name)
    return {
        key: {"label": name, "href": f"/locations/{key}"}
        for key, (_rank, name) in best.items()
    }


def _handle_labels(db: Session, handles: list[str]) -> dict[str, dict[str, object]]:
    """Имя участника по хендлу из адреса профиля: public_slug или номер участника."""
    if not handles:
        return {}
    lowered = {handle.lower(): handle for handle in handles}
    serials = [int(handle) for handle in lowered if handle.isdigit()]
    users = (
        db.query(User)
        .filter(
            or_(
                func.lower(User.public_slug).in_(list(lowered)),
                User.serial_id.in_(serials) if serials else false(),
            )
        )
        .all()
    )
    labels: dict[str, dict[str, object]] = {}
    for user in users:
        name = user.display_name or user.telegram_first_name or f"#{user.serial_id}"
        for candidate in ((user.public_slug or "").lower(), str(user.serial_id)):
            key = lowered.get(candidate)
            if key is not None:
                labels[key] = {"label": name, "href": f"/users/{key}"}
    return labels


def build_home_link_clicks(
    db: Session, *, start: date, end: date, limit: int = 20
) -> list[dict[str, object]]:
    """Переходы по ссылкам с главной: куда именно уводит главная страница.

    Ссылки на локации и профили участников появились на главной 01.08.2026 —
    за более ранние периоды таблица пустая. Событие пишется обоими вариантами
    АБ-теста (ссылки есть и в A, и в B), поэтому вариант здесь не разделяем:
    вопрос отчёта — «куда переходят», а не «какой вариант лучше».
    """
    rows = db.execute(
        text(
            """
            SELECT value,
                   count(*) AS clicks,
                   count(DISTINCT visitor_key) AS visitors
            FROM ab_events
            WHERE experiment = :experiment
              AND event_type = 'home_link_click'
              AND ts >= :start AND ts < :end_exclusive
            GROUP BY value
            ORDER BY clicks DESC, value
            LIMIT :limit
            """
        ),
        {
            "experiment": HOME_EXPERIMENT,
            "start": start,
            "end_exclusive": end + timedelta(days=1),
            "limit": limit,
        },
    ).all()

    parsed = [(row.value.split(":", 1), row) for row in rows if ":" in (row.value or "")]
    location_keys = [target for (kind, target), _row in parsed if kind == "location"]
    runner_handles = [target for (kind, target), _row in parsed if kind == "runner"]
    location_labels = _location_labels(db, location_keys)
    runner_labels = _handle_labels(db, runner_handles)

    result: list[dict[str, object]] = []
    for (kind, target), row in parsed:
        if kind == "location":
            fallback = {"label": target, "href": f"/locations/{target}"}
            label = location_labels.get(target, fallback)
        else:
            fallback = {"label": target, "href": f"/users/{target}"}
            label = runner_labels.get(target, fallback)
        result.append(
            {
                "kind": kind,
                "entity_key": target,
                **label,
                "clicks": int(row.clicks or 0),
                "visitors": int(row.visitors or 0),
            }
        )
    return result


# Канал постоянного счётчика воронки регистрации (см. FUNNEL_EXPERIMENT в
# ab_service). Пять ступеней: главная → клик по кнопке → старт входа у
# провайдера → аккаунт → привязанная платформа.
FUNNEL_EXPERIMENT = "funnel"


def build_funnel_stats(db: Session, *, start: date, end: date) -> list[dict[str, object]]:
    """Воронка регистрации по посетителям: сколько дошло до каждой ступени.

    Считаем ПОСЕТИТЕЛЕЙ (visitor_key), а не события: человек может открыть
    главную пять раз и нажать кнопку трижды, и в конверсии это один человек.
    visitor_key анонимный и переживает редирект на VK/Яндекс, поэтому цепочка
    не рвётся на входе.

    Ступени со второй по четвёртую берутся только у тех, кто в этом же периоде
    открывал главную: клик и вход бывают и без неё (прямая ссылка на /login),
    но тогда знаменатель и числитель считались бы по разным людям.

    Пятая ступень — привязка платформы — считается по platform_links, а не по
    событию: таблица и есть источник правды. Она сознательно НЕ ограничена
    концом периода — человеку, зарегистрировавшемуся в последний день, нужно
    время; иначе свежие периоды показывали бы заниженную активацию.

    Пишется с 22.08.2026, за более ранние периоды ступени будут пустыми.
    """
    row = db.execute(
        text(
            """
            WITH home AS (
                SELECT DISTINCT visitor_key FROM ab_events
                WHERE experiment = :experiment AND event_type = 'home_view'
                  AND ts >= :start AND ts < :end_exclusive
            ),
            step AS (
                SELECT event_type, visitor_key, min(cohort) AS cohort,
                       min(viewer_user_id::text) AS user_id
                FROM ab_events
                WHERE experiment = :experiment
                  AND ts >= :start AND ts < :end_exclusive
                  AND visitor_key IN (SELECT visitor_key FROM home)
                GROUP BY event_type, visitor_key
            )
            SELECT
              (SELECT count(*) FROM home) AS home_view,
              (SELECT count(*) FROM step WHERE event_type = 'cta_click') AS cta_click,
              (SELECT count(*) FROM step WHERE event_type = 'auth_start') AS auth_start,
              (SELECT count(*) FROM step WHERE event_type = 'auth_done' AND cohort = 'new')
                AS registered,
              (SELECT count(*) FROM step s
                 WHERE s.event_type = 'auth_done' AND s.cohort = 'new'
                   AND EXISTS (SELECT 1 FROM platform_links pl
                               WHERE pl.user_id::text = s.user_id)) AS activated,
              (SELECT count(*) FROM step WHERE event_type = 'auth_done' AND cohort = 'returning')
                AS returning_logins
            """
        ),
        {
            "experiment": FUNNEL_EXPERIMENT,
            "start": start,
            "end_exclusive": end + timedelta(days=1),
        },
    ).one()

    steps = [
        ("Открыли главную", int(row.home_view or 0)),
        ("Нажали кнопку входа", int(row.cta_click or 0)),
        ("Дошли до провайдера", int(row.auth_start or 0)),
        ("Завели аккаунт", int(row.registered or 0)),
        ("Привязали платформу", int(row.activated or 0)),
    ]
    base = steps[0][1]
    result: list[dict[str, object]] = []
    previous: int | None = None
    for label, visitors in steps:
        result.append(
            {
                "step": label,
                "visitors": visitors,
                # Доля от первой ступени — «сквозная» конверсия воронки.
                "pct_of_start": round(100.0 * visitors / base, 1) if base else None,
                # Доля от предыдущей ступени — где именно рвётся.
                "pct_of_prev": (
                    round(100.0 * visitors / previous, 1) if previous else None
                ),
            }
        )
        previous = visitors
    # Вернувшиеся не ступень воронки (аккаунт у них уже был), но без них
    # непонятно, почему входов больше, чем регистраций.
    result.append(
        {
            "step": "— из них вернувшихся (не конверсия)",
            "visitors": int(row.returning_logins or 0),
            "pct_of_start": None,
            "pct_of_prev": None,
        }
    )
    return result


# Канал событий фичи «Поделиться» в ab_events (см. KNOWN_EXPERIMENTS в ab_service).
SHARE_EXPERIMENT = "share"

# Порядок ступеней воронки шаринга в отчёте.
_SHARE_FUNNEL_ORDER = ("share_moment_shown", "share_open", "share_customize", "share_success")


def build_share_stats(db: Session, *, start: date, end: date) -> dict[str, object]:
    """Воронка и разрезы фичи «Поделиться».

    Все события лежат в ab_events с experiment='share'; value кодируется
    префиксами: "сюжет:вход" у показов/открытий, "канал:сюжет" у успехов,
    "look:<id>"/"format:<id>" у переключений. Пишется с релиза «Поделиться
    2.0» — за более ранние периоды отчёт пустой.
    """
    params = {
        "experiment": SHARE_EXPERIMENT,
        "start": start,
        "end_exclusive": end + timedelta(days=1),
    }
    funnel_rows = db.execute(
        text(
            """
            SELECT event_type,
                   count(*) AS events,
                   count(DISTINCT visitor_key) AS visitors
            FROM ab_events
            WHERE experiment = :experiment
              AND event_type <> 'og_preview_fetch'
              AND ts >= :start AND ts < :end_exclusive
            GROUP BY event_type
            """
        ),
        params,
    ).all()
    detail_rows = db.execute(
        text(
            """
            SELECT event_type, value, count(*) AS events
            FROM ab_events
            WHERE experiment = :experiment
              AND event_type <> 'og_preview_fetch'
              AND ts >= :start AND ts < :end_exclusive
            GROUP BY event_type, value
            """
        ),
        params,
    ).all()

    by_type = {row.event_type: row for row in funnel_rows}
    funnel = [
        {
            "event_type": event_type,
            "events": int(by_type[event_type].events or 0),
            "visitors": int(by_type[event_type].visitors or 0),
        }
        for event_type in _SHARE_FUNNEL_ORDER
        if event_type in by_type
    ]

    # Пары «сюжет + вход»: главный разрез — ГДЕ именно люди жмут «Поделиться»
    # (веха в истории, своя пробежка на локации, строка таблицы пробежек…).
    pairs: dict[tuple[str, str], dict[str, int]] = {}
    channels: dict[str, int] = {}
    looks: dict[str, int] = {}
    formats: dict[str, int] = {}
    photo_added = 0

    def pair_bucket(subject: str, entry: str) -> dict[str, int]:
        return pairs.setdefault((subject, entry), {"shown": 0, "opens": 0})

    for row in detail_rows:
        value = row.value or ""
        count = int(row.events or 0)
        head, _, tail = value.partition(":")
        if row.event_type == "share_moment_shown" and tail:
            pair_bucket(head, tail)["shown"] += count
        elif row.event_type == "share_open" and tail:
            pair_bucket(head, tail)["opens"] += count
        elif row.event_type == "share_success" and tail:
            channels[head] = channels.get(head, 0) + count
        elif row.event_type == "share_template_switch" and tail:
            if head == "look":
                looks[tail] = looks.get(tail, 0) + count
            elif head == "format":
                formats[tail] = formats.get(tail, 0) + count
        elif row.event_type == "share_customize" and value == "photo":
            photo_added += count

    return {
        "funnel": funnel,
        "pairs": [
            {"subject": subject, "entry": entry, **bucket}
            for (subject, entry), bucket in sorted(pairs.items(), key=lambda kv: -kv[1]["opens"])
        ],
        "channels": [
            {"channel": channel, "successes": count}
            for channel, count in sorted(channels.items(), key=lambda kv: -kv[1])
        ],
        "looks": [
            {"value": value, "count": count}
            for value, count in sorted(looks.items(), key=lambda kv: -kv[1])
        ],
        "formats": [
            {"value": value, "count": count}
            for value, count in sorted(formats.items(), key=lambda kv: -kv[1])
        ],
        "photo_added": photo_added,
    }


def build_og_fetch_stats(
    db: Session, *, start: date, end: date, limit: int = 20
) -> list[dict[str, object]]:
    """«Разворачивания ссылок»: сколько раз боты мессенджеров и поисковиков
    запрашивали превью страниц (событие og_preview_fetch пишет сам бэкенд в
    /__prerender). Прокси-метрика «ссылку кинули в чат», которой раньше не
    было ни в каком виде."""
    rows = db.execute(
        text(
            """
            SELECT value,
                   count(*) AS fetches,
                   count(DISTINCT visitor_key) AS bots
            FROM ab_events
            WHERE experiment = :experiment
              AND event_type = 'og_preview_fetch'
              AND ts >= :start AND ts < :end_exclusive
            GROUP BY value
            ORDER BY fetches DESC, value
            LIMIT :limit
            """
        ),
        {
            "experiment": SHARE_EXPERIMENT,
            "start": start,
            "end_exclusive": end + timedelta(days=1),
            "limit": limit,
        },
    ).all()

    parsed: list[tuple[str, str, object]] = []
    for row in rows:
        page_type, _, entity_key = (row.value or "").partition(":")
        parsed.append((page_type or "other", entity_key, row))

    location_keys = [
        entity_key
        for page_type, entity_key, _row in parsed
        if entity_key and page_type in ("location", "location_events")
    ]
    location_labels = _location_labels(db, location_keys)

    result: list[dict[str, object]] = []
    for page_type, entity_key, row in parsed:
        label: dict[str, object] = {"label": entity_key or page_type, "href": None}
        if entity_key and page_type in ("location", "location_events"):
            label = location_labels.get(
                entity_key, {"label": entity_key, "href": f"/locations/{entity_key}"}
            )
        result.append(
            {
                "page_type": page_type,
                "entity_key": entity_key,
                **label,
                "fetches": int(row.fetches or 0),
                "bots": int(row.bots or 0),
            }
        )
    return result


def build_home_ab_stats(db: Session, *, start: date, end: date) -> list[dict[str, object]]:
    """Архив АБ-теста главной: показы вариантов A и B по периоду.

    Эксперимент шёл 27.07–22.08.2026 и завершён — вариант B принят как
    единственная главная, событие variant_view больше не пишется. Отчёт живёт
    дальше как окно в историю: за периоды вне теста он пустой и блок в админке
    просто не показывается.

    Только показы. Воронку (клики CTA, логины, конверсия) отчёт не считал
    никогда — итоги теста разобраны офлайн, SQL по ab_events.
    """
    rows = db.execute(
        text(
            """
            SELECT variant,
                   count(*) AS views,
                   count(DISTINCT visitor_key) AS viewers
            FROM ab_events
            WHERE experiment = :experiment
              AND event_type = 'variant_view'
              AND ts >= :start AND ts < :end_exclusive
            GROUP BY variant
            ORDER BY variant
            """
        ),
        # Верхнюю границу делаем исключающей здесь, а не в SQL: «::date» внутри
        # text() SQLAlchemy принимает за экранированное двоеточие и ломает запрос.
        {"experiment": HOME_EXPERIMENT, "start": start, "end_exclusive": end + timedelta(days=1)},
    ).all()

    return [
        {"variant": row.variant, "views": int(row.views or 0), "viewers": int(row.viewers or 0)}
        for row in rows
    ]
