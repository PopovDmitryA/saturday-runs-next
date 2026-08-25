#!/usr/bin/env python3
"""Отчёт по счётчику посещений legacy-Grafana (grafana.run5k.run).

Читает /var/log/nginx/grafana_hits.log — туда пишет nginx события от
deploy/grafana/counter/count.js. В этом логе только живые браузеры:
сканеры и боты JS не исполняют, поэтому цифры честнее, чем в access.log.

Запуск на сервере:
    python3 scripts/grafana_usage_report.py
Запуск с Мака (сам заберёт лог по ssh, креды из .env):
    python3 scripts/grafana_usage_report.py --remote

Ключи: --days N (сколько дней показывать, по умолчанию 7),
       --now N (окно «прямо сейчас» в минутах, по умолчанию 15).
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import gzip
import io
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

LOG_GLOB = "/var/log/nginx/grafana_hits.log*"
DASH_RE = re.compile(r"^/d/(?P<uid>[\w-]+)(?:/(?P<slug>[\w-]*))?")
MSK = dt.timezone(dt.timedelta(hours=3))


def read_remote() -> str:
    """Забирает логи с прода одной ssh-командой (креды из .env репозитория)."""
    root = Path(__file__).resolve().parent.parent
    env = {}
    env_file = root / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    host = env.get("TEMP_SSH_HOST")
    user = env.get("TEMP_SSH_USER")
    password = os.environ.get("TEMP_SSH_PASSWORD") or env.get("TEMP_SSH_PASSWORD")
    if not (host and user and password):
        sys.exit("нет TEMP_SSH_HOST/TEMP_SSH_USER/TEMP_SSH_PASSWORD — запусти на сервере без --remote")
    cmd = [
        "sshpass", "-p", password,
        "ssh", "-o", "StrictHostKeyChecking=no", f"{user}@{host}",
        f"zcat -f {LOG_GLOB} 2>/dev/null",
    ]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0 and not out.stdout:
        sys.exit(f"ssh не отдал лог: {out.stderr.strip()[:300]}")
    return out.stdout


def read_local() -> str:
    import glob

    chunks = []
    for path in sorted(glob.glob(LOG_GLOB)):
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt", errors="replace") as fh:  # type: ignore[operator]
            chunks.append(fh.read())
    if not chunks:
        sys.exit(f"логов не нашлось: {LOG_GLOB} — счётчик ещё не поставлен?")
    return "".join(chunks)


def parse(raw: str) -> list[dict]:
    events = []
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        try:
            ts = dt.datetime.fromisoformat(parts[0]).astimezone(MSK)
        except ValueError:
            continue
        # nginx пишет $arg_p как есть, то есть percent-encoded.
        path = urllib.parse.unquote(parts[3])
        m = DASH_RE.match(path)
        events.append(
            {
                "ts": ts,
                "ip": parts[1],
                "vid": parts[2],
                "path": path,
                "kind": parts[4],
                "uid": m.group("uid") if m else None,
                "slug": (m.group("slug") or "") if m else "",
                "ua": parts[5].strip('"') if len(parts) > 5 else "",
            }
        )
    return events


def dashboard_titles() -> dict[str, str]:
    """Человеческие названия дашбордов из самой Grafana (вход анонимный)."""
    try:
        with urllib.request.urlopen(
            "https://grafana.run5k.run/api/search?type=dash-db&limit=500", timeout=10
        ) as resp:
            return {d["uid"]: d["title"] for d in json.load(resp) if d.get("uid")}
    except Exception:
        return {}


def label(ev_uid: str | None, ev_slug: str, titles: dict[str, str]) -> str:
    if not ev_uid:
        return "(не дашборд)"
    return titles.get(ev_uid) or ev_slug or ev_uid


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--remote", action="store_true", help="забрать лог с прода по ssh")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--now", type=int, default=15, help="окно «прямо сейчас», минут")
    args = ap.parse_args()

    events = parse(read_remote() if args.remote else read_local())
    if not events:
        sys.exit("лог пуст — счётчик стоит, но событий ещё не было")
    titles = dashboard_titles()
    now = dt.datetime.now(MSK)
    since = now - dt.timedelta(days=args.days)
    events = [e for e in events if e["ts"] >= since]

    print(f"Счётчик grafana.run5k.run — {len(events)} событий с {since:%d.%m %H:%M} (МСК)\n")

    # --- прямо сейчас ---
    window = now - dt.timedelta(minutes=args.now)
    live = [e for e in events if e["ts"] >= window]
    by_vid = collections.defaultdict(list)
    for e in live:
        by_vid[e["vid"]].append(e)
    print(f"=== Прямо сейчас (последние {args.now} мин): {len(by_vid)} чел. ===")
    if not by_vid:
        print("  никого")
    for vid, evs in sorted(by_vid.items(), key=lambda kv: kv[1][-1]["ts"], reverse=True):
        last = evs[-1]
        dash = ", ".join(dict.fromkeys(label(e["uid"], e["slug"], titles) for e in evs))
        mins = round((last["ts"] - evs[0]["ts"]).total_seconds() / 60)
        print(f"  {last['ts']:%H:%M} {last['ip']:>15}  {mins:>3} мин  {dash[:70]}")

    # --- по дням ---
    print(f"\n=== По дням ===\n{'день':>10} {'человек':>8} {'визитов':>8} {'дашбордов':>10}")
    days = collections.defaultdict(lambda: {"v": set(), "loads": 0, "d": set()})
    for e in events:
        d = days[e["ts"].date()]
        d["v"].add(e["vid"])
        if e["kind"] in ("load", "nav"):
            d["loads"] += 1
        if e["uid"]:
            d["d"].add(e["uid"])
    for day in sorted(days):
        d = days[day]
        print(f"{day.strftime('%d.%m.%Y'):>10} {len(d['v']):8} {d['loads']:8} {len(d['d']):10}")

    # --- дашборды ---
    print(f"\n=== Дашборды за {args.days} дн. (уникальных людей / открытий) ===")
    dash = collections.defaultdict(lambda: [set(), 0])
    for e in events:
        if not e["uid"]:
            continue
        key = label(e["uid"], e["slug"], titles)
        dash[key][0].add(e["vid"])
        if e["kind"] in ("load", "nav"):
            dash[key][1] += 1
    for name, (vids, opens) in sorted(dash.items(), key=lambda kv: -len(kv[1][0])):
        print(f"  {len(vids):4} чел. {opens:5} откр.  {name}")

    # --- возвращаемость ---
    seen_days = collections.defaultdict(set)
    for e in events:
        seen_days[e["vid"]].add(e["ts"].date())
    repeat = sum(1 for v in seen_days.values() if len(v) > 1)
    print(
        f"\nВсего людей за период: {len(seen_days)}; "
        f"заходили больше одного дня: {repeat}"
    )


if __name__ == "__main__":
    main()
