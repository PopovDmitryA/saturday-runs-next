"""Статус мирового обхода атлетов parkrun (staging pm-postgres, отдельный проект)."""

from __future__ import annotations

import os
import re


def _sweep_flag(name: str) -> str:
    if name.startswith("mac"):
        return "💻"
    m = re.match(r"([a-z])([a-z])", name)
    if not m:
        return "🏳️"
    return chr(0x1F1E6 + ord(m.group(1)) - 97) + chr(0x1F1E6 + ord(m.group(2)) - 97)


def sweep_report_text() -> str:
    dsn = os.getenv("PM_WORLD_DSN")
    if not dsn:
        return "Обход атлетов не настроен (PM_WORLD_DSN)."
    import psycopg

    try:
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            by = dict(conn.execute("SELECT status, count(*) FROM crawl_queue GROUP BY status").fetchall())
            crawled = conn.execute("SELECT count(*) FROM athletes WHERE source='crawl'").fetchone()[0]
            runs = conn.execute("SELECT count(*) FROM runs").fetchone()[0]
            exits = conn.execute(
                "SELECT name, delay_sec, ban_level, "
                "GREATEST(0, EXTRACT(EPOCH FROM (cooldown_until - now())) / 60) "
                "FROM sweep_exits WHERE enabled ORDER BY (cooldown_until > now()), name"
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        return f"Обход: БД недоступна ({exc!r})"[:160]
    total = sum(by.values())
    pending = by.get("pending", 0)
    done = total - pending
    pct = (done / total * 100) if total else 0
    working = sum(1 for *_, cd in exits if cd <= 0)
    lines = [
        "🌍 Обход атлетов parkrun",
        f"📊 пройдено {done:,}/{total:,} ({pct:.1f}%), собрано {crawled:,}, забегов {runs:,}",
        f"🔌 рабочих {working}/{len(exits)}",
        "",
    ]
    for name, delay, bl, cd in exits:
        flag = _sweep_flag(name)
        if cd > 0:
            h, m = divmod(int(cd), 60)
            left = f"{h}ч{m:02d}м" if h else f"{m}м"
            lines.append(f"{flag} {name}: 💤 {left} (ур.{bl}, {delay:.0f}с)")
        else:
            lines.append(f"{flag} {name}: ✅ ({delay:.0f}с)")
    return "\n".join(lines)
