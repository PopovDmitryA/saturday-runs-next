"""EXIF (и гео-метка) не переживает обработку картинок.

Фото с телефона несёт координаты съёмки — обычно дома. process_photo кладёт
в публичный бакет перекодированный JPEG, и в нём EXIF быть не должно; на это
опирается фото в отзывах и бэклоге. strip_image_metadata — то же для мест,
где хранят «оригинал» (полная аватарка, SEC-04).
"""

from __future__ import annotations

import io

from PIL import Image
from PIL.TiffImagePlugin import IFDRational

from app.core.image_processing import extract_image_metadata, has_exif, process_photo, strip_image_metadata

_GPS_IFD = 0x8825


def _jpeg_with_gps(width: int = 80, height: int = 60) -> bytes:
    image = Image.new("RGB", (width, height), (30, 120, 60))
    exif = Image.Exif()
    exif[0x0110] = "TestPhone 12"  # Model
    exif[0x0112] = 6  # Orientation: повёрнуто на 90°
    gps = exif.get_ifd(_GPS_IFD)
    gps[1] = "N"
    gps[2] = (IFDRational(55, 1), IFDRational(45, 1), IFDRational(0, 1))
    gps[3] = "E"
    gps[4] = (IFDRational(37, 1), IFDRational(37, 1), IFDRational(0, 1))
    out = io.BytesIO()
    image.save(out, format="JPEG", exif=exif.tobytes())
    return out.getvalue()


def test_fixture_really_carries_gps() -> None:
    raw = _jpeg_with_gps()
    assert has_exif(raw)
    assert Image.open(io.BytesIO(raw)).getexif().get_ifd(_GPS_IFD)


def test_process_photo_drops_exif_and_applies_orientation() -> None:
    data, width, height = process_photo(_jpeg_with_gps(80, 60))
    assert not has_exif(data)
    # Ориентация 6 = поворот: кадр 80×60 стал 60×80, и это уже «в пикселях».
    assert (width, height) == (60, 80)


def test_strip_image_metadata_keeps_format_and_drops_gps() -> None:
    stripped, fmt = strip_image_metadata(_jpeg_with_gps())
    assert fmt == "JPEG"
    assert not has_exif(stripped)
    with Image.open(io.BytesIO(stripped)) as image:
        assert image.format == "JPEG"
        assert image.size == (60, 80)


def test_strip_image_metadata_png_stays_png() -> None:
    out = io.BytesIO()
    Image.new("RGBA", (10, 10), (1, 2, 3, 128)).save(out, format="PNG")
    stripped, fmt = strip_image_metadata(out.getvalue())
    assert fmt == "PNG"
    with Image.open(io.BytesIO(stripped)) as image:
        assert image.format == "PNG"
        assert image.mode == "RGBA"


def test_extract_image_metadata_reads_gps_as_decimal_degrees() -> None:
    """Теги — себе (users.avatar_exif): DMS → десятичные, модель, ориентация."""
    meta = extract_image_metadata(_jpeg_with_gps())
    assert meta["model"] == "TestPhone 12"
    assert meta["orientation"] == 6
    assert meta["gps"] == {"lat": 55.75, "lon": 37.616667}
    # Чего в снимке не было — того и в словаре нет.
    assert "make" not in meta
    assert "datetime_original" not in meta


def test_extract_image_metadata_southern_hemisphere_and_altitude() -> None:
    exif = Image.Exif()
    exif[0x010F] = "Acme"
    exif.get_ifd(0x8769)[0x9003] = "2026:09:21 10:11:12"
    gps = exif.get_ifd(_GPS_IFD)
    gps[1], gps[2] = "S", (IFDRational(33, 1), IFDRational(52, 1), IFDRational(30, 1))
    gps[3], gps[4] = "W", (IFDRational(70, 1), IFDRational(0, 1), IFDRational(0, 1))
    gps[5], gps[6] = 1, IFDRational(125, 10)
    out = io.BytesIO()
    Image.new("RGB", (8, 8)).save(out, format="JPEG", exif=exif.tobytes())

    meta = extract_image_metadata(out.getvalue())
    assert meta["make"] == "Acme"
    assert meta["datetime_original"] == "2026:09:21 10:11:12"
    assert meta["gps"] == {"lat": -33.875, "lon": -70.0, "alt": -12.5}


def test_extract_image_metadata_without_exif_is_empty() -> None:
    out = io.BytesIO()
    Image.new("RGB", (8, 8)).save(out, format="PNG")
    assert extract_image_metadata(out.getvalue()) == {}
    assert extract_image_metadata(b"not an image") == {}
