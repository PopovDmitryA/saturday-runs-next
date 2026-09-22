#!/usr/bin/env python3
"""Привести participants.profile_url у RunPark в порядок.

Что накопилось на проде к 14.09.2026 (23 518 участников RunPark):

  22 831  https://5verst.ru/userstats/<id>/   — адрес ЧУЖОЙ системы, причём с
                                                идентификатором RunPark внутри;
     325  .../Account/Karmas/anon:<...>       — «anon:» не идентификатор аккаунта;
     166  .../Account/Karmas/barcode:A…       — «barcode:» тоже не идентификатор;
     145  .../Account/Karmas/<GUID>           — единственный рабочий вид;
      51  A790124572                          — голый штрихкод вместо ссылки.

Правило простое: публичная страница RunPark открывается только по GUID аккаунта
(проверено 14.09.2026 — по чужому GUID из-под логина открывается нормально).
Значит у участника с ключом-GUID ссылка собирается из него, а у личностей с
ключами «barcode:» и «anon:» аккаунта на RunPark нет вовсе — там NULL, и
интерфейс просто не покажет ссылку вместо того, чтобы вести в никуда.

Идемпотентен: строки, уже приведённые к правилу, не трогает.

Запуск:
    docker compose exec api python scripts/backfill_runpark_profile_urls.py --dry-run
    make prod-run ARGS="scripts/backfill_runpark_profile_urls.py"
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import get_session_factory
from app.models import Participant, Platform

# Ключ аккаунта RunPark — GUID. Всё остальное («barcode:A…», «anon:<uuid>») мы
# придумали сами, чтобы различать строки протокола без аккаунта.
_GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

KARMAS_BASE = "https://runpark.ru/Account/Karmas"


def expected_profile_url(external_user_id: str) -> str | None:
    if _GUID_RE.match(external_user_id or ""):
        return f"{KARMAS_BASE}/{external_user_id}"
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Не писать в БД, только посчитать.")
    parser.add_argument("--show", type=int, default=10, help="Сколько примеров напечатать.")
    args = parser.parse_args()

    db = get_session_factory()()
    try:
        platform = db.query(Platform).filter(Platform.code == "runpark").one_or_none()
        if platform is None:
            print("Платформа runpark не найдена")
            return 1

        rows = db.query(Participant).filter(Participant.platform_id == platform.id).all()
        to_url = 0
        to_null = 0
        shown = 0
        for row in rows:
            want = expected_profile_url(row.external_user_id)
            if row.profile_url == want:
                continue
            if shown < args.show:
                print(f"  {row.external_user_id[:40]:42} {str(row.profile_url)[:46]:48} → {want}")
                shown += 1
            if want is None:
                to_null += 1
            else:
                to_url += 1
            if not args.dry_run:
                row.profile_url = want

        if not args.dry_run:
            db.commit()

        verb = "Поставил бы" if args.dry_run else "Поставил"
        print(
            f"\nВсего участников RunPark: {len(rows)}\n"
            f"{verb} рабочую ссылку по GUID: {to_url}\n"
            f"{verb} NULL (аккаунта на RunPark нет): {to_null}\n"
            f"Уже по правилу, не тронуто: {len(rows) - to_url - to_null}"
        )
        if args.dry_run:
            print("Запустите без --dry-run, чтобы применить.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
