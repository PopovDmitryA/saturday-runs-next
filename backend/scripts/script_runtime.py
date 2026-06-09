"""Shared CLI bootstrap for local script runs (prod DB, verbose logging)."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from urllib.parse import urlparse

_VERBOSE_LOGGERS = (
    "app.five_verst",
    "app.sync",
    "app.platform_adapters.five_verst",
    "app.platform_adapters.s95",
    "app.s95",
)


def reset_app_caches() -> None:
    from app.config import get_settings
    from app.db.session import get_engine, get_session_factory

    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()


def configure_logging(*, verbose: bool) -> None:
    if not verbose and not os.environ.get("SCRIPT_VERBOSE"):
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )
    for name in _VERBOSE_LOGGERS:
        logging.getLogger(name).setLevel(logging.INFO)


def _is_prod_database_url(url: str) -> bool:
    if os.environ.get("PROD_DB_TARGET") == "1":
        return True
    host = (urlparse(url).hostname or "").lower()
    return host not in {"", "localhost", "127.0.0.1", "postgres"}


def confirm_prod_write(database_url: str, *, dry_run: bool) -> None:
    if dry_run or os.environ.get("CONFIRM_PROD") == "1":
        return
    if not _is_prod_database_url(database_url):
        return
    host = urlparse(database_url).hostname or "?"
    print(
        f"\n⚠️  DATABASE_URL указывает на prod ({host}). "
        "Запись в prod-БД.\n"
        "   Dry-run: добавьте --dry-run\n"
        "   Запись:  CONFIRM_PROD=1 в окружении\n",
        file=sys.stderr,
    )
    raise SystemExit(2)


def add_bootstrap_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Подробный лог парсинга и HTTP-запросов (INFO в терминал)",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Переопределить DATABASE_URL (иначе из окружения / .env)",
    )


def apply_bootstrap_args(args: argparse.Namespace) -> None:
    database_url = args.database_url or os.environ.get("DATABASE_URL")
    if database_url:
        os.environ["DATABASE_URL"] = database_url
        reset_app_caches()
    configure_logging(verbose=bool(args.verbose))
    if database_url and _is_prod_database_url(database_url):
        dry_run = getattr(args, "dry_run", False)
        confirm_prod_write(database_url, dry_run=dry_run)


def bootstrap_from_env() -> None:
    """Call when SCRIPT_VERBOSE=1 is set by run_prod_script.sh before exec."""
    configure_logging(verbose=True)
    database_url = os.environ.get("DATABASE_URL", "")
    if database_url and _is_prod_database_url(database_url):
        print(f"[prod-db] {urlparse(database_url).hostname}", flush=True)
