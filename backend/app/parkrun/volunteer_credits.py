from __future__ import annotations

import re
from collections.abc import Iterable
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import Event, Participant, VolunteerResult

ROLE_OCCASIONS_RE = re.compile(r"\((\d+)×\)\s*$")
TOTAL_CREDITS_LABEL = "total credit"


def volunteer_credits_from_profile_extra(profile_extra: dict | None) -> int | None:
    if not profile_extra:
        return None
    total = profile_extra.get("volunteer_occasions_total")
    if isinstance(total, int) and total > 0:
        return total
    summary = profile_extra.get("volunteer_summary")
    if not isinstance(summary, list) or not summary:
        return None
    # Одна и та же волонтёрская смена может дать кредит сразу нескольким ролям
    # (напр. Results Processor + Timekeeper в один день) — сумма по ролям
    # переучитывает такие дни. У parkrun есть отдельная строка "Total Credits",
    # которая и есть правильное число occasions — её и берём, если она есть;
    # сумма по ролям — только запасной вариант, когда её нет.
    total_item = next(
        (
            item
            for item in summary
            if isinstance(item, dict) and is_total_credits_label(str(item.get("role") or ""))
        ),
        None,
    )
    if total_item is not None:
        total_occasions = int(total_item.get("occasions") or 0)
        if total_occasions > 0:
            return total_occasions
    occasions_sum = sum(
        int(item.get("occasions") or 0)
        for item in summary
        if isinstance(item, dict) and not is_total_credits_label(str(item.get("role") or ""))
    )
    return occasions_sum if occasions_sum > 0 else None


def volunteer_credits_from_role_labels(roles: Iterable[str]) -> int | None:
    # Тот же принцип, что и в volunteer_credits_from_profile_extra: строка role
    # "Total Credits (N×)" — уже правильный итог (без переучёта смен с
    # несколькими ролями за раз), берём её вместо суммы отдельных ролей.
    total_from_summary: int | None = None
    fallback_sum = 0
    matched = False
    for role in roles:
        label = role.strip()
        if not label:
            continue
        match = ROLE_OCCASIONS_RE.search(label)
        if is_total_credits_label(label):
            if match:
                total_from_summary = int(match.group(1))
            continue
        if match:
            matched = True
            fallback_sum += int(match.group(1))
    if total_from_summary is not None:
        return total_from_summary
    return fallback_sum if matched else None


def resolve_parkrun_volunteering_count(
    *,
    profile_extra: dict | None,
    summary_role_labels: Iterable[str] | None = None,
) -> int:
    credits = volunteer_credits_from_profile_extra(profile_extra)
    if credits is not None:
        return credits
    if summary_role_labels is not None:
        from_roles = volunteer_credits_from_role_labels(summary_role_labels)
        if from_roles is not None:
            return from_roles
    return 0


def count_parkrun_volunteering(
    db: Session,
    participant: Participant,
    platform_id: UUID,
) -> int:
    roles = (
        db.query(VolunteerResult.role)
        .join(Event, VolunteerResult.event_id == Event.id)
        .filter(
            VolunteerResult.participant_id == participant.id,
            Event.platform_id == platform_id,
            Event.is_test_event.is_(False),
        )
        .all()
    )
    return resolve_parkrun_volunteering_count(
        profile_extra=participant.profile_extra,
        summary_role_labels=[role for (role,) in roles if role],
    )


def is_total_credits_label(label: str) -> bool:
    return TOTAL_CREDITS_LABEL in label.casefold()
