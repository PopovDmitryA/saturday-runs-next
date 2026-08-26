from __future__ import annotations

import pytest

from app.config import Settings
from app.core.abuse_store import list_signup_blocks
from app.core.signup_guard import (
    SignupContext,
    check_signup_allowed,
    record_block,
    register_signup,
    signup_block_message,
)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        signup_limit_per_ip_daily=3,
        signup_limit_per_device_daily=2,
    )


def test_first_signup_is_allowed(settings: Settings) -> None:
    context = SignupContext(ip="10.0.0.1", device_id="device-one")
    assert check_signup_allowed(context, settings).allowed


def test_device_limit_stops_third_account_from_one_browser(settings: Settings) -> None:
    context = SignupContext(ip="10.0.0.1", device_id="device-one")
    register_signup(context, settings)
    register_signup(context, settings)

    decision = check_signup_allowed(context, settings)
    assert not decision.allowed
    assert decision.reason == "device"
    assert decision.retry_after > 0


def test_ip_limit_stops_fourth_account_even_from_new_devices(settings: Settings) -> None:
    """Смена браузера или режим инкогнито обнуляют куку — остаётся лимит по адресу."""
    for index in range(3):
        register_signup(SignupContext(ip="10.0.0.2", device_id=f"device-{index}"), settings)

    decision = check_signup_allowed(SignupContext(ip="10.0.0.2", device_id="device-fresh"), settings)
    assert not decision.allowed
    assert decision.reason == "ip"


def test_other_person_behind_same_nat_is_not_punished_immediately(settings: Settings) -> None:
    """Два аккаунта с одного адреса — обычная семья, а не фрод: третий ещё пускаем."""
    register_signup(SignupContext(ip="10.0.0.3", device_id="device-a"), settings)
    register_signup(SignupContext(ip="10.0.0.3", device_id="device-b"), settings)

    assert check_signup_allowed(SignupContext(ip="10.0.0.3", device_id="device-c"), settings).allowed


def test_counters_are_independent_between_addresses(settings: Settings) -> None:
    for index in range(3):
        register_signup(SignupContext(ip="10.0.0.4", device_id=f"device-{index}"), settings)

    assert check_signup_allowed(SignupContext(ip="10.0.0.5", device_id="device-x"), settings).allowed


def test_guard_can_be_switched_off(settings: Settings) -> None:
    disabled = settings.model_copy(update={"signup_guard_enabled": False})
    context = SignupContext(ip="10.0.0.6", device_id="device-one")
    for _ in range(10):
        register_signup(context, settings)

    assert check_signup_allowed(context, disabled).allowed


def test_missing_device_cookie_falls_back_to_ip(settings: Settings) -> None:
    for _ in range(3):
        register_signup(SignupContext(ip="10.0.0.7"), settings)

    decision = check_signup_allowed(SignupContext(ip="10.0.0.7"), settings)
    assert not decision.allowed
    assert decision.reason == "ip"


def test_unknown_ip_is_not_counted(settings: Settings) -> None:
    """Без адреса и без куки считать нечего — вход не ломаем."""
    for _ in range(5):
        register_signup(SignupContext(ip="unknown"), settings)

    assert check_signup_allowed(SignupContext(ip="unknown"), settings).allowed


def test_block_is_written_to_admin_journal(settings: Settings) -> None:
    context = SignupContext(ip="10.0.0.8", device_id="device-one")
    register_signup(context, settings)
    register_signup(context, settings)
    decision = check_signup_allowed(context, settings)
    record_block(context, decision, provider="vk")

    blocks = list_signup_blocks()
    assert len(blocks) == 1
    assert blocks[0].ip == "10.0.0.8"
    assert blocks[0].provider == "vk"
    assert blocks[0].reason == "device"
    # В журнал попадает только хэш метки устройства, не сама подписанная кука.
    assert blocks[0].device_ref
    assert blocks[0].device_ref != "device-one"


def test_message_explains_which_limit_fired() -> None:
    from app.core.signup_guard import SignupDecision

    assert "устройства" in signup_block_message(SignupDecision(allowed=False, reason="device"))
    assert "сети" in signup_block_message(SignupDecision(allowed=False, reason="ip"))
