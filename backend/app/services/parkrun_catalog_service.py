"""Сверка сайта с официальным каталогом parkrun (events.json).

С 18.10.2026 каталог площадок parkrun живёт в боевой базе сайта (решение
Дмитрия: «все данные по локациям parkrun — в боевую базу»). Раньше его раз в
два-три дня скачивал parkrun-monitoring на VPS в свою SQLite и писал сводку в
ВК, а сайт смотрел только на снимки JSON в репо — и отставал от них.

Прогон (задача `parkrun_catalog.sync`, раз в сутки):
1. скачать events.json (на VPS напрямую; дома — только через прокси, см.
   fetch_catalog_payload: домашний адрес parkrun не видит);
2. разобрать и проверить, не урезан ли ответ (CatalogSuspicious — ничего не
   применяем и одной строкой говорим админу);
3. записать площадки, имена и страны, завести журнал изменений
   (появилась, вернулась, закрылась, переименована, перенесён старт, поменялось
   имя или серия);
4. полечить parkrun-локации сайта (services/parkrun_catalog_heal.py);
5. прислать одно сообщение в Telegram (services/parkrun_catalog_notify.py).

Повторный прогон того же каталога ничего не меняет: изменения считаются от
состояния базы, а не от прошлого ответа.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.runtime_env import is_test_run
from app.models import ParkrunCatalogRun, ParkrunCountry, ParkrunEvent, ParkrunEventChange
from app.parkrun.event_slugs import normalize_event_name

logger = logging.getLogger(__name__)

EVENTS_URL = "https://images.parkrun.com/events.json"
USER_AGENT = "saturday-runs-stats/1.0 (+https://run5k.run)"
# Псевдострана каталога: под нулём parkrun отдаёт мировые итоги, площадок нет.
GLOBAL_COUNTRY_CODE = 0
# Порог «перенесли старт»: меньше — уточнение точки, не новость.
MOVE_THRESHOLD_KM = 0.05


class CatalogFetchError(RuntimeError):
    """Каталог не скачался или не разбирается — прогон failed."""


class CatalogSuspicious(RuntimeError):
    """Каталог скачался, но похож на урезанный — не применяем."""


@dataclass(frozen=True)
class FeedEvent:
    parkrun_id: int | None
    slug: str
    short_name: str
    long_name: str | None
    local_name: str | None
    country_code: int | None
    series_id: int | None
    place: str | None
    latitude: float
    longitude: float


@dataclass
class Feed:
    events: list[FeedEvent]
    country_domains: dict[int, str | None]


@dataclass
class ApplyResult:
    added: int = 0
    returned: int = 0
    closed: int = 0
    renamed: int = 0
    moved: int = 0
    changed: int = 0
    active_total: int = 0
    changes: list[ParkrunEventChange] = field(default_factory=list)


def bare_name(value: str | None) -> str:
    """«parkrun Kolomenskoe», «Bushy Park parkrun» → название без слова parkrun."""
    name = (value or "").strip()
    name = re.sub(r"^parkrun\s+", "", name, flags=re.IGNORECASE)
    return re.sub(r"\s+parkrun$", "", name, flags=re.IGNORECASE).strip()


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlmb = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


# ------------------------------------------------------------------ скачивание


def fetch_catalog_payload() -> bytes:
    """Скачать events.json. Без сети в тестах; с домашнего адреса — только через прокси.

    `PARKRUN_SERVER_FETCH_ENABLED=false` ставится там, где адрес общий с домом
    (сайт после переезда): напрямую к parkrun оттуда не ходим, только через
    `PARKRUN_CATALOG_PROXY_URL`. Нет прокси — прогон пропускается.
    """
    if is_test_run():
        raise CatalogFetchError("под pytest в сеть не ходим — передайте каталог явно")
    settings = get_settings()
    proxy = (settings.parkrun_catalog_proxy_url or "").strip() or None
    if not settings.parkrun_server_fetch_enabled and proxy is None:
        raise CatalogFetchError("прямой запрос к parkrun с этого адреса запрещён, а прокси для каталога не задана")
    url = (settings.parkrun_catalog_url or "").strip() or EVENTS_URL
    try:
        response = httpx.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"},
            proxy=proxy,
            timeout=60.0,
            follow_redirects=True,
        )
    except httpx.HTTPError as exc:
        raise CatalogFetchError(f"{type(exc).__name__}: {exc}") from exc
    if response.status_code != 200:
        raise CatalogFetchError(f"HTTP {response.status_code}")
    return response.content


# ------------------------------------------------------------------- разбор


def parse_feed(payload: bytes | dict[str, Any]) -> Feed:
    if isinstance(payload, (bytes, bytearray)):
        try:
            data = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise CatalogFetchError("ответ не JSON (страница защиты?)") from exc
    else:
        data = payload
    if not isinstance(data, dict):
        raise CatalogFetchError("в ответе не объект JSON")
    events_block = data.get("events")
    features = events_block.get("features") if isinstance(events_block, dict) else None
    countries = data.get("countries")
    if not isinstance(features, list) or not features or not isinstance(countries, dict):
        raise CatalogFetchError("в ответе нет списка площадок или стран")
    country_domains: dict[int, str | None] = {}
    for code, info in countries.items():
        try:
            number = int(code)
        except (TypeError, ValueError):
            continue
        if number == GLOBAL_COUNTRY_CODE:
            continue
        url = info.get("url") if isinstance(info, dict) else None
        country_domains[number] = (str(url or "").strip() or None) if url is None or isinstance(url, str) else None
    events: list[FeedEvent] = []
    seen: set[str] = set()
    for feature in features:
        if not isinstance(feature, dict):
            continue
        properties = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
        geometry = feature.get("geometry") if isinstance(feature.get("geometry"), dict) else {}
        point = _point(geometry.get("coordinates"))
        slug = _text(properties.get("eventname"), 128)
        short = _text(properties.get("EventShortName"), 256)
        if not slug or not short or point is None or slug in seen:
            continue
        seen.add(slug)
        code = properties.get("countrycode")
        series = properties.get("seriesid")
        parkrun_id = feature.get("id")
        events.append(
            FeedEvent(
                parkrun_id=parkrun_id if isinstance(parkrun_id, int) and not isinstance(parkrun_id, bool) else None,
                slug=slug,
                short_name=short,
                long_name=_text(properties.get("EventLongName"), 256),
                local_name=_text(properties.get("LocalisedEventLongName"), 256),
                country_code=code if isinstance(code, int) and not isinstance(code, bool) else None,
                series_id=series if isinstance(series, int) and not isinstance(series, bool) else None,
                place=_text(properties.get("EventLocation"), 256),
                latitude=point[1],
                longitude=point[0],
            )
        )
    if not events:
        raise CatalogFetchError("в каталоге ни одной площадки")
    return Feed(events=events, country_domains=country_domains)


def _text(value: Any, limit: int) -> str | None:
    """Строка из каталога под размер колонки. Слаг длиннее колонки — не слаг."""
    if value is None or isinstance(value, (dict, list)):
        return None
    out = str(value).strip()
    if not out:
        return None
    if len(out) > limit:
        return None if limit == 128 else out[:limit]
    return out


def _point(value: Any) -> tuple[float, float] | None:
    """[долгота, широта] — только два настоящих числа в своих пределах."""
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        longitude, latitude = float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(latitude) and math.isfinite(longitude)) or abs(latitude) > 90 or abs(longitude) > 180:
        return None
    return longitude, latitude


def check_feed(db: Session, feed: Feed) -> list[str]:
    """Причины не доверять каталогу. Пусто — можно применять.

    Урезанный ответ (обрыв, страница CDN, ошибка parkrun) иначе закрыл бы
    сотни площадок разом и разослал бы об этом сообщение.
    """
    active = {row[0] for row in db.query(ParkrunEvent.slug).filter(ParkrunEvent.is_active.is_(True)).all()}
    if not active:
        return []  # первый прогон: сравнивать не с чем
    feed_slugs = {event.slug for event in feed.events}
    feed_ids = {event.parkrun_id for event in feed.events if event.parkrun_id is not None}
    # Переименованная площадка уходит из слагов, но остаётся по id — не пропажа.
    renamed_away = {
        row[0]
        for row in db.query(ParkrunEvent.slug)
        .filter(ParkrunEvent.is_active.is_(True), ParkrunEvent.parkrun_id.in_(feed_ids or {-1}))
        .all()
    }
    vanished = active - feed_slugs - renamed_away
    reasons: list[str] = []
    limit = max(20, round(len(active) * 0.02))
    if len(vanished) > limit:
        reasons.append(f"пропало бы {len(vanished)} из {len(active)} действующих (порог {limit})")
    if len(feed.events) < len(active) * 0.95:
        reasons.append(f"площадок в ответе {len(feed.events)} против {len(active)} действующих")
    known_countries = {
        row[0]
        for row in db.query(ParkrunCountry.code).filter(ParkrunCountry.is_active.is_(True)).all()
    }
    lost_countries = known_countries - set(feed.country_domains)
    if len(lost_countries) > 2:
        reasons.append(f"пропало стран: {len(lost_countries)}")
    return reasons


# ---------------------------------------------------------------- применение


def _names_for(event: FeedEvent) -> dict[str, str]:
    names: dict[str, str] = {}
    for kind, value in (("short", event.short_name), ("long", bare_name(event.long_name)), ("local", bare_name(event.local_name))):
        key = normalize_event_name(value or "")
        if key and key not in names:
            names[key] = kind
    return names


def _upsert_names(db: Session, event_id: int, names: dict[str, str]) -> None:
    """Имена площадки с их видом. Вид обновляется: прежнее короткое имя после
    переименования становится старым, а вернувшееся имя — снова нынешним."""
    for name_norm, kind in names.items():
        db.execute(
            text(
                """
                insert into parkrun_event_names (name_norm, event_id, kind) values (:name, :event, :kind)
                on conflict (name_norm, event_id) do update set kind = excluded.kind
                """
            ),
            {"name": name_norm, "event": event_id, "kind": kind},
        )


def apply_feed(db: Session, feed: Feed, run: ParkrunCatalogRun, now: datetime) -> ApplyResult:
    """Привести каталог в базе к ответу parkrun и записать журнал изменений."""
    result = ApplyResult()
    rows = db.query(ParkrunEvent).all()
    by_id = {row.parkrun_id: row for row in rows if row.parkrun_id is not None}
    by_slug = {row.slug: row for row in rows}
    feed_ids = {item.parkrun_id for item in feed.events if item.parkrun_id is not None}
    original_slug: dict[int, str] = {}
    seen_ids: set[int] = set()

    def change(event: ParkrunEvent, kind: str, details: dict[str, Any]) -> None:
        item = ParkrunEventChange(run_id=run.id, event_id=event.id, kind=kind, details=details, detected_at=now)
        db.add(item)
        result.changes.append(item)

    for item in feed.events:
        row = by_id.get(item.parkrun_id) if item.parkrun_id is not None else None
        if row is None:
            row = by_slug.get(item.slug)
        if row is None:
            row = ParkrunEvent(
                parkrun_id=item.parkrun_id,
                slug=item.slug,
                short_name=item.short_name,
                long_name=item.long_name,
                local_name=item.local_name,
                country_code=item.country_code,
                series_id=item.series_id,
                place=item.place,
                latitude=item.latitude,
                longitude=item.longitude,
                is_active=True,
                source="live",
                first_seen_at=now,
                last_seen_at=now,
            )
            db.add(row)
            db.flush()
            by_slug[row.slug] = row
            change(row, "added", {"slug": row.slug, "name": row.short_name})
            result.added += 1
        else:
            if not row.is_active:
                change(row, "returned", {"closed_at": row.closed_at.isoformat() if row.closed_at else None})
                result.returned += 1
            if row.slug != item.slug:
                old_slug = original_slug.get(row.id, row.slug)
                old_name = row.short_name
                if item.slug in by_slug and by_slug[item.slug] is not row:
                    holder = by_slug.pop(item.slug)
                    if holder.parkrun_id is not None and holder.parkrun_id in feed_ids:
                        # Слаг занят живой площадкой, которую parkrun в этом же ответе
                        # переименовал дальше (A→B, B→C) — уступит слаг, когда до неё
                        # дойдёт очередь. Пока — временный слаг, журнал помнит прежний.
                        original_slug.setdefault(holder.id, holder.slug)
                        holder.slug = f"~{holder.id}"
                        by_slug[holder.slug] = holder
                        db.flush()
                    else:
                        # Слаг занят строкой без id или пропавшей из каталога (сид, архив) —
                        # сливаем её сюда.
                        db.execute(
                            text("update parkrun_event_names set event_id = :keep where event_id = :drop "
                                 "and name_norm not in (select name_norm from parkrun_event_names where event_id = :keep)"),
                            {"keep": row.id, "drop": holder.id},
                        )
                        db.delete(holder)
                        db.flush()
                        rows = [other for other in rows if other is not holder]
                by_slug.pop(row.slug, None)
                row.slug = item.slug
                by_slug[row.slug] = row
                db.flush()
                # Старые имена остаются при площадке: профиль атлета может прислать их.
                old_key = normalize_event_name(old_name or "")
                if old_key and old_key != normalize_event_name(item.short_name):
                    _upsert_names(db, row.id, {old_key: "old"})
                change(row, "renamed", {"old_slug": old_slug, "new_slug": item.slug, "old_name": old_name, "new_name": item.short_name})
                result.renamed += 1
            if row.latitude is not None and row.longitude is not None:
                moved = distance_km(row.latitude, row.longitude, item.latitude, item.longitude)
                if moved > MOVE_THRESHOLD_KM:
                    change(
                        row,
                        "moved",
                        {"old": [row.latitude, row.longitude], "new": [item.latitude, item.longitude], "distance_km": round(moved, 3)},
                    )
                    result.moved += 1
            fields = {}
            for attr in ("short_name", "long_name", "local_name", "country_code", "series_id", "place"):
                old_value, new_value = getattr(row, attr), getattr(item, attr)
                if old_value != new_value and new_value is not None:
                    fields[attr] = [old_value, new_value]
            if fields and row.parkrun_id is not None:
                # У строки из сида (без id) поля заполнялись из других источников —
                # первое совпадение с живым каталогом не новость.
                change(row, "field", fields)
                result.changed += 1
            if row.parkrun_id is None and item.parkrun_id is not None:
                row.parkrun_id = item.parkrun_id
            row.short_name = item.short_name
            row.long_name = item.long_name
            row.local_name = item.local_name
            row.country_code = item.country_code
            row.series_id = item.series_id
            row.place = item.place
            row.latitude = item.latitude
            row.longitude = item.longitude
            row.is_active = True
            row.source = "live"
            row.closed_at = None
            row.first_seen_at = row.first_seen_at or now
            row.last_seen_at = now
            row.updated_at = now
        if item.parkrun_id is not None:
            seen_ids.add(item.parkrun_id)
        _upsert_names(db, row.id, _names_for(item))

    feed_slugs = {item.slug for item in feed.events}
    for row in rows:
        if row.is_active and row.slug not in feed_slugs and (row.parkrun_id is None or row.parkrun_id not in seen_ids):
            row.is_active = False
            row.source = "closed"
            row.closed_at = now
            row.updated_at = now
            change(row, "closed", {"last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None})
            result.closed += 1

    for code, domain in feed.country_domains.items():
        country = db.get(ParkrunCountry, code)
        if country is None:
            db.add(ParkrunCountry(code=code, domain=domain, is_active=True))
        elif country.domain != domain or not country.is_active:
            country.domain = domain
            country.is_active = True
            country.updated_at = now
    db.flush()
    result.active_total = sum(1 for item in feed.events)
    return result


# ------------------------------------------------------------------ прогон


def payload_digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def last_good_run(db: Session) -> ParkrunCatalogRun | None:
    return (
        db.query(ParkrunCatalogRun)
        .filter(ParkrunCatalogRun.status.in_(("ok", "unchanged", "seed")))
        .order_by(ParkrunCatalogRun.started_at.desc())
        .first()
    )


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- оркестрация

# Ключ advisory-lock: два прогона сверки одновременно не идут.
LOCK_KEY = 0x70726B63  # 'prkc'
FAILING = ("failed", "suspicious")
GOOD = ("ok", "unchanged", "seed")
REMIND_EVERY_DAYS = 7
# Сколько дней досылаем изменения, которые не дошли до админа (упала отправка).
PENDING_DAYS = 7


@contextmanager
def _run_lock(db: Session):
    """Advisory-lock прогона — на своём соединении, а не на соединении сессии.

    Блокировка сессионная: commit возвращает соединение сессии в пул, unlock
    ушёл бы на другое, а блокировка осталась бы висеть на простаивающем, пока
    его не переоткроют. Своё соединение — в AUTOCOMMIT: транзакцию оно не
    держит, и idle_in_transaction_session_timeout его не убивает.
    """
    bind = db.get_bind()
    if isinstance(bind, Engine):
        conn = bind.connect().execution_options(isolation_level="AUTOCOMMIT")
        owned = True
    else:  # тесты: сессии привязаны к одному соединению
        conn, owned = bind, False
    locked = False
    try:
        locked = bool(conn.execute(text("select pg_try_advisory_lock(:key)"), {"key": LOCK_KEY}).scalar())
        yield locked
    finally:
        try:
            if locked:
                try:
                    conn.execute(text("select pg_advisory_unlock(:key)"), {"key": LOCK_KEY})
                except Exception:  # noqa: BLE001
                    if owned:
                        conn.invalidate()  # закрытое соединение уносит блокировку с собой
        finally:
            if owned:
                conn.close()


def _change_dicts(db: Session, changes: list[ParkrunEventChange]) -> list[dict[str, Any]]:
    events = {
        row.id: row
        for row in db.query(ParkrunEvent).filter(ParkrunEvent.id.in_({c.event_id for c in changes} or {-1})).all()
    }
    out = []
    for change in sorted(changes, key=lambda item: item.id or 0):
        event = events.get(change.event_id)
        if event is None:
            continue
        out.append(
            {
                "kind": change.kind,
                "slug": event.slug,
                "name": event.short_name,
                "country_code": event.country_code,
                "series_id": event.series_id,
                "details": change.details or {},
            }
        )
    return out


def _previous_runs(db: Session, current_id: int) -> list[ParkrunCatalogRun]:
    return (
        db.query(ParkrunCatalogRun)
        .filter(ParkrunCatalogRun.id != current_id, ParkrunCatalogRun.status != "skipped")
        .order_by(ParkrunCatalogRun.started_at.desc(), ParkrunCatalogRun.id.desc())
        .limit(60)
        .all()
    )


def _pending_runs(db: Session, now: datetime) -> list[ParkrunCatalogRun]:
    """Прогоны с изменениями, о которых админ ещё не узнал (в том числе текущий).

    notified_at у прогона «ok» ставится, когда сводка дошла или сообщать было
    нечего. Упала отправка (Telegram и ВК лежат, Redis недоступен) — изменения
    уйдут со следующим прогоном, а не потеряются: завтрашний каталог обычно тот
    же, и прогон «unchanged» своих изменений не имеет.
    """
    return (
        db.query(ParkrunCatalogRun)
        .filter(
            ParkrunCatalogRun.status == "ok",
            ParkrunCatalogRun.notified_at.is_(None),
            ParkrunCatalogRun.started_at >= now - timedelta(days=PENDING_DAYS),
        )
        .order_by(ParkrunCatalogRun.started_at, ParkrunCatalogRun.id)
        .all()
    )


def _alert_for(db: Session, run: ParkrunCatalogRun, now: datetime) -> str | None:
    """Тревога или «восстановлено» по смене состояния — без ежедневного спама."""
    from app.services.parkrun_catalog_notify import failed_alert, recovered_alert, suspicious_alert

    previous = _previous_runs(db, run.id)
    if run.status in FAILING:
        failing_streak = []
        for item in previous:
            if item.status not in FAILING:
                break
            failing_streak.append(item)
        last_alert = max((item.notified_at for item in failing_streak if item.notified_at), default=None)
        if failing_streak and last_alert and (now - last_alert).days < REMIND_EVERY_DAYS:
            return None
        if run.status == "failed":
            return failed_alert(now, run.error or "неизвестная ошибка")
        return suspicious_alert(now, (run.summary or {}).get("reasons") or [run.error or ""])
    if run.status in GOOD and previous and previous[0].status in FAILING:
        skipped = 0
        for item in previous:
            if item.status not in FAILING:
                break
            skipped += 1
        return recovered_alert(skipped)
    return None


def conflict_key(item: str) -> str:
    """Ключ конфликта лечения без чисел и текста ошибки — чтобы узнать его назавтра."""
    return re.sub(r"\d+", "#", item.split(": ", 1)[0])


def _reported_conflicts(db: Session, current_id: int, now: datetime) -> set[str]:
    """Конфликты, о которых уже говорили на этой неделе: повторяем раз в неделю, а не каждый день."""
    keys: set[str] = set()
    for item in (
        db.query(ParkrunCatalogRun)
        .filter(
            ParkrunCatalogRun.id != current_id,
            ParkrunCatalogRun.notified_at >= now - timedelta(days=REMIND_EVERY_DAYS),
        )
        .all()
    ):
        keys.update(((item.summary or {}).get("heal") or {}).get("conflicts_reported") or [])
    return keys


def _set_summary(db: Session, run_id: int, **values: Any) -> None:
    run = db.get(ParkrunCatalogRun, run_id)
    run.summary = {**(run.summary or {}), **values}
    db.commit()


def run_catalog_sync(
    session_factory,
    *,
    payload: bytes | None = None,
    force: bool = False,
    heal: bool = True,
    notify: bool = True,
    heal_limits: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Один прогон сверки. Возвращает итог для истории автообновления."""
    now = now or now_utc()
    db = session_factory()
    try:
        with _run_lock(db) as locked:
            if not locked:
                return {"status": "skipped", "skipped": True, "reason": "сверка уже идёт", "errors": []}
            return _locked_run(
                db, session_factory, payload=payload, force=force, heal=heal, notify=notify, heal_limits=heal_limits, now=now
            )
    finally:
        db.close()


def _locked_run(
    db: Session,
    session_factory,
    *,
    payload: bytes | None,
    force: bool,
    heal: bool,
    notify: bool,
    heal_limits: bool,
    now: datetime,
) -> dict[str, Any]:
    from app.services.admin_telegram_notify import send_admin_report
    from app.services.parkrun_catalog_heal import heal_parkrun_locations
    from app.services.parkrun_catalog_notify import build_catalog_message

    run = ParkrunCatalogRun(status="running", started_at=now, summary={})
    db.add(run)
    db.commit()
    run_id = run.id

    reasons: list[str] = []
    size = digest = None
    try:
        data = payload if payload is not None else fetch_catalog_payload()
        size, digest = len(data), payload_digest(data)
        run.payload_bytes, run.payload_sha256 = size, digest
        previous_good = (
            db.query(ParkrunCatalogRun)
            .filter(ParkrunCatalogRun.id != run_id, ParkrunCatalogRun.status.in_(GOOD))
            .order_by(ParkrunCatalogRun.started_at.desc(), ParkrunCatalogRun.id.desc())
            .first()
        )
        if not force and previous_good is not None and previous_good.payload_sha256 == digest:
            run.status = "unchanged"
            run.active_total = previous_good.active_total
        else:
            feed = parse_feed(data)
            reasons = check_feed(db, feed)
            if reasons and not force:
                raise CatalogSuspicious("; ".join(reasons))
            result = apply_feed(db, feed, run, now)
            run.status = "ok"
            run.active_total = result.active_total
            run.summary = {
                "catalog_added": result.added,
                "catalog_returned": result.returned,
                "catalog_closed": result.closed,
                "catalog_renamed": result.renamed,
                "catalog_moved": result.moved,
                "catalog_changed": result.changed,
                "forced": bool(force and reasons),
            }
    except CatalogSuspicious as exc:
        db.rollback()
        run.status = "suspicious"
        run.error = str(exc)[:500]
        run.summary = {"reasons": reasons}
    except CatalogFetchError as exc:
        db.rollback()
        run.status = "failed"
        run.error = str(exc)[:500]
    except Exception as exc:  # noqa: BLE001 — любой сбой: прогон failed и тревога, а не вечный «running»
        logger.exception("parkrun catalog: прогон упал")
        db.rollback()
        run.status = "failed"
        run.error = f"{type(exc).__name__}: {(str(exc).splitlines() or [''])[0]}"[:500]
    if run.status in FAILING and size is not None:
        run.payload_bytes, run.payload_sha256 = size, digest
    run.finished_at = now_utc()
    db.commit()

    status = run.status
    errors: list[str] = [run.error] if status in FAILING and run.error else []

    pending = _pending_runs(db, now) if status in ("ok", "unchanged") else []
    changes = _change_dicts(
        db,
        db.query(ParkrunEventChange).filter(ParkrunEventChange.run_id.in_([item.id for item in pending] or [-1])).all(),
    )
    pending_ids = [item.id for item in pending]
    db.commit()  # пока идут лечение и отправка, транзакцию не держим

    heal_report = None
    if heal and status in ("ok", "unchanged"):
        moved = [
            {"slug": c["slug"], "old": c["details"]["old"], "new": c["details"]["new"], "distance_km": c["details"].get("distance_km")}
            for c in changes
            if c["kind"] == "moved"
        ]
        try:
            heal_report = heal_parkrun_locations(session_factory, moved_events=moved, limits=heal_limits)
        except Exception as exc:  # noqa: BLE001 — лечение не должно ронять сверку
            logger.exception("parkrun catalog heal: упало")
            from app.services.parkrun_catalog_heal import HealReport

            heal_report = HealReport(conflicts=[f"лечение: {type(exc).__name__}: {(str(exc).splitlines() or [''])[0][:200]}"])
        _set_summary(db, run_id, heal=heal_report.as_dict())
        if heal_report.touched_location_ids:
            try:
                from app.services.location_freshness import mark_location_results_changed

                mark_location_results_changed(db, heal_report.touched_location_ids, reason="parkrun: сверка с каталогом")
                db.commit()
            except Exception:  # noqa: BLE001 — кэши дотухнут сами
                db.rollback()
                logger.exception("parkrun catalog heal: кэши локаций не сброшены")

    if notify:
        run = db.get(ParkrunCatalogRun, run_id)
        alert = _alert_for(db, run, now)
        heal_dict = heal_report.as_dict() if heal_report is not None else {}
        reported = _reported_conflicts(db, run_id, now) if heal_dict.get("conflicts") else set()
        fresh_conflicts = [item for item in heal_dict.get("conflicts") or [] if conflict_key(item) not in reported]
        message = build_catalog_message(
            db,
            changes,
            {**heal_dict, "conflicts": fresh_conflicts},
            active_total=run.active_total or 0,
            when=now,
        )
        db.commit()  # перед походом в сеть — без открытой транзакции
        alert_sent = bool(alert) and _send(send_admin_report, alert, errors)
        message_sent = bool(message) and _send(send_admin_report, message, errors)
        stamp = now  # время прогона: по нему считаются «раз в неделю» тревог и конфликтов
        run = db.get(ParkrunCatalogRun, run_id)
        if alert_sent or message_sent:
            run.notified_at = stamp
        if message is None or message_sent:
            # Сообщать было нечего или сводка дошла: досылать нечего.
            for item in db.query(ParkrunCatalogRun).filter(ParkrunCatalogRun.id.in_(pending_ids or [-1])).all():
                item.notified_at = item.notified_at or stamp
        if message_sent and fresh_conflicts:
            heal_summary = dict((run.summary or {}).get("heal") or {})
            heal_summary["conflicts_reported"] = sorted({conflict_key(item) for item in fresh_conflicts})
            run.summary = {**(run.summary or {}), "heal": heal_summary}
        db.commit()
    elif pending_ids:
        # --no-notify: о каталоге этого прогона молчим и потом не досылаем.
        stamp = now
        for item in db.query(ParkrunCatalogRun).filter(ParkrunCatalogRun.id.in_(pending_ids)).all():
            item.notified_at = item.notified_at or stamp
        db.commit()

    run = db.get(ParkrunCatalogRun, run_id)
    result: dict[str, Any] = {"status": run.status, "run_id": run_id, "active_total": run.active_total, **(run.summary or {})}
    if errors:
        result["errors"] = errors
    db.commit()
    return result


def _send(send, text_value: str, errors: list[str]) -> bool:
    try:
        return bool(send(text_value, markup=True))
    except Exception as exc:  # noqa: BLE001 — недоставка не роняет прогон, сводку дошлём завтра
        logger.exception("parkrun catalog: сообщение не отправлено")
        errors.append(f"отправка: {type(exc).__name__}: {exc}"[:300])
        return False
