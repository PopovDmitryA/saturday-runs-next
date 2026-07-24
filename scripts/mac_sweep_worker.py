#!/usr/bin/env python3
"""Mac-воркер мирового обхода parkrun: один поток против серверной pm-postgres.

Режимы фетча:
- httpx (по умолчанию): прямой выход Мака без браузера, быстро. При 3 капчах
  подряд поднимает браузер, ты проходишь капчу — снимаем aws-waf-token и дальше
  httpx идёт с ним (пока снова не протухнет).
- --browser: браузер поднимается СРАЗУ и все страницы качаются через него. Для
  ночного прогона: держит свежий токен, silent-челлендж WAF проходит сам, капча
  почти не выпадает → «молотит всю ночь» без остановок.

Капча (в любом режиме) больше НЕ блокирует навсегда на Enter. Поведение при
зависшей капче (эскалация, чтобы ночью без человека скрипт сам реагировал):
  5 мин без изменений  → перезагрузить страницу;
  10 мин               → ещё раз перезагрузить;
  30 мин               → сдаться (вернуть атлета в очередь) и остановить прогон.
Страница парсится ТОЛЬКО после полной загрузки (networkidle) — раньше ранний
Enter до догрузки давал 0 пользователей (баг 775123).

Рейтинг на табло: пишем heartbeat только пока реально работаем; на выходе (в т.ч.
по Ctrl-C) чистим — не висим «работает», если скрипт не запущен.

Запуск — через лончер (scripts/parkrun_launcher.py, пункт 2) или напрямую:
  .conda-parkrun/bin/python scripts/mac_sweep_worker.py --delay 12 [--browser]
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
from datetime import datetime

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

# Эскалация при зависшей капче (сек).
CAPTCHA_RELOAD_1 = 300    # 5 мин — перезагрузить страницу
CAPTCHA_RELOAD_2 = 600    # 10 мин — ещё раз
CAPTCHA_GIVEUP = 1800     # 30 мин — сдаться и остановить прогон
PW_PROFILE = os.path.join(ROOT, ".pw-sweep-profile")  # персист. профиль браузера (токен переживает рестарт)


def _env(key: str, default: str = "") -> str:
    try:
        for line in open(os.path.join(ROOT, ".env")):
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return default


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def is_captcha(html: str | None) -> bool:
    if not html:
        return False
    low = html[:3000].lower()
    return any(m in low for m in WAF_MARKERS) or "human verification" in low


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


def cleanup_registration(conn) -> None:
    """Снять «работает» с табло — вызывается на любом выходе."""
    try:
        conn.execute("UPDATE sweep_exits SET worker_heartbeat_at=NULL, enabled=false "
                     "WHERE name=%s", (WORKER,))
        conn.commit()
    except Exception:
        pass


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


def make_client(token: str | None) -> httpx.Client:
    cookies = {"aws-waf-token": token} if token else {}
    return httpx.Client(headers={"User-Agent": UA, "Accept-Language": "en-GB,en;q=0.9"},
                        cookies=cookies, timeout=30.0, follow_redirects=True)


# ─────────────────────────── браузер ───────────────────────────
class Browser:
    """Долгоживущий headful-браузер. Персистентный профиль — токен/куки переживают
    перезапуск скрипта, ночью капча выпадает реже."""

    def __init__(self) -> None:
        self._pw = None
        self.ctx = None
        self.page = None

    def start(self) -> None:
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self.ctx = self._pw.chromium.launch_persistent_context(
            PW_PROFILE, headless=False, no_viewport=True, args=["--start-maximized"])
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        self.page.bring_to_front()

    def token(self) -> str | None:
        try:
            for c in self.ctx.cookies():
                if c["name"] == "aws-waf-token":
                    return c["value"]
        except Exception:
            pass
        return None

    def close(self) -> None:
        for fn in (lambda: self.ctx.close(), lambda: self._pw.stop()):
            try:
                fn()
            except Exception:
                pass


def load_page(br: Browser, url: str) -> tuple[str, str | None]:
    """Открыть URL в браузере и дождаться ПОЛНОЙ загрузки, прежде чем читать.
    При капче — эскалация reload 5/10 мин, сдаться на 30 мин.
    Возвращает (status, html): status ∈ ok|giveup."""
    page = br.page
    t0 = time.time()
    did5 = did10 = False
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
    except Exception:
        pass
    while True:
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        try:
            html = page.content()
        except Exception:
            html = None
        if not is_captcha(html):
            return "ok", html            # реальная страница уже прогрузилась (networkidle)
        el = time.time() - t0
        if el >= CAPTCHA_GIVEUP:
            return "giveup", None
        if el >= CAPTCHA_RELOAD_2 and not did10:
            did10 = True
            print(f"{_now()} капча 10 мин — перезагружаю страницу…", flush=True)
            _safe_reload(page, url)
        elif el >= CAPTCHA_RELOAD_1 and not did5:
            did5 = True
            print(f"{_now()} капча 5 мин — перезагружаю страницу…", flush=True)
            _safe_reload(page, url)
        time.sleep(3)


def _safe_reload(page, url: str) -> None:
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
    except Exception:
        pass


def parse_via_browser(br: Browser, aid: int, base: str) -> AthleteData | None:
    """Собрать атлета целиком через браузер (summary + /all). None = сдались 30 мин."""
    st, html = load_page(br, base)
    if st == "giveup":
        return None
    data = parse_summary(html, str(aid)) if html else AthleteData(status="unclassified")
    if data.status == "ok":
        st2, html2 = load_page(br, base + "all/")
        if st2 == "giveup":
            return None
        if html2 and not is_captcha(html2):
            data.runs = parse_all_runs(html2, str(aid))
    return data


def finish(conn, aid: int, data, raw: str | None, delay: float) -> None:
    store(conn, aid, data, raw)
    conn.execute("UPDATE crawl_queue SET status=%s, claimed_by=NULL, fetched_at=now() "
                 "WHERE athlete_id=%s", (data.status, aid))
    conn.execute("UPDATE sweep_exits SET collected_total=collected_total+1, "
                 "active_seconds=active_seconds+%s, last_ok_at=now(), "
                 "worker_heartbeat_at=now() WHERE name=%s", (int(delay), WORKER))
    conn.commit()


def requeue(conn, aid: int, err: str | None = None) -> None:
    conn.execute("UPDATE crawl_queue SET status='pending', claimed_by=NULL, "
                 "attempts=attempts+1, error=%s WHERE athlete_id=%s", (err, aid))
    conn.commit()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--delay", type=float, default=12.0, help="пауза между атлетами, сек")
    ap.add_argument("--limit", type=int, default=0, help="сколько атлетов (0 = без предела)")
    ap.add_argument("--browser", action="store_true",
                    help="качать всё через браузер сразу (ночной режим, капча почти не выпадает)")
    args = ap.parse_args()

    port = open_tunnel()
    dsn = f"postgresql://{PM_USER}:{PM_PASS}@127.0.0.1:{port}/{PM_DB}"
    conn = psycopg.connect(dsn, autocommit=False)
    register(conn, args.delay)
    atexit.register(cleanup_registration, conn)

    br: Browser | None = None
    if args.browser:
        print(f"{_now()} поднимаю браузер (ночной режим)…", flush=True)
        br = Browser()
        br.start()
        # первичный прогрев: открыть парк-страницу, при необходимости пройти капчу раз
        load_page(br, "https://www.parkrun.org.uk/parkrunner/620/")
        print(f"{_now()} браузер готов, токен {'есть' if br.token() else 'нет'}.", flush=True)

    mode = "браузер" if args.browser else "httpx"
    print(f"{_now()} macbook на связи · {mode} · задержка {args.delay:.0f}с · "
          f"{'без предела' if not args.limit else str(args.limit)+' атлетов'}", flush=True)

    token: str | None = None
    client = make_client(token)
    done = 0
    consec_waf = 0
    try:
        while True:
            if args.limit and done >= args.limit:
                print(f"{_now()} лимит {args.limit} достигнут.", flush=True)
                break
            aid = claim(conn, WORKER, 60)
            if aid is None:
                print(f"{_now()} очередь пуста, жду 60с…", flush=True)
                conn.execute("UPDATE sweep_exits SET worker_heartbeat_at=now() WHERE name=%s", (WORKER,))
                conn.commit()
                time.sleep(60)
                continue
            base = f"https://www.parkrun.org.uk/parkrunner/{aid}/"
            t0 = time.time()
            pause = args.delay * random.uniform(0.85, 1.15)  # ±15% джиттер

            # ── ветка БРАУЗЕР ──
            if args.browser:
                data = parse_via_browser(br, aid, base)
                if data is None:  # 30 мин капчи — сдаёмся и останавливаемся
                    requeue(conn, aid, "captcha giveup 30m")
                    print(f"{_now()} капча висит 30 мин — останавливаю прогон "
                          f"(атлет {aid} вернулся в очередь).", flush=True)
                    break
                finish(conn, aid, data, html_raw(data), args.delay)
                done += 1
                print(f"{_now()} #{done} атлет {aid}: {data.name or data.status} "
                      f"({data.status}, {data.total_runs or 0} заб.) "
                      f"[{time.time()-t0:.1f}с · пауза {pause:.1f}с]", flush=True)
                time.sleep(pause)
                continue

            # ── ветка HTTPX ──
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
                finish(conn, aid, data, html_raw(data, html), args.delay)
                consec_waf = 0
                done += 1
                print(f"{_now()} #{done} атлет {aid}: {data.name or data.status} "
                      f"({data.status}, {data.total_runs or 0} заб.) "
                      f"[обработка {time.time()-t0:.1f}с · пауза {pause:.1f}с]", flush=True)
            except _Protected:
                consec_waf += 1
                if consec_waf < 3:
                    requeue(conn, aid)
                    continue
                # 3 капчи подряд → браузер на этом атлете, парсим его же (с ожиданием загрузки)
                if br is None:
                    print(f"{_now()} ⚠️  3 капчи подряд — поднимаю браузер на атлете {aid}, "
                          f"пройди «Human Verification»…", flush=True)
                    br = Browser()
                    br.start()
                data = parse_via_browser(br, aid, base)
                consec_waf = 0
                if data is None:
                    requeue(conn, aid, "captcha giveup 30m")
                    print(f"{_now()} капча висит 30 мин — останавливаю прогон.", flush=True)
                    break
                token = br.token()
                client.close(); client = make_client(token)
                finish(conn, aid, data, html_raw(data), args.delay)
                done += 1
                print(f"{_now()} #{done} атлет {aid}: {data.name or data.status} "
                      f"({data.status}, {data.total_runs or 0} заб.) [после капчи]", flush=True)
            except Exception as exc:
                requeue(conn, aid, repr(exc)[:200])
                print(f"{_now()} атлет {aid} сбой: {exc!r}", flush=True)
            time.sleep(pause)
    except KeyboardInterrupt:
        print(f"\n{_now()} остановлено. Собрано за сессию: {done}.", flush=True)
    finally:
        cleanup_registration(conn)
        if br is not None:
            br.close()
        conn.close()


def html_raw(data, html: str | None = None) -> str | None:
    """raw_html храним только для unclassified (для ручного разбора)."""
    return html if (data and data.status == "unclassified") else None


class _Protected(Exception):
    pass


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nОтмена.")
