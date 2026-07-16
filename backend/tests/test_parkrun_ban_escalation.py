from __future__ import annotations

import time
from unittest.mock import patch

import fakeredis
import pytest

from app.parkrun.errors import ParkrunBanDetected


@pytest.fixture
def fake_redis() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis(decode_responses=True)


def _patch_redis(fake: fakeredis.FakeRedis):
    return patch("app.parkrun.fetch.captcha_state.get_redis_client", return_value=fake)


def test_escalation_ladder_steps(fake_redis: fakeredis.FakeRedis) -> None:
    from app.parkrun.fetch.captcha_state import ban_cooldown_level, escalate_ban_cooldown

    steps = [3600, 18000, 86400, 259200, 604800]
    with _patch_redis(fake_redis):
        for i, step in enumerate(steps, start=1):
            until = escalate_ban_cooldown()
            assert ban_cooldown_level() == i
            assert until == pytest.approx(time.time() + step, abs=5)
        # Выше последней ступени не поднимаемся — неделя повторяется.
        until = escalate_ban_cooldown()
        assert until == pytest.approx(time.time() + steps[-1], abs=5)


def test_success_resets_ladder(fake_redis: fakeredis.FakeRedis) -> None:
    from app.parkrun.fetch.captcha_state import (
        ban_cooldown_level,
        clear_captcha_pending,
        escalate_ban_cooldown,
        is_captcha_pending,
        set_captcha_pending,
    )

    with _patch_redis(fake_redis):
        set_captcha_pending("captcha detected")
        escalate_ban_cooldown()
        escalate_ban_cooldown()
        assert ban_cooldown_level() == 2
        assert is_captcha_pending()

        clear_captcha_pending()
        assert ban_cooldown_level() == 0
        assert not is_captcha_pending()
        assert fake_redis.get("parkrun:fetch:ban_cooldown_until") is None
        # Следующая капча начинает лестницу заново с 1 часа.
        until = escalate_ban_cooldown()
        assert ban_cooldown_level() == 1
        assert until == pytest.approx(time.time() + 3600, abs=5)


def test_coordinator_blocks_while_cooldown_active(fake_redis: fakeredis.FakeRedis) -> None:
    from app.parkrun.fetch import coordinator
    from app.parkrun.fetch.captcha_state import escalate_ban_cooldown

    with (
        _patch_redis(fake_redis),
        patch("app.parkrun.fetch.coordinator.get_redis_client", return_value=fake_redis),
    ):
        escalate_ban_cooldown()
        with pytest.raises(ParkrunBanDetected, match="cooldown until"):
            coordinator._check_ban_cooldown()


def test_coordinator_allows_probe_after_cooldown_expiry(fake_redis: fakeredis.FakeRedis) -> None:
    from app.parkrun.fetch import coordinator
    from app.parkrun.fetch.captcha_state import (
        BAN_COOLDOWN_KEY,
        ban_cooldown_level,
        escalate_ban_cooldown,
        set_captcha_pending,
    )

    with (
        _patch_redis(fake_redis),
        patch("app.parkrun.fetch.coordinator.get_redis_client", return_value=fake_redis),
    ):
        set_captcha_pending("captcha detected")
        escalate_ban_cooldown()
        # Истекшая ступень: висящий captcha_pending сам по себе не блокирует.
        fake_redis.set(BAN_COOLDOWN_KEY, str(time.time() - 10))
        coordinator._check_ban_cooldown()  # не бросает — проба разрешена

        # Проба снова упёрлась в капчу — следующая ступень (5 часов).
        until = escalate_ban_cooldown()
        assert ban_cooldown_level() == 2
        assert until == pytest.approx(time.time() + 18000, abs=5)


def test_cooldown_message_parseable_for_pending_rows(fake_redis: fakeredis.FakeRedis) -> None:
    """parse_cooldown_until_from_message должен извлекать срок из текста ошибки."""
    from app.parkrun.fetch import coordinator
    from app.parkrun.fetch.captcha_state import escalate_ban_cooldown
    from app.platform_fetch.cooldown import parse_cooldown_until_from_message

    with (
        _patch_redis(fake_redis),
        patch("app.parkrun.fetch.coordinator.get_redis_client", return_value=fake_redis),
    ):
        until = escalate_ban_cooldown()
        with pytest.raises(ParkrunBanDetected) as exc_info:
            coordinator._check_ban_cooldown()
        parsed = parse_cooldown_until_from_message(str(exc_info.value))
        assert parsed is not None
        assert parsed.timestamp() == pytest.approx(until, abs=1)
