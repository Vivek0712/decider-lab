"""Compute API: Doctor, vast.ai, AWS and SSH hosts (API.md section 10). With DECIDER_LAB_FAKE_CLOUD=1 use
`st.cloud` (FakeCloud) and never call vastai or boto3.

Owned endpoints (all under the `/api` prefix this router registers):

    GET /api/compute/doctor (reuse `api_system.doctor_report(st)`)
    GET /api/compute/vast/status|offers|instances
    POST /api/compute/vast/instances/{id}/destroy
    POST /api/compute/vast/instances/destroy-all
    GET /api/compute/aws/profiles|identity|quotas|instance-types|instances|bedrock-models
    POST /api/compute/aws/instances/{id}/terminate
    POST /api/compute/aws/instances/terminate-all
    GET, POST /api/compute/ssh/hosts
    PUT, DELETE /api/compute/ssh/hosts/{host_id}
    POST /api/compute/ssh/hosts/{host_id}/test

Conventions: raise `ApiError` for errors (never return error dicts); get shared state with
`st: StudioState = Depends(get_state)`; pass every string that can carry a secret through
`st.redact` / `st.redact.obj`; tests go in tests/ui/test_compute*.py using the `client` fixture.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["compute"])
