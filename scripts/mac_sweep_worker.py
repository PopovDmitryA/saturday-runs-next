#!/usr/bin/env python3
"""Mac-воркер мирового обхода parkrun: один поток против серверной pm-postgres.

Что делает (аналог make parkrun, но для очереди ОБХОДА, без браузера в норме):
- поднимает SSH-туннель к pm-postgres на сервере (127.0.0.1:5433);
- в один поток: claim атлета из crawl_queue → fetch двух страниц через httpx
  (прямой выход Мака, без Chromium) → parse → store;
- регистрируется на табло сайта как «macbook» (строка в sweep_exits, account=mac):
  копит collected_total/active_seconds + heartbeat, попадает в рейтинг;
- ПРИ КАПЧЕ/БАНЕ поднимает видимый браузер поверх окон, ты проходишь капчу,
  скрипт снимает aws-waf-token и дальше httpx идёт с ним (пока снова не протухнет).

Запуск — через лончер (scripts/parkrun_launcher.py) или напрямую:
  .conda-parkrun/bin/python scripts/mac_sweep_worker.py --delay 12
"""
from __future__ import annotations

import argparse
import atexit
import os
import random
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone

import httpx
import psycopg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.expanduser("~"), "Projects", "parkrun-monitoring"))
from athlete_sweep.parse import AthleteData, parse_all_runs, parse_summary  # noqa: E402
from athlete_sweep.worker import UA, WAF_MARKERS, claim, store  # noqa: E402

WORKER = "macbook"
PM_DB = "parkrun_world"
PM_USER = "parkrun"
PM_PASS = "parkrun_world_local"


def _env(key: str, default: str = "") -> str:
    for line in open(os.path.join(ROOT, ".env")):
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip()
    return default


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def open_tunnel() -> int:
    host = _env("TEMP_SSH_HOST") or _env("PROD_SSH_HOST") or "195.58.34.112"
    user = _env("TEMP_SSH_USER") or "viewer"
    pwd = _env("TEMP_SSH_PASSWORD")
    port = _free_port()
    env = {**os.environ, "SSHPASS": pwd}
    print(f"{_now()} SSH-туннель к pm-postgres…", flush=True)
    tun = subprocess.Popen(
        ["sshpass", "-e", "ssh", "-o", "StrictHostKeyChecking=no",
         "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=30",
         "-L", f"{port}:127.0.0.1:5433", "-N", f"{user}@{host}"],
        env=env,
    )
    atexit.register(tun.terminate)
    for _ in range(40):
        try:
            with socket.create_connection(("127.0.0.1", port), 0.5):
                return port
        except OSError:
            time.sleep(0.5)
    tun.terminate()
    sys.exit("туннель к pm-postgres не поднялся")


def register(conn, delay: float) -> None:
    conn.execute(
        """INSERT INTO sweep_exits (name, proxy, kind, account, enabled, delay_sec)
           VALUES (%s, 'mac-direct', 'mac', 'mac', true, %s)
           ON CONFLICT (name) DO UPDATE SET enabled=true, account='mac',
             delay_sec=EXCLUDED.delay_sec, cooldown_until=NULL, ban_level=0,
             worker_heartbeat_at=now()""",
        (WORKER, delay),
    )
    conn.commit()


def fetch(client: httpx.Client, url: str) -> tuple[str, str]:
    r = client.get(url)
    body = r.text
    low = body[:2000].lower()
    waf = "x-amzn-waf-action" in {k.lower() for k in r.headers}
    if r.status_code in (403, 405) or waf or any(m in low for m in WAF_MARKERS):
        return "protected", body
    if r.status_code == 404:
        return "not_found", body
    return "ok", body


def solve_captcha_get_token() -> str | None:
    """Поднять видимый браузер поверх окон, дать пройти капчу, снять aws-waf-token."""
    from playwright.sync_api import sync_playwright

    print(f"\n{_now()} ⚠️  КАПЧА — открываю браузер, пройди «Human Verification»…", flush=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=["--start-maximized"])
        ctx = browser.new_context(no_viewport=True)
        page = ctx.new_page()
        page.bring_to_front()
        try:
            page.goto("https://www.parkrun.org.uk/parkrunner/620/", timeout=180000,
                      wait_until="domcontentloaded")
        except Exception:
            pass
        input(">>> Пройди капчу, дождись страницы атлета, нажми Enter здесь… ")
        token = next((c for c in ctx.cookies() if c["name"] == "aws-waf-token"), None)
        browser.close()
    if token:
        print(f"{_now()} токен снят, продолжаю без браузера.", flush=True)
        return token["value"]
    print(f"{_now()} токен не найден — попробую продолжить как есть.", flush=True)
    return None


def make_client(token: str | None) -> httpx.Client:
    cookies = {"aws-waf-token": token} if token else {}
    return httpx.Client(headers={"User-Agent": UA, "Accept-Language": "en-GB,en;q=0.9"},
                        cookies=cookies, timeout=30.0, follow_redirects=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--delay", type=float, default=12.0, help="пауза между атлетами, сек")
    ap.add_argument("--limit", type=int, default=0, help="сколько атлетов (0 = без предела)")
    args = ap.parse_args()

    port = open_tunnel()
    dsn = f"postgresql://{PM_USER}:{PM_PASS}@127.0.0.1:{port}/{PM_DB}"
    conn = psycopg.connect(dsn, autocommit=False)
    register(conn, args.delay)
    print(f"{_now()} macbook на связи · задержка {args.delay:.0f}с · "
          f"{'без предела' if not args.limit else str(args.limit)+' атлетов'}", flush=True)

    token: str | None = None
    client = make_client(token)
    done = 0
    consec_waf = 0
    try:
        while True:
            if args.limit and done >= args.limit:
                print(f"{_now()} лимит {args.limit} достигнут.", flush=True); break
            aid = claim(conn, WORKER, 60)
            if aid is None:
                print(f"{_now()} очередь пуста, жду 60с…", flush=True); time.sleep(60); continue
            base = f"https://www.parkrun.org.uk/parkrunner/{aid}/"
            t0 = time.time()
            pause = args.delay * random.uniform(0.85, 1.15)  # ±15% джиттер
            try:
                kind, html = fetch(client, base)
                if kind == "protected":
                    raise _Protected()
                data = (AthleteData(status="not_found") if kind == "not_found"
                        else parse_summary(html, str(aid)))
                if data.status == "ok":
                    time.sleep(1.0)
                    kind2, html2 = fetch(client, base + "all/")
                    if kind2 == "protected":
                        raise _Protected()
                    data.runs = parse_all_runs(html2, str(aid))
                raw = html if data.status == "unclassified" else None
                store(conn, aid, data, raw)
                conn.execute("UPDATE crawl_queue SET status=%s, claimed_by=NULL, fetched_at=now() "
                             "WHERE athlete_id=%s", (data.status, aid))
                conn.execute("UPDATE sweep_exits SET collected_total=collected_total+1, "
                             "active_seconds=active_seconds+%s, last_ok_at=now(), "
                             "worker_heartbeat_at=now() WHERE name=%s", (int(args.delay), WORKER))
                conn.commit()
                consec_waf = 0
                done += 1
                nm = (data.name or data.status)
                print(f"{_now()} #{done} атлет {aid}: {nm} ({data.status}, {data.total_runs or 0} заб.) "
                      f"[обработка {time.time()-t0:.1f}с · пауза {pause:.1f}с]", flush=True)
            except _Protected:
                conn.execute("UPDATE crawl_queue SET status='pending', claimed_by=NULL WHERE athlete_id=%s", (aid,))
                conn.commit()
                consec_waf += 1
                if consec_waf >= 3:
                    token = solve_captcha_get_token()
                    client.close(); client = make_client(token)
                    consec_waf = 0
                continue
            except Exception as exc:
                conn.execute("UPDATE crawl_queue SET status='pending', claimed_by=NULL, "
                             "attempts=attempts+1, error=%s WHERE athlete_id=%s", (repr(exc)[:200], aid))
                conn.commit()
                print(f"{_now()} атлет {aid} сбой: {exc!r}", flush=True)
            time.sleep(pause)
    except KeyboardInterrupt:
        print(f"\n{_now()} остановлено. Собрано за сессию: {done}.", flush=True)
    finally:
        try:
            conn.execute("UPDATE sweep_exits SET worker_heartbeat_at=NULL, enabled=false WHERE name=%s", (WORKER,))
            conn.commit()
        except Exception:
            pass
        conn.close()


class _Protected(Exception):
    pass


if __name__ == "__main__":
    main()
