from __future__ import annotations

import re
import unicodedata

from app.migration.helpers import normalize_role_key

# Lookup keys are ASCII-ish (Serbian latin folded, punctuation -> underscore).
# Values are canonical Russian labels used across s95.ru / s95.rs / protocols.
_S95_VOLUNTEER_ROLE_ALIASES: dict[str, str] = {
    # Warm-up / briefing
    "zagrevanje": "Проведение разминки",
    "provodenie_razminki": "Проведение разминки",
    "provedenie_razminki": "Проведение разминки",
    "razminka": "Проведение разминки",
    "warm_up": "Проведение разминки",
    "warm_up_leader": "Проведение разминки",
    # Photo
    "fotograf": "Фотограф",
    "photographer": "Фотограф",
    # Marshal
    "marsal": "Маршал",
    "marshal": "Маршал",
    # Timing
    "hronometar": "Хронометраж",
    "hronometarist": "Хронометраж",
    "hronometraz": "Хронометраж",
    "hronometrist": "Хронометраж",
    "merac_vremena": "Хронометраж",
    "merac_vremena_trke": "Хронометраж",
    "timekeeper": "Хронометраж",
    # Barcode
    "barkod": "Сканирование штрих-кодов",
    "barkod_skener": "Сканирование штрих-кодов",
    "skener_barkoda": "Сканирование штрих-кодов",
    "barcode": "Сканирование штрих-кодов",
    "barcode_scanner": "Сканирование штрих-кодов",
    "skener_shtrih_kodova": "Сканирование штрих-кодов",
    "skanirovanie_shtrih_kodov": "Сканирование штрих-кодов",
    # Tail walker / closer
    "zatvarac": "Замыкающий",
    "poslednji": "Замыкающий",
    "poslednji_trkac": "Замыкающий",
    "poslednji_trkač": "Замыкающий",
    "tail_walker": "Замыкающий",
    # Course marking
    "obilazak_staze": "Разметка трассы",
    "obelezavanje_staze": "Разметка трассы",
    "obeležavanje_staze": "Разметка трассы",
    "marking_maker": "Разметка трассы",
    "course_marking": "Разметка трассы",
    "razmetka_trassy": "Разметка трассы",
    # Directors / leads
    "direktor_trke": "Директор забега",
    "direktor_trka": "Директор забега",
    "direktor_trcanja": "Директор забега",
    "run_director": "Директор забега",
    "direktor_zabega": "Директор забега",
    "organizator": "Организатор",
    "organizer": "Организатор",
    "koordinator_volontera": "Координатор волонтёров",
    "koordinator_volonterа": "Координатор волонтёров",
    "volunteer_coordinator": "Координатор волонтёров",
    # First-timers / briefing
    "informisanje_novih_ucesnika": "Инструктаж новичков",
    "informisanje_novih_učesnika": "Инструктаж новичков",
    "informisanje_novih": "Инструктаж новичков",
    "first_timers_briefing": "Инструктаж новичков",
    "instruktaz_novichkov": "Инструктаж новичков",
    "instruktaz": "Инструктаж новичков",
    # Setup / equipment / numbers
    "priprema_pre_trke": "Подготовка до старта",
    "pre_event_setup": "Подготовка до старта",
    "podgotovka_do_starta": "Подготовка до старта",
    "oprema": "Оборудование",
    "equipment": "Оборудование",
    "oborudovanie": "Оборудование",
    "brojevi": "Выдача номеров",
    "startni_brojevi": "Выдача номеров",
    "vidacha_nomerov": "Выдача номеров",
    # Walk leader
    "vodja_grupe": "Ведущий группы",
    "vođa_grupe": "Ведущий группы",
    "parkwalker": "Ведущий группы",
    "setac": "Ведущий группы",
    "šetač": "Ведущий группы",
    # Generic volunteer
    "volonter": "Волонтёр",
    "volonterstvo": "Волонтёр",
    "volunteer": "Волонтёр",
    "volonter_generic": "Волонтёр",
}

_SERBIAN_LATIN_FOLDS = str.maketrans(
    {
        "š": "s",
        "Š": "s",
        "č": "c",
        "Č": "c",
        "ć": "c",
        "Ć": "c",
        "ž": "z",
        "Ž": "z",
        "đ": "dj",
        "Đ": "dj",
    }
)

_CANONICAL_ROLE_LOOKUP: dict[str, str] | None = None


def _canonical_lookup() -> dict[str, str]:
    global _CANONICAL_ROLE_LOOKUP
    if _CANONICAL_ROLE_LOOKUP is None:
        lookup: dict[str, str] = {}
        for alias_key, canonical in _S95_VOLUNTEER_ROLE_ALIASES.items():
            lookup[alias_key] = canonical
            lookup[_role_lookup_key(canonical)] = canonical
        _CANONICAL_ROLE_LOOKUP = lookup
    return _CANONICAL_ROLE_LOOKUP


def _fold_latin(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    return ascii_only.translate(_SERBIAN_LATIN_FOLDS)


def _role_lookup_key(role: str) -> str:
    cleaned = role.strip()
    if not cleaned:
        return "volunteer"
    if _contains_cyrillic(cleaned):
        return re.sub(r"[^\w]+", "_", cleaned.lower(), flags=re.UNICODE).strip("_") or "volunteer"
    folded = _fold_latin(cleaned).lower()
    return re.sub(r"[^\w]+", "_", folded, flags=re.UNICODE).strip("_") or "volunteer"


def _contains_cyrillic(value: str) -> bool:
    return any("\u0400" <= char <= "\u04FF" for char in value)


def canonical_s95_volunteer_role(role: str | None) -> str | None:
    if role is None:
        return None
    cleaned = role.strip()
    if not cleaned:
        return None

    lookup = _canonical_lookup()
    direct = lookup.get(_role_lookup_key(cleaned))
    if direct is not None:
        return direct

    # Already canonical Russian (or unknown custom role) — keep as-is.
    return cleaned


def s95_volunteer_role_key(role: str | None) -> str:
    canonical = canonical_s95_volunteer_role(role)
    if not canonical:
        return "volunteer"
    return normalize_role_key(canonical)


def prefer_s95_volunteer_role(existing: str | None, incoming: str | None) -> str | None:
    existing_canonical = canonical_s95_volunteer_role(existing)
    incoming_canonical = canonical_s95_volunteer_role(incoming)
    if existing_canonical is None:
        return incoming_canonical
    if incoming_canonical is None:
        return existing_canonical
    if s95_volunteer_role_key(existing_canonical) != s95_volunteer_role_key(incoming_canonical):
        return incoming_canonical if _contains_cyrillic(incoming_canonical) else existing_canonical
    if _contains_cyrillic(incoming_canonical) and not _contains_cyrillic(existing_canonical):
        return incoming_canonical
    if _contains_cyrillic(existing_canonical) and not _contains_cyrillic(incoming_canonical):
        return existing_canonical
    return incoming_canonical if len(incoming_canonical) >= len(existing_canonical) else existing_canonical
