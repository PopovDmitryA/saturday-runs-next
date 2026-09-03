"""HEIC с айфона читается и перекодируется в JPEG.

03.09.2026: «фотки в heic формате сайт не принимает». В образе не было
декодера HEIF — Pillow такой файл не узнавал.
"""

from __future__ import annotations

import io

from PIL import Image

from app.core.image_processing import process_photo


def _heic_bytes(width: int = 64, height: int = 48) -> bytes:
    image = Image.new("RGB", (width, height), (200, 30, 30))
    out = io.BytesIO()
    image.save(out, format="HEIF")  # кодек тот же pillow-heif, что и на чтение
    return out.getvalue()


def test_heic_is_decoded_and_converted_to_jpeg() -> None:
    data, width, height = process_photo(_heic_bytes())
    assert (width, height) == (64, 48)
    assert Image.open(io.BytesIO(data)).format == "JPEG"
