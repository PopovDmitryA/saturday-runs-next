from app.core.security import hash_token, sign_session_id, verify_signed_session_id


def test_hash_token_is_deterministic() -> None:
    assert hash_token("abc") == hash_token("abc")
    assert hash_token("abc") != hash_token("def")


def test_session_sign_and_verify() -> None:
    secret = "test-secret"
    session_id = "session-123"
    signed = sign_session_id(session_id, secret)
    assert verify_signed_session_id(signed, secret) == session_id
    assert verify_signed_session_id(signed, "wrong") is None
    assert verify_signed_session_id("tampered.value", secret) is None
