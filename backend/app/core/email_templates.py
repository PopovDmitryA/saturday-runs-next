"""Вёрстка писем сайта.

Почтовые клиенты — это браузеры из середины двухтысячных: Gmail вырезает
<style> в <head>, Outlook рисует движком Word, тёмная тема у каждого своя.
Поэтому здесь нарочно скучный HTML: одна таблица, инлайновые стили, никаких
внешних картинок, шрифтов и медиазапросов. Ширина 600 пикселей с
`width: 100%` — это ровно то, что помещается и в десктопный клиент, и в
телефон, не требуя адаптива.

Текстовая версия обязательна и идёт первой: по ней письмо читают спам-фильтры
и те, у кого HTML отключён.
"""

from __future__ import annotations

from html import escape

# Цвета берём светлые и контрастные: тёмная тема почтовых клиентов инвертирует
# фон сама, а насыщенный брендовый цвет на белом переживает инверсию лучше,
# чем собственный тёмный макет.
_TEXT = "#1c2430"
_MUTED = "#6b7280"
_ACCENT = "#3b5bfd"
_BORDER = "#e3e7ee"
_SITE = "run5k.run"


def _shell(inner: str) -> str:
    """Общий каркас письма: центрирующая таблица и подвал."""
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="background:#f4f6fa;margin:0;padding:24px 12px;">'
        "<tr><td align=\"center\">"
        '<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" '
        'style="width:100%;max-width:600px;background:#ffffff;border:1px solid '
        f'{_BORDER};border-radius:12px;">'
        f'<tr><td style="padding:28px 28px 8px 28px;font-family:Arial,Helvetica,sans-serif;'
        f'font-size:15px;line-height:1.5;color:{_TEXT};">'
        f'<div style="font-size:17px;font-weight:bold;color:{_TEXT};">{_SITE}</div>'
        f'<div style="font-size:13px;color:{_MUTED};padding-top:2px;">'
        "Статистика парковых пробежек</div>"
        "</td></tr>"
        f"{inner}"
        f'<tr><td style="padding:8px 28px 26px 28px;font-family:Arial,Helvetica,sans-serif;'
        f'font-size:12px;line-height:1.5;color:{_MUTED};border-top:1px solid {_BORDER};">'
        "Это письмо отправлено автоматически. "
        f'Если у вас есть вопрос, ответьте на него — мы читаем ответы.'
        "</td></tr>"
        "</table></td></tr></table>"
    )


def login_code_email(code: str, *, minutes: int, subscribe_url: str | None = None) -> tuple[str, str]:
    """Письмо с кодом входа. Возвращает (текст, html).

    subscribe_url — ссылка «подписаться на новости»; передаётся только тем, кто
    ещё не подписан. Подписанным ничего не предлагаем, а отписка живёт в самих
    рассылках, а не в служебном письме про вход.
    """
    text_lines = [
        f"Код для входа на {_SITE}: {code}",
        "",
        f"Он действует {minutes} мин. и подходит только для одного входа.",
        "",
        "Если вы не запрашивали код, просто удалите это письмо — "
        "без него войти в профиль нельзя.",
    ]
    if subscribe_url:
        text_lines += [
            "",
            "Хотите знать о крупных обновлениях сайта? Подпишитесь на новости:",
            subscribe_url,
        ]
    text_body = "\n".join(text_lines) + "\n"

    safe_code = escape(code)
    inner = (
        f'<tr><td style="padding:18px 28px 0 28px;font-family:Arial,Helvetica,sans-serif;'
        f'font-size:15px;line-height:1.5;color:{_TEXT};">'
        "Код для входа:"
        "</td></tr>"
        f'<tr><td style="padding:10px 28px 0 28px;font-family:Arial,Helvetica,sans-serif;">'
        f'<div style="font-size:32px;font-weight:bold;letter-spacing:6px;color:{_ACCENT};">'
        f"{safe_code}</div>"
        "</td></tr>"
        f'<tr><td style="padding:14px 28px 18px 28px;font-family:Arial,Helvetica,sans-serif;'
        f'font-size:14px;line-height:1.55;color:{_TEXT};">'
        f"Код действует {minutes} мин. и подходит только для одного входа.<br>"
        f'<span style="color:{_MUTED};">Если вы не запрашивали код, просто удалите это '
        "письмо — без него войти в профиль нельзя.</span>"
        "</td></tr>"
    )
    if subscribe_url:
        safe_url = escape(subscribe_url, quote=True)
        inner += (
            f'<tr><td style="padding:0 28px 20px 28px;font-family:Arial,Helvetica,sans-serif;'
            f'font-size:13px;line-height:1.55;color:{_MUTED};border-top:1px solid {_BORDER};'
            'padding-top:16px;">'
            "Хотите знать о крупных обновлениях сайта? "
            f'<a href="{safe_url}" style="color:{_ACCENT};">Подписаться на новости</a> — '
            "письма редкие, отписаться можно в один клик."
            "</td></tr>"
        )

    return text_body, _shell(inner)
