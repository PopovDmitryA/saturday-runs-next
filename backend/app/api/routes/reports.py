"""Internal read-only reporting API.

Защищённый bearer-токеном эндпоинт для выполнения read-only SELECT-запросов и
именованных отчётов. Токен задаётся в ``REPORT_API_TOKEN`` (пусто → 503).

Примеры:
    curl -H "Authorization: Bearer $TOKEN" \\
        https://run5k.run/api/internal/reports/

    curl -H "Authorization: Bearer $TOKEN" \\
        "https://run5k.run/api/internal/reports/named/table_sizes?limit=20"

    curl -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \\
        -d '{"sql":"SELECT count(*) FROM users","limit":100}' \\
        https://run5k.run/api/internal/reports/query
"""

from __future__ import annotations

import hmac
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.session import get_db
from app.services.report_query import (
    NAMED_REPORTS,
    ReportQueryError,
    ReportResult,
    run_read_only_query,
)

router = APIRouter(prefix="/internal/reports", tags=["internal-reports"])


def _verify_report_token(
    authorization: Annotated[str | None, Header()] = None,
    x_report_token: Annotated[str | None, Header()] = None,
    settings: Settings = Depends(get_settings),
) -> Settings:
    if not settings.report_api_token:
        raise HTTPException(status_code=503, detail="Reports API disabled")
    provided = x_report_token
    if authorization and authorization.lower().startswith("bearer "):
        provided = authorization[len("bearer ") :].strip()
    if not provided or not hmac.compare_digest(provided, settings.report_api_token):
        raise HTTPException(status_code=403, detail="Invalid report token")
    return settings


def _resolve_limit(requested: int | None, settings: Settings) -> int:
    max_rows = settings.report_query_max_rows
    if requested is None:
        return max_rows
    return max(1, min(requested, max_rows))


class QueryRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=20000)
    limit: int | None = Field(default=None, ge=1)


class ReportResponse(BaseModel):
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool
    elapsed_ms: int


class NamedReportItem(BaseModel):
    name: str
    description: str


class NamedReportsResponse(BaseModel):
    reports: list[NamedReportItem]
    max_rows: int
    timeout_seconds: int


def _to_response(result: ReportResult) -> ReportResponse:
    return ReportResponse(
        columns=result.columns,
        rows=result.rows,
        row_count=result.row_count,
        truncated=result.truncated,
        elapsed_ms=result.elapsed_ms,
    )


@router.get("/", response_model=NamedReportsResponse)
def list_reports(
    settings: Annotated[Settings, Depends(_verify_report_token)],
) -> NamedReportsResponse:
    return NamedReportsResponse(
        reports=[
            NamedReportItem(name=r.name, description=r.description)
            for r in NAMED_REPORTS.values()
        ],
        max_rows=settings.report_query_max_rows,
        timeout_seconds=settings.report_query_timeout_seconds,
    )


@router.post("/query", response_model=ReportResponse)
def run_query(
    body: QueryRequest,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(_verify_report_token)],
) -> ReportResponse:
    try:
        result = run_read_only_query(
            db,
            body.sql,
            limit=_resolve_limit(body.limit, settings),
            timeout_seconds=settings.report_query_timeout_seconds,
        )
    except ReportQueryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _to_response(result)


@router.get("/named/{name}", response_model=ReportResponse)
def run_named_report(
    name: str,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(_verify_report_token)],
    limit: Annotated[int | None, Query(ge=1)] = None,
) -> ReportResponse:
    report = NAMED_REPORTS.get(name)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Unknown report '{name}'")
    try:
        result = run_read_only_query(
            db,
            report.sql,
            limit=_resolve_limit(limit, settings),
            timeout_seconds=settings.report_query_timeout_seconds,
        )
    except ReportQueryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _to_response(result)
