from __future__ import annotations

import re

# Краулеры, которые исполняют JavaScript и потому доходят до наших фронтовых
# счётчиков наравне с людьми. Обычный поисковый робот забирает HTML и уходит —
# он аналитику не двигает; эти же прогоняют SPA целиком, и каждая их страница
# попадала в page_view_events как «уникальный посетитель» (localStorage между
# страницами они не хранят, поэтому visitor_key у них новый на каждый заход).
#
# Поводом стал meta-externalagent, обошедший 30.08.2026 страницы протоколов:
# 2229 просмотров за сутки при обычных 300-800 по всему сайту.
_BOT_MARKERS = (
    "meta-externalagent",
    "facebookexternalhit",
    "gptbot",
    "oai-searchbot",
    "chatgpt-user",
    "claudebot",
    "anthropic-ai",
    "perplexitybot",
    "bytespider",
    "amazonbot",
    "applebot",
    "googlebot",
    "google-inspectiontool",
    "adsbot-google",
    "bingbot",
    "yandexbot",
    "yandeximages",
    "mail.ru_bot",
    "duckduckbot",
    "baiduspider",
    "ahrefsbot",
    "semrushbot",
    "mj12bot",
    "dotbot",
    "dataforseobot",
    "petalbot",
    "seznambot",
    "telegrambot",
    "twitterbot",
    "slackbot",
    "discordbot",
    "whatsapp",
    "vkshare",
    "linkedinbot",
    "headlesschrome",
    "phantomjs",
    "puppeteer",
    "playwright",
    "python-requests",
    "httpx",
    "curl/",
    "wget/",
    "scrapy",
)

# Родовые слова ловим только как отдельный токен: подстрокой "bot" метится,
# например, "Botswana" в локали, а "spider" — редкие, но живые user-agent'ы.
_GENERIC_BOT_RE = re.compile(r"(?:^|[^a-z])(bot|crawler|spider|scraper)(?:[^a-z]|$)")


def is_bot_user_agent(user_agent: str | None) -> bool:
    """Похож ли user-agent на робота.

    Пустой user-agent тоже считаем роботом: живой браузер его всегда шлёт, а
    вот скрипты и прогревалки — сплошь и рядом нет.
    """
    if user_agent is None:
        return True
    normalized = user_agent.strip().lower()
    if not normalized:
        return True
    if any(marker in normalized for marker in _BOT_MARKERS):
        return True
    return bool(_GENERIC_BOT_RE.search(normalized))
