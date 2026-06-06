from __future__ import annotations

import random

from app.config import get_settings
from app.core.request_cancel import interruptible_sleep


def wait_between_protocols(*, reason: str = "protocol") -> None:
    """Extra pause between full protocol upserts (runs + volunteers)."""
    del reason
    settings = get_settings()
    delay = random.uniform(
        settings.five_verst_protocol_min_interval_seconds,
        settings.five_verst_protocol_max_interval_seconds,
    )
    interruptible_sleep(delay)
