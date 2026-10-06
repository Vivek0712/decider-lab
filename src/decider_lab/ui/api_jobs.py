"""Jobs API: Tracked CLI subprocesses (API.md section 6). Build on `st.jobs` (JobManager) and the SSE helpers
in `decider_lab.ui.jobs` (`job_event_stream`, `bus_event_stream`, `sse_response`).

Owned endpoints (all under the `/api` prefix this router registers):

    GET, POST /api/jobs
    GET, DELETE /api/jobs/{job_id}
    POST /api/jobs/{job_id}/cancel
    GET /api/jobs/{job_id}/log
    GET /api/jobs/{job_id}/log.txt
    GET /api/jobs/{job_id}/telemetry
    GET (SSE) /api/jobs/{job_id}/events
    GET (SSE) /api/events

Conventions: raise `ApiError` for errors (never return error dicts); get shared state with
`st: StudioState = Depends(get_state)`; pass every string that can carry a secret through
`st.redact` / `st.redact.obj`; tests go in tests/ui/test_jobs*.py using the `client` fixture.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["jobs"])
