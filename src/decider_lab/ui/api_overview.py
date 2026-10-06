"""Overview API: KPIs, active jobs, recent results, onboarding (API.md section 3).

Owned endpoints (all under the `/api` prefix this router registers):

    GET /api/overview

Conventions: raise `ApiError` for errors (never return error dicts); get shared state with
`st: StudioState = Depends(get_state)`; pass every string that can carry a secret through
`st.redact` / `st.redact.obj`; tests go in tests/ui/test_overview*.py using the `client` fixture.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["overview"])
