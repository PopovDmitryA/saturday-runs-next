from __future__ import annotations

BAN_MARKERS = (
    "recaptcha",
    "g-recaptcha",
    "are you a robot",
    "unusual traffic",
    "our systems have detected",
    "to continue, please enable javascript",
    "cloudflare",
    "access denied",
    "too many requests",
    "captcha",
    "forbidden",
)


def is_ban_or_protection_html(html: str) -> bool:
    if not html:
        return True
    text_value = html.lower()
    if any(marker in text_value for marker in BAN_MARKERS):
        return True
    # Chromium wraps HTTP 403 in a tiny error page with <pre>Forbidden</pre>.
    return len(html) < 400 and "forbidden" in text_value
