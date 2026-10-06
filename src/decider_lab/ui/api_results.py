"""Results API: Run roots, leaderboards with CIs, comparisons, exports (API.md section 7).
Run roots come from `st.workspace.run_roots()`.

Owned endpoints (all under the `/api` prefix this router registers):

    GET /api/runs
    GET /api/runs/refs (register before /api/runs/{root_id})
    GET, DELETE /api/runs/{root_id}
    GET /api/runs/{root_id}/leaderboard[.csv|.md]
    GET /api/runs/{root_id}/vs-baseline
    GET /api/runs/{root_id}/families
    GET /api/runs/{root_id}/latency
    GET /api/runs/{root_id}/jevbench
    GET /api/runs/{root_id}/calibration
    GET /api/runs/{root_id}/rows[/{row_id}]
    GET /api/runs/{root_id}/provenance
    POST /api/runs/{root_id}/report
    GET /api/runs/{root_id}/report.md, /report.json
    GET /api/runs/{root_id}/runs/{model}/{suite}[/predictions[.csv]|/reliability|/latency]
    GET /api/compare

Conventions: raise `ApiError` for errors (never return error dicts); get shared state with
`st: StudioState = Depends(get_state)`; pass every string that can carry a secret through
`st.redact` / `st.redact.obj`; tests go in tests/ui/test_results*.py using the `client` fixture.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["results"])
