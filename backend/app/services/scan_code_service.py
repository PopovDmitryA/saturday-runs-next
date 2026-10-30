"""Штрихкод участника для сканера на финише — тот же, что в «Профилях беговых
систем» на сайте (кнопка с QR в карточке привязки).

Код собирается из привязки так же, как на сайте (`platformScanCode` в
frontend/src/lib/format.ts): штрихкод системы, а у 5 вёрст без него — номер
участника; на выходе «A» + цифры. QR — с теми же параметрами, что рисует
сайт (QrCodeModal.tsx): режим alphanumeric и коррекция Q, чтобы код читался
с бликующего экрана телефона.

Нужен боту: под напоминанием о плане кнопка «Мой штрихкод», и бот присылает
картинку прямо в чат — на старте её не ищут по сайту.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from uuid import UUID

import segno
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Participant, Platform, PlatformLink

# Как SCAN_CODE_RE на сайте: буква «A» (латинская или русская) необязательна.
_SCAN_CODE_RE = re.compile(r"^[AaАа]?(\d+)$")
# Сторона картинки — как у QR на сайте (320 px): модулей у кода ~25–29.
QR_SCALE = 10
QR_BORDER = 1
QR_DARK = "#0f172a"


def scan_code(platform_code: str, barcode_id: str | None, external_user_id: str | None) -> str | None:
    source = barcode_id or (external_user_id if platform_code == "five_verst" else None)
    if not source:
        return None
    match = _SCAN_CODE_RE.match(re.sub(r"\s+", "", source.strip()))
    return f"A{match.group(1)}" if match else None


@dataclass(frozen=True)
class ScanCode:
    platform_code: str
    code: str
    display_name: str | None


def user_scan_codes(db: Session, user_id: UUID) -> dict[str, ScanCode]:
    """Система → штрихкод человека: только привязки, из которых код собирается."""
    rows = db.execute(
        select(Platform.code, PlatformLink.external_user_id, Participant.barcode_id, Participant.display_name)
        .join(Platform, Platform.id == PlatformLink.platform_id)
        .outerjoin(Participant, Participant.id == PlatformLink.participant_id)
        .where(PlatformLink.user_id == user_id)
    ).all()
    codes: dict[str, ScanCode] = {}
    for platform_code, external_user_id, barcode_id, display_name in rows:
        code = scan_code(platform_code, barcode_id, external_user_id)
        if code:
            codes[platform_code] = ScanCode(platform_code, code, display_name)
    return codes


def qr_png(code: str) -> bytes:
    qr = segno.make_qr(code, mode="alphanumeric", error="q", boost_error=False)
    buffer = io.BytesIO()
    qr.save(buffer, kind="png", scale=QR_SCALE, border=QR_BORDER, dark=QR_DARK, light="#ffffff")
    return buffer.getvalue()
