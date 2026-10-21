"""Где следить за отменами стартов: локации, города, регионы, страны.

Отмены по всей стране приходят и тем, кто дальше своего города не ездит, —
на это жаловались. Человек набирает в поле название, выбирает подсказку, и
она встаёт в строку чипом; так можно набрать сколько угодно мест и убрать
любое. Место — это:

  * конкретная локация (ключ идентичности каталога, как у домашней);
  * город, регион или страна целиком — все локации, у которых в каталоге
    записано это место, включая открывшиеся после выбора.

Ни одного места — вся страна (умолчание, как было до миграции 113). Фильтр
мест работает вместе с фильтром систем: отмена должна пройти оба.

Подсказки — из того же каталога в Redis, что и поиск по сайту
(site_search_service), с теми же сокращениями («спб», «мск», «Подмосковье»).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select, tuple_
from sqlalchemy.orm import Session

from app.models import Location, Platform, UserCancellationPlace
from app.services import site_search_service as search
from app.services.platform_titles import platform_title

PLACE_KINDS = ("city", "region", "country")
MAX_PLACES = 30
SUGGEST_PLACE_LIMIT = 4
SUGGEST_LOCATION_LIMIT = 6
MIN_QUERY_LENGTH = 2


class PlacesError(ValueError):
    """Ошибка выбора мест — уходит человеку текстом 400."""


def place_key(kind: str, value: str) -> str:
    return f"{kind}:{search._fold(value).strip()}"


def place_kind(key: str) -> str:
    """Вид места по ключу: всё, что не город/регион/страна, — локация."""
    prefix = key.split(":", 1)[0]
    return prefix if prefix in PLACE_KINDS else "location"


def _valid_key(key: str) -> bool:
    prefix, _sep, rest = key.partition(":")
    return bool(rest.strip()) and prefix in (*PLACE_KINDS, "catalog", "location") and len(key) <= 160


def _place_label(kind: str, value: str) -> str:
    return search._region_label(value) if kind == "region" else value


# ---------------------------------------------------------------------------
# Каталог мест
# ---------------------------------------------------------------------------


@dataclass
class _Place:
    key: str
    kind: str
    label: str
    tokens: list[str]
    folded: str
    count: int
    # Подсказка под названием: для города — регион или страна.
    hint: str | None
    # Регион, который одновременно город (Москва, Санкт-Петербург).
    city_like: bool = False


_places_memo: tuple[list[search._LocationDoc], list[_Place]] | None = None


def _places(docs: list[search._LocationDoc]) -> list[_Place]:
    """Города, регионы и страны каталога с числом локаций в каждом.

    Серии не считаем: у них нет постоянного места, и «Москва (45)» с сериями
    обещала бы больше локаций, чем человек увидит в каталоге.
    """
    global _places_memo
    memo = _places_memo
    if memo is not None and memo[0] is docs:
        return memo[1]
    found: dict[str, _Place] = {}
    for doc in docs:
        entry = doc.entry
        if entry.get("is_series"):
            continue
        for kind in PLACE_KINDS:
            value = str(entry.get(kind) or "").strip()
            if not value:
                continue
            key = place_key(kind, value)
            place = found.get(key)
            if place is None:
                label = _place_label(kind, value)
                hint = None
                if kind == "city":
                    region = str(entry.get("region") or "").strip()
                    country = str(entry.get("country") or "").strip()
                    hint = _place_label("region", region) if region and region != value else (country or None)
                elif kind == "region":
                    hint = str(entry.get("country") or "").strip() or None
                place = _Place(
                    key=key,
                    kind=kind,
                    label=label,
                    tokens=search._tokens(label),
                    folded=search._fold(value),
                    count=0,
                    hint=hint,
                )
                found[key] = place
            place.count += 1
    # Москва и Петербург записаны в каталоге и городом, и регионом. Две
    # одинаковые подсказки «Москва» сбивают с толку, а регион шире города
    # (в нём и Зеленоград), поэтому остаётся он — с подписью «город».
    regions = {place.folded for place in found.values() if place.kind == "region"}
    cities = {place.folded for place in found.values() if place.kind == "city"}
    places = []
    for place in found.values():
        if place.kind == "city" and place.folded in regions:
            continue
        if place.kind == "region" and place.folded in cities:
            place.city_like = True
        places.append(place)
    _places_memo = (docs, places)
    return places


def _place_match(words: list[str], place: _Place) -> int:
    """3 — место целиком («москва», «спб»), 2 — каждое слово начинает слово
    названия («моск» → Москва, Московская область), 0 — мимо."""
    joined = " ".join(words)
    alias = search._PLACE_ALIASES.get(joined, joined)
    if alias == place.folded or joined == " ".join(place.tokens):
        return 3
    if all(any(token.startswith(word) for token in place.tokens) for word in words):
        return 2
    return 0


def _locations_word(count: int) -> str:
    tail = count % 100
    if 11 <= tail <= 14:
        return "локаций"
    if count % 10 == 1:
        return "локация"
    if 2 <= count % 10 <= 4:
        return "локации"
    return "локаций"


_KIND_TITLES = {"city": "город", "region": "регион", "country": "страна"}


def suggest(db: Session, query_text: str) -> list[dict[str, Any]]:
    """Подсказки для поля: сначала места целиком, потом локации.

    Места — первыми: кто набрал «Москва», чаще хочет весь город, а отдельные
    парки найдёт, допечатав название.
    """
    if len(query_text.strip()) < MIN_QUERY_LENGTH:
        return []
    docs = search._location_docs(db)
    if not docs:
        return []
    words = search._significant_location_words(query_text)
    if not words:
        return []

    ranked: list[tuple[tuple[int, int, str], _Place]] = []
    for place in _places(docs):
        level = _place_match(words, place)
        if level:
            ranked.append(((-level, -place.count, place.label), place))
    ranked.sort(key=lambda item: item[0])
    items: list[dict[str, Any]] = []
    for _rank, place in ranked[:SUGGEST_PLACE_LIMIT]:
        title = _KIND_TITLES["city" if place.city_like else place.kind]
        hint = f"{title} · {place.count} {_locations_word(place.count)}"
        if place.hint:
            hint = f"{hint} · {place.hint}"
        kind = "city" if place.city_like else place.kind
        items.append({"key": place.key, "kind": kind, "label": place.label, "hint": hint})

    by_slug = {str(doc.entry.get("slug") or ""): doc.entry for doc in docs}
    page = search.search_locations_page(db, query_text, docs)
    for found in page["locations"]:
        entry = by_slug.get(found["slug"])
        key = str((entry or {}).get("identity_key") or "")
        if not entry or not key or entry.get("is_series"):
            continue
        hint_parts = [str(entry.get("city") or "").strip()]
        hint_parts.append(", ".join(platform_title(code) for code in entry.get("platform_codes") or []))
        items.append(
            {
                "key": key,
                "kind": "location",
                "label": str(entry.get("name") or found["name"]),
                "hint": " · ".join(part for part in hint_parts if part) or None,
            }
        )
        if sum(1 for item in items if item["kind"] == "location") >= SUGGEST_LOCATION_LIMIT:
            break
    return items


def _catalog_labels(db: Session) -> dict[str, str]:
    """Ключ → подпись для всего, что можно выбрать прямо сейчас."""
    docs = search._location_docs(db)
    labels = {place.key: place.label for place in _places(docs)}
    for doc in docs:
        key = str(doc.entry.get("identity_key") or "")
        if key:
            labels.setdefault(key, str(doc.entry.get("name") or ""))
    return labels


# ---------------------------------------------------------------------------
# Выбор человека
# ---------------------------------------------------------------------------


def user_places(db: Session, user_id: UUID) -> list[dict[str, str]]:
    rows = db.execute(
        select(UserCancellationPlace.place_key, UserCancellationPlace.label)
        .where(UserCancellationPlace.user_id == user_id)
        .order_by(UserCancellationPlace.created_at, UserCancellationPlace.label)
    ).all()
    city_like = {place.key for place in _places(search._location_docs(db)) if place.city_like} if rows else set()
    return [
        {"key": key, "kind": "city" if key in city_like else place_kind(key), "label": label} for key, label in rows
    ]


def places_by_user(db: Session, user_ids: list[UUID]) -> dict[UUID, set[str]]:
    """Выбор всех получателей одним запросом. Нет в словаре — вся страна."""
    if not user_ids:
        return {}
    chosen: dict[UUID, set[str]] = defaultdict(set)
    rows = db.execute(
        select(UserCancellationPlace.user_id, UserCancellationPlace.place_key).where(
            UserCancellationPlace.user_id.in_(user_ids)
        )
    ).all()
    for user_id, key in rows:
        chosen[user_id].add(key)
    return dict(chosen)


def set_user_places(db: Session, user_id: UUID, keys: list[str]) -> None:
    """Заменить выбор целиком. Порядок ключей — порядок чипов в строке."""
    ordered = list(dict.fromkeys(key.strip() for key in keys if key and key.strip()))
    if len(ordered) > MAX_PLACES:
        raise PlacesError(f"Можно выбрать не больше {MAX_PLACES} мест")
    bad = [key for key in ordered if not _valid_key(key)]
    if bad:
        raise PlacesError("Неизвестное место")

    existing = {
        key: label
        for key, label in db.execute(
            select(UserCancellationPlace.place_key, UserCancellationPlace.label).where(
                UserCancellationPlace.user_id == user_id
            )
        ).all()
    }
    new_keys = [key for key in ordered if key not in existing]
    labels: dict[str, str] = {}
    if new_keys:
        catalog = _catalog_labels(db)
        if not catalog:
            raise PlacesError("Каталог локаций обновляется — попробуйте через минуту")
        unknown = [key for key in new_keys if key not in catalog]
        if unknown:
            raise PlacesError("Неизвестное место")
        labels = {key: catalog[key] for key in new_keys}

    keep = set(ordered)
    removed = [key for key in existing if key not in keep]
    if removed:
        db.execute(
            delete(UserCancellationPlace).where(
                UserCancellationPlace.user_id == user_id, UserCancellationPlace.place_key.in_(removed)
            )
        )
    for key in new_keys:
        db.add(UserCancellationPlace(user_id=user_id, place_key=key, label=labels[key][:256]))
    db.flush()


# ---------------------------------------------------------------------------
# Куда относится отменённая локация
# ---------------------------------------------------------------------------


def location_place_keys(db: Session, pairs: list[tuple[str, str]]) -> dict[tuple[str, str], frozenset[str]]:
    """(код системы, external_key) → все ключи, под которые попадает локация:
    её идентичность в каталоге, город, регион и страна.

    Место берём и из строки локации, и из записи каталога: у площадки в
    нескольких системах город мог быть записан только в одной из них, а
    человек выбирал по тому, что видел в подсказке, то есть по каталогу.
    """
    from app.services.location_catalog_service import catalog_ids_for_locations

    pairs = list(dict.fromkeys(pairs))
    if not pairs:
        return {}
    rows = db.execute(
        select(Location.id, Platform.code, Location.external_key, Location.city, Location.region, Location.country)
        .join(Platform, Platform.id == Location.platform_id)
        .where(tuple_(Platform.code, Location.external_key).in_(pairs))
    ).all()
    catalog_ids = catalog_ids_for_locations(db, {row[0]: (row[1], row[2]) for row in rows})
    entries = {str(doc.entry.get("identity_key") or ""): doc.entry for doc in search._location_docs(db)}

    result: dict[tuple[str, str], frozenset[str]] = {}
    for location_id, code, external_key, city, region, country in rows:
        catalog_id = catalog_ids.get(location_id)
        identity = f"catalog:{catalog_id}" if catalog_id is not None else f"location:{location_id}"
        keys = {identity}
        entry = entries.get(identity) or {}
        for kind, own in (("city", city), ("region", region), ("country", country)):
            for value in (own, entry.get(kind)):
                if value and str(value).strip():
                    keys.add(place_key(kind, str(value)))
        result[(code, external_key)] = frozenset(keys)
    return result


def place_allowed(chosen: set[str] | None, keys: frozenset[str] | None) -> bool:
    """Ничего не выбрано — вся страна. Локацию не нашли в базе — пропускаем
    её только тем, кто мест не выбирал: молчать безопаснее, чем слать чужое."""
    if not chosen:
        return True
    return bool(keys) and not chosen.isdisjoint(keys or ())
