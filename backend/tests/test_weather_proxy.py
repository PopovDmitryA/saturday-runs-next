"""Прогноз Open-Meteo дома — через прокси, архив — напрямую (21.10.2026).

С домашнего адреса api.open-meteo.com не отвечает вовсе (пакеты глушатся у
провайдера), а archive-api отвечает. Через архив ночью идут тысячи вызовов,
поэтому через прокси пускаем только прогнозный хост.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import httpx

from app.services.weather_service import OPEN_METEO_ARCHIVE_URL, OPEN_METEO_FORECAST_URL, open_meteo_client


def _client(proxy: str) -> httpx.Client:
    with patch("app.config.get_settings", return_value=SimpleNamespace(weather_proxy_url=proxy)):
        return open_meteo_client("test")


def test_forecast_goes_through_proxy_and_archive_does_not() -> None:
    with _client("http://tg-proxy:8118") as client:
        forecast = client._transport_for_url(httpx.URL(OPEN_METEO_FORECAST_URL))
        archive = client._transport_for_url(httpx.URL(OPEN_METEO_ARCHIVE_URL))
        assert forecast is not client._transport
        assert archive is client._transport
        assert client.headers["User-Agent"] == "test"


def test_without_proxy_everything_goes_direct() -> None:
    with _client("") as client:
        assert client._transport_for_url(httpx.URL(OPEN_METEO_FORECAST_URL)) is client._transport
