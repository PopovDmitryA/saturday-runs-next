from __future__ import annotations

import pytest

from app.core.user_agent import describe_user_agent

CHROME_ANDROID = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Mobile Safari/537.36"
)
SAFARI_IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.5 Mobile/15E148 Safari/604.1"
)
YANDEX_WINDOWS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 YaBrowser/24.7.0.0 Safari/537.36"
)
TELEGRAM_IOS = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Mobile/15E148 Telegram-iOS/10.9"
)
FIREFOX_MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:128.0) Gecko/20100101 Firefox/128.0"


@pytest.mark.parametrize(
    ("user_agent", "browser", "os_name"),
    [
        (CHROME_ANDROID, "Chrome", "Android"),
        (SAFARI_IPHONE, "Safari", "iPhone"),
        (YANDEX_WINDOWS, "Яндекс Браузер", "Windows"),
        (TELEGRAM_IOS, "браузер Telegram", "iPhone"),
        (FIREFOX_MAC, "Firefox", "macOS"),
        ("", "", ""),
    ],
)
def test_describe_user_agent(user_agent: str, browser: str, os_name: str) -> None:
    info = describe_user_agent(user_agent)
    assert (info.browser, info.os) == (browser, os_name)


def test_label_falls_back_when_unknown() -> None:
    assert describe_user_agent("curl/8.0").label() == "неизвестное устройство"
    assert describe_user_agent(CHROME_ANDROID).label() == "Chrome, Android"
