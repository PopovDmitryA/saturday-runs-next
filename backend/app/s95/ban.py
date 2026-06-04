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
)


def is_ban_or_protection_html(html: str) -> bool:
    if not html:
        return True
    text_value = html.lower()
    return any(marker in text_value for marker in BAN_MARKERS)
