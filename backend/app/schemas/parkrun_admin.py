from __future__ import annotations

from pydantic import BaseModel, Field


class ParkrunSessionStatusResponse(BaseModel):
    captcha_pending: bool
    captcha_message: str | None = None
    cooldown_remaining_seconds: float | None = None
    storage_state_configured: bool
    storage_state_exists: bool
    storage_state_path: str
    default_cdp_url: str
    pending_queue_count: int
    failed_queue_count: int = 0
    stuck_done_queue_count: int = 0
    worker_alive: bool = False
    worker_status: str = "idle"
    worker_requested_at: str | None = None
    worker_last_handled_at: str | None = None
    worker_progress: dict[str, object] = Field(default_factory=dict)


class ParkrunCdpSaveRequest(BaseModel):
    cdp_url: str = Field(default="", max_length=512)


class ParkrunCdpSaveResponse(BaseModel):
    storage_path: str
    page_title: str | None = None
    cdp_url: str
    message: str


class ParkrunProcessPendingRequest(BaseModel):
    limit: int = Field(default=10, ge=1, le=50)


class ParkrunProcessPendingResponse(BaseModel):
    summary: dict[str, int]
    details: list[str]


class ParkrunLocalWorkerStartRequest(BaseModel):
    reset_failed: bool = True


class ParkrunLocalWorkerStartResponse(BaseModel):
    queued: bool
    message: str
    requested_at: str | None = None


class ParkrunLocalWorkerStatusResponse(BaseModel):
    worker_alive: bool
    worker_status: str
    worker_requested_at: str | None = None
    worker_last_handled_at: str | None = None
    worker_progress: dict[str, object] = Field(default_factory=dict)
