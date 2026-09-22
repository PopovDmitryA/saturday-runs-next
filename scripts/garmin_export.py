#!/usr/bin/env python3
"""Выгрузка своих пробежек из Garmin Connect: список, треки, оригинальные файлы.

Зачем: наполнить базу треков (Ч33) реальными данными, не дожидаясь официального
экспорта аккаунта. Скрипт логинится ТВОЕЙ учёткой Garmin, забирает беговые
активности и складывает по каждой: сводку, детальный трек с высотами,
оригинальный FIT и GPX. Повторный запуск не перекачивает уже скачанное.

Пароль нигде не сохраняется: он спрашивается в терминале и уходит только в
Garmin. После первого входа рядом лежит токен сессии (--token-dir), дальше вход
без пароля. Двухфакторный код тоже спрашивается в терминале.

Библиотека — python-garminconnect: garth, на котором обычно делают такие
выгрузки, с весны 2026 не работает для новых входов (Garmin сменил авторизацию).

    python3 -m venv ~/.venvs/garmin && ~/.venvs/garmin/bin/pip install garminconnect
    ~/.venvs/garmin/bin/python scripts/garmin_export.py --limit 3

Полезные флаги:
    --out DIR          куда складывать (по умолчанию ~/garmin_export)
    --since YYYY-MM-DD брать активности не раньше даты
    --saturdays        только субботы и воскресенья (парковые старты)
    --limit N          ограничить число активностей (для пробы: --limit 3)
    --no-files         не качать FIT и GPX, только сводки и треки (быстро)
    --skip-gpx         не качать GPX: в FIT есть всё то же, а запросов вдвое меньше
    --pause N          пауза между запросами, если Garmin отвечает 429

На выходе в DIR:
    index.json / index.csv         список выгруженного с ключевыми полями
    <id>/summary.json              сводка: дистанция, набор, устройство, время старта
    <id>/details.json              трек по точкам: координаты, высоты, пульс, каденс
    <id>/original.fit              оригинальный файл с часов (самый полный источник)
    <id>/activity.gpx              тот же трек в GPX
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import time
import zipfile
from datetime import date, datetime
from pathlib import Path

PAGE_SIZE = 100
# Пауза между запросами: Garmin не любит частых обращений, а нам спешить некуда.
# Меняется флагом --pause: на входе он уже отвечал 429 «слишком часто».
DEFAULT_PAUSE_SEC = 1.5
REQUEST_PAUSE_SEC = DEFAULT_PAUSE_SEC
# Сколько ждать, когда Garmin всё-таки ответил 429, и сколько раз пробовать.
RATE_LIMIT_WAIT_SEC = 60
RATE_LIMIT_RETRIES = 3
# Сколько точек трека просить у details: 4000 с запасом покрывают часовую
# пробежку с посекундной записью.
MAX_POLYLINE_POINTS = 4000
MAX_CHART_POINTS = 4000


def _require_client():
    try:
        from garminconnect import Garmin
    except ImportError:
        sys.exit(
            "Нужна библиотека garminconnect. Поставь её в отдельное окружение:\n"
            "    python3 -m venv ~/.venvs/garmin\n"
            "    ~/.venvs/garmin/bin/pip install garminconnect\n"
            "и запусти скрипт этим питоном: ~/.venvs/garmin/bin/python scripts/garmin_export.py"
        )
    return Garmin


def login(token_dir: Path):
    """Вход: сначала пробуем сохранённый токен, потом почту с паролем."""
    Garmin = _require_client()
    token_dir.mkdir(parents=True, exist_ok=True)
    tokenstore = str(token_dir)

    client = Garmin()
    try:
        client.login(tokenstore)
        print(f"Вход по сохранённому токену ({tokenstore})")
        return client
    except Exception:
        pass

    import getpass

    print("Вход в Garmin Connect. Пароль не сохраняется и никуда, кроме Garmin, не уходит.")
    email = input("E-mail Garmin: ").strip()
    password = getpass.getpass("Пароль: ")
    client = Garmin(
        email=email,
        password=password,
        # Если на аккаунте двухфакторка, Garmin пришлёт код — спросим его здесь.
        prompt_mfa=lambda: input("Код подтверждения из приложения или почты: ").strip(),
    )
    client.login(tokenstore)
    print(f"Вход выполнен, токен сохранён в {tokenstore} — в следующий раз пароль не понадобится.")
    return client


def fetch_activity_list(client, activity_type: str, since: date | None, limit: int | None) -> list[dict]:
    """Список активностей с постраничным обходом, от свежих к старым."""
    items: list[dict] = []
    start = 0
    while True:
        page = client.get_activities(start, PAGE_SIZE, activitytype=activity_type or None)
        if not page:
            break
        stop = False
        for item in page:
            started = _started_at(item)
            if since and started and started.date() < since:
                stop = True
                break
            items.append(item)
            if limit and len(items) >= limit:
                stop = True
                break
        print(f"  получено {len(items)} активностей…")
        if stop or len(page) < PAGE_SIZE:
            break
        start += PAGE_SIZE
        time.sleep(REQUEST_PAUSE_SEC)
    return items


def _started_at(item: dict) -> datetime | None:
    raw = item.get("startTimeLocal") or item.get("startTimeGMT")
    if not raw:
        return None
    try:
        return datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            return datetime.fromisoformat(raw[:19])
        except ValueError:
            return None


def save_activity(client, activity: dict, out_dir: Path, *, want_fit: bool, want_gpx: bool) -> dict:
    """Сохраняет одну активность и возвращает строку для индекса."""
    Garmin = _require_client()
    activity_id = activity["activityId"]
    folder = out_dir / str(activity_id)
    folder.mkdir(parents=True, exist_ok=True)

    summary_path = folder / "summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    else:
        summary = _safe(lambda: client.get_activity(str(activity_id)), "сводку") or activity
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        time.sleep(REQUEST_PAUSE_SEC)

    details_path = folder / "details.json"
    if not details_path.exists():
        details = _safe(
            lambda: client.get_activity_details(
                str(activity_id), maxchart=MAX_CHART_POINTS, maxpoly=MAX_POLYLINE_POINTS
            ),
            "трек",
        )
        if details:
            details_path.write_text(json.dumps(details, ensure_ascii=False), encoding="utf-8")
        time.sleep(REQUEST_PAUSE_SEC)

    if want_fit:
        fit_path = folder / "original.fit"
        if not fit_path.exists():
            blob = _safe(
                lambda: client.download_activity(
                    str(activity_id), dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL
                ),
                "оригинальный файл",
            )
            if blob:
                _write_original(blob, folder, fit_path)
            time.sleep(REQUEST_PAUSE_SEC)

    if want_gpx:
        gpx_path = folder / "activity.gpx"
        if not gpx_path.exists():
            blob = _safe(
                lambda: client.download_activity(str(activity_id), dl_fmt=Garmin.ActivityDownloadFormat.GPX),
                "GPX",
            )
            if blob:
                gpx_path.write_bytes(blob)
            time.sleep(REQUEST_PAUSE_SEC)

    return _index_row(activity_id, summary, folder)


def _safe(action, what: str):
    """Одна неудачная активность не должна ронять всю выгрузку.

    Отдельно ловим 429: Garmin ограничивает по IP, и правильная реакция —
    подождать и повторить, а не пропустить активность.
    """
    for attempt in range(RATE_LIMIT_RETRIES):
        try:
            return action()
        except Exception as exc:  # noqa: BLE001
            too_many = "429" in str(exc) or "TooManyRequests" in type(exc).__name__
            if too_many and attempt < RATE_LIMIT_RETRIES - 1:
                print(f"    Garmin просит подождать ({what}), пауза {RATE_LIMIT_WAIT_SEC} с…")
                time.sleep(RATE_LIMIT_WAIT_SEC)
                continue
            print(f"    не удалось получить {what}: {type(exc).__name__}: {exc}")
            return None
    return None


def _write_original(blob: bytes, folder: Path, fit_path: Path) -> None:
    """Garmin отдаёт оригинал в zip; внутри обычно один .fit."""
    if blob[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            names = archive.namelist()
            fit_names = [name for name in names if name.lower().endswith(".fit")]
            if fit_names:
                fit_path.write_bytes(archive.read(fit_names[0]))
                return
            for name in names:
                (folder / Path(name).name).write_bytes(archive.read(name))
        return
    fit_path.write_bytes(blob)


def _index_row(activity_id: int, summary: dict, folder: Path) -> dict:
    s = summary.get("summaryDTO") or {}
    meta = summary.get("metadataDTO") or {}
    device = (summary.get("deviceInfo") or {}).get("productDisplayName") or summary.get("deviceName")
    polyline = ((summary.get("activityDetailsForMap") or {}).get("geoPolylineDTO")) or {}
    start_point = polyline.get("startPoint") or {}
    return {
        "activity_id": activity_id,
        "name": summary.get("activityName"),
        "start_local": s.get("startTimeLocal") or summary.get("startTimeLocal"),
        "start_gmt": s.get("startTimeGMT") or summary.get("startTimeGMT"),
        "distance_m": s.get("distance") or summary.get("distance"),
        "duration_sec": s.get("duration") or summary.get("duration"),
        "elevation_gain_m": s.get("elevationGain") or summary.get("elevationGain"),
        "elevation_loss_m": s.get("elevationLoss"),
        "min_elevation_m": s.get("minElevation"),
        "max_elevation_m": s.get("maxElevation"),
        "avg_hr": s.get("averageHR"),
        "device": device,
        # Правил ли Garmin высоту по картам вместо барометра.
        "elevation_corrected": meta.get("elevationCorrected"),
        "has_polyline": meta.get("hasPolyline"),
        "start_lat": start_point.get("lat"),
        "start_lon": start_point.get("lon"),
        "folder": str(folder),
    }


def write_index(rows: list[dict], out_dir: Path) -> None:
    (out_dir / "index.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    if not rows:
        return
    with (out_dir / "index.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    global REQUEST_PAUSE_SEC

    parser = argparse.ArgumentParser(description="Выгрузка пробежек из Garmin Connect")
    parser.add_argument("--out", default=str(Path.home() / "garmin_export"), help="папка для выгрузки")
    parser.add_argument("--token-dir", default=str(Path.home() / ".garmin_token"), help="где хранить токен входа")
    parser.add_argument("--since", help="брать активности не раньше даты, YYYY-MM-DD")
    parser.add_argument("--limit", type=int, help="ограничить число активностей")
    parser.add_argument("--type", default="running", help="тип активности (running, cycling, all)")
    parser.add_argument("--saturdays", action="store_true", help="только суббота и воскресенье")
    parser.add_argument("--no-files", action="store_true", help="не качать FIT и GPX")
    parser.add_argument(
        "--skip-gpx",
        action="store_true",
        help="не качать GPX: в FIT есть всё то же и больше, а запросов вдвое меньше",
    )
    parser.add_argument("--pause", type=float, help=f"пауза между запросами, сек (по умолчанию {DEFAULT_PAUSE_SEC})")
    args = parser.parse_args()

    if args.pause:
        REQUEST_PAUSE_SEC = args.pause

    since = date.fromisoformat(args.since) if args.since else None
    out_dir = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    client = login(Path(args.token_dir).expanduser())

    activity_type = "" if args.type == "all" else args.type
    print(f"Забираю список активностей (тип: {args.type})…")
    activities = fetch_activity_list(client, activity_type, since, args.limit)

    if args.saturdays:
        before = len(activities)
        activities = [a for a in activities if (_started_at(a) or datetime.now()).weekday() in (5, 6)]
        print(f"Оставил выходные: {len(activities)} из {before}")

    print(f"К выгрузке: {len(activities)} активностей → {out_dir}")
    rows: list[dict] = []
    for number, activity in enumerate(activities, start=1):
        started = _started_at(activity)
        label = started.strftime("%Y-%m-%d %H:%M") if started else "без даты"
        print(f"[{number}/{len(activities)}] {activity['activityId']} · {label} · {activity.get('activityName')}")
        rows.append(
            save_activity(
                client,
                activity,
                out_dir,
                want_fit=not args.no_files,
                want_gpx=not args.no_files and not args.skip_gpx,
            )
        )
        write_index(rows, out_dir)

    print(f"\nГотово. Активностей: {len(rows)}. Папка: {out_dir}")
    print(f"Список: {out_dir / 'index.csv'}")


if __name__ == "__main__":
    main()
