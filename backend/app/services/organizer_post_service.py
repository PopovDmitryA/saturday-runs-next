"""Шаблоны постов кабинета организатора.

Набор собран по анализу телеграм-каналов пяти локаций (Мещерский, Коломенское,
Пестовский, Петергоф, Филатов Луг; выгрузки 17.08.2026): у каждой оргкоманды
свой регулярный пост — кто-то публикует полный отчёт, кто-то список волонтёров
с ролями, кто-то отдельно приветствует новичков или поздравляет юбиляров.
Шаблоны закрывают все замеченные форматы; данные — те же расчёты, что свод
(build_event_svod) и отчёт (build_event_report), ничего не считается заново.

Тексты нарочно «болванки»: оргкоманда добавит свою интонацию, а цифры и списки
имён — самая трудоёмкая часть — уже готовы.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.services.admin_event_report_service import (
    SITE_POST_SIGNATURE,
    build_event_report,
    build_event_svod,
    fmt_date_ru,
    ru_plural,
)
from app.volunteer_role_taxonomy import canonical_volunteer_role

# Ключ → (название для селекта, короткое описание для подсказки).
POST_TEMPLATES: dict[str, tuple[str, str]] = {
    "full": (
        "Сводный пост",
        "Всё сразу: цифры старта, топ финишей, новички, рекорды, юбилеи и клубы.",
    ),
    "stats": (
        "Герои старта",
        "Кого отметить после субботы: личники, новички, гости и юбилеи.",
    ),
    "volunteers": (
        "Спасибо волонтёрам",
        "Состав команды от ролей: эмодзи роли, роль — имена (формат Мещерского).",
    ),
    "newcomers": (
        "Привет новичкам",
        "Первый финиш, первое волонтёрство и гости локации — поимённо.",
    ),
    "milestones": (
        "Юбилеи дня",
        "Юбилейные финиши и волонтёрства события — по уровням.",
    ),
    "upcoming": (
        "Юбилеи завтра",
        "Пятничная рубрика: у кого юбилей случится на ближайшем старте.",
    ),
    "vacancies": (
        "Нужны волонтёры",
        "Живой призыв по таблице записи 5 вёрст: кто нужен на ближайший старт.",
    ),
    "travelers": (
        "Наши в гостях",
        "Кто из постоянных участников бегал в эту дату в других парках.",
    ),
}

# Эмодзи-классификатор роли (формат Мещерского: «1. ⏱️ Секундомер — Имя»).
# Ключи — канонические роли volunteer_role_taxonomy; незнакомым — 🙌.
ROLE_EMOJI: dict[str, str] = {
    "run_director": "🪇",
    "volunteer_coordinator": "🤝",
    "pre_event_setup": "🛠",
    "course_setup": "🚧",
    "course_check": "🔍",
    "first_timers": "🗺",
    "pre_run_briefing": "📢",
    "warm_up": "🤸",
    "host": "🎤",
    "marshal": "🦺",
    "pacer": "🐇",
    "lead_bike": "🚴",
    "vi_guide": "🧑‍🤝‍🧑",
    "walk_leader": "🚶",
    "sign_language": "🤟",
    "parking": "🅿️",
    "timekeeper": "⏱️",
    "tail_walker": "🐢",
    "funnel_manager": "🏁",
    "finish_tokens": "🏷",
    "finish_token_support": "🏷",
    "barcode_scanning": "📲",
    "number_checker": "🔢",
    "token_sorting": "🗂",
    "bib_numbers": "🔖",
    "results_processor": "💻",
    "course_takedown": "🧹",
    "post_event": "🧺",
    "equipment": "📦",
    "refreshments": "☕",
    "photographer": "📸",
    "videographer": "🎥",
    "designer": "🎨",
    "communications": "📣",
    "report_writer": "✍️",
}


def _role_emoji(label: str) -> str:
    canonical = canonical_volunteer_role(label)
    if canonical is None:
        return "🙌"
    return ROLE_EMOJI.get(canonical.key, "🙌")


def _names(rows: list[dict[str, Any]]) -> str:
    return ", ".join(str(row.get("name") or "Неизвестный участник") for row in rows)


def _event_header(event: dict[str, Any], title: str) -> list[str]:
    number = event.get("event_number")
    number_part = f" — забег №{number}" if number else ""
    return [
        f"{title}",
        f"📍 {event['location_name']}{number_part} · {fmt_date_ru(event['event_date'])}",
    ]


def _stats_post(svod: dict[str, Any], guest_homes: list[dict[str, Any]] | None = None) -> str:
    event = svod["event"]
    runners = svod["runners"]

    pbs = [r for r in runners if r["is_pb"] and not r["first_in_system"]]
    newcomers = [r for r in runners if r["first_in_system"]]
    jubilees = [r for r in runners if r["location_milestone"]]

    lines = _event_header(event, "🏅 Герои старта")
    lines.append("")
    lines.append(f"🏃 Финишёров: {event['finishers_count']}")
    lines.append(f"🤝 Волонтёров: {event['volunteers_count']}")
    if pbs:
        lines.append("")
        lines.append(
            f"🚀 Личные рекорды обновили ({len(pbs)}): {_names(pbs)}. "
            "Гордимся каждым новым максимумом!"
        )
    if newcomers:
        lines.append("")
        lines.append(
            f"👑 Первый раз на старте ({len(newcomers)}): {_names(newcomers)}. "
            "Добро пожаловать в беговую семью!"
        )
    # «Откуда гости» (просьба Дмитрия 24.08.2026): гость — финишёр, чья
    # домашняя локация другая (по общесайтовой логике дома), и гостить он
    # может не в первый раз. Пишем, откуда приехал.
    if guest_homes:
        lines.append("")
        lines.append(f"🧳 Гости локации ({len(guest_homes)}):")
        for guest in guest_homes:
            home = guest["home_name"]
            if guest.get("home_city"):
                home += f" ({guest['home_city']})"
            lines.append(f"• {guest['name']} — {home}")
    if jubilees:
        lines.append("")
        jubilee_parts = [
            f"{row['name']} — {row['location_milestone']}-й финиш здесь" for row in jubilees
        ]
        lines.append("🎂 Юбилеи: " + "; ".join(jubilee_parts) + ".")
    lines.append("")
    lines.append(SITE_POST_SIGNATURE)
    return "\n".join(lines)


def _volunteers_post(svod: dict[str, Any]) -> str:
    """Формат Мещерского (выбор Дмитрия 17.08.2026): идём от роли, в начале
    строки — эмодзи-классификатор роли, дальше имена. Человек с несколькими
    ролями появляется в каждой своей роли."""
    event = svod["event"]
    volunteers = svod["volunteers"]

    by_role: dict[str, list[str]] = {}
    for row in volunteers:
        name = str(row.get("name") or "Неизвестный участник")
        roles = row["roles"] or [{"label": "Волонтёр"}]
        for role in roles:
            by_role.setdefault(role["label"], []).append(name)

    def _role_order(item: tuple[str, list[str]]) -> tuple[int, int, str]:
        label, names = item
        canonical = canonical_volunteer_role(label)
        is_director = canonical is not None and canonical.key == "run_director"
        # Директор — первым, дальше роли с большими командами.
        return (0 if is_director else 1, -len(names), label)

    lines = _event_header(event, "🤝 Команда волонтёров")
    lines.append("")
    if not by_role:
        lines.append("Список волонтёров этого события ещё не загружен.")
    for index, (label, names) in enumerate(sorted(by_role.items(), key=_role_order), start=1):
        lines.append(f"{index}. {_role_emoji(label)} {label} — {', '.join(names)}")
    count = len(volunteers)
    lines.append("")
    lines.append(
        f"Всего {count} {ru_plural(count, ('волонтёр', 'волонтёра', 'волонтёров'))}. "
        "Без вас не бывает ни одного старта — спасибо!"
    )
    lines.append("")
    lines.append(SITE_POST_SIGNATURE)
    return "\n".join(lines)


def _newcomers_post(svod: dict[str, Any]) -> str:
    event = svod["event"]
    runners = svod["runners"]
    volunteers = svod["volunteers"]

    first_runs = [r for r in runners if r["first_in_system"]]
    guests = [r for r in runners if r["first_at_location"] and not r["first_in_system"]]
    first_vols = [v for v in volunteers if v["first_volunteering"]]

    lines = _event_header(event, "👑 Новые лица")
    if first_runs:
        lines.append("")
        lines.append("🏃 Первый финиш:")
        for row in first_runs:
            lines.append(f"• {row.get('name') or 'Неизвестный участник'}")
    if first_vols:
        lines.append("")
        lines.append("🙋 Первое волонтёрство:")
        for row in first_vols:
            lines.append(f"• {row.get('name') or 'Неизвестный участник'}")
    if guests:
        lines.append("")
        lines.append("🧳 Впервые на нашей локации:")
        for row in guests:
            lines.append(f"• {row.get('name') or 'Неизвестный участник'}")
    if not (first_runs or first_vols or guests):
        lines.append("")
        lines.append("На этом старте новых лиц не было — все свои!")
    else:
        lines.append("")
        lines.append("Добро пожаловать — ждём вас снова каждую субботу!")
    lines.append("")
    lines.append(SITE_POST_SIGNATURE)
    return "\n".join(lines)


# Ключевые позиции, без которых старт не состоится, — запасной список для
# локаций без страницы записи (не-5в или сеть недоступна). Состав сверен с
# тем, что реально запрашивают парки в постах «нужны волонтёры» и что стоит
# на страницах записи 5 вёрст (решение Дмитрия 17.08.2026: факультативные
# роли в призыв не попадают).
KEY_VACANCY_ROLE_KEYS: tuple[str, ...] = (
    "run_director",
    "timekeeper",
    "barcode_scanning",
    "finish_tokens",
    "tail_walker",
    "marshal",
    "pre_event_setup",
    "warm_up",
    "photographer",
)


def build_vacancies_post(db: Session, identity: Any) -> str:
    """«Нужны волонтёры» на ближайший старт.

    Основной путь — живая таблица записи 5 вёрст (fetch_volunteer_roster):
    список ролей там и есть ключевые позиции локации, пустая клетка ближайшей
    даты = вакансия, заполненная — «уже в строю». Запасной путь (не-5в или сеть
    недоступна) — ключевые канонические роли списком под ручную правку.
    """
    from app.services.organizer_roster_service import fetch_volunteer_roster, roster_url
    from app.volunteer_role_taxonomy import CANONICAL_ROLE_LABELS

    five_verst_slug = None
    for location, code in identity.locations:
        if code == "five_verst":
            five_verst_slug = location.external_key.strip().lower()
            break

    roster = fetch_volunteer_roster(five_verst_slug) if five_verst_slug else None

    if roster and roster.get("dates"):
        target_date = roster["dates"][0]
        needed = [row["role"] for row in roster["roles"] if target_date not in row["filled"]]
        filled = [
            (row["role"], row["filled"][target_date])
            for row in roster["roles"]
            if target_date in row["filled"]
        ]
        lines = [
            f"🙌 Нужны волонтёры на {target_date}!",
            f"📍 {identity.name}",
            "",
        ]
        if needed:
            lines.append("Старт не состоится без вас — свободные позиции:")
            for role in needed:
                lines.append(f"❗️ {_role_emoji(role)} {role}")
        else:
            lines.append("Все позиции ближайшего старта заняты — команда в сборе! 💪")
        if filled:
            lines.append("")
            lines.append("Уже в строю:")
            for role, name in filled:
                lines.append(f"✅ {_role_emoji(role)} {role} — {name}")
        lines.append("")
        lines.append(f"✍️ Запись: {roster['source_url']}")
        lines.append("💬 «5 раз побегал — 1 раз помоги!»")
        lines.append("")
        lines.append(SITE_POST_SIGNATURE)
        return "\n".join(lines)

    # Запасной путь: ключевые роли без живой записи.
    lines = [
        "🙌 Нужны волонтёры!",
        f"📍 {identity.name}",
        "",
        "Старт не состоится без ключевых позиций:",
    ]
    for key in KEY_VACANCY_ROLE_KEYS:
        label = CANONICAL_ROLE_LABELS[key]
        lines.append(f"❗️ {ROLE_EMOJI.get(key, '🙌')} {label}")
    lines.append("")
    lines.append("💬 «5 раз побегал — 1 раз помоги!»")
    lines.append(
        "Напишите в чат локации или подойдите к оргкоманде на старте — научим любой роли."
    )
    if five_verst_slug:
        lines.append("")
        lines.append(f"✍️ Запись: {roster_url(five_verst_slug)}")
    lines.append("")
    lines.append("✂️ Перед отправкой оставьте только роли, которые нужно закрыть.")
    lines.append("")
    lines.append(SITE_POST_SIGNATURE)
    return "\n".join(lines)


def _grouped_milestones(rows: list[dict[str, Any]], key: str) -> dict[int, list[str]]:
    grouped: dict[int, list[str]] = {}
    for row in rows:
        milestone = row.get(key)
        if milestone:
            grouped.setdefault(int(milestone), []).append(
                str(row.get("name") or "Неизвестный участник")
            )
    return dict(sorted(grouped.items()))


def _milestones_post(svod: dict[str, Any]) -> str:
    event = svod["event"]
    runners = svod["runners"]
    volunteers = svod["volunteers"]

    run_local = _grouped_milestones(runners, "location_milestone")
    run_platform = _grouped_milestones(runners, "platform_milestone")
    vol_local = _grouped_milestones(volunteers, "location_milestone")
    vol_platform = _grouped_milestones(volunteers, "platform_milestone")

    lines = _event_header(event, "🎂 Юбилеи дня")
    if run_local:
        lines.append("")
        lines.append("🏃 Юбилейные финиши на нашей локации:")
        for milestone, names in run_local.items():
            lines.append(f"🔹 {milestone}-й: {', '.join(names)}")
    if run_platform:
        lines.append("")
        lines.append(f"🏅 Юбилейные финиши в системе {event['platform_name']}:")
        for milestone, names in run_platform.items():
            lines.append(f"🔹 {milestone}-й: {', '.join(names)}")
    if vol_local:
        lines.append("")
        lines.append("🙌 Юбилейные волонтёрства на локации:")
        for milestone, names in vol_local.items():
            lines.append(f"🔹 {milestone}-е: {', '.join(names)}")
    if vol_platform:
        lines.append("")
        lines.append(f"🤝 Юбилейные волонтёрства в системе {event['platform_name']}:")
        for milestone, names in vol_platform.items():
            lines.append(f"🔹 {milestone}-е: {', '.join(names)}")
    if not (run_local or run_platform or vol_local or vol_platform):
        lines.append("")
        lines.append("Юбилеев на этом старте не случилось — значит, всё впереди!")
    else:
        lines.append("")
        lines.append("Поздравляем наших героев — так держать!")
    lines.append("")
    lines.append(SITE_POST_SIGNATURE)
    return "\n".join(lines)


def build_upcoming_post(
    milestones: dict[str, Any],
    *,
    min_run_milestone: int = 10,
    min_vol_milestone: int = 10,
) -> str:
    """Пятничная рубрика: у кого юбилей случится на ближайшем старте.

    Берём людей «в шаге от юбилея» (remaining == 1) из календаря юбилеев —
    события ещё нет, поэтому пост строится по локации, а не по забегу.
    Пороги отдельные для пробежек и волонтёрств (просьба Дмитрия 17.08.2026):
    на больших локациях десятки «10-х» каждую неделю забивают пост, оргкоманда
    сама решает, с какого числа юбилей достоин упоминания.
    """
    location = milestones["location"]
    ready = [
        item
        for item in milestones["items"]
        if item["remaining"] == 1
        and item["milestone"]
        >= (min_run_milestone if item["kind"].startswith("runs") else min_vol_milestone)
    ]

    lines = [
        "🔮 Завтра возможны юбилеи!",
        f"📍 {location['name']}",
        "",
        "Если выйдут на старт, юбилей отпразднуют:",
    ]
    if not ready:
        return "\n".join(
            [
                "🔮 Юбилеи на подходе",
                f"📍 {location['name']}",
                "",
                "На ближайший старт юбиляров не видно — но кратные даты уже зреют, "
                "загляните в календарь юбилеев кабинета.",
                "",
                SITE_POST_SIGNATURE,
            ]
        )
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for item in ready:
        by_kind.setdefault(item["kind_label"], []).append(item)
    kind_emoji = {
        "Пробежки здесь": "🏃",
        "Пробежки в системе": "🏅",
        "Волонтёрства здесь": "🙌",
        "Волонтёрства в системе": "🤝",
    }
    for kind_label, items in by_kind.items():
        lines.append("")
        lines.append(f"{kind_emoji.get(kind_label, '⭐')} {kind_label}:")
        for item in sorted(items, key=lambda x: -x["milestone"]):
            suffix = "я" if kind_label.startswith("Пробежки") else "е"
            lines.append(f"🔹 {item['milestone']}-{suffix}: {item['name']}")
    lines.append("")
    lines.append("Приходите поддержать — будет повод для аплодисментов! 🎉")
    lines.append("")
    lines.append(SITE_POST_SIGNATURE)
    return "\n".join(lines)


def build_event_post(db: Session, event_id: UUID, template: str) -> dict[str, Any] | None:
    """Пост по шаблону. None — событие не найдено; KeyError не бывает:
    неизвестный шаблон отсекает роут по списку POST_TEMPLATES."""
    if template == "full":
        report = build_event_report(db, event_id, post_signature=SITE_POST_SIGNATURE)
        if report is None:
            return None
        return {
            "post_text": report["post_text"],
            "template": template,
            "location_id": report["event"]["location_id"],
        }

    svod = build_event_svod(db, event_id)
    if svod is None:
        return None
    if template == "stats":
        post_text = _stats_post(svod, _event_guest_homes(db, svod))
    else:
        builders = {
            "volunteers": _volunteers_post,
            "newcomers": _newcomers_post,
            "milestones": _milestones_post,
        }
        post_text = builders[template](svod)
    return {
        "post_text": post_text,
        "template": template,
        "location_id": svod["event"]["location_id"],
    }


def _event_guest_homes(db: Session, svod: dict[str, Any]) -> list[dict[str, Any]]:
    """Гости события с их домашними локациями («Имя — Вернадского»).

    Гость — финишёр, чья домашняя локация (общесайтовая логика) не совпадает
    с локацией события. Участники без определившегося дома (первые старты,
    мало данных) в гости не записываются.
    """
    from app.models import Event, Location, Platform
    from app.services.location_catalog_service import LocationCatalogIndex
    from app.services.location_page_service import build_locations_index
    from app.services.organizer_service import participant_home_keys

    event_row = db.query(Event).filter(Event.id == svod["event"]["event_id"]).one_or_none()
    if event_row is None:
        return []
    location = db.query(Location).filter(Location.id == event_row.location_id).one()
    platform = db.query(Platform).filter(Platform.id == location.platform_id).one()
    our_key = LocationCatalogIndex(db).canonical_identity_key(location, platform.code)

    runners = [r for r in svod["runners"] if r.get("participant_id")]
    homes = participant_home_keys(db, {r["participant_id"] for r in runners})
    index_by_key = {
        entry.get("identity_key"): entry
        for entry in build_locations_index(db).get("items", [])
    }

    guests: list[dict[str, Any]] = []
    for runner in runners:
        home_key = homes.get(runner["participant_id"])
        if home_key is None or home_key == our_key:
            continue
        entry = index_by_key.get(home_key)
        if entry is None:
            continue
        guests.append(
            {
                "name": runner.get("name") or "Неизвестный участник",
                "home_name": entry.get("name") or "другая локация",
                "home_city": entry.get("city"),
            }
        )
    guests.sort(key=lambda item: item["name"])
    return guests


def build_travelers_post(
    db: Session, identity: Any, event: Any, *, min_runs: int | None = None
) -> str:
    """«Наши в гостях»: постоянные участники локации на чужих стартах в эту дату.

    Рубрика, которой нет ни у одной локации из выборки, — чистый дифференциатор:
    руками её собрать почти невозможно, а по протоколам всех парков — легко.
    """
    from app.services.organizer_service import (
        TRAVELERS_LOCAL_MIN_RUNS,
        list_event_travelers,
    )
    from app.time_format import format_finish_time_display

    threshold = min_runs or TRAVELERS_LOCAL_MIN_RUNS
    travelers = list_event_travelers(db, identity, event.event_date, min_runs=threshold)

    lines = [
        "🧭 Наши в гостях",
        f"📍 {identity.name} · {fmt_date_ru(event.event_date)}",
        "",
    ]
    if not travelers:
        lines.append(
            "В эту субботу все свои бежали дома — выездных стартов у постоянных "
            "участников не случилось."
        )
    else:
        lines.append("Пока мы бежали дома, наши постоянные участники открывали другие парки:")
        for row in travelers:
            city = f" ({row['away_city']})" if row.get("away_city") else ""
            time_part = (
                f" — {format_finish_time_display(row['finish_time_sec'])}"
                if row.get("finish_time_sec")
                else ""
            )
            # Счётчик своих пробежек рядом с именем: сразу видно, насколько
            # человек «наш» (просьба Дмитрия 18.08.2026).
            runs = row.get("runs_here") or 0
            runs_part = (
                f" ({runs} {ru_plural(runs, ('пробежка', 'пробежки', 'пробежек'))} у нас)"
                if runs
                else ""
            )
            name = row["name"] or "Неизвестный участник"
            lines.append(f"• {name}{runs_part} — {row['away_location']}{city}{time_part}")
        count = len(travelers)
        lines.append("")
        lines.append(
            f"{count} {ru_plural(count, ('выезд', 'выезда', 'выездов'))} за субботу. "
            "Возвращайтесь с новыми историями!"
        )
    lines.append("")
    lines.append(
        f"«Свои» — от {threshold} финишей у нас, и наша локация для них домашняя."
    )
    lines.append("")
    lines.append(SITE_POST_SIGNATURE)
    return "\n".join(lines)
