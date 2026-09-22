"""Приведение загруженной картинки к разумному JPEG.

Пользователь грузит фото прямо с телефона — это 3–12 МБ и 4000+ px по длинной
стороне. Хранить и раздавать такое незачем: всё, что больше MAX_SIDE_PX,
ужимается по длинной стороне с сохранением пропорций, результат всегда JPEG.
Картинка меньше лимита не растягивается — апскейл только портит.

Отдельный модуль от avatars.py сознательно: там своя задача (квадратный кроп
256×256 под аватарку), здесь — сохранить кадр целиком, просто поменьше.
"""

from __future__ import annotations

import io
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError  # type: ignore[import-untyped]
from PIL.ExifTags import IFD  # type: ignore[import-untyped]
from pillow_heif import register_heif_opener  # type: ignore[import-untyped]

# Айфон снимает в HEIC, и люди грузят фото прямо из галереи. Без этой
# регистрации Pillow такой файл не узнаёт (03.09.2026: «сайт не принимает
# фотки в heic»). Регистрируем один раз при импорте модуля.
register_heif_opener()

# 2K по длинной стороне (решение Дмитрия 28.07.2026: «сжимаем до 2К»).
# Одной константой меняется на 1920, если захочется экономнее.
MAX_SIDE_PX = 2560
JPEG_QUALITY = 85
# Больше — это уже не фотография со старта, а промах с файлом.
MAX_UPLOAD_BYTES = 15 * 1024 * 1024


class ImageProcessingError(ValueError):
    """Файл не удалось прочитать как изображение."""


def process_photo(raw: bytes, *, max_side: int = MAX_SIDE_PX) -> tuple[bytes, int, int]:
    """Любой поддерживаемый формат → JPEG не длиннее max_side. → (данные, w, h)."""
    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ImageProcessingError(
            "Не удалось прочитать изображение — поддерживаются JPEG, PNG, WebP и HEIC"
        ) from exc

    # Фото с телефона часто «лежит на боку» из-за EXIF-ориентации — сначала
    # поворачиваем по тегу, а сам EXIF (гео-метка, модель телефона, время
    # съёмки) дальше не едет: save() ниже вызывается без exif=, и Pillow его
    # не пишет. Это проверяет test_image_processing_exif.py.
    image = ImageOps.exif_transpose(image) or image

    if image.mode != "RGB":
        if image.mode in ("RGBA", "LA", "P"):
            # Прозрачный PNG иначе лёг бы на чёрный фон.
            converted = image.convert("RGBA")
            background = Image.new("RGB", converted.size, (255, 255, 255))
            background.paste(converted, mask=converted.split()[-1])
            image = background
        else:
            image = image.convert("RGB")

    width, height = image.size
    longest = max(width, height)
    if longest > max_side:
        scale = max_side / longest
        image = image.resize(
            (max(1, round(width * scale)), max(1, round(height * scale))),
            Image.Resampling.LANCZOS,
        )

    out = io.BytesIO()
    image.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
    return out.getvalue(), image.size[0], image.size[1]


def has_exif(data: bytes) -> bool:
    """Есть ли в картинке EXIF-блок (для тестов и проверок хранилища)."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            return bool(image.getexif())
    except (UnidentifiedImageError, OSError):
        return False


def strip_image_metadata(raw: bytes) -> tuple[bytes, str]:
    """Та же картинка, тот же формат и размер — но без EXIF/GPS.

    Возвращает байты и формат Pillow, в котором они записаны: для HEIC он
    меняется (на JPEG), а вызывающий обязан положить файл под правильным
    расширением — иначе браузер получит .heic под именем .jpg.

    Для мест, где оригинал хранят «как прислали» (полная аватарка по клику,
    avatars.py): байт-в-байт файл с телефона несёт координаты съёмки — обычно
    дома, — и в публичном бакете их прочитает кто угодно (SEC-04). Перекодируем
    в тот же формат без exif=; HEIC — в JPEG, потому что HEIF-оригинал в бакете
    под расширением .jpg браузер всё равно не покажет. Поворот по EXIF применяем
    до сброса, иначе снимок ляжет на бок.
    """
    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ImageProcessingError(
            "Не удалось прочитать изображение — поддерживаются JPEG, PNG, WebP и HEIC"
        ) from exc
    source_format = image.format or "JPEG"
    image = ImageOps.exif_transpose(image) or image
    out = io.BytesIO()
    if source_format in ("PNG", "WEBP", "GIF"):
        image.save(out, format=source_format)
        return out.getvalue(), source_format
    if image.mode != "RGB":
        image = image.convert("RGB")
    image.save(out, format="JPEG", quality=92, optimize=True)
    return out.getvalue(), "JPEG"


# Теги базового IFD, которые сохраняем у себя (номера — стандарт TIFF/EXIF).
_TAG_MAKE = 0x010F
_TAG_MODEL = 0x0110
_TAG_SOFTWARE = 0x0131
_TAG_ORIENTATION = 0x0112
_TAG_DATETIME = 0x0132
# В под-IFD Exif: время съёмки (а не последней правки файла).
_TAG_DATETIME_ORIGINAL = 0x9003
# GPS IFD: ref/значение широты, долготы и высоты.
_GPS_LAT_REF, _GPS_LAT, _GPS_LON_REF, _GPS_LON, _GPS_ALT_REF, _GPS_ALT = 1, 2, 3, 4, 5, 6
_MAX_TEXT = 128


def _exif_text(value: object) -> str | None:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if not isinstance(value, str):
        return None
    text = value.replace("\x00", "").strip()
    return text[:_MAX_TEXT] or None


def _dms_to_decimal(dms: object, ref: object) -> float | None:
    """(градусы, минуты, секунды) + буква полушария → десятичные градусы."""
    try:
        deg, minutes, seconds = (float(part) for part in dms)  # type: ignore[union-attr]
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    value = deg + minutes / 60 + seconds / 3600
    ref_text = _exif_text(ref) or ""
    if ref_text.upper() in ("S", "W"):
        value = -value
    return round(value, 6)


def extract_image_metadata(raw: bytes) -> dict[str, Any]:
    """EXIF снимка словарём для users.avatar_exif — только то, что есть.

    {datetime_original, make, model, software, orientation, gps: {lat, lon, alt}};
    координаты переведены из DMS в десятичные градусы, высота — метры (ниже
    уровня моря — отрицательная). Битый или отсутствующий EXIF — пустой словарь:
    метаданные не стоят того, чтобы ронять загрузку аватарки.
    """
    try:
        with Image.open(io.BytesIO(raw)) as image:
            exif = image.getexif()
            if not exif:
                return {}
            result: dict[str, Any] = {}
            exif_ifd = exif.get_ifd(IFD.Exif)
            taken = _exif_text(exif_ifd.get(_TAG_DATETIME_ORIGINAL)) or _exif_text(exif.get(_TAG_DATETIME))
            if taken:
                result["datetime_original"] = taken
            for key, tag in (("make", _TAG_MAKE), ("model", _TAG_MODEL), ("software", _TAG_SOFTWARE)):
                text = _exif_text(exif.get(tag))
                if text:
                    result[key] = text
            orientation = exif.get(_TAG_ORIENTATION)
            if isinstance(orientation, int) and 1 <= orientation <= 8:
                result["orientation"] = orientation
            gps_ifd = exif.get_ifd(IFD.GPSInfo)
            gps: dict[str, float] = {}
            if _GPS_LAT in gps_ifd and _GPS_LON in gps_ifd:
                lat = _dms_to_decimal(gps_ifd.get(_GPS_LAT), gps_ifd.get(_GPS_LAT_REF))
                lon = _dms_to_decimal(gps_ifd.get(_GPS_LON), gps_ifd.get(_GPS_LON_REF))
                if lat is not None and lon is not None:
                    gps["lat"], gps["lon"] = lat, lon
            if _GPS_ALT in gps_ifd:
                try:
                    alt = round(float(gps_ifd[_GPS_ALT]), 1)
                    # AltitudeRef 1 — ниже уровня моря.
                    if gps_ifd.get(_GPS_ALT_REF) in (1, b"\x01"):
                        alt = -alt
                    gps["alt"] = alt
                except (TypeError, ValueError, ZeroDivisionError):
                    pass
            if gps:
                result["gps"] = gps
            return result
    except (UnidentifiedImageError, OSError, ValueError, TypeError, KeyError):
        return {}
