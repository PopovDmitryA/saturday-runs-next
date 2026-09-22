#!/usr/bin/env python3
"""Export Grafana dashboards to data/grafana/ and build frontend catalog JSON."""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "grafana" / "dashboards"
CATALOG_PATH = ROOT / "frontend" / "src" / "data" / "grafana" / "catalog.json"
GRAFANA_ORIGIN = "https://grafana.run5k.run"

CATEGORY_RULES: tuple[tuple[str, str], ...] = (
    ("maps", "карт"),
    ("ratings", "рейтинг"),
    ("journals", "журнал"),
    ("records", "рекорд"),
    ("stats", "статистик"),
    ("stats", "свод"),
    ("stats", "клуб"),
    ("stats", "календар"),
    ("stats", "прогноз"),
    ("stats", "протокол"),
    ("stats", "пауз"),
    ("challenges", "челлендж"),
    ("main", "главн"),
)

CATEGORY_LABELS = {
    "main": "Обзор",
    "maps": "Карты",
    "ratings": "Рейтинги",
    "journals": "Журналы",
    "records": "Рекорды",
    "stats": "Статистика и отчёты",
    "challenges": "Челленджи",
    "other": "Прочее",
}


def slugify(title: str, uri: str) -> str:
    if uri.startswith("db/"):
        return uri.removeprefix("db/")
    cleaned = re.sub(r"[^\w\s-]", "", title.lower(), flags=re.UNICODE)
    cleaned = re.sub(r"[\s_]+", "-", cleaned.strip())
    return cleaned or "dashboard"


def categorize(title: str) -> str:
    lowered = title.lower()
    for category, needle in CATEGORY_RULES:
        if needle in lowered:
            return category
    return "other"


def fetch_json(url: str) -> object:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def iter_panels(panels: list[dict]) -> list[dict]:
    items: list[dict] = []
    for panel in panels:
        if panel.get("type") == "row":
            items.extend(iter_panels(panel.get("panels") or []))
            continue
        panel_type = str(panel.get("type") or "unknown")
        title = str(panel.get("title") or "").strip()
        description = str(panel.get("description") or "").strip()
        if panel_type == "text" and not title and not description:
            options = panel.get("options") or {}
            content = str(options.get("content") or "").strip()
            if content:
                plain = re.sub(r"<[^>]+>", " ", content)
                plain = re.sub(r"\s+", " ", plain).strip()
                if plain:
                    description = plain[:280]
        if panel_type == "text" and not title and not description:
            continue
        items.append(
            {
                "title": title or panel_type,
                "type": panel_type,
                "description": description,
            }
        )
    return items


def extract_variables(dashboard: dict) -> list[dict]:
    variables: list[dict] = []
    for item in dashboard.get("templating", {}).get("list") or []:
        if item.get("hide") == 2:
            continue
        name = str(item.get("name") or "").strip()
        label = str(item.get("label") or name).strip()
        if not name:
            continue
        variables.append(
            {
                "name": name,
                "label": label,
                "type": str(item.get("type") or ""),
            }
        )
    return variables


def main() -> int:
    search_url = f"{GRAFANA_ORIGIN}/api/search?type=dash-db"
    try:
        search_items = fetch_json(search_url)
    except urllib.error.URLError as exc:
        print(f"export_grafana_dashboards: failed to fetch list: {exc}", file=sys.stderr)
        return 1

    if not isinstance(search_items, list):
        print("export_grafana_dashboards: unexpected search response", file=sys.stderr)
        return 1

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    catalog_entries: list[dict] = []
    for item in search_items:
        uid = str(item.get("uid") or "")
        title = str(item.get("title") or uid)
        if not uid:
            continue
        detail_url = f"{GRAFANA_ORIGIN}/api/dashboards/uid/{uid}"
        try:
            detail = fetch_json(detail_url)
        except urllib.error.URLError as exc:
            print(f"warn: skip {uid} ({title}): {exc}", file=sys.stderr)
            continue
        if not isinstance(detail, dict) or "dashboard" not in detail:
            continue
        dashboard = detail["dashboard"]
        slug = slugify(title, str(item.get("uri") or ""))
        raw_path = RAW_DIR / f"{uid}.json"
        raw_path.write_text(json.dumps(detail, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        panels = iter_panels(dashboard.get("panels") or [])
        variables = extract_variables(dashboard)
        panel_types = sorted({panel["type"] for panel in panels})
        entry = {
            "uid": uid,
            "slug": slug,
            "title": title,
            "description": str(dashboard.get("description") or "").strip(),
            "category": categorize(title),
            "categoryLabel": CATEGORY_LABELS[categorize(title)],
            "grafanaUrl": f"{GRAFANA_ORIGIN}{item.get('url') or f'/d/{uid}'}",
            "updatedAt": str((detail.get("meta") or {}).get("updated") or ""),
            "panelCount": len(panels),
            "panelTypes": panel_types,
            "panels": panels,
            "variables": variables,
        }
        catalog_entries.append(entry)
        print(f"exported {title} ({uid}) -> {raw_path.name}")

    catalog_entries.sort(key=lambda row: (row["category"], row["title"].lower()))
    catalog = {
        "generatedAt": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "grafanaOrigin": GRAFANA_ORIGIN,
        "categories": [
            {"id": key, "label": label}
            for key, label in CATEGORY_LABELS.items()
        ],
        "dashboards": catalog_entries,
    }
    CATALOG_PATH.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"catalog: {len(catalog_entries)} dashboards -> {CATALOG_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
