#!/usr/bin/env python3
"""Проверить, что сайт умеет отправлять почту.

Запуск внутри контейнера api (на проде или локально):
    docker compose exec api python scripts/send_test_email.py --to you@example.com

Сначала проверяется сеть: открыт ли исходящий порт до SMTP-хоста. Это первое,
что ломается — у сервера может не быть исходящего доступа наружу (с
api.telegram.org мы это уже проходили). Дальше — реальная отправка письма.

    --check-only   только проверить настройки и доступность порта, не отправлять
"""

from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.core.mailer import (  # noqa: E402
    MailerError,
    is_configured,
    reply_to_email,
    send_email,
    sender_email,
)


def _check_port(host: str, port: int, timeout: float) -> str | None:
    """Вернуть None, если порт открыт, иначе текст ошибки."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return None
    except OSError as exc:
        return str(exc)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--to", help="Кому отправить пробное письмо")
    parser.add_argument("--check-only", action="store_true", help="Не отправлять письмо")
    args = parser.parse_args()

    settings = get_settings()

    print(f"SMTP host:     {settings.smtp_host}:{settings.smtp_port} (ssl={settings.smtp_use_ssl})")
    print(f"SMTP user:     {settings.smtp_user or '— не задан —'}")
    print(f"From:          {settings.smtp_from_name} <{sender_email(settings) or '—'}>")
    print(f"Reply-To:      {reply_to_email(settings) or '—'}")
    print(f"smtp_enabled:  {settings.smtp_enabled}")
    print()

    if not settings.smtp_host.strip():
        print("✗ SMTP_HOST пуст — заполните .env")
        return 1

    print(f"Проверяю доступность {settings.smtp_host}:{settings.smtp_port} …")
    error = _check_port(settings.smtp_host, settings.smtp_port, settings.smtp_timeout_seconds)
    if error is not None:
        print(f"✗ порт недоступен: {error}")
        print("  Похоже, у сервера нет исходящего доступа к почтовому хосту.")
        print("  Проверьте фаервол и попробуйте порт 587 (SMTP_PORT=587, SMTP_USE_SSL=false).")
        return 1
    print("✓ порт открыт")

    if not is_configured(settings):
        print("✗ не хватает настроек: нужны SMTP_ENABLED=true, SMTP_USER, SMTP_PASSWORD")
        return 1
    print("✓ настройки на месте")

    if args.check_only:
        return 0

    if not args.to:
        print("\nУкажите --to, чтобы отправить пробное письмо (или запустите с --check-only).")
        return 1

    print(f"\nОтправляю письмо на {args.to} …")
    try:
        send_email(
            settings,
            to=args.to,
            subject="Проверка почты run5k.run",
            text_body=(
                "Это пробное письмо с сайта run5k.run.\n\n"
                "Если вы его видите, отправка настроена верно.\n"
            ),
        )
    except MailerError as exc:
        print(f"✗ не отправилось: {exc}")
        return 1

    print("✓ письмо отправлено — проверьте ящик, в том числе папку «Спам»")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
