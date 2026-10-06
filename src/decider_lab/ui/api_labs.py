"""Labs API: Lab files, templates, validation, run estimates (API.md section 5).

Owned endpoints (all under the `/api` prefix this router registers):

    GET, POST /api/labs
    POST /api/labs/import
    GET /api/templates
    GET, PUT, DELETE /api/labs/{lab_id}
    POST /api/labs/validate
    POST /api/labs/{lab_id}/duplicate
    POST /api/labs/{lab_id}/fix-secret
    POST /api/labs/{lab_id}/estimate

Conventions: raise `ApiError` for errors (never return error dicts); get shared state with
`st: StudioState = Depends(get_state)`; pass every string that can carry a secret through
`st.redact` / `st.redact.obj`; tests go in tests/ui/test_labs*.py using the `client` fixture.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["labs"])
