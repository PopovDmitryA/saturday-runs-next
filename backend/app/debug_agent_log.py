from __future__ import annotations

import json
import os
import time
from typing import Any

_LOG_PATH = os.environ.get(
    "DEBUG_AGENT_LOG_PATH",
    "/Users/dmitry/Projects/saturday_runs_stats/.cursor/debug-31e6fc.log",
)
_SESSION_ID = "31e6fc"


def agent_log(
    *,
    location: str,
    message: str,
    data: dict[str, Any] | None = None,
    hypothesis_id: str = "",
    run_id: str = "pre-fix",
) -> None:
    # #region agent log
    try:
        payload = {
            "sessionId": _SESSION_ID,
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data or {},
            "timestamp": int(time.time() * 1000),
        }
        with open(_LOG_PATH, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass
    # #endregion
