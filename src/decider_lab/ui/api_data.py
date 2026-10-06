"""Data API: Suites and data tools (API.md section 9).

Owned endpoints (all under the `/api` prefix this router registers):

    GET /api/suites
    GET /api/suites/{ref}/stats
    GET /api/suites/{ref}/rows
    POST /api/data/export-suite
    POST /api/data/csv/preview
    POST /api/data/csv/convert
    POST /api/data/generate
    POST /api/data/split
    POST /api/data/check
    POST /api/data/leakcheck
    GET /api/files/raw

Conventions: raise `ApiError` for errors (never return error dicts); get shared state with
`st: StudioState = Depends(get_state)`; pass every string that can carry a secret through
`st.redact` / `st.redact.obj`; tests go in tests/ui/test_data*.py using the `client` fixture.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["data"])
