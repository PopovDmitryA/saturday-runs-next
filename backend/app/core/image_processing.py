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

from PIL import Image, ImageOps, UnidentifiedImageError  # type: ignore[import-untyped]
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

    # Фото с телефона часто «лежит на боку» из-за EXIF-ориентации; заодно
    # transpose срезает сам EXIF — гео-метка снимка наружу не утекает.
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
