"""АБ-эксперименты: приём сырых событий с вариантом.

Дополняет событийную аналитику страниц (page_analytics_service): та отвечает
на «сколько смотрят страницы», эта — на «какой вариант страницы работает
лучше». События пишутся в ab_events и анализируются SQL-запросами вручную
(объём маленький, агрегатов и админки пока нет).

Ключевые договорённости:
- visitor_key всегда анонимный ("a:<id>") и не меняется после логина — по нему
  события одной воронки сшиваются сквозь VK-редирект (см. модель AbEvent);
- login_complete классифицируется в когорты по возрасту аккаунта: свежий
  аккаунт — «new» (регистрация, конверсия эксперимента), старый — «returning»
  (вернувшийся разлогиненный участник, в конверсию НЕ входит).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import AbEvent, User

# Известные эксперименты; чужие имена не пишем — мусор из ручных запросов.
KNOWN_EXPERIMENTS = frozenset({"home_v1"})

# Белый список типов событий (защита от мусора в свободном поле).
KNOWN_EVENT_TYPES = frozenset(
    {
        "scroll_depth",  # value: "25" | "50" | "75" | "100"
        "chart_tab",  # value: ключ вкладки графика
        "period",  # value: "all" | "year"
        "cta_view",  # value: размещение CTA ("bottom", позже "hero", "week")
        "cta_click",  # value: размещение CTA
        "teaser_preview",  # value: код системы (тизер Т10, на будущее)
        "login_complete",  # когорта считается на сервере
    }
)

# Аккаунт моложе этого порога на момент login_complete = «логин создал
# аккаунт», то есть регистрация. Старше — вернувшийся участник.
NEW_USER_WINDOW = timedelta(hours=1)


def classify_login_cohort(user: User, now: datetime | None = None) -> str:
    moment = now or datetime.now(timezone.utc)
    created = user.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return "new" if moment - created <= NEW_USER_WINDOW else "returning"


def record_ab_event(
    db: Session,
    *,
    experiment: str,
    variant: str,
    visitor_key: str,
    event_type: str,
    value: str = "",
    path: str = "",
    viewer: User | None = None,
) -> AbEvent | None:
    """Пишет событие эксперимента; неизвестное молча отбрасывает.

    Возвращает записанное событие или None (отброшено). login_complete без
    авторизованного пользователя не имеет смысла — тоже отбрасывается.
    """
    if experiment not in KNOWN_EXPERIMENTS or event_type not in KNOWN_EVENT_TYPES:
        return None

    cohort = ""
    if event_type == "login_complete":
        if viewer is None:
            return None
        cohort = classify_login_cohort(viewer)

    event = AbEvent(
        experiment=experiment,
        variant=variant[:8],
        visitor_key=visitor_key[:80],
        event_type=event_type,
        value=value[:128],
        path=path[:256],
        cohort=cohort,
        viewer_user_id=viewer.id if viewer is not None else None,
    )
    db.add(event)
    db.commit()
    return event
