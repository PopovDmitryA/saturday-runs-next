#!/usr/bin/env python3
"""Интерактивное меню обновления данных (как legacy main.py в 5_verst)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def _load_project_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _prompt_int(label: str, *, default: int | None = None) -> int:
    while True:
        suffix = f" [{default}]" if default is not None else ""
        raw = input(f"{label}{suffix}: ").strip()
        if not raw and default is not None:
            return default
        try:
            return int(raw)
        except ValueError:
            print("Введите целое число.")


def _prompt_slug(label: str = "slug локации") -> str:
    while True:
        raw = input(f"{label}: ").strip()
        if raw:
            return raw
        print("Slug не может быть пустым.")


def _print_result(payload: object) -> None:
    if is_dataclass(payload):
        payload = asdict(payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _apply_prod_database_url() -> None:
    cmd = (
        f'source "{ROOT}/scripts/prod_db_env.sh" && '
        "export_prod_database_url && "
        'printf "DATABASE_URL=%s\\n" "$DATABASE_URL"'
    )
    result = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, cwd=ROOT)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Не удалось открыть prod PostgreSQL (SSH-туннель)")
    for line in result.stderr.splitlines():
        if line.strip():
            print(line)
    for line in result.stdout.splitlines():
        if line.startswith("DATABASE_URL="):
            os.environ["DATABASE_URL"] = line.split("=", 1)[1]
    os.environ["PROD_DB_TARGET"] = "1"


def _apply_local_database_url() -> None:
    user = os.environ.get("POSTGRES_USER", "saturday_runs")
    password = os.environ.get("POSTGRES_PASSWORD", "saturday_runs")
    db = os.environ.get("POSTGRES_DB", "saturday_runs_lk")
    port = os.environ.get("POSTGRES_PUBLISH_PORT", "5433")
    os.environ["DATABASE_URL"] = f"postgresql+psycopg://{user}:{password}@127.0.0.1:{port}/{db}"
    os.environ.pop("PROD_DB_TARGET", None)


def _ensure_redis() -> None:
    url = os.environ.get("LOCAL_REDIS_URL", "redis://127.0.0.1:6379/0")
    os.environ["REDIS_URL"] = url
    try:
        import redis

        redis.from_url(url, socket_connect_timeout=2).ping()
        return
    except Exception:
        pass
    print("Redis недоступен — запускаю контейнер (один раз)…")
    subprocess.run(
        ["docker", "compose", "up", "-d", "redis"],
        cwd=ROOT,
        capture_output=True,
    )
    time.sleep(1)
    os.environ["REDIS_URL"] = "redis://127.0.0.1:6379/0"


def _warn_prod_locks() -> None:
    from sqlalchemy import create_engine, text

    url = os.environ.get("DATABASE_URL")
    if not url:
        return
    try:
        engine = create_engine(url, connect_args={"connect_timeout": 5})
        with engine.connect() as conn:
            waiting = conn.execute(
                text("SELECT count(*) FROM pg_locks WHERE NOT granted AND pid <> pg_backend_pid()")
            ).scalar()
            idle_tx = conn.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE state = 'idle in transaction' AND pid <> pg_backend_pid()"
                )
            ).scalar()
        if waiting or idle_tx:
            print(
                f"\n⚠️  На prod есть блокировки (waiting={waiting}, idle_tx={idle_tx}). "
                "Если sync зависнет — остановите worker-five-verst на сервере.\n"
            )
    except Exception:
        pass


class SessionConfig:
    def __init__(self, *, prod: bool, dry_run: bool) -> None:
        self.prod = prod
        self.dry_run = dry_run

    @property
    def db_label(self) -> str:
        return "prod (run5k.run)" if self.prod else "local"

    @property
    def mode_label(self) -> str:
        return "только проверка" if self.dry_run else "запись в БД"


def _setup_session() -> SessionConfig:
    print("\n══════════════════════════════════════════════")
    print(" Обновление данных — saturday_runs_stats")
    print("══════════════════════════════════════════════")

    db_choice = input(
        "\nБаза данных:\n"
        "  1 — Prod (run5k.run)\n"
        "  2 — Local (postgres на Mac, порт из .env)\n"
        "Выбор [1]: "
    ).strip() or "1"
    prod = db_choice != "2"

    mode_choice = input(
        "\nРежим:\n"
        "  1 — Только проверка (dry-run, без записи)\n"
        "  2 — Записать в базу\n"
        "Выбор [1]: "
    ).strip() or "1"
    dry_run = mode_choice != "2"

    if prod and not dry_run:
        print(
            "\n⚠️  Запись в prod. Если sync «завис» на записи — "
            "на сервере: docker compose … stop worker-five-verst"
        )
        confirm = input("Продолжить? Enter — да, n — отмена: ").strip().lower()
        if confirm == "n":
            raise SystemExit(0)

    if prod:
        _apply_prod_database_url()
    else:
        _apply_local_database_url()

    _ensure_redis()

    from scripts.script_runtime import configure_logging, reset_app_caches

    os.environ["SCRIPT_VERBOSE"] = "1"
    if not dry_run:
        os.environ["CONFIRM_PROD"] = "1"
    configure_logging(verbose=True)
    reset_app_caches()

    if prod and not dry_run:
        _warn_prod_locks()

    cfg = SessionConfig(prod=prod, dry_run=dry_run)
    print(f"\n→ {cfg.db_label}, {cfg.mode_label}\n")
    return cfg


def _get_db():
    from app.db.session import get_session_factory

    return get_session_factory()()


def _list_five_verst_locations(db) -> list[tuple[str, str]]:
    from app.models import Location
    from app.sync import upsert

    platform = upsert.get_platform(db, "five_verst")
    rows = (
        db.query(Location)
        .filter(Location.platform_id == platform.id)
        .order_by(Location.name, Location.external_key)
        .all()
    )
    return [(row.external_key, row.name) for row in rows]


def _pick_locations(db) -> list[str]:
    locations = _list_five_verst_locations(db)
    if not locations:
        print("В базе нет локаций 5verst. Сначала запустите пункт «Реестр локаций».")
        return []

    print("\nРежим выбора локаций:")
    print("  1 — Одна локация (номер из списка)")
    print("  2 — Диапазон номеров")
    print("  3 — Ввести slug вручную")
    print("  4 — Все локации из базы")
    sub = input("Выбор [1]: ").strip() or "1"

    if sub == "3":
        return [_prompt_slug()]

    if sub == "4":
        return [slug for slug, _ in locations]

    for index, (slug, name) in enumerate(locations, start=1):
        print(f"  {index:4d}. {name} ({slug})")

    if sub == "2":
        start = _prompt_int("Начальный номер")
        end = _prompt_int("Конечный номер")
        if not (1 <= start <= end <= len(locations)):
            print("Диапазон вне списка.")
            return []
        return [locations[i - 1][0] for i in range(start, end + 1)]

    num = _prompt_int("Номер локации")
    if not (1 <= num <= len(locations)):
        print("Неверный номер.")
        return []
    return [locations[num - 1][0]]


def _run_five_verst_latest(cfg: SessionConfig) -> None:
    from app.config import get_settings
    from app.platform_adapters.five_verst import bulk_parser
    from app.sync.five_verst_latest import LatestResultsSyncOptions, plan_latest_results_sync, sync_latest_results

    protocols = _prompt_int("Сколько протоколов загрузить (0 = все новые/изменённые)", default=0)
    db = _get_db()
    try:
        if cfg.dry_run:
            summaries, _ = bulk_parser.fetch_latest_results()
            plan = plan_latest_results_sync(db, summaries)
            payload = {
                "summaries_total": len(summaries),
                "unchanged": sum(1 for x in plan if x.action.value == "unchanged"),
                "new_summaries": sum(1 for x in plan if x.action.value == "new_summary"),
                "changed_summaries": sum(1 for x in plan if x.action.value == "changed_summary"),
                "missing_protocol": sum(1 for x in plan if x.action.value == "missing_protocol"),
                "needs_update": sum(1 for x in plan if x.action.value != "unchanged"),
            }
            _print_result(payload)
            return

        settings = get_settings()
        result = sync_latest_results(
            db,
            LatestResultsSyncOptions(
                dry_run=False,
                update_limit=settings.five_verst_sync_latest_update_limit or None,
                protocol_fetch_limit=protocols if protocols > 0 else None,
                ensure_locations=True,
                fetch_all_protocols_on_change=settings.five_verst_fetch_all_protocols_on_change,
            ),
        )
        _print_result(result)
    finally:
        db.close()


def _run_five_verst_location(cfg: SessionConfig, slug: str, *, summaries: int, protocols: int) -> None:
    from app.config import get_settings
    from app.sync.global_sync import LocationSyncOptions, sync_location

    db = _get_db()
    try:
        settings = get_settings()
        if cfg.dry_run:
            print(f"(dry-run) Локация {slug}: парсинг без записи пока не реализован — выберите «запись» или latest dry-run.")
            return
        result = sync_location(
            db,
            LocationSyncOptions(
                location_slug=slug,
                summaries_limit=None if summaries == 0 else summaries,
                protocol_fetch_limit=protocols,
                fetch_all_protocols_on_change=settings.five_verst_fetch_all_protocols_on_change,
            ),
        )
        _print_result(result)
    finally:
        db.close()


def _run_five_verst_locations_batch(cfg: SessionConfig) -> None:
    db = _get_db()
    try:
        slugs = _pick_locations(db)
    finally:
        db.close()
    if not slugs:
        return

    summaries = _prompt_int("Саммари на локацию (0 = все)", default=5)
    protocols = _prompt_int("Протоколов на локацию (0 = пропустить)", default=1)

    for index, slug in enumerate(slugs, start=1):
        print(f"\n── [{index}/{len(slugs)}] {slug} ──")
        _run_five_verst_location(cfg, slug, summaries=summaries, protocols=protocols)


def _run_five_verst_registry(cfg: SessionConfig) -> None:
    from app.sync.five_verst_locations import LocationRegistrySyncOptions, sync_locations_registry

    limit = _prompt_int("Сколько записей реестра обработать (0 = все)", default=0)
    db = _get_db()
    try:
        if cfg.dry_run:
            print("(dry-run) Реестр локаций всегда пишет в БД — для проверки выберите limit=5 на local.")
            return
        result = sync_locations_registry(
            db,
            LocationRegistrySyncOptions(
                fetch_missing_coordinates=True,
                detect_duplicates=True,
                limit=limit if limit > 0 else None,
            ),
        )
        _print_result(result)
    finally:
        db.close()


def _run_five_verst_reconcile(cfg: SessionConfig) -> None:
    from app.config import get_settings
    from app.sync.five_verst_reconcile import (
        ReconcileProtocolsOptions,
        plan_stale_protocol_reconcile,
        reconcile_stale_protocols,
    )

    limit = _prompt_int("Сколько протоколов перепроверить", default=10)
    min_age = _prompt_int("Мин. давность проверки (дней)", default=0)
    slug_raw = input("Slug локации (Enter = все): ").strip()
    slug = slug_raw or None

    db = _get_db()
    try:
        if cfg.dry_run:
            candidates = plan_stale_protocol_reconcile(
                db,
                limit=limit,
                min_check_interval_days=min_age,
                location_slug=slug,
            )
            _print_result({"candidates_total": len(candidates), "planned": [asdict(c) for c in candidates[:20]]})
            return
        settings = get_settings()
        result = reconcile_stale_protocols(
            db,
            ReconcileProtocolsOptions(
                limit=limit,
                min_check_interval_days=min_age or settings.five_verst_reconcile_min_check_interval_days,
                location_slug=slug,
            ),
        )
        _print_result(result)
    finally:
        db.close()


def _run_s95_latest(cfg: SessionConfig) -> None:
    from app.config import get_settings
    from app.sync.s95_latest import S95LatestSyncOptions, fetch_latest_activity_summaries, sync_s95_latest
    from app.sync.s95_summary_plan import plan_event_summaries_sync

    protocols = _prompt_int("Протоколов загрузить (0 = по настройке)", default=0)
    db = _get_db()
    try:
        if cfg.dry_run:
            summaries = fetch_latest_activity_summaries()
            plan = plan_event_summaries_sync(db, summaries)
            payload = {
                "summaries_total": len(summaries),
                "needs_update": sum(1 for x in plan if x.action.value != "unchanged"),
            }
            _print_result(payload)
            return
        settings = get_settings()
        result = sync_s95_latest(
            db,
            S95LatestSyncOptions(
                dry_run=False,
                update_limit=settings.s95_sync_latest_update_limit or None,
                protocol_fetch_limit=protocols if protocols > 0 else settings.s95_sync_protocol_limit,
                ensure_locations=True,
                fetch_all_protocols_on_change=settings.s95_fetch_all_protocols_on_change,
            ),
        )
        _print_result(result)
    finally:
        db.close()


def _run_s95_registry(cfg: SessionConfig) -> None:
    from app.sync.s95_locations_registry import S95LocationRegistrySyncOptions, sync_s95_locations_registry

    limit = _prompt_int("Сколько локаций (0 = все)", default=0)
    db = _get_db()
    try:
        if cfg.dry_run:
            print("(dry-run) S95 registry — выберите local + limit для пробного прогона.")
            return
        result = sync_s95_locations_registry(
            db,
            S95LocationRegistrySyncOptions(limit=limit if limit > 0 else None),
        )
        _print_result(result)
    finally:
        db.close()


def _run_s95_reconcile(cfg: SessionConfig) -> None:
    from app.config import get_settings
    from app.sync.s95_reconcile import (
        ReconcileProtocolsOptions,
        plan_stale_protocol_reconcile,
        reconcile_stale_protocols,
    )

    limit = _prompt_int("Сколько протоколов", default=10)
    db = _get_db()
    try:
        if cfg.dry_run:
            candidates = plan_stale_protocol_reconcile(db, limit=limit)
            _print_result({"candidates_total": len(candidates)})
            return
        settings = get_settings()
        result = reconcile_stale_protocols(
            db,
            ReconcileProtocolsOptions(limit=limit, min_check_interval_days=settings.s95_reconcile_min_check_interval_days),
        )
        _print_result(result)
    finally:
        db.close()


def _main_menu(cfg: SessionConfig) -> None:
    while True:
        choice = input(
            "\nВыберите задачу:\n"
            "  1 — 5verst: последние протоколы (/results/latest/)\n"
            "  2 — 5verst: одна или несколько локаций\n"
            "  3 — 5verst: перепроверка старых протоколов (reconcile)\n"
            "  4 — 5verst: реестр локаций (/events/)\n"
            "  5 — S95: последние активности\n"
            "  6 — S95: реестр локаций\n"
            "  7 — S95: перепроверка протоколов\n"
            "  0 — Выход\n"
            "Ваш выбор: "
        ).strip()

        if choice == "0":
            return
        if choice == "1":
            _run_five_verst_latest(cfg)
        elif choice == "2":
            _run_five_verst_locations_batch(cfg)
        elif choice == "3":
            _run_five_verst_reconcile(cfg)
        elif choice == "4":
            _run_five_verst_registry(cfg)
        elif choice == "5":
            _run_s95_latest(cfg)
        elif choice == "6":
            _run_s95_registry(cfg)
        elif choice == "7":
            _run_s95_reconcile(cfg)
        else:
            print("Неверный ввод.")
            continue

        again = input("\nЕщё задачу в этой сессии? (Enter — да, n — выход): ").strip().lower()
        if again == "n":
            return


def main() -> int:
    _load_project_env()
    os.chdir(BACKEND)
    try:
        cfg = _setup_session()
        _main_menu(cfg)
    except KeyboardInterrupt:
        print("\nПрервано.")
        return 130
    except Exception as exc:
        print(f"\nОшибка: {exc}", file=sys.stderr)
        return 1
    print("\nГотово.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
