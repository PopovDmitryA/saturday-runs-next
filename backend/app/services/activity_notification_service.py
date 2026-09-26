"""Уведомление о собственной пробежке — одним сообщением.

Сканер зовётся после синков (прогрев дашбордов — для батчей, user sync — для
кнопки «Обновить») и раз в десять минут по beat (ловит результаты, которые
пишут мимо воркеров, например parkrun с Mac-демона). Для каждого человека с
включёнными уведомлениями:

* новые результаты — run_results, записанные после водяного знака
  prefs.runs_notified_through, с датой старта не старше окна
  notifications_runs_window_days (первичная загрузка истории приносит сотни
  строк, писать о каждой нельзя);
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
from uuid import UUID

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Event, Location, Participant, Platform, PlatformLink, RunResult, User
from app.notification_kinds import kind_enabled
from app.notification_markup import bold, link
from app.services import notification_channels_service as channels
from app.services import notification_service as notifications
from app.services.achievements_service import TIER_LABELS, compute_challenges
from app.services.leaderboard_service import get_my_leaderboard_row, metric_title
from app.services.my_history_service import get_my_history
from app.services.platform_titles import PLATFORM_TITLES
from app.time_format import normalize_finish_time_display

logger = logging.getLogger(__name__)

KIND_RUNS = "runs"
KIND_RATINGS = "ratings"

# Рейтинги, за движением в которых следим: код → подпись в сообщении.
# Подпись — то же название, что у рейтинга на сайте (METRIC_META = дерево
# навигации, RATING_GROUPS): «Количество пробежек», «Уникальные локации»,
# «Первые места». Раньше тут были свои слова («Пробежки», «Победы»), и один
# рейтинг звался в уведомлении иначе, чем на странице, куда ведёт ссылка.
RATING_METRICS: tuple[tuple[str, str], ...] = tuple(
    (metric, metric_title(metric)) for metric in ("runs", "locations", "wins")
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
        )
        for result, event, location, platform in rows
    ]


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


def run_block(run: NewRun) -> str:
    """Две строки: где и когда — жирным, ниже время, место и отметки."""
    head = f"📍 {run.location_name}"
    system = PLATFORM_TITLES.get(run.platform_code)
    if system:
        head += f" ({system})"
    if run.event_number:
        head += f" №{run.event_number}"
    head += f" · {_date_label(run.event_date)}"
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
    lines = [bold(head)]
    if facts:
        lines.append(" · ".join(facts))
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
) -> tuple[str, str]:
    """(заголовок, тело в разметке)."""
    settings = get_settings()
    limit = settings.notifications_runs_max_listed
    sections: list[str] = []
    if runs:
        blocks = [run_block(run) for run in runs[:limit]]
        rest = len(runs) - limit
        if rest > 0:
            blocks.append(f"…и ещё {rest}")
        sections.append("\n\n".join(blocks))
    if ups:
        sections.append("🏆 " + bold("Челленджи:") + "\n" + "\n".join(level_line(u) for u in ups))
    if milestones:
        sections.append("🎖 " + bold("Вехи истории:") + "\n" + "\n".join(milestone_line(m) for m in milestones[:6]))
    if runs and poster_url:
        sections.append("🖼 " + link("Собрать постер о пробежке", poster_url) + " — поделитесь результатом в сториз.")

    if runs:
        title = "🏃 Пробежка попала на сайт" if len(runs) == 1 else f"🏃 Новые пробежки на сайте: {len(runs)}"
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
    if not kind_enabled(prefs.kinds, KIND_RUNS):
        return {"skipped": "kind off"}
    user = db.get(User, user_id)
    if user is None:
        return {"skipped": "no user"}

    settings = get_settings()
    base = settings.app_base_url.rstrip("/")
    handle = _profile_handle(user)

    since = prefs.runs_notified_through or (now - timedelta(days=1))
    runs = _new_runs(db, user_id, since=since, until=now, today=now.date())
    prefs.runs_notified_through = now

    payload = compute_challenges(db, user_id)
    current_levels = challenge_levels_snapshot(payload)
    ups = level_ups(prefs.challenge_levels, current_levels, payload)
    prefs.challenge_levels = current_levels

    keys, items = milestones_snapshot(db, user_id)
    fresh = new_milestones(prefs.milestones_seen, keys, items)
    prefs.milestones_seen = keys

    db.flush()
    delivery = None
    if runs or ups or fresh:
        title, text = compose_message(runs=runs, ups=ups, milestones=fresh, poster_url=f"{base}/share")
        delivery = notifications.notify_user(
            db,
            user,
            KIND_RUNS,
            title=title,
            text=text,
            dedupe_key="runs:"
            + _digest(
                [str(r.result_id) for r in runs]
                + [f"{u.code}:{u.tier}:{u.level}" for u in ups]
                + [milestone_key(m) for m in fresh]
            ),
            url=f"{base}/users/{handle}/runs",
            url_label="Мои пробежки на сайте",
            commit=False,
        )
    db.commit()
    if delivery is not None:
        notifications.enqueue_delivery(delivery.id)
    return {
        "runs": len(runs),
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
