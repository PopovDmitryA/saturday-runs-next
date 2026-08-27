#!/usr/bin/env python3
"""Разовый прогон наблюдателя отмен s95 (то же, что делает beat 4 раза в сутки).

Нужен там, где ждать расписание нечего: сразу после деплоя, чтобы отмена
доехала до сайта в тот же час, и при разборе «а почему на странице ничего не
написано». `--no-notify` — прогон без сообщения админу.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import get_session_factory
from app.sync.s95_cancellations import watch_s95_cancellations
from scripts.script_runtime import add_bootstrap_args, apply_bootstrap_args, bootstrap_from_env


def main() -> int:
    bootstrap_from_env()
    parser = argparse.ArgumentParser(description="Проверить отмены ближайшего старта у s95")
    add_bootstrap_args(parser)
    parser.add_argument("--no-notify", action="store_true", help="Не слать сообщение админу")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    apply_bootstrap_args(args)

    db = get_session_factory()()
    try:
        result = watch_s95_cancellations(db, notify=not args.no_notify)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    finally:
        db.close()

    indent = 2 if args.pretty else None
    print(json.dumps(asdict(result), ensure_ascii=False, indent=indent))
    return 0 if not result.errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
