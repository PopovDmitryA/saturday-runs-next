#!/usr/bin/env python3
"""
Один скрипт для ежедневной обработки очереди parkrun на Mac.

Берёт из БД profile_fetch_pending + привязки без пробежек,
сам поднимает Chromium (Playwright, как legacy .py),
при капче ждёт в том же окне и продолжает.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

if sys.version_info < (3, 12):
    print("Needs Python 3.12+", file=sys.stderr)
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.db.session import get_session_factory
from app.parkrun.fetch.daemon_log import cline, setup_daemon_logging
from app.parkrun.fetch.proxy_pool import load_proxies
from app.services.parkrun_queue_daemon import run_daemon

logger = logging.getLogger(__name__)


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Parkrun queue daemon (Mac + Playwright)")
    parser.add_argument(
        "--use-cdp",
        action="store_true",
        help="Use Chrome CDP instead of Playwright (not recommended on Mac)",
    )
    parser.add_argument(
        "--cdp-url",
        default=settings.parkrun_cdp_url or "http://127.0.0.1:9222",
        help="Chrome remote debugging URL (only with --use-cdp)",
    )
    parser.add_argument(
        "--no-launch-chrome",
        action="store_true",
        help="With --use-cdp: do not auto-start Chrome",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Max pending rows from DB; 0 = вся очередь (личные рекорды "
             "пересчитываются одним проходом в конце прогона)",
    )
    parser.add_argument(
        "--pending-only",
        action="store_true",
        help="Skip sync for linked profiles with missing runs",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help=(
            "Trim the LOG FILE: skip library INFO (DB flush, HTTP, page loads), "
            "keep warnings and full tracebacks. The terminal stays clean either "
            "way — it only ever shows the in-the-moment status lines"
        ),
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help=(
            "EXPERIMENTAL: fetch via plain httpx instead of Playwright — no "
            "browser, no captcha-solving window. Aborts the whole remaining "
            "batch on the first sign of WAF protection. Use with a small "
            "--limit and --fast-delay; carries a real ban risk"
        ),
    )
    parser.add_argument(
        "--fast-delay",
        type=float,
        default=3.0,
        help="With --no-browser: seconds between requests (jittered ±30%%)",
    )

    parser.add_argument(

        "--proxies",

        default="",

        help=(

            "Список исходящих прокси через запятую, например "

            "socks5://127.0.0.1:10865,socks5://127.0.0.1:10866. Упёрлись в "

            "защиту на одном выходе — берём следующий, вместо остановки "

            "пачки. Пусто — берём из PARKRUN_FETCH_PROXIES, иначе идём "

            "напрямую."

        ),

    )

    parser.add_argument(
        "--solve-captcha",
        action="store_true",
        help=(
            "With --no-browser: on WAF/captcha, raise a headless browser, solve "
            "with CLIP, harvest aws-waf-token and continue via httpx — no manual "
            "captcha window. Needs parkrun-monitoring + torch/open_clip"
        ),
    )
    args = parser.parse_args()

    default_log = Path(os.environ.get("PARKRUN_DAEMON_LOG") or (Path.cwd() / "data" / "parkrun_daemon.log"))
    log_file = setup_daemon_logging(default_log, quiet=args.quiet)
    cline(f"Лог этого прогона (полный, с трейсбэками): {log_file}")

    Session = get_session_factory()
    try:
        with Session() as db:
            result = run_daemon(
                db,
                use_cdp=args.use_cdp,
                cdp_url=args.cdp_url.strip() if args.use_cdp else None,
                launch_chrome=not args.no_launch_chrome,
                limit_pending=args.limit,
                include_sync=not args.pending_only,
                use_httpx=args.no_browser,
                fast_delay_seconds=args.fast_delay if args.no_browser else None,
                solve_captcha=args.solve_captcha and args.no_browser,
                proxies=load_proxies(args.proxies or None),
            )
    except Exception:
        # Полное падение скрипта (не отдельная строка очереди) — единственный
        # случай, когда трейсбэк уместен и в терминале. Полный текст — в файле.
        logger.exception("parkrun daemon crashed")
        cline(f"СКРИПТ УПАЛ — подробности в {log_file}")
        return 1

    _print_run_summary(result)
    summary = result.get("summary") or {}
    if summary.get("error") or summary.get("sync_error") or summary.get("cooldown"):
        return 1
    return 0


# Человеческие названия исходов очереди — в консоли вместо машинных ключей.
OUTCOME_LABELS = {
    "done": "обработано",
    "sync_ok": "синк профиля",
    "not_found": "профиль не найден",
    "error": "ошибки",
    "sync_error": "ошибки синка",
    "cooldown": "остановлено защитой (капча/бан)",
    "skipped": "пропущено",
    "db_connection_lost": "обрыв связи с БД",
}


def _print_run_summary(result: dict) -> None:
    """Сводка прогона — как в мировом обходе: сколько, за сколько, с какой
    скоростью. Печатается и при штатном финише, и при Ctrl+C."""
    summary: dict = result.get("summary") or {}
    processed = int(result.get("processed") or 0)
    elapsed = float(result.get("elapsed") or 0.0)
    interrupted = bool(result.get("interrupted"))
    total = int(result.get("total") or 0)
    done = int(summary.get("done", 0)) + int(summary.get("sync_ok", 0))

    cline("=" * 46)
    if interrupted:
        cline(f"ОСТАНОВЛЕНО вручную на {processed} из {total} задач(и)")
    else:
        cline(f"ПРОГОН ЗАВЕРШЁН: {processed} из {total} задач(и)")

    if elapsed > 0:
        per_item = elapsed / max(processed, 1)
        per_hour = 3600.0 / per_item if per_item > 0 else 0.0
        cline(
            f"успешно {done} · за {elapsed / 60:.1f} мин "
            f"({per_item:.1f} с на задачу, ~{per_hour:.0f}/час)"
        )

    if summary:
        parts = [
            f"{OUTCOME_LABELS.get(key, key)}: {value}"
            for key, value in sorted(summary.items(), key=lambda kv: -kv[1])
        ]
        cline("разбивка — " + " · ".join(parts))

    details = result.get("details") or []
    if details:
        cline(f"подробности по задачам ({len(details)}):")
        for line in details[-15:]:
            cline(f"  {line}")
        if len(details) > 15:
            cline(f"  … ещё {len(details) - 15}, полный список в логе")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nОстановлено.", flush=True)
        raise SystemExit(130) from None
