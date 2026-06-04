from app.auth.providers import yandex as yandex_provider
from app.config import Settings


def test_yandex_authorize_url_omits_scope_by_default() -> None:
    settings = Settings(
        yandex_oauth_client_id="12345",
        app_base_url="https://app.run5k.run",
    )
    url = yandex_provider.yandex_authorize_url(settings, "state-token-32chars-minimum-ok")
    assert url.startswith("https://oauth.yandex.ru/authorize?")
    assert "client_id=12345" in url
    assert "scope=" not in url


def test_yandex_authorize_url_includes_configured_scope() -> None:
    settings = Settings(
        yandex_oauth_client_id="12345",
        app_base_url="https://app.run5k.run",
        yandex_oauth_scopes="login:info,login:email",
    )
    url = yandex_provider.yandex_authorize_url(settings, "state-token-32chars-minimum-ok")
    assert "scope=login%3Ainfo%2Clogin%3Aemail" in url
