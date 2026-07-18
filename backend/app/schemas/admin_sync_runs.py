from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class SyncRunMetric(BaseModel):
    key: str
    label: str
    value: Any


class SyncRunItem(BaseModel):
    id: int
    pipeline: str
    pipeline_label: str
    platform: str
    platform_label: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = None
    records_updated: int = 0
    error_count: int = 0
    skip_reason: str | None = None
    metrics: list[SyncRunMetric] = []
    errors: list[str] = []


class SyncPipelineSummary(BaseModel):
    pipeline: str
    pipeline_label: str
    runs: int
    ok: int
    problems: int
    skipped: int
    errors_total: int
    metrics: list[SyncRunMetric] = []
    last_run_at: datetime | None = None
    last_status: str | None = None


class SyncPlatformSummary(BaseModel):
    platform: str
    platform_label: str
    runs: int
    ok: int
    problems: int
    skipped: int
    errors_total: int
    metrics: list[SyncRunMetric] = []
    pipelines: list[SyncPipelineSummary] = []


class AdminSyncRunsResponse(BaseModel):
    generated_at: datetime
    date_from: datetime
    date_to: datetime
    platforms: list[SyncPlatformSummary] = []
    runs: list[SyncRunItem] = []
    total: int = 0
