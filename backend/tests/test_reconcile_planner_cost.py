"""Планировщик сверки не агрегирует таблицы результатов ради отбора кандидатов.

Обычный проход (ротация) раньше тянул два подзапроса с GROUP BY по всем
run_results и volunteer_results, чтобы отобрать десяток протоколов: на dev-базе
в 2,3 млн строк это 1,9 с и полмиллиона буферов на вызов, а вызывается он
дважды на кусок и девять кусков за прогон. Агрегаты там нужны не для фильтра,
а только чтобы подписать причину у уже отобранных строк — значит считать их
надо ПОСЛЕ LIMIT и только по выбранным забегам.

Приоритетный проход (расхождения) подзапросы сохраняет намеренно: лучшее время
и число волонтёров стоят в самом WHERE, взять их больше неоткуда.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Platform
from app.sync import five_verst_reconcile as reconcile


def _sql(query: object, db: Session) -> str:
    return " ".join(str(query.statement.compile(db.bind)).split())  # type: ignore[attr-defined]


def _platform(db: Session) -> Platform:
    platform = db.query(Platform).filter(Platform.code == "five_verst").first()
    if platform is None:
        platform = Platform(code="five_verst", name="5 вёрст")
        db.add(platform)
        db.flush()
    return platform


def test_rotation_pass_does_not_aggregate_result_tables(db_session: Session) -> None:
    sql = _sql(reconcile._plain_candidates_query(db_session, _platform(db_session), None), db_session)
    assert "FROM run_results" not in sql
    assert "FROM volunteer_results" not in sql


def test_priority_pass_keeps_aggregates_for_the_filter(db_session: Session) -> None:
    query, _aggregates, _volunteers = reconcile._candidates_query(db_session, _platform(db_session), None)
    sql = _sql(query, db_session)
    assert "FROM run_results" in sql
    assert "FROM volunteer_results" in sql


def test_aggregates_for_events_is_empty_without_ids(db_session: Session) -> None:
    assert reconcile._aggregates_for_events(db_session, []) == {}
