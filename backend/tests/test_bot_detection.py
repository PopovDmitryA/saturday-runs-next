from __future__ import annotations

import pytest

from app.core.bot_detection import is_bot_user_agent

CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)
META_CRAWLER = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Safari/537.36 (compatible; meta-externalagent/1.1 "
    "(+https://developers.facebook.com/docs/sharing/webmasters/crawler))"
)
YANDEX_MOBILE = (
    "Mozilla/5.0 (Linux; arm_64; Android 16; RMX5555) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/150.0.7871.65 YaSearchBrowser/26.81.1 BroPP/1.0 YaSearchApp/26.81.1 webOmni Mobile Safari/537.36"
)
IPHONE_SAFARI = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/26.6 Mobile/15E148 Safari/604.1"
)


@pytest.mark.parametrize(
    "user_agent",
    [
        META_CRAWLER,
        "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
        "Mozilla/5.0 (compatible; YandexBot/3.0; +http://yandex.com/bots)",
        "GPTBot/1.2",
        "ClaudeBot/1.0",
        "PerplexityBot/1.0",
        "TelegramBot (like TwitterBot)",
        "curl/7.82.0",
        "python-requests/2.31.0",
        "Mozilla/5.0 HeadlessChrome/145.0.0.0",
        "some-random-crawler/1.0",
        "",
        "   ",
        None,
    ],
)
def test_bots_are_detected(user_agent: str | None) -> None:
    assert is_bot_user_agent(user_agent) is True


@pytest.mark.parametrize("user_agent", [CHROME, YANDEX_MOBILE, IPHONE_SAFARI])
def test_real_browsers_pass(user_agent: str) -> None:
    assert is_bot_user_agent(user_agent) is False


def test_generic_marker_needs_word_boundary() -> None:
    # "bot" внутри слова — не признак робота: иначе под нож пойдут живые
    # user-agent'ы с локалью вроде Botswana и браузеры типа Abbotsford.
    assert is_bot_user_agent(f"{CHROME} bot-swana/1.0") is True
    assert is_bot_user_agent(CHROME.replace("Windows NT 10.0", "Botswana Edition")) is False
