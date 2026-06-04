#!/usr/bin/env python3
"""Send a Telegram broadcast to users with news_subscribed enabled."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.db.session import get_session_factory
from app.services.news_broadcast_service import list_news_subscribers, send_news_broadcast

DEFAULT_MESSAGE = (
    "Тестовая рассылка Saturday Runs.\n\n"
    "Уведомления работают — вы подписаны на новости сервиса. "
    "Отключить можно в разделе «Настройки» на сайте."
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Send Telegram news broadcast")
    parser.add_argument(
        "--message",
        default=DEFAULT_MESSAGE,
        help="Message text (default: test broadcast)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List recipients without sending",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.05,
        help="Delay between messages in seconds (default: 0.05)",
    )
    args = parser.parse_args()

    settings = get_settings()
    if not settings.telegram_bot_token:
        print("TELEGRAM_BOT_TOKEN is not set", file=sys.stderr)
        return 1

    db = get_session_factory()()
    try:
        subscribers = list_news_subscribers(db)
    finally:
        db.close()

    if not subscribers:
        print("No subscribers (news_subscribed=true with telegram_chat_id)")
        return 0

    print(f"Recipients: {len(subscribers)}")
    for user in subscribers:
        print(f"  - {user.label} (telegram_id={user.telegram_id}, chat_id={user.telegram_chat_id})")

    if args.dry_run:
        print("Dry run — messages not sent")
        return 0

    db = get_session_factory()()
    try:
        result = send_news_broadcast(
            db,
            args.message,
            settings=settings,
            delay_seconds=args.delay,
        )
    finally:
        db.close()

    print(f"Done: sent={result.sent}, failed={result.failed}")
    for failure in result.failures:
        print(f"Failed for {failure.label}: {failure.error}", file=sys.stderr)
    return 0 if result.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
