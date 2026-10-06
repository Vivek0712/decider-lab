"""Models API: The model cache and source inspection (API.md section 8). Pulls are jobs (`kind: pull`).

Owned endpoints (all under the `/api` prefix this router registers):

    GET /api/models
    POST /api/models/inspect
    DELETE /api/models/{model_key}

Conventions: raise `ApiError` for errors (never return error dicts); get shared state with
`st: StudioState = Depends(get_state)`; pass every string that can carry a secret through
`st.redact` / `st.redact.obj`; tests go in tests/ui/test_models*.py using the `client` fixture.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["models"])
