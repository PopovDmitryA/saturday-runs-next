"""Уведомление о собственной пробежке — одним сообщением.

Сканер зовётся после синков (прогрев дашбордов — для батчей, user sync — для
кнопки «Обновить») и раз в десять минут по beat (ловит результаты, которые
пишут мимо воркеров, например parkrun с Mac-демона). Для каждого человека с
включёнными уведомлениями:

* новые результаты — run_results, записанные после водяного знака
  prefs.runs_notified_through, с датой старта не старше окна
  notifications_runs_window_days (первичная загрузка истории приносит сотни
  строк, писать о каждой нельзя);
* волонтёрство — volunteer_results после того же водяного знака, свой вид
  уведомления; роли одного старта — одним блоком. Бежал и волонтёрил в один
  день — одно сообщение;
* челленджи — уровни против снимка prefs.challenge_levels;
* вехи «Моей истории» — новые ключи против prefs.milestones_seen;
* призыв собрать постер о пробежке.

Рейтинги — отдельным сообщением раз в неделю (weekly_ratings_message):
протоколы субботы догружаются до воскресенья, и место, посчитанное сразу
после пробежки, через день уже другое.

Первый снимок каждой части молчит: у человека и так есть всё, что набрано
до включения уведомлений. Если пробежки нет, а уровни или вехи изменились
(пересчёт, оценки, погода) — уходит короткое сообщение без строки пробежки.
Стоимость сканирования растёт с числом включивших: сначала проверяем
настройки, тяжёлые расчёты — только для них.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx
from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.runtime_env import is_test_run
from app.db.session import get_session_factory
from app.models import Event, Location, Participant, Platform, PlatformLink, RunResult, User, VolunteerResult
from app.notification_kinds import kind_enabled
from app.notification_markup import bold, link
from app.services import notification_channels_service as channels
from app.services import notification_service as notifications
from app.services.achievements_service import TIER_LABELS, compute_challenges
from app.services.leaderboard_service import get_my_leaderboard_row
from app.services.my_history_service import get_my_history
from app.services.platform_titles import PLATFORM_TITLES
from app.services.start_weather_service import weather_for_pairs, weather_line
from app.services.weather_service import collect_event_weather, event_weather_needed
from app.time_format import normalize_finish_time_display

logger = logging.getLogger(__name__)

KIND_RUNS = "runs"
KIND_VOLUNTEERING = "volunteering"
KIND_RATINGS = "ratings"

# Рейтинги, за движением в которых следим: код → подпись в сообщении.
RATING_METRICS: tuple[tuple[str, str], ...] = (
    ("runs", "Пробежки"),
    ("locations", "Локации"),
    ("wins", "Победы"),
)

_LEVEL_RANK = {None: 0, "bronze": 1, "silver": 2, "gold": 3}
_LEVEL_LABELS = {"bronze": "бронза", "silver": "серебро", "gold": "золото"}
_LEVEL_ICONS = {"bronze": "🥉", "silver": "🥈", "gold": "🥇"}
_MONTHS_GENITIVE = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)  # fmt: skip


@dataclass(frozen=True)
class NewRun:
    result_id: UUID
    event_date: date
    location_name: str
    platform_code: str
    event_number: int | None
    finish_time_sec: int | None
    position: int | None
    is_pr: bool
    is_first_run_at_location: bool
    location_id: UUID | None = None
    #: Готовая строка «🌤️ 12°, малооблачно, ветер 3 м/с» или None.
    weather: str | None = None
    #: Путь протокола старта на сайте (/locations/…/protocol/…) — ссылка заголовка.
    protocol_path: str | None = None


@dataclass(frozen=True)
class NewVolunteering:
    """Волонтёрство на одном старте: все роли человека в этот день."""

    result_ids: tuple[UUID, ...]
    event_date: date
    location_id: UUID
    location_name: str
    platform_code: str
    event_number: int | None
    roles: tuple[str, ...]
    weather: str | None = None
    protocol_path: str | None = None


@dataclass(frozen=True)
class LevelUp:
    code: str
    title: str
    icon: str
    tier: str
    level: str


@dataclass(frozen=True)
class RatingMove:
    metric: str
    label: str
    rank: int
    gained: int


# ---------------------------------------------------------------------------
# Сбор


def _profile_handle(user: User) -> str:
    slug = (user.public_slug or "").strip()
    return slug or str(user.serial_id)


def _new_runs(db: Session, user_id: UUID, *, since: datetime, until: datetime, today: date) -> list[NewRun]:
    window_days = get_settings().notifications_runs_window_days
    rows = (
        db.query(RunResult, Event, Location, Platform)
        .join(Event, RunResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Event.platform_id == Platform.id)
        .join(Participant, RunResult.participant_id == Participant.id)
        .join(
            PlatformLink,
            and_(
                PlatformLink.platform_id == Participant.platform_id,
                PlatformLink.external_user_id == Participant.external_user_id,
            ),
        )
        .filter(
            PlatformLink.user_id == user_id,
            RunResult.created_at > since,
            RunResult.created_at <= until,
            RunResult.finish_time_sec.isnot(None),
            Event.is_test_event.is_(False),
            Event.event_date >= today - timedelta(days=window_days),
        )
        .order_by(Event.event_date.asc(), Location.name.asc())
        .all()
    )
    pairs = [(event.location_id, event.event_date) for _result, event, _location, _platform in rows]
    weather = _weather_for_runs(db, pairs)
    return [
        NewRun(
            result_id=result.id,
            event_date=event.event_date,
            location_name=location.name,
            platform_code=platform.code,
            event_number=event.event_number,
            finish_time_sec=result.finish_time_sec,
            position=result.position,
            is_pr=bool(result.is_pr),
            is_first_run_at_location=bool(result.is_first_run_at_location),
            location_id=event.location_id,
            weather=weather_line(weather.get((event.location_id, event.event_date))),
            protocol_path=protocol_path(location, platform.code, event.event_date),
        )
        for result, event, location, platform in rows
    ]


def protocol_path(location: Location, platform_code: str, event_date: date) -> str | None:
    """Протокол старта на сайте. Страница локации открывается по слагу любой
    своей системы, поэтому хватает external_key самой площадки."""
    slug = (location.external_key or "").strip()
    if not slug:
        return None
    return f"/locations/{quote(slug, safe='')}/protocol/{platform_code}/{event_date.isoformat()}"


def _new_volunteerings(
    db: Session, user_id: UUID, *, since: datetime, until: datetime, today: date
) -> list[NewVolunteering]:
    window_days = get_settings().notifications_runs_window_days
    rows = (
        db.query(VolunteerResult, Event, Location, Platform)
        .join(Event, VolunteerResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Event.platform_id == Platform.id)
        .join(Participant, VolunteerResult.participant_id == Participant.id)
        .join(
            PlatformLink,
            and_(
                PlatformLink.platform_id == Participant.platform_id,
                PlatformLink.external_user_id == Participant.external_user_id,
            ),
        )
        .filter(
            PlatformLink.user_id == user_id,
            VolunteerResult.created_at > since,
            VolunteerResult.created_at <= until,
            Event.is_test_event.is_(False),
            Event.event_date >= today - timedelta(days=window_days),
        )
        .order_by(Event.event_date.asc(), Location.name.asc(), VolunteerResult.role.asc())
        .all()
    )
    by_event: dict[UUID, list[tuple[VolunteerResult, Event, Location, Platform]]] = {}
    for row in rows:
        by_event.setdefault(row[1].id, []).append(row)
    weather = _weather_for_runs(db, [(event.location_id, event.event_date) for _v, event, _l, _p in rows])
    items: list[NewVolunteering] = []
    for group in by_event.values():
        _first, event, location, platform = group[0]
        roles = tuple(dict.fromkeys(v.role.strip() for v, *_ in group if v.role and v.role.strip()))
        items.append(
            NewVolunteering(
                result_ids=tuple(v.id for v, *_ in group),
                event_date=event.event_date,
                location_id=event.location_id,
                location_name=location.name,
                platform_code=platform.code,
                event_number=event.event_number,
                roles=roles,
                weather=weather_line(weather.get((event.location_id, event.event_date))),
                protocol_path=protocol_path(location, platform.code, event.event_date),
            )
        )
    return items


def _weather_for_runs(db: Session, pairs: list[tuple[UUID, date]]) -> dict[tuple[UUID, date], dict[str, Any]]:
    """Погода стартов для сообщения. Свежий старт без погоды добираем сами:
    задача, поставленная загрузкой протокола, могла ещё не отработать, а
    сообщение уходит один раз. Сбой сети — сообщение уйдёт без погоды."""
    if not pairs:
        return {}
    found = weather_for_pairs(db, pairs)
    missing = {pair for pair in pairs if pair not in found and event_weather_needed(pair[1])}
    if not missing or is_test_run():
        return found
    # Своя сессия: сбор коммитит и откатывает, а у сканера в транзакции уже
    # лежат незаписанные настройки человека.
    weather_db = get_session_factory()()
    try:
        with httpx.Client(headers={"User-Agent": "run5k.run weather collector"}, timeout=20) as client:
            for location_id, event_date in sorted(missing, key=str):
                collect_event_weather(weather_db, client, location_id, event_date)
    except Exception:  # noqa: BLE001 — погода не должна задерживать уведомление
        logger.warning("notify: weather for new runs not collected", exc_info=True)
        weather_db.rollback()
    finally:
        weather_db.close()
    return weather_for_pairs(db, pairs)


def challenge_levels_snapshot(payload: dict[str, Any]) -> dict[str, dict[str, str | None]]:
    """{код: {тир: уровень}} из ответа compute_challenges."""
    snapshot: dict[str, dict[str, str | None]] = {}
    for challenge in payload.get("challenges") or []:
        code = str(challenge.get("code"))
        snapshot[code] = {str(t["tier"]): t.get("level") for t in challenge.get("tiers") or []}
    return snapshot


def level_ups(
    previous: dict[str, Any] | None,
    current: dict[str, dict[str, str | None]],
    payload: dict[str, Any],
) -> list[LevelUp]:
    """Тиры, где уровень вырос относительно снимка. Пустой снимок — ничего."""
    if previous is None:
        return []
    meta = {str(c.get("code")): c for c in payload.get("challenges") or []}
    ups: list[LevelUp] = []
    for code, tiers in current.items():
        before = previous.get(code) or {}
        for tier, level in tiers.items():
            if level and _LEVEL_RANK.get(level, 0) > _LEVEL_RANK.get(before.get(tier), 0):
                challenge = meta.get(code) or {}
                ups.append(
                    LevelUp(
                        code=code,
                        title=str(challenge.get("title") or code),
                        icon=str(challenge.get("icon") or "🏅"),
                        tier=tier,
                        level=level,
                    )
                )
    return ups


def ratings_snapshot(db: Session, user: User) -> dict[str, int]:
    """Текущее место в каждом из рейтингов; нет в рейтинге — метрики нет в снимке."""
    snapshot: dict[str, int] = {}
    for metric, _label in RATING_METRICS:
        try:
            row = get_my_leaderboard_row(db, metric, user)  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001 — рейтинг не построился: пропускаем метрику, не скан
            logger.exception("notify: leaderboard row failed for %s/%s", user.id, metric)
            continue
        rank = row.get("rank_overall")
        if isinstance(rank, int) and rank > 0:
            snapshot[metric] = rank
    return snapshot


def rating_moves(previous: dict[str, Any] | None, current: dict[str, int]) -> list[RatingMove]:
    """Изменения места за неделю: вверх (gained > 0) и вниз (gained < 0)."""
    if previous is None:
        return []
    labels = dict(RATING_METRICS)
    moves: list[RatingMove] = []
    for metric, rank in current.items():
        before = previous.get(metric)
        if isinstance(before, int) and rank != before:
            moves.append(RatingMove(metric=metric, label=labels.get(metric, metric), rank=rank, gained=before - rank))
    return moves


def milestone_key(item: dict[str, Any]) -> str:
    return "|".join(
        str(item.get(k) or "")
        for k in ("kind", "event_date", "platform_code", "location_name", "number", "role", "record_scope", "age_group")
    )


def milestones_snapshot(db: Session, user_id: UUID) -> tuple[list[str], list[dict[str, Any]]]:
    payload = get_my_history(db, user_id)
    raw = payload.get("milestones")
    items: list[dict[str, Any]] = list(raw) if isinstance(raw, list) else []
    return [milestone_key(item) for item in items], items


def new_milestones(previous: list[str] | None, keys: list[str], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if previous is None:
        return []
    seen = set(previous)
    return [item for key, item in zip(keys, items, strict=True) if key not in seen]


# ---------------------------------------------------------------------------
# Текст


def _date_label(value: date) -> str:
    return f"{value.day} {_MONTHS_GENITIVE[value.month - 1]}"


def _time_label(finish_time_sec: int | None) -> str | None:
    if not finish_time_sec:
        return None
    if finish_time_sec >= 3600:
        return normalize_finish_time_display(finish_time_sec, None)
    return f"{finish_time_sec // 60}:{finish_time_sec % 60:02d}"


def _ordinal(n: int) -> str:
    return f"{n}-е"


def _event_head(icon: str, location_name: str, platform_code: str, event_number: int | None, event_date: date) -> str:
    head = f"{icon} {location_name}"
    system = PLATFORM_TITLES.get(platform_code)
    if system:
        head += f" ({system})"
    if event_number:
        head += f" №{event_number}"
    return head + f" · {_date_label(event_date)}"


def bold_link(text: str, url: str | None) -> str:
    """Жирная подпись, кликабельная, если есть адрес.

    Порядок именно `[**…**](url)`: рендер сначала находит ссылку и уже внутри
    подписи — жирное; `**[…](url)**` он бы съел как жирный текст целиком.
    """
    if not url or "]" in text:
        return bold(text)
    return link(bold(text), url)


def run_block(run: NewRun, *, base_url: str | None = None) -> str:
    """Где и когда — жирным (ссылка на протокол), ниже время, место и отметки, третьей строкой — погода."""
    head = _event_head("📍", run.location_name, run.platform_code, run.event_number, run.event_date)
    head_url = f"{base_url}{run.protocol_path}" if base_url and run.protocol_path else None
    facts = []
    time_label = _time_label(run.finish_time_sec)
    if time_label:
        facts.append(f"⏱ {time_label}")
    if run.position:
        facts.append(f"🏅 {_ordinal(run.position)} место")
    if run.is_pr:
        facts.append("🔥 личный рекорд")
    if run.is_first_run_at_location:
        facts.append("🆕 первый раз здесь")
    lines = [bold_link(head, head_url)]
    if facts:
        lines.append(" · ".join(facts))
    if run.weather:
        lines.append(run.weather)
    return "\n".join(lines)


def volunteer_block(item: NewVolunteering, *, with_weather: bool = True, base_url: str | None = None) -> str:
    """Где и когда — жирным (ссылка на протокол), ниже роли; погода — если её не показал блок пробежки."""
    head = _event_head("🦺", item.location_name, item.platform_code, item.event_number, item.event_date)
    head_url = f"{base_url}{item.protocol_path}" if base_url and item.protocol_path else None
    lines = [bold_link(head, head_url), "🙌 " + (", ".join(item.roles) if item.roles else "волонтёр")]
    if with_weather and item.weather:
        lines.append(item.weather)
    return "\n".join(lines)


def level_line(up: LevelUp) -> str:
    tier_label = TIER_LABELS.get(up.tier)
    level = f"{_LEVEL_ICONS.get(up.level, '')} {_LEVEL_LABELS.get(up.level, up.level)}".strip()
    suffix = f" ({tier_label.lower()} уровень)" if tier_label else ""
    return f"{up.icon} {up.title} — {level}{suffix}"


def rating_line(move: RatingMove) -> str:
    arrow = f"▲{move.gained}" if move.gained > 0 else f"▼{-move.gained}"
    return f"«{move.label}» — {bold(_ordinal(move.rank) + ' место')} ({arrow})"


def milestone_line(item: dict[str, Any]) -> str:
    """Короткая строка вехи для сообщения — по мотивам подписей «Моей истории»."""
    kind = str(item.get("kind"))
    number = item.get("number")
    name = str(item.get("location_name") or "")
    platform = PLATFORM_TITLES.get(str(item.get("platform_code")), "")
    if kind == "first_run":
        return "🏁 Первая пробежка в истории"
    if kind == "first_run_platform":
        return f"🚩 Первый старт в системе «{platform}»"
    if kind == "run_club":
        return f"🏅 {number}-я пробежка — клуб {number}!"
    if kind == "run_club_platform":
        return f"🎖 {number}-я пробежка в системе «{platform}»"
    if kind == "location_club":
        return f"🎯 {number}-я пробежка в локации «{name}»"
    if kind == "global_pr":
        return "🏆 Новый глобальный рекорд!"
    if kind == "pr":
        return f"⚡ Личный рекорд в системе «{platform}»"
    if kind == "location_pr":
        return f"⚡ Личный рекорд в локации «{name}»"
    if kind == "location_course_record":
        return f"👑 Рекорд локации «{name}» — лучшее время площадки!"
    if kind == "location_age_group_record":
        group = item.get("age_group")
        return (
            f"🏵 Рекорд локации «{name}» в группе {group}" if group else f"🏵 Рекорд локации «{name}» в возрастной группе"
        )
    if kind == "first_foreign_parkrun":
        return "✈️ Первый зарубежный паркран"
    if kind == "first_foreign_run":
        country = item.get("country")
        return f"✈️ Первый зарубежный старт — {country}" if country else "✈️ Первый зарубежный старт"
    if kind == "new_country":
        return f"🌍 Новая страна — {item.get('country') or name}"
    if kind == "new_region":
        return f"🧭 Новый регион — {item.get('region') or name}"
    if kind == "new_city":
        return f"🏙 Новый город — {item.get('location_city') or name}"
    if kind == "new_location":
        return f"🗺 Новая локация — {name}" + (f" (№{number})" if number else "")
    if kind == "first_volunteer":
        return "🤝 Первое волонтёрство"
    if kind == "volunteer_club":
        return f"🤝 {number}-е волонтёрство — клуб волонтёров"
    if kind == "volunteer_club_platform":
        return f"🙌 {number}-е волонтёрство в системе «{platform}»"
    if kind == "volunteer_location_club":
        return f"📍 {number}-е волонтёрство в локации «{name}»"
    if kind in ("saturday_streak", "saturday_run_streak", "saturday_volunteer_streak"):
        what = {
            "saturday_streak": "серии суббот",
            "saturday_run_streak": "серии пробежек",
            "saturday_volunteer_streak": "серии волонтёрств",
        }[kind]
        return f"📆 Рекорд {what} — {number} подряд"
    return f"🎖 Веха: {kind}"


def compose_message(
    *,
    runs: list[NewRun],
    ups: list[LevelUp],
    milestones: list[dict[str, Any]],
    poster_url: str | None,
    volunteerings: list[NewVolunteering] | None = None,
    base_url: str | None = None,
    profile_url: str | None = None,
) -> tuple[str, str]:
    """(заголовок, тело в разметке).

    base_url — адрес сайта для ссылок на протоколы стартов; profile_url —
    кабинет человека (/users/{хендл}): заголовки разделов ведут на его же
    вкладки «Челленджи» и «История».
    """
    volunteerings = volunteerings or []
    settings = get_settings()
    limit = settings.notifications_runs_max_listed
    sections: list[str] = []
    if runs:
        blocks = [run_block(run, base_url=base_url) for run in runs[:limit]]
        rest = len(runs) - limit
        if rest > 0:
            blocks.append(f"…и ещё {rest}")
        sections.append("\n\n".join(blocks))
    if volunteerings:
        # Погода старта, где человек и бежал, уже стоит в блоке пробежки.
        shown = {(run.location_id, run.event_date) for run in runs[:limit]}
        blocks = [
            volunteer_block(item, with_weather=(item.location_id, item.event_date) not in shown, base_url=base_url)
            for item in volunteerings[:limit]
        ]
        rest = len(volunteerings) - limit
        if rest > 0:
            blocks.append(f"…и ещё {rest}")
        sections.append("\n\n".join(blocks))
    achievements_url = f"{profile_url}/achievements" if profile_url else None
    history_url = f"{profile_url}/history" if profile_url else None
    if ups:
        sections.append(
            "🏆 " + bold_link("Челленджи:", achievements_url) + "\n" + "\n".join(level_line(u) for u in ups)
        )
    if milestones:
        sections.append(
            "🎖 "
            + bold_link("Вехи истории:", history_url)
            + "\n"
            + "\n".join(milestone_line(m) for m in milestones[:6])
        )
    if runs and poster_url:
        sections.append("🖼 " + link("Собрать постер о пробежке", poster_url) + " — поделитесь результатом в сториз.")

    if runs and volunteerings:
        title = "🏃 Пробежка и волонтёрство на сайте"
    elif runs:
        title = "🏃 Пробежка попала на сайт" if len(runs) == 1 else f"🏃 Новые пробежки на сайте: {len(runs)}"
    elif volunteerings:
        title = (
            "🦺 Волонтёрство попало на сайт"
            if len(volunteerings) == 1
            else f"🦺 Новые волонтёрства на сайте: {len(volunteerings)}"
        )
    elif ups and not milestones:
        title = "🏆 Новый уровень в челлендже" if len(ups) == 1 else "🏆 Новые уровни в челленджах"
    else:
        title = "🎖 Что нового в вашей истории"
    return title, "\n\n".join(sections)


def compose_ratings_message(moves: list[RatingMove]) -> tuple[str, str]:
    lines = [rating_line(m) for m in moves]
    ups = sum(1 for m in moves if m.gained > 0)
    title = "📈 Рейтинги за неделю: вы поднялись" if ups == len(moves) else "📊 Рейтинги за неделю"
    return title, "\n".join(lines)


def _digest(parts: list[str]) -> str:
    return hashlib.sha1("|".join(sorted(parts)).encode()).hexdigest()[:24]


# ---------------------------------------------------------------------------
# Сканирование


def scan_user_activity(db: Session, user_id: UUID, *, now: datetime | None = None) -> dict[str, Any]:
    """Сравнить состояние человека с последним сканом и отправить, что изменилось."""
    now = now or datetime.now(UTC)
    if not channels.is_enabled(db, user_id):
        return {"skipped": "disabled"}
    prefs = notifications.ensure_prefs(db, user_id)
    runs_on = kind_enabled(prefs.kinds, KIND_RUNS)
    volunteering_on = kind_enabled(prefs.kinds, KIND_VOLUNTEERING)
    if not runs_on and not volunteering_on:
        return {"skipped": "kind off"}
    user = db.get(User, user_id)
    if user is None:
        return {"skipped": "no user"}

    settings = get_settings()
    base = settings.app_base_url.rstrip("/")
    handle = _profile_handle(user)

    # Водяной знак общий: выключенный вид не копит долг — включив его,
    # человек не получит пачку старых новостей.
    since = prefs.runs_notified_through or (now - timedelta(days=1))
    runs: list[NewRun] = []
    ups: list[LevelUp] = []
    fresh: list[dict[str, Any]] = []
    volunteerings: list[NewVolunteering] = []
    if runs_on:
        runs = _new_runs(db, user_id, since=since, until=now, today=now.date())

        payload = compute_challenges(db, user_id)
        current_levels = challenge_levels_snapshot(payload)
        ups = level_ups(prefs.challenge_levels, current_levels, payload)
        prefs.challenge_levels = current_levels

        keys, items = milestones_snapshot(db, user_id)
        fresh = new_milestones(prefs.milestones_seen, keys, items)
        prefs.milestones_seen = keys
    if volunteering_on:
        volunteerings = _new_volunteerings(db, user_id, since=since, until=now, today=now.date())
    prefs.runs_notified_through = now

    db.flush()
    delivery = None
    if runs or ups or fresh or volunteerings:
        title, text = compose_message(
            runs=runs,
            ups=ups,
            milestones=fresh,
            poster_url=f"{base}/share",
            volunteerings=volunteerings,
            base_url=base,
            profile_url=f"{base}/users/{handle}",
        )
        only_volunteering = bool(volunteerings) and not (runs or ups or fresh)
        delivery = notifications.notify_user(
            db,
            user,
            KIND_VOLUNTEERING if only_volunteering else KIND_RUNS,
            title=title,
            text=text,
            dedupe_key="runs:"
            + _digest(
                [str(r.result_id) for r in runs]
                + [str(result_id) for item in volunteerings for result_id in item.result_ids]
                + [f"{u.code}:{u.tier}:{u.level}" for u in ups]
                + [milestone_key(m) for m in fresh]
            ),
            url=f"{base}/users/{handle}/volunteering" if only_volunteering else f"{base}/users/{handle}/runs",
            url_label="Моё волонтёрство на сайте" if only_volunteering else "Мои пробежки на сайте",
            commit=False,
        )
    db.commit()
    if delivery is not None:
        notifications.enqueue_delivery(delivery.id)
    return {
        "runs": len(runs),
        "volunteerings": len(volunteerings),
        "level_ups": len(ups),
        "milestones": len(fresh),
        "queued": delivery is not None,
    }


def weekly_ratings_message(db: Session, user_id: UUID) -> dict[str, Any]:
    """Воскресный проход: место в рейтингах против снимка недельной давности.

    Первый снимок молчит; дальше — одно сообщение в неделю, только если место
    в каком-то из рейтингов изменилось. Снимок обновляется здесь же, поэтому
    дельта всегда «за неделю», а не «с последней пробежки».
    """
    if not channels.is_enabled(db, user_id):
        return {"skipped": "disabled"}
    prefs = notifications.ensure_prefs(db, user_id)
    if not kind_enabled(prefs.kinds, KIND_RATINGS):
        return {"skipped": "kind off"}
    user = db.get(User, user_id)
    if user is None:
        return {"skipped": "no user"}
    current = ratings_snapshot(db, user)
    moves = rating_moves(prefs.ratings_snapshot, current)
    prefs.ratings_snapshot = current
    db.flush()
    delivery = None
    if moves:
        title, text = compose_ratings_message(moves)
        base = get_settings().app_base_url.rstrip("/")
        delivery = notifications.notify_user(
            db,
            user,
            KIND_RATINGS,
            title=title,
            text=text,
            dedupe_key="ratings:" + _digest([f"{m.metric}:{m.rank}:{m.gained}" for m in moves]),
            url=f"{base}/ratings",
            url_label="Рейтинги",
            commit=False,
        )
    db.commit()
    if delivery is not None:
        notifications.enqueue_delivery(delivery.id)
    return {"rating_moves": len(moves), "queued": delivery is not None}


def seed_ratings_snapshot(db: Session, user: User) -> None:
    """При включении уведомлений — запомнить места сразу, чтобы первое
    воскресное сообщение показало движение за реальную неделю, а не молчало."""
    try:
        prefs = notifications.ensure_prefs(db, user.id)
        prefs.ratings_snapshot = ratings_snapshot(db, user)
        db.flush()
    except Exception:  # noqa: BLE001 — рейтинг не построился: снимок возьмёт воскресный проход
        logger.exception("notify: ratings seed failed for %s", user.id)
