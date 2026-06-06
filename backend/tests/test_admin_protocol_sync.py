from app.services.admin_protocol_sync_service import (
    FIVE_VERST_PROTOCOL_URL_RE,
    sync_protocol_from_url,
)


def test_five_verst_protocol_url_pattern() -> None:
    match = FIVE_VERST_PROTOCOL_URL_RE.search(
        "https://5verst.ru/zil/results/31.05.2025/?utm=1"
    )
    assert match is not None
    assert match.group("slug") == "zil"
    assert match.group("date") == "31.05.2025"


def test_sync_protocol_rejects_unknown_host(db_session) -> None:
    try:
        sync_protocol_from_url(db_session, "https://example.com/foo")
    except ValueError as exc:
        assert "5verst" in str(exc).lower() or "s95" in str(exc).lower()
    else:
        raise AssertionError("expected ValueError")
