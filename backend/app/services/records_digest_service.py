"""Дайджест рекордов за дату: что рассказать каждой локации в её чат.

Перенос adhoc-скрипта generate_records_report.py. Отличия от легаси:
- в карточку добавлены юбиляры локации и рекорд посещаемости;
- локации ранжируются по «весу» новости (важность), а не по наличию ссылки;
- контакты и флаг «не беспокоить» берутся из location_contacts, а не из легаси-таблицы.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from app.location_page_url import location_page_url
from app.services.admin_event_report_service import (
    NOT_SECONDARY_SQL,
    POST_SIGNATURE,
    age_group_sql,
    fmt_date_ru,
    fmt_time,
    gender_sql,
    ru_plural,
)

# Локации, в чаты которых мы пишем (parkrun не анонсируем).
DIGEST_PLATFORM_CODES = ("five_verst", "s95", "runpark")

# Юбилейные отметки в локации, о которых стоит рассказать.
LOCATION_MILESTONES = (10, 25, 50, 75, 100, 150, 200, 250, 300, 400, 500)


@dataclass
class _Weights:
    """Вес новости в баллах важности.

    Рекорд трассы — главная новость; чем дольше стоял прежний, тем ценнее.
    Возрастной рекорд весит меньше и тем больше, чем «обжитее» группа.
    """

    course_record: int = 100
    course_record_first: int = 35
    course_record_per_year: int = 12
    course_record_max_bonus: int = 60
    attendance_record: int = 55
    age_record: int = 22
    age_record_first: int = 6
    age_record_group_bonus_max: int = 18
    milestone_base: dict[int, int] = field(
        default_factory=lambda: {10: 6, 25: 14, 50: 22, 75: 26, 100: 40, 150: 45, 200: 50, 250: 60, 300: 65, 400: 70, 500: 80}
    )


WEIGHTS = _Weights()


def _query(db: Session, sql: str, params: dict[str, Any], expanding: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    stmt = text(sql)
    for name in expanding:
        stmt = stmt.bindparams(bindparam(name, expanding=True))
    return [dict(row) for row in db.execute(stmt, params).mappings()]


def list_digest_dates(db: Session, *, limit: int = 20) -> list[dict[str, Any]]:
    """Даты, когда были забеги, — для выбора в форме (свежие сверху)."""
    return _query(
        db,
        f"""
        SELECT e.event_date,
               count(DISTINCT e.id) AS events_count,
               count(DISTINCT e.location_id) AS locations_count
        FROM events e
        JOIN platforms p ON p.id = e.platform_id
        WHERE p.code IN :platform_codes
          AND NOT e.is_test_event
          AND e.{NOT_SECONDARY_SQL}
          AND e.event_date <= CURRENT_DATE
        GROUP BY e.event_date
        ORDER BY e.event_date DESC
        LIMIT :limit
        """,
        {"platform_codes": list(DIGEST_PLATFORM_CODES), "limit": limit},
        expanding=("platform_codes",),
    )


def _course_records(db: Session, event_date: date) -> list[dict[str, Any]]:
    """Рекорды трассы по полу, обновлённые в эту дату (по каждой локации)."""
    gender_expr = gender_sql("rr.age_category")
    return _query(
        db,
        f"""
        WITH hist AS (
            SELECT e.location_id, e.id AS event_id, e.event_date, rr.finish_time_sec,
                   {gender_expr} AS gender, rr.participant_id
            FROM run_results rr
            JOIN events e ON e.id = rr.event_id
            JOIN platforms p ON p.id = e.platform_id
            WHERE p.code IN :platform_codes
              AND NOT e.is_test_event
              AND e.{NOT_SECONDARY_SQL}
              AND e.event_date <= :event_date
              AND rr.finish_time_sec IS NOT NULL
        ),
        today_best AS (
            SELECT DISTINCT ON (location_id, gender)
                   location_id, gender, finish_time_sec, participant_id
            FROM hist
            WHERE event_date = :event_date AND gender IS NOT NULL
            ORDER BY location_id, gender, finish_time_sec
        ),
        prev_best AS (
            SELECT DISTINCT ON (location_id, gender)
                   location_id, gender, finish_time_sec, event_date, participant_id
            FROM hist
            WHERE event_date < :event_date AND gender IS NOT NULL
            ORDER BY location_id, gender, finish_time_sec, event_date
        )
        SELECT t.location_id, t.gender,
               t.finish_time_sec AS record_sec,
               p.display_name AS record_name,
               pb.finish_time_sec AS previous_sec,
               pb.event_date AS previous_date,
               pp.display_name AS previous_name
        FROM today_best t
        LEFT JOIN prev_best pb ON pb.location_id = t.location_id AND pb.gender = t.gender
        LEFT JOIN participants p ON p.id = t.participant_id
        LEFT JOIN participants pp ON pp.id = pb.participant_id
        WHERE pb.finish_time_sec IS NULL OR t.finish_time_sec < pb.finish_time_sec
        """,
        {"platform_codes": list(DIGEST_PLATFORM_CODES), "event_date": event_date},
        expanding=("platform_codes",),
    )


def _age_records(db: Session, event_date: date) -> list[dict[str, Any]]:
    """Рекорды возрастных групп, обновлённые в эту дату."""
    age_expr = age_group_sql("rr.age_category")
    return _query(
        db,
        f"""
        WITH hist AS (
            SELECT e.location_id, e.event_date, rr.finish_time_sec, rr.participant_id,
                   {age_expr} AS age_group
            FROM run_results rr
            JOIN events e ON e.id = rr.event_id
            JOIN platforms p ON p.id = e.platform_id
            WHERE p.code IN :platform_codes
              AND NOT e.is_test_event
              AND e.{NOT_SECONDARY_SQL}
              AND e.event_date <= :event_date
              AND rr.finish_time_sec IS NOT NULL
        ),
        sized AS (
            SELECT location_id, age_group, count(*) AS group_total
            FROM hist WHERE age_group IS NOT NULL
            GROUP BY location_id, age_group
        ),
        today_best AS (
            SELECT DISTINCT ON (location_id, age_group)
                   location_id, age_group, finish_time_sec, participant_id
            FROM hist
            WHERE event_date = :event_date AND age_group IS NOT NULL
            ORDER BY location_id, age_group, finish_time_sec
        ),
        prev_best AS (
            SELECT DISTINCT ON (location_id, age_group)
                   location_id, age_group, finish_time_sec, event_date, participant_id
            FROM hist
            WHERE event_date < :event_date AND age_group IS NOT NULL
            ORDER BY location_id, age_group, finish_time_sec, event_date
        )
        SELECT t.location_id, t.age_group,
               t.finish_time_sec AS record_sec,
               p.display_name AS record_name,
               pb.finish_time_sec AS previous_sec,
               pb.event_date AS previous_date,
               pp.display_name AS previous_name,
               s.group_total
        FROM today_best t
        LEFT JOIN prev_best pb ON pb.location_id = t.location_id AND pb.age_group = t.age_group
        LEFT JOIN participants p ON p.id = t.participant_id
        LEFT JOIN participants pp ON pp.id = pb.participant_id
        LEFT JOIN sized s ON s.location_id = t.location_id AND s.age_group = t.age_group
        WHERE pb.finish_time_sec IS NULL OR t.finish_time_sec < pb.finish_time_sec
        ORDER BY t.location_id, t.age_group
        """,
        {"platform_codes": list(DIGEST_PLATFORM_CODES), "event_date": event_date},
        expanding=("platform_codes",),
    )


def _attendance_records(db: Session, event_date: date) -> list[dict[str, Any]]:
    return _query(
        db,
        f"""
        WITH per_event AS (
            -- Численность старта — все строки протокола, включая «неизвестных»
            -- (status='unknown', время не распознано): они физически бежали.
            -- Так же считают страница локации, журнал протоколов и отчёт по
            -- событию; фильтр по finish_time_sec занижал рекорд и мог вовсе
            -- не показать его — на крупных стартах неизвестных бывают сотни.
            SELECT e.location_id, e.event_date,
                   count(rr.id) AS finishers
            FROM events e
            JOIN platforms p ON p.id = e.platform_id
            LEFT JOIN run_results rr ON rr.event_id = e.id
            WHERE p.code IN :platform_codes
              AND NOT e.is_test_event
              AND e.{NOT_SECONDARY_SQL}
              AND e.event_date <= :event_date
            GROUP BY e.location_id, e.event_date
        )
        SELECT t.location_id, t.finishers,
               (SELECT max(h.finishers) FROM per_event h
                WHERE h.location_id = t.location_id AND h.event_date < :event_date) AS prior_max
        FROM per_event t
        WHERE t.event_date = :event_date
        """,
        {"platform_codes": list(DIGEST_PLATFORM_CODES), "event_date": event_date},
        expanding=("platform_codes",),
    )


def _location_milestones(db: Session, event_date: date) -> list[dict[str, Any]]:
    """Юбиляры локации: круглое число пробежек/волонтёрств именно в этот день."""
    rows: list[dict[str, Any]] = []
    for table, kind in (("run_results", "runs"), ("volunteer_results", "volunteering")):
        rows += [
            {**row, "kind": kind}
            for row in _query(
                db,
                f"""
                WITH today AS (
                    SELECT DISTINCT x.participant_id, e.location_id
                    FROM {table} x
                    JOIN events e ON e.id = x.event_id
                    JOIN platforms p ON p.id = e.platform_id
                    WHERE p.code IN :platform_codes
                      AND e.event_date = :event_date
                      AND NOT e.is_test_event
                      AND e.{NOT_SECONDARY_SQL}
                      AND x.participant_id IS NOT NULL
                ),
                counts AS (
                    SELECT t.participant_id, t.location_id,
                           count(DISTINCT x.event_id) AS total
                    FROM today t
                    JOIN {table} x ON x.participant_id = t.participant_id
                    JOIN events e ON e.id = x.event_id AND e.location_id = t.location_id
                    WHERE NOT e.is_test_event
                      AND e.event_date <= :event_date
                      AND e.{NOT_SECONDARY_SQL}
                    GROUP BY t.participant_id, t.location_id
                )
                SELECT c.location_id, c.total, p.display_name AS name
                FROM counts c
                JOIN participants p ON p.id = c.participant_id
                WHERE c.total IN :milestones
                ORDER BY c.total DESC, p.display_name
                """,
                {
                    "platform_codes": list(DIGEST_PLATFORM_CODES),
                    "event_date": event_date,
                    "milestones": list(LOCATION_MILESTONES),
                },
                expanding=("platform_codes", "milestones"),
            )
        ]
    return rows


def _locations_meta(db: Session, location_ids: list[UUID]) -> dict[UUID, dict[str, Any]]:
    if not location_ids:
        return {}
    rows = _query(
        db,
        """
        SELECT l.id AS location_id, l.name AS location_name, l.city, l.country,
               l.external_key, l.source_url,
               p.code AS platform_code, p.name AS platform_name
        FROM locations l
        JOIN platforms p ON p.id = l.platform_id
        WHERE l.id IN :location_ids
        """,
        {"location_ids": [str(x) for x in location_ids]},
        expanding=("location_ids",),
    )
    # Чат и «не беспокоить» — на уровне физической точки (общие для всех платформ
    # площадки), а не отдельной локации-платформы, где случился рекорд.
    from app.services.location_contacts_service import resolve_group_announce_targets

    group_targets = resolve_group_announce_targets(db, location_ids)
    meta: dict[UUID, dict[str, Any]] = {}
    for row in rows:
        target = group_targets.get(row["location_id"], {})
        meta[row["location_id"]] = {
            **row,
            "location_url": location_page_url(
                row["platform_code"], row["external_key"], row["source_url"]
            ),
            "telegram_contacts": target.get("telegram_contacts", []),
            "do_not_disturb": bool(target.get("do_not_disturb")),
            "comment": target.get("comment"),
        }
    return meta


def _years_between(previous: date | None, current: date) -> float:
    if previous is None:
        return 0.0
    return max(0.0, (current - previous).days / 365.25)


def build_records_digest(db: Session, event_date: date) -> dict[str, Any]:
    course = _course_records(db, event_date)
    ages = _age_records(db, event_date)
    attendance = [
        row
        for row in _attendance_records(db, event_date)
        if row["prior_max"] is not None and row["finishers"] > row["prior_max"]
    ]
    milestones = _location_milestones(db, event_date)

    by_location: dict[UUID, dict[str, Any]] = {}

    def bucket(location_id: UUID) -> dict[str, Any]:
        return by_location.setdefault(
            location_id,
            {
                "course_records": [],
                "age_records": [],
                "attendance_record": None,
                "milestones": {"runs": [], "volunteering": []},
                "score": 0,
            },
        )

    for row in course:
        item = bucket(row["location_id"])
        years = _years_between(row["previous_date"], event_date)
        score = WEIGHTS.course_record if row["previous_sec"] else WEIGHTS.course_record_first
        if row["previous_sec"]:
            score += min(
                int(years * WEIGHTS.course_record_per_year), WEIGHTS.course_record_max_bonus
            )
        item["course_records"].append({**row, "years_since": round(years, 1)})
        item["score"] += score

    # Рекордсмен трассы не должен дублироваться в возрастных рекордах той же
    # локации — как в легаси: про него уже рассказали более крупной новостью.
    course_holders = {
        (row["location_id"], row["record_name"]) for row in course if row["record_name"]
    }
    for row in ages:
        if (row["location_id"], row["record_name"]) in course_holders:
            continue
        item = bucket(row["location_id"])
        years = _years_between(row["previous_date"], event_date)
        if row["previous_sec"]:
            score = WEIGHTS.age_record + min(
                (row["group_total"] or 0) // 25, WEIGHTS.age_record_group_bonus_max
            )
        else:
            score = WEIGHTS.age_record_first
        item["age_records"].append({**row, "years_since": round(years, 1)})
        item["score"] += score

    for row in attendance:
        item = bucket(row["location_id"])
        item["attendance_record"] = {"finishers": row["finishers"], "prior_max": row["prior_max"]}
        item["score"] += WEIGHTS.attendance_record

    for row in milestones:
        item = bucket(row["location_id"])
        item["milestones"][row["kind"]].append({"name": row["name"], "count": row["total"]})
        item["score"] += WEIGHTS.milestone_base.get(row["total"], 20)

    meta = _locations_meta(db, list(by_location))
    cards: list[dict[str, Any]] = []
    for location_id, item in by_location.items():
        info = meta.get(location_id)
        if info is None:
            continue
        card = {
            **info,
            **item,
            "importance": (
                "high" if item["score"] >= 100 else "medium" if item["score"] >= 45 else "low"
            ),
        }
        card["post_text"] = _build_digest_text(card, event_date)
        cards.append(card)

    cards.sort(key=lambda c: (c["do_not_disturb"], -c["score"], c["location_name"]))
    return {
        "event_date": event_date,
        "generated_locations": len(cards),
        "with_telegram": sum(1 for c in cards if c["telegram_contacts"] and not c["do_not_disturb"]),
        "without_telegram": sum(
            1 for c in cards if not c["telegram_contacts"] and not c["do_not_disturb"]
        ),
        "do_not_disturb": sum(1 for c in cards if c["do_not_disturb"]),
        "cards": cards,
    }


# ===== Текст рассылки =====

_GREETINGS = ("Всем привет!", "Друзья, привет!", "{location}, привет!")


def _pick(options: tuple[str, ...], seed_key: str) -> str:
    """Детерминированный выбор фразы: один и тот же отчёт не «пляшет» между открытиями."""
    rng = random.Random(seed_key)
    return rng.choice(list(options))


def _gender_label(gender: str) -> str:
    return "Женский" if gender == "F" else "Мужской"


def _build_digest_text(card: dict[str, Any], event_date: date) -> str:
    location = card["location_name"]
    seed_key = f"{location}:{event_date.isoformat()}"
    greeting = _pick(_GREETINGS, seed_key).format(location=location)

    blocks: list[str] = []

    if card["course_records"]:
        intro = _pick(
            (
                "У вас обновлён рекорд трассы:",
                "Классные новости — обновлён рекорд трассы:",
                "История локации пополнилась — рекорд трассы обновлён:",
            ),
            seed_key + ":course",
        )
        lines = [f"{greeting} {intro}"]
        for record in sorted(card["course_records"], key=lambda r: r["record_sec"]):
            label = f"{_gender_label(record['gender'])} рекорд трассы"
            name = record["record_name"] or "Неизвестный участник"
            if record["previous_sec"]:
                spark = " ⚡" if record["years_since"] >= 1 else ""
                prev_name = record["previous_name"] or "неизвестный участник"
                lines.append(
                    f"{label}\n"
                    f"⏱️ Старый рекорд — {fmt_time(record['previous_sec'])} "
                    f"от {fmt_date_ru(record['previous_date'])} — {prev_name}{spark}\n"
                    f"🚀 Новый рекорд — {fmt_time(record['record_sec'])} — {name}"
                )
            else:
                lines.append(
                    f"{label}\n"
                    f"⏱️ Ранее рекорд по полу не фиксировался.\n"
                    f"🚀 Новый рекорд — {fmt_time(record['record_sec'])} — {name}"
                )
        blocks.append("\n\n".join(lines))

    if card["age_records"]:
        count = len(card["age_records"])
        if card["course_records"]:
            intro = (
                "Также обновлён рекорд локации по возрастной группе:"
                if count == 1
                else "Также обновлены рекорды локации по возрастным группам:"
            )
        else:
            intro = (
                f"{greeting} У вас обновлён рекорд локации по возрастной группе:"
                if count == 1
                else f"{greeting} У вас обновлены рекорды локации по возрастным группам:"
            )
        lines = [intro]
        for record in sorted(card["age_records"], key=lambda r: r["age_group"] or ""):
            name = record["record_name"] or "Неизвестный участник"
            if record["previous_sec"]:
                spark = " ⚡" if record["years_since"] >= 1 else ""
                prev_name = record["previous_name"] or "неизвестный участник"
                lines.append(
                    f"{record['age_group']}. Всего финишей: {record['group_total']}\n"
                    f"⏱️ Старый рекорд — {fmt_time(record['previous_sec'])} "
                    f"от {fmt_date_ru(record['previous_date'])} — {prev_name}{spark}\n"
                    f"🚀 Новый рекорд — {fmt_time(record['record_sec'])} — {name}"
                )
            else:
                lines.append(
                    f"{record['age_group']}. Первый финиш в этой возрастной категории — "
                    f"{fmt_time(record['record_sec'])} — {name}"
                )
        blocks.append("\n\n".join(lines))

    attendance = card["attendance_record"]
    if attendance:
        line = (
            f"👥 Рекорд посещаемости локации: {attendance['finishers']} "
            f"{ru_plural(attendance['finishers'], ('финишёр', 'финишёра', 'финишёров'))} "
            f"(прежний максимум — {attendance['prior_max']})"
        )
        blocks.append(line if blocks else f"{greeting} {line}")

    milestone_lines: list[str] = []
    for kind, word in (("runs", "пробежек"), ("volunteering", "волонтёрств")):
        by_count: dict[int, list[str]] = {}
        for item in card["milestones"][kind]:
            by_count.setdefault(item["count"], []).append(item["name"] or "Неизвестный участник")
        for count, names in sorted(by_count.items(), reverse=True):
            milestone_lines.append(f"{count} {word} в локации: {', '.join(names)}")
    if milestone_lines:
        header = "🏆 Юбилейные участия в локации:"
        block = header + "\n" + "\n".join(milestone_lines)
        blocks.append(block if blocks else f"{greeting}\n\n{block}")

    records_total = len(card["course_records"]) + len(card["age_records"])
    if records_total == 1:
        blocks.append("Поздравляю с рекордом! 🎉")
    elif records_total > 1:
        blocks.append("Поздравляю с рекордами! 🎉")
    elif milestone_lines or attendance:
        blocks.append("Поздравляю! 🎉")

    blocks.append(POST_SIGNATURE)
    return "\n\n".join(blocks)
