"""Сообщение админу о смене каталога parkrun — Telegram, удобно читать с телефона.

До 18.10.2026 сводку слал parkrun-monitoring в ВК голым списком
«eventname (long_name)» через запятую, без страны и ссылок, а изменения —
именами полей без значений («medina: latitude, longitude»). Теперь:
* сначала то, что требует внимания (⚠️): закрылась площадка, где бегали наши,
  перенесли старт, сайт не смог что-то поправить сам;
* дальше — что сайт сделал сам и что нового в каталоге, по странам с флагами;
* у каждой площадки ссылка на её страницу у parkrun (домен страны) и, если она
  есть у нас, на страницу локации run5k.run со счётчиком стартов;
* без изменений — тишина; сбой сбора — одна тревога, а не каждый день.

Разметка — app/notification_markup.py (`**жирный**`, `[подпись](url)`).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.notification_markup import link, to_telegram_html
from app.parkrun.catalog_store import resolve_catalog_event

MSK = ZoneInfo("Europe/Moscow")
# Telegram режет сообщение на 4096 символах HTML; держим запас.
MAX_HTML = 3800
JUNIORS_SERIES = 2

# Код страны parkrun → ISO для флага.
COUNTRY_ISO2: dict[int, str] = {
    3: "AU", 4: "AT", 14: "CA", 23: "DK", 30: "FI", 31: "FR", 32: "DE", 42: "IE", 44: "IT", 46: "JP",
    54: "LT", 57: "MY", 64: "NL", 65: "NZ", 67: "NO", 74: "PL", 79: "RU", 82: "SG", 85: "ZA", 88: "SE",
    97: "GB", 98: "US",
}


def flag(code: int | None) -> str:
    iso = COUNTRY_ISO2.get(code or -1)
    if not iso:
        return "🏳"
    return "".join(chr(0x1F1E6 + ord(letter) - ord("A")) for letter in iso)


@dataclass
class SiteStats:
    starts: int
    runners: int
    last_date: date | None


def site_stats(db: Session, slug: str) -> SiteStats | None:
    row = db.execute(
        text(
            """
            select count(distinct e.id), count(distinct pl.user_id), max(e.event_date)
              from locations l
              join platforms p on p.id = l.platform_id and p.code = 'parkrun'
              left join events e on e.location_id = l.id
              left join run_results rr on rr.event_id = e.id
              left join platform_links pl on pl.participant_id = rr.participant_id and pl.user_id is not null
             where l.external_key = :slug
            """
        ),
        {"slug": slug},
    ).first()
    if row is None or not row[0]:
        return None
    return SiteStats(starts=int(row[0]), runners=int(row[1] or 0), last_date=row[2])


def _plural(count: int, one: str, few: str, many: str) -> str:
    tail = count % 100
    if 11 <= tail <= 14:
        return many
    tail %= 10
    if tail == 1:
        return one
    if 2 <= tail <= 4:
        return few
    return many


def _event_label(db: Session, change: dict[str, Any]) -> str:
    event = resolve_catalog_event(db, change["slug"])
    name = change.get("name") or change["slug"]
    url = event.page_url if event is not None else None
    juniors = "🧒 " if change.get("series_id") == JUNIORS_SERIES else ""
    return f"{juniors}{link(name, url) if url else name}"


def _listing(items: list[str], limit: int = 5) -> str:
    """«3: a, b, c» или «87: a, b, c, d, e и ещё 82» — список не раздувает сообщение."""
    shown = ", ".join(items[:limit])
    rest = len(items) - limit
    return f"{len(items)}: {shown}" + (f" и ещё {rest}" if rest > 0 else "")


def _site_link(slug: str) -> str:
    return link("у нас", f"{get_settings().app_base_url.rstrip('/')}/locations/{slug}")


def _stats_line(stats: SiteStats) -> str:
    parts = [f"{stats.starts} {_plural(stats.starts, 'старт', 'старта', 'стартов')}"]
    if stats.runners:
        parts.append(f"{stats.runners} {_plural(stats.runners, 'бегун', 'бегуна', 'бегунов')} сайта")
    if stats.last_date:
        parts.append(f"последний {stats.last_date:%d.%m.%Y}")
    return " · ".join(parts)


def build_catalog_message(
    db: Session,
    changes: list[dict[str, Any]],
    heal: dict[str, Any] | None,
    *,
    active_total: int,
    when: datetime,
) -> str | None:
    """Текст сводки (разметка) или None, если сообщать нечего.

    `changes` — словари {kind, slug, name, country_code, series_id, details}.
    Сообщаем о появившихся, вернувшихся, закрытых, переименованных площадках,
    о переносе старта больше километра и обо всём, что сайт сделал сам.
    Поправки имён и мелкие уточнения точки сайт молча применяет.
    """
    heal = heal or {}
    by_kind: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for change in changes:
        by_kind[change["kind"]].append(change)
    big_moves = [c for c in by_kind["moved"] if (c.get("details") or {}).get("distance_km", 0) >= 1.0]
    big_move_slugs = {c["slug"] for c in big_moves}

    counters = []
    if by_kind["added"]:
        counters.append(f"+{len(by_kind['added'])}")
    if by_kind["closed"]:
        counters.append(f"−{len(by_kind['closed'])}")
    if by_kind["returned"]:
        counters.append(f"↩{len(by_kind['returned'])}")
    if by_kind["renamed"]:
        counters.append(f"✏️{len(by_kind['renamed'])}")
    header = f"🗺 **parkrun · каталог {when.astimezone(MSK):%d.%m}**" + (f" · {' '.join(counters)}" if counters else "")

    attention: list[str] = []
    closed_ours: list[str] = []
    closed_other: list[dict[str, Any]] = []
    for change in by_kind["closed"]:
        stats = site_stats(db, change["slug"])
        if stats is not None:
            closed_ours.append(f"{flag(change.get('country_code'))} {_event_label(db, change)} — {_stats_line(stats)} · {_site_link(change['slug'])}")
        else:
            closed_other.append(change)
    if closed_ours:
        attention += ["⚠️ **Закрылись — у нас были старты**", *closed_ours, ""]

    moved_done = {item["slug"] for item in heal.get("moved") or []}
    moved_skipped = {item["slug"]: item for item in heal.get("moved_skipped") or []}
    move_lines = []
    for change in big_moves:
        distance = (change.get("details") or {}).get("distance_km", 0)
        km = f"{distance:.1f}".replace(".", ",")
        line = f"{flag(change.get('country_code'))} {_event_label(db, change)} — на {km} км"
        if site_stats(db, change["slug"]) is not None:
            line += f" · {_site_link(change['slug'])}"
            if change["slug"] in moved_done:
                line += "\n   точка на сайте перенесена"
            elif change["slug"] in moved_skipped:
                line += "\n   точку на сайте правили руками — не трогал"
        move_lines.append(line)
    if move_lines:
        attention += ["⚠️ **Перенесли старт**", *move_lines, ""]
    if heal.get("conflicts"):
        attention += ["⚠️ **Сайт не смог поправить сам**", *[f"- {item}" for item in heal["conflicts"]], ""]

    body: list[str] = []
    if by_kind["returned"]:
        lines = []
        for change in by_kind["returned"]:
            line = f"{flag(change.get('country_code'))} {_event_label(db, change)}"
            closed_at = (change.get("details") or {}).get("closed_at")
            if closed_at:
                line += f" — пропадала с {datetime.fromisoformat(closed_at).astimezone(MSK):%d.%m.%Y}"
            if site_stats(db, change["slug"]) is not None:
                line += f" · {_site_link(change['slug'])}"
            lines.append(line)
        body += ["↩️ **Вернулись в каталог**", *lines, ""]
    if by_kind["renamed"]:
        body += [
            "✏️ **Переименованы**",
            *[
                f"{flag(c.get('country_code'))} {(c.get('details') or {}).get('old_name') or (c.get('details') or {}).get('old_slug')} → {_event_label(db, c)}"
                for c in by_kind["renamed"]
            ],
            "",
        ]
    fixed = []
    if heal.get("merged"):
        fixed.append(f"- слиты двойники — {_listing(heal['merged'])}")
    if heal.get("renamed"):
        fixed.append(f"- ключ по слагу parkrun — {_listing(heal['renamed'])}")
    if heal.get("points_set"):
        fixed.append(f"- точка старта — {_listing(heal['points_set'])}")
    if heal.get("countries_set"):
        fixed.append(f"- страна — {_listing(heal['countries_set'])}")
    if heal.get("links_fixed"):
        count = heal["links_fixed"]
        fixed.append(f"- ссылки на сайт parkrun страны: {count} {_plural(count, 'локация', 'локации', 'локаций')}")
    small_moves = [item.get("name") or item["slug"] for item in heal.get("moved") or [] if item["slug"] not in big_move_slugs]
    if small_moves:
        fixed.append(f"- точка старта вслед за parkrun — {_listing(small_moves)}")
    if fixed:
        body += ["🔧 **Сайт поправил сам**", *fixed, ""]

    def grouped(items: list[dict[str, Any]], *, compact: bool) -> list[str]:
        per_country: dict[int | None, list[dict[str, Any]]] = defaultdict(list)
        for change in items:
            per_country[change.get("country_code")].append(change)
        order = sorted(per_country, key=lambda code: (-len(per_country[code]), code or 0))
        if compact:
            return [" · ".join(f"{flag(code)} {len(per_country[code])}" for code in order)]
        return [f"{flag(code)} " + " · ".join(_event_label(db, c) for c in per_country[code]) for code in order]

    def render(compact: bool) -> str:
        tail: list[str] = []
        if by_kind["added"]:
            tail += [f"➕ **Новые — {len(by_kind['added'])}**", *grouped(by_kind["added"], compact=compact), ""]
        if closed_other:
            tail += [f"➖ **Закрылись — {len(closed_other)}**", *grouped(closed_other, compact=compact), ""]
        footer = f"Действующих площадок: {active_total:,}".replace(",", " ")
        return "\n".join([header, "", *attention, *body, *tail, footer]).strip()

    if not (attention or body or by_kind["added"] or closed_other):
        return None  # одни поправки имён и уточнения точек — сайт применил молча
    message = render(compact=False)
    if len(to_telegram_html(message)) > MAX_HTML:
        message = render(compact=True)
    if len(to_telegram_html(message)) > MAX_HTML:
        message = message[: MAX_HTML - 40].rsplit("\n", 1)[0] + "\n… (сообщение сокращено)"
    return message


def failed_alert(when: datetime, error: str) -> str:
    return (
        "⚠️ **parkrun · сверка с каталогом не прошла**\n"
        f"{when.astimezone(MSK):%d.%m %H:%M} · {error}\n"
        "Сайт живёт на прежнем каталоге. Завтра попробую снова; пока не починится — напомню раз в неделю."
    )


def suspicious_alert(when: datetime, reasons: list[str]) -> str:
    return (
        "⚠️ **parkrun · каталог подозрительный — не применён**\n"
        f"{when.astimezone(MSK):%d.%m %H:%M} · " + "; ".join(reasons) + "\n"
        "Если parkrun правда столько закрыл: scripts/parkrun_catalog_run.py --force"
    )


def recovered_alert(skipped: int) -> str:
    return f"✅ **parkrun · каталог снова сверяется** · пропущено прогонов: {skipped}"
