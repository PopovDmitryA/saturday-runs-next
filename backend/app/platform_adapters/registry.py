from __future__ import annotations

from app.platform_adapters.base import PlatformAdapter

_adapters: dict[str, PlatformAdapter] = {}

_PLATFORM_ADAPTER_CLASSES: tuple[tuple[str, str], ...] = (
    ("five_verst", "app.platform_adapters.five_verst.adapter.FiveVerstAdapter"),
    ("s95", "app.platform_adapters.s95.adapter.S95Adapter"),
    ("parkrun", "app.platform_adapters.parkrun.adapter.ParkrunAdapter"),
)


def register_adapter(adapter: PlatformAdapter) -> None:
    _adapters[adapter.platform_code] = adapter


def get_adapter(platform_code: str) -> PlatformAdapter:
    ensure_adapters_registered()
    if platform_code not in _adapters:
        raise KeyError(f"No adapter registered for platform: {platform_code}")
    return _adapters[platform_code]


def list_adapters() -> list[PlatformAdapter]:
    ensure_adapters_registered()
    return list(_adapters.values())


def ensure_adapters_registered() -> None:
    for platform_code, class_path in _PLATFORM_ADAPTER_CLASSES:
        if platform_code in _adapters:
            continue
        module_path, _, class_name = class_path.rpartition(".")
        module = __import__(module_path, fromlist=[class_name])
        adapter_cls = getattr(module, class_name)
        register_adapter(adapter_cls())
