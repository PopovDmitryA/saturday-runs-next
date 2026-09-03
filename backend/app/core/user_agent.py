"""Человеческое описание браузера и системы по User-Agent.

Нужно одному месту — сообщению бота «откуда вход»: человек должен узнать
свой телефон или ноутбук, а не разбирать строку UA. Точность до семейства
(Chrome / Safari / Яндекс Браузер, iPhone / Android / Windows / macOS), без
версий — их человек всё равно не помнит. Библиотек не тянем: десяток регулярок
закрывает всё, что реально ходит на сайт (см. журнал входов).
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class UserAgentInfo:
    browser: str
    os: str

    def label(self) -> str:
        parts = [part for part in (self.browser, self.os) if part]
        return ", ".join(parts) if parts else "неизвестное устройство"


# Порядок важен: почти каждый браузер на Chromium несёт в UA и «Chrome», и
# «Safari», поэтому сначала проверяем более узкие метки.
_BROWSERS: tuple[tuple[str, str], ...] = (
    (r"YaBrowser|YaApp_", "Яндекс Браузер"),
    (r"Telegram", "браузер Telegram"),
    (r"VKApp|VkBrowser|VKAndroidApp|vk_android|vkclient", "приложение VK"),
    (r"\bEdgA?/|\bEdg/", "Edge"),
    (r"\bOPR/|\bOpera", "Opera"),
    (r"SamsungBrowser", "Samsung Browser"),
    (r"\bFirefox/|\bFxiOS/", "Firefox"),
    (r"\bCriOS/", "Chrome"),
    (r"\bChrome/|\bChromium/", "Chrome"),
    (r"\bSafari/", "Safari"),
)

_SYSTEMS: tuple[tuple[str, str], ...] = (
    (r"iPhone", "iPhone"),
    (r"iPad", "iPad"),
    (r"Android", "Android"),
    (r"HarmonyOS|HUAWEI", "Huawei"),
    (r"Windows", "Windows"),
    (r"Macintosh|Mac OS X", "macOS"),
    (r"CrOS", "ChromeOS"),
    (r"Linux", "Linux"),
)


def describe_user_agent(user_agent: str | None) -> UserAgentInfo:
    ua = (user_agent or "").strip()
    if not ua:
        return UserAgentInfo(browser="", os="")

    browser = ""
    for pattern, name in _BROWSERS:
        if re.search(pattern, ua, flags=re.IGNORECASE):
            browser = name
            break

    os_name = ""
    for pattern, name in _SYSTEMS:
        if re.search(pattern, ua, flags=re.IGNORECASE):
            os_name = name
            break

    # Safari на Android не бывает: это WebView или неизвестный Chromium.
    if browser == "Safari" and os_name in {"Android", "Huawei"}:
        browser = "браузер"
    return UserAgentInfo(browser=browser, os=os_name)
