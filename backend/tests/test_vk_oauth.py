import base64
import hashlib

from app.auth.providers import vk as vk_provider
from app.config import Settings


def test_code_challenge_matches_verifier() -> None:
    verifier = vk_provider.generate_code_verifier()
    challenge = vk_provider.code_challenge_from_verifier(verifier)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    assert challenge == expected
    assert len(verifier) == 64


def test_vk_authorize_url_uses_vk_id_and_pkce() -> None:
    settings = Settings(
        vk_oauth_client_id="12345",
        app_base_url="http://localhost:8080",
        vk_oauth_redirect_uri="",
    )
    verifier = vk_provider.generate_code_verifier()
    url = vk_provider.vk_authorize_url(settings, "state-token-32chars-minimum-ok", verifier)
    assert url.startswith("https://id.vk.ru/authorize?")
    assert "code_challenge=" in url
    assert "code_challenge_method=S256" in url
    assert "client_id=12345" in url
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A8080%2Fapi%2Fauth%2Foauth%2Fvk%2Fcallback" in url


def test_vk_redirect_uri_for_localhost_port80() -> None:
    settings = Settings(
        vk_oauth_client_id="1",
        app_base_url="http://localhost",
    )
    assert vk_provider.vk_redirect_uri(settings) == "http://localhost/api/auth/oauth/vk/callback"
