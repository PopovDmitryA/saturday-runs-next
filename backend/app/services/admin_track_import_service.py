"""Импорт чужих треков из админки: архивы, файлы, ссылки — с предпросмотром.

Сценарий: турист присылает выгрузку из Garmin, админ загружает её и указывает,
чьи это пробежки. Треки разбираются и создаются сразу, но со статусом preview —
в кабинет участника они попадают только после подтверждения.

Разбор идёт порциями: выгрузка аккаунта за несколько лет — это сотни файлов,
в один запрос они не укладываются. Файлы до подтверждения лежат во временной
папке, очередь необработанного — в `pending` сессии.
"""

from __future__ import annotations

import hashlib
import shutil
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AdminTrackImport, Event, Location, RunTrack, User
from app.services.location_course_service import rebuild_for_tracks
from app.services.run_track_service import build_track
from app.services.track_parsing import TrackParseError, parse_garmin_link, parse_upload

# Где лежат распакованные файлы до подтверждения. Не /tmp: он живёт внутри
# контейнера, и деплой посреди разбора уносил очередь вместе с ним — архив
# на 205 файлов приходилось загружать заново (поймано 23.09.2026). /data
# смонтирован с хоста и перезапуск переживает.
STAGING_ROOT = Path("/data/track_imports")
TRACK_SUFFIXES = (".fit", ".gpx", ".tcx")
# Архив выгрузки Garmin за годы — это десятки мегабайт и сотни файлов.
# Тот же потолок стоит на nginx (location /api/admin/track-imports в
# nginx/conf.d/default.conf): без него запрос не доходил до приложения и
# админ видел HTML-страницу «413 Request Entity Too Large» вместо ошибки.
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_FILES_PER_BATCH = 2000
# Вложенные архивы: выгрузка аккаунта кладёт zip внутрь zip.
MAX_ARCHIVE_DEPTH = 3
# Сколько источников разбираем за один запрос: 25 файлов укладываются
# примерно в десять секунд и не упираются в таймаут nginx.
DEFAULT_CHUNK = 25
# Трек считаем дублем, если у участника уже есть трек с тем же началом.
DUPLICATE_WINDOW = timedelta(minutes=2)


class TrackImportError(ValueError):
    """Ошибка, которую показываем админу как есть."""


@dataclass
class ExtractedFile:
    name: str
    data: bytes


def _staging_dir(batch_id: UUID) -> Path:
    return STAGING_ROOT / str(batch_id)


def extract_track_files(name: str, data: bytes, *, depth: int = 0) -> list[ExtractedFile]:
    """Разворачивает архив в список файлов треков; обычный файл возвращает как есть."""
    lowered = name.lower()
    if lowered.endswith(".zip") or data[:2] == b"PK":
        if depth >= MAX_ARCHIVE_DEPTH:
            return []
        found: list[ExtractedFile] = []
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                for info in archive.infolist():
                    if info.is_dir() or info.file_size > MAX_ARCHIVE_BYTES:
                        continue
                    inner = info.filename.lower()
                    if not (inner.endswith(TRACK_SUFFIXES) or inner.endswith(".zip")):
                        continue
                    found.extend(
                        extract_track_files(info.filename, archive.read(info), depth=depth + 1)
                    )
                    if len(found) >= MAX_FILES_PER_BATCH:
                        break
        except zipfile.BadZipFile as exc:
            raise TrackImportError(f"Архив «{name}» не читается.") from exc
        return found
    if lowered.endswith(TRACK_SUFFIXES):
        return [ExtractedFile(name=Path(name).name, data=data)]
    return []


def create_batch(
    db: Session,
    *,
    admin: User,
    target_user: User,
    source_kind: str,
    files: list[ExtractedFile],
    links: list[str],
) -> AdminTrackImport:
    """Создаёт сессию импорта и раскладывает источники в очередь на разбор."""
    if not files and not links:
        raise TrackImportError("Не нашёл ни одного трека: приложите файлы, архив или ссылки.")

    batch = AdminTrackImport(
        created_by_user_id=admin.id,
        target_user_id=target_user.id,
        source_kind=source_kind,
        status="collecting",
        total_count=len(files) + len(links),
        pending=[],
    )
    db.add(batch)
    db.flush()

    pending: list[dict[str, str]] = []
    folder = _staging_dir(batch.id)
    folder.mkdir(parents=True, exist_ok=True)
    for index, item in enumerate(files):
        # Имена внутри архива повторяются (original.fit в каждой папке),
        # поэтому на диск кладём под порядковым номером.
        stored = folder / f"{index:05d}{Path(item.name).suffix.lower()}"
        stored.write_bytes(item.data)
        pending.append({"kind": "file", "name": item.name, "path": str(stored)})
    for link in links:
        pending.append({"kind": "link", "name": link, "path": link})

    batch.pending = pending
    db.flush()
    return batch


def process_chunk(db: Session, batch: AdminTrackImport, *, limit: int = DEFAULT_CHUNK) -> AdminTrackImport:
    """Разбирает следующую порцию источников сессии."""
    if batch.status not in {"collecting", "previewing"}:
        raise TrackImportError("Эта загрузка уже закрыта.")

    target_user = db.get(User, batch.target_user_id)
    if target_user is None:
        raise TrackImportError("Участник не найден.")

    pending: list[dict[str, str]] = list(batch.pending or [])
    problems: list[dict[str, str]] = list(batch.problems or [])
    imported = batch.imported_count
    skipped = batch.skipped_count
    processed = batch.processed_count

    for item in pending[:limit]:
        processed += 1
        try:
            track = _build_from_source(db, target_user, item)
        except TrackParseError as exc:
            problems.append({"name": item["name"], "reason": str(exc)})
            skipped += 1
            continue
        except Exception as exc:  # noqa: BLE001 — один плохой файл не должен ронять импорт
            problems.append({"name": item["name"], "reason": f"{type(exc).__name__}: {exc}"})
            skipped += 1
            continue

        if track is None:
            problems.append({"name": item["name"], "reason": "Этот трек уже загружен"})
            skipped += 1
            continue

        track.status = "preview"
        track.import_batch_id = batch.id
        track.imported_by_user_id = batch.created_by_user_id
        db.add(track)
        db.flush()
        imported += 1

    batch.pending = pending[limit:]
    batch.problems = problems
    batch.processed_count = processed
    batch.imported_count = imported
    batch.skipped_count = skipped
    batch.status = "previewing" if not batch.pending else "collecting"
    db.flush()
    return batch


def _build_from_source(db: Session, target_user: User, item: dict[str, str]) -> RunTrack | None:
    if item["kind"] == "link":
        parsed = parse_garmin_link(item["path"])
    else:
        source = Path(item["path"])
        if not source.exists():
            # Старые сессии держали файлы в /tmp контейнера — после его
            # пересоздания дорешать такую загрузку нечем.
            raise TrackParseError("Файл больше не лежит на сервере — загрузите архив заново")
        data = source.read_bytes()
        parsed = parse_upload(item["name"], data)
        # Внутри архива имена одинаковые, поэтому ключом делаем отпечаток
        # содержимого: он же ловит повторную загрузку того же файла.
        digest = hashlib.sha1(data).hexdigest()[:12]
        parsed.source_ref = f"{digest}/{Path(item['name']).name}"[:512]

    if _is_duplicate(db, target_user.id, parsed.source, parsed.source_ref, parsed.started_at):
        return None
    return build_track(db, target_user, parsed)


def _is_duplicate(
    db: Session,
    user_id: UUID,
    source: str,
    source_ref: str,
    started_at: datetime | None,
) -> bool:
    same_ref = db.scalar(
        select(RunTrack.id).where(
            RunTrack.user_id == user_id,
            RunTrack.source == source,
            RunTrack.source_ref == source_ref,
        )
    )
    if same_ref is not None:
        return True
    if started_at is None:
        return False
    # Один и тот же старт мог приехать файлом и ссылкой — сверяем по времени.
    return (
        db.scalar(
            select(RunTrack.id).where(
                RunTrack.user_id == user_id,
                RunTrack.started_at.is_not(None),
                RunTrack.started_at >= started_at - DUPLICATE_WINDOW,
                RunTrack.started_at <= started_at + DUPLICATE_WINDOW,
            )
        )
        is not None
    )


# Ниже какой длины трек — точно не субботняя пятёрка, а обрывок записи:
# забыли остановить часы, перезапустили посреди старта, записали заминку.
# 3 км выбраны с большим запасом: реальные пятёрки короче 4,6 км не бывают
# (нижняя граница годности трека — в track_validation.py).
JUNK_DISTANCE_M = 3000
# Выше этого трек содержит явно не только пробежку — например, дорогу домой.
LONG_DISTANCE_M = 9000


def _better_of(left: RunTrack, right: RunTrack) -> RunTrack:
    """Какой из двух треков одной пробежки считать основным.

    Сначала годность для измерения трассы, затем близость к времени
    протокола, затем длина ближе к пяти километрам.
    """
    def rank(track: RunTrack) -> tuple[int, int, float]:
        delta = abs(track.protocol_delta_sec) if track.protocol_delta_sec is not None else 10**6
        distance_miss = abs((track.distance_m or 0) - 5000)
        return (0 if track.is_course_eligible else 1, delta, distance_miss)

    return left if rank(left) <= rank(right) else right


def _suggestions(tracks: list[RunTrack]) -> dict[UUID, tuple[bool, str | None]]:
    """Что предлагаем взять по умолчанию, чтобы админ смотрел только спорное.

    Правило: берём всё, кроме явных отклонений. Снимаем галочку у треков без
    пробежки в протоколе (это будние тренировки, сайту они не нужны), у
    обрывков и слишком длинных записей и у второго трека на ту же пробежку —
    из дублей остаётся лучший.
    """
    best_for_run: dict[UUID, RunTrack] = {}
    for track in tracks:
        if track.run_result_id is None:
            continue
        current = best_for_run.get(track.run_result_id)
        best_for_run[track.run_result_id] = track if current is None else _better_of(current, track)

    result: dict[UUID, tuple[bool, str | None]] = {}
    for track in tracks:
        if track.run_result_id is None:
            result[track.id] = (False, "пробежки нет в протоколе — похоже на обычную тренировку")
            continue
        distance = track.distance_m or 0
        if distance < JUNK_DISTANCE_M:
            result[track.id] = (False, "слишком короткий для пятёрки — обрывок записи")
            continue
        if distance > LONG_DISTANCE_M:
            result[track.id] = (False, "слишком длинный — в записи не только сам старт")
            continue
        if best_for_run.get(track.run_result_id) is not track:
            result[track.id] = (False, "на эту пробежку уже есть трек лучше")
            continue
        result[track.id] = (True, None)
    return result


def preview_items(db: Session, batch: AdminTrackImport) -> list[dict[str, Any]]:
    """Что получилось разобрать: по треку на строку, с найденной пробежкой."""
    rows = db.execute(
        select(RunTrack, Event, Location)
        .outerjoin(Event, RunTrack.event_id == Event.id)
        .outerjoin(Location, RunTrack.location_id == Location.id)
        .where(RunTrack.import_batch_id == batch.id)
        .order_by(RunTrack.started_at.asc().nullsfirst())
    ).all()

    suggestions = _suggestions([track for track, _event, _location in rows])
    items: list[dict[str, Any]] = []
    for track, event, location in rows:
        metrics = track.metrics or {}
        suggested, suggestion_note = suggestions[track.id]
        items.append(
            {
                "track_id": track.id,
                "source": track.source,
                "source_ref": track.source_ref,
                "started_at": track.started_at,
                "distance_m": track.distance_m,
                "duration_sec": track.duration_sec,
                "elevation_gain_m": track.elevation_gain_m,
                "has_elevation_profile": bool(metrics.get("elevation_profile")),
                "device_name": track.device_name,
                "quality_class": track.quality_class,
                "lap_count": metrics.get("lap_count"),
                "location_name": location.name if location else None,
                "event_date": event.event_date if event else None,
                "matched_run": track.run_result_id is not None,
                "protocol_delta_sec": track.protocol_delta_sec,
                "is_course_eligible": track.is_course_eligible,
                "exclusion_reason": track.exclusion_reason,
                "exclusion_note": track.exclusion_note,
                "suggested": suggested,
                "suggestion_note": suggestion_note,
            }
        )
    return items


def apply_batch(
    db: Session,
    batch: AdminTrackImport,
    *,
    track_ids: list[UUID] | None = None,
) -> AdminTrackImport:
    """Подтверждение: отмеченные треки становятся видимыми в кабинете.

    `track_ids` — что именно админ отметил галочками в предпросмотре;
    остальные разобранные треки удаляются вместе с сессией. None означает
    «взять всё разобранное» (так ведут себя старые вызовы и тесты).
    """
    if batch.status not in {"collecting", "previewing"}:
        raise TrackImportError("Эта загрузка уже закрыта.")
    if batch.pending:
        raise TrackImportError("Сначала дождитесь разбора всех файлов.")

    parsed = list(db.scalars(select(RunTrack).where(RunTrack.import_batch_id == batch.id)))
    if track_ids is None:
        tracks, dropped = parsed, []
    else:
        chosen = set(track_ids)
        tracks = [track for track in parsed if track.id in chosen]
        dropped = [track for track in parsed if track.id not in chosen]
    if not tracks:
        raise TrackImportError("Не отмечено ни одного трека.")

    for track in dropped:
        db.delete(track)
    for track in tracks:
        track.status = "ok"
    batch.imported_count = len(tracks)
    batch.skipped_count = batch.skipped_count + len(dropped)
    db.flush()
    # Паспорта трасс пересобираем сразу: иначе локация покажет старые цифры.
    rebuild_for_tracks(db, tracks)
    target = db.get(User, batch.target_user_id)
    if target is not None and target.track_consent_at is None:
        # Треки принёс админ со слов участника — отметка о согласии всё равно
        # нужна, иначе непонятно, на каком основании они лежат.
        target.track_consent_at = datetime.now(UTC)
    batch.status = "applied"
    batch.applied_at = datetime.now(UTC)
    db.flush()
    _cleanup_staging(batch.id)
    return batch


def discard_batch(db: Session, batch: AdminTrackImport) -> AdminTrackImport:
    """Отмена: разобранные треки удаляются вместе с файлами."""
    if batch.status == "applied":
        raise TrackImportError("Загрузка уже подтверждена, отменить её нельзя.")
    for track in db.scalars(select(RunTrack).where(RunTrack.import_batch_id == batch.id)):
        db.delete(track)
    batch.status = "discarded"
    batch.pending = []
    db.flush()
    _cleanup_staging(batch.id)
    return batch


def _cleanup_staging(batch_id: UUID) -> None:
    shutil.rmtree(_staging_dir(batch_id), ignore_errors=True)


def get_batch(db: Session, batch_id: UUID) -> AdminTrackImport | None:
    return db.get(AdminTrackImport, batch_id)


def list_batches(db: Session, *, limit: int = 20) -> list[AdminTrackImport]:
    return list(
        db.scalars(select(AdminTrackImport).order_by(AdminTrackImport.created_at.desc()).limit(limit))
    )
