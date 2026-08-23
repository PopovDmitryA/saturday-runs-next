#!/usr/bin/env python3
"""Закрывает дашборд Grafana: копия в скрытый архив + заглушка со ссылкой на сайт.

Дашборды переезжают на run5k.run, но старые ссылки живут в закладках и чатах
локаций, поэтому дашборд не удаляется: по прежнему адресу остаётся заглушка
«переехало сюда», а прежнее содержимое ложится в папку «Архив закрытых
дашбордов» — она видна только админу (у роли Viewer прав на неё нет).

Использование:
    export GRAFANA_AUTH='admin:пароль'      # или GRAFANA_TOKEN='glsa_...'
    python3 scripts/grafana_close_dashboard.py --uid de1hu8dabny80c \
        --url https://run5k.run/ratings/locations \
        --where 'в рейтинге туризма' --dry-run

Что делает:
  1. кладёт текущий JSON в data/grafana/dashboards/ (третий рубеж на случай отката);
  2. копирует дашборд в архивную папку под именем «… (архив ДД.ММ.ГГГГ)»;
  3. подменяет содержимое основного дашборда заглушкой, сохраняя uid и адрес.

Откат: в Grafana у дашборда остаётся история версий (Dashboard settings →
Versions → Restore), плюс архивная копия, плюс файл в data/grafana/dashboards/.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ORIGIN = os.environ.get("GRAFANA_ORIGIN", "https://grafana.run5k.run")
ARCHIVE_TITLE = "Архив закрытых дашбордов"
BACKUP_DIR = Path(__file__).resolve().parents[1] / "data" / "grafana" / "dashboards"
CLOSED_TAG = "закрыт"


def api(path: str, payload: dict | None = None, method: str | None = None) -> dict:
    req = urllib.request.Request(ORIGIN + path, method=method or ("POST" if payload else "GET"))
    token = os.environ.get("GRAFANA_TOKEN")
    auth = os.environ.get("GRAFANA_AUTH")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    elif auth:
        import base64

        req.add_header("Authorization", "Basic " + base64.b64encode(auth.encode()).decode())
    else:
        sys.exit("нужен GRAFANA_TOKEN или GRAFANA_AUTH в окружении")
    if payload is not None:
        req.add_header("Content-Type", "application/json")
        req.data = json.dumps(payload).encode()
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as err:
        sys.exit(f"{path} → {err.code}: {err.read().decode()[:300]}")


def archive_folder_uid() -> str:
    for folder in api("/api/folders"):
        if folder["title"] == ARCHIVE_TITLE:
            return folder["uid"]
    sys.exit(f"нет папки «{ARCHIVE_TITLE}» — создай её и сними права у ролей Viewer и Editor")


def stub_panel(title: str, url: str, where: str) -> dict:
    # --where — законченное предложение-подсказка, отдельным абзацем: раньше он
    # вклеивался в середину фразы, и длинная подсказка её ломала.
    where_text = f"{where.rstrip('.')}.\n\n" if where else ""
    content = (
        # Через «дашборд», а не через название: иначе не согласуется род
        # («Карта … переехала», но «Рейтинг … переехал»).
        f"# {title}\n\n"
        f"**Дашборд закрыт** и больше не обновляется — он переехал на сайт:\n\n"
        f"## 👉 [{url.replace('https://', '')}]({url})\n\n"
        f"Там то же самое, только свежее и быстрее.\n\n"
        f"{where_text}"
        f"---\n\n"
        f"*Эта Grafana скоро будет отключена: все дашборды переезжают на "
        f"[run5k.run](https://run5k.run).*"
    )
    return {
        "type": "text",
        "id": 1,
        "title": "",
        "gridPos": {"h": 14, "w": 24, "x": 0, "y": 0},
        "transparent": True,
        "options": {"mode": "markdown", "content": content},
    }


def archive_from_history(args, dash: dict, meta: dict, title: str) -> None:
    """Для дашбордов, закрытых заглушкой вручную: архив берём из истории версий.

    Ищем самую свежую версию, где есть панель с данными — предыдущие правки
    заглушки (опечатка в тексте, поправленная ссылка) пропускаем.
    """
    versions = api(f"/api/dashboards/uid/{args.uid}/versions?limit=50")
    items = versions.get("versions", versions) if isinstance(versions, dict) else versions
    full = None
    for item in items:
        data = api(f"/api/dashboards/uid/{args.uid}/versions/{item['version']}").get("data") or {}
        if any(p.get("type") != "text" for p in data.get("panels", [])):
            full = (item["version"], data)
            break
    if full is None:
        sys.exit(f"«{title}»: в истории нет версии с панелями данных — архивировать нечего")
    version, data = full
    print(f"дашборд:  {title} ({args.uid}) — заглушка уже стоит")
    print(f"в архив:  версия v{version} от {items[0]['created'][:10] if items else '?'}, "
          f"панелей {len(data.get('panels', []))}")

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup = BACKUP_DIR / f"{args.uid}.v{version}.json"
    if args.dry_run:
        print("--- dry-run, ничего не записано ---")
        return
    backup.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print(f"копия на диск: {backup}")

    archived = dict(data)
    archived.pop("id", None)
    archived.pop("uid", None)
    archived["title"] = f"{title} (архив версии v{version})"
    archived["tags"] = sorted(set(data.get("tags") or []) | {"архив"})
    res = api(
        "/api/dashboards/db",
        {
            "dashboard": archived,
            "folderUid": archive_folder_uid(),
            "message": f"Архивная копия из истории версий (v{version})",
            "overwrite": False,
        },
    )
    print(f"архив: {ORIGIN}{res['url']}")

    if CLOSED_TAG not in (dash.get("tags") or []):
        dash["tags"] = sorted(set(dash.get("tags") or []) | {CLOSED_TAG})
        api(
            "/api/dashboards/db",
            {"dashboard": dash, "folderUid": meta.get("folderUid", ""),
             "message": "Тег «закрыт»", "overwrite": True},
        )
        print("тег «закрыт» проставлен")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uid", required=True, help="uid закрываемого дашборда")
    ap.add_argument("--url", default="", help="куда ведём на сайте (не нужен с --from-history)")
    ap.add_argument("--where", default="", help="уточнение, где именно на странице")
    ap.add_argument(
        "--from-history",
        action="store_true",
        help="дашборд уже закрыт заглушкой руками: достать из истории версий последнюю "
             "версию с данными, положить её в архив и проставить тег «закрыт»",
    )
    ap.add_argument(
        "--restub",
        action="store_true",
        help="только переписать текст уже стоящей заглушки (без архивирования)",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    got = api(f"/api/dashboards/uid/{args.uid}")
    dash, meta = got["dashboard"], got["meta"]
    title = dash["title"]

    if args.from_history:
        archive_from_history(args, dash, meta, title)
        return

    if not args.url:
        sys.exit("нужен --url: куда ведём на сайте")
    closed = CLOSED_TAG in (dash.get("tags") or [])
    if closed and not args.restub:
        sys.exit(f"«{title}» уже закрыт — выходим, чтобы не затереть архивом заглушку "
                 f"(переписать текст: --restub)")
    if args.restub and not closed:
        sys.exit(f"«{title}» ещё не закрыт — --restub нечего переписывать")

    print(f"дашборд:  {title} ({args.uid}), версия {dash.get('version')}, панелей {len(dash.get('panels', []))}")
    print(f"ведём на: {args.url}")

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup = BACKUP_DIR / f"{args.uid}.before-close.json"
    if not args.dry_run and not args.restub:
        backup.write_text(json.dumps(dash, ensure_ascii=False, indent=2))
    print(f"копия на диск: {backup}")

    today = dt.date.today().strftime("%d.%m.%Y")
    archived = dict(dash)
    archived.pop("id", None)
    archived.pop("uid", None)
    archived["title"] = f"{title} (архив {today})"
    archived["tags"] = sorted(set(dash.get("tags") or []) | {"архив"})
    if not args.dry_run and not args.restub:
        res = api(
            "/api/dashboards/db",
            {
                "dashboard": archived,
                "folderUid": archive_folder_uid(),
                "message": f"Архивная копия перед закрытием {today}",
                "overwrite": False,
            },
        )
        print(f"архив: {ORIGIN}{res['url']}")

    stub = {
        "uid": args.uid,
        "title": title,
        "tags": sorted(set(dash.get("tags") or []) | {CLOSED_TAG}),
        "timezone": dash.get("timezone", "browser"),
        "schemaVersion": dash.get("schemaVersion", 39),
        "version": dash.get("version"),
        "editable": True,
        "panels": [stub_panel(title, args.url, args.where)],
        "links": [
            {
                "title": f"Открыть на run5k.run",
                "type": "link",
                "url": args.url,
                "targetBlank": True,
                "icon": "external link",
                "tooltip": "",
                "tags": [],
                "asDropdown": False,
                "includeVars": False,
                "keepTime": False,
            }
        ],
    }
    if args.dry_run:
        print("--- dry-run, заглушка не записана ---")
        print(stub["panels"][0]["options"]["content"])
        return
    res = api(
        "/api/dashboards/db",
        {"dashboard": stub, "folderUid": meta.get("folderUid", ""), "message": f"Закрыт: переезд на {args.url}", "overwrite": True},
    )
    print(f"заглушка: {ORIGIN}{res['url']}")


if __name__ == "__main__":
    main()
