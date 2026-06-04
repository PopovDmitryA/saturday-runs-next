from __future__ import annotations

import app.platform_adapters.registry as registry
from app.platform_adapters.five_verst.adapter import FiveVerstAdapter
from app.platform_adapters.registry import ensure_adapters_registered, get_adapter, register_adapter


def test_ensure_registers_s95_after_partial_registry() -> None:
    registry._adapters.clear()
    register_adapter(FiveVerstAdapter())
    ensure_adapters_registered()
    assert get_adapter("s95").platform_code == "s95"
    assert get_adapter("five_verst").platform_code == "five_verst"
