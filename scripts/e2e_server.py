"""Start Studio on a fresh seeded workspace for Playwright (ui/e2e).

    E2E_PORT=7871 E2E_TOKEN=e2e-token-0123456789 .venv/bin/python scripts/e2e_server.py

Env: E2E_PORT (default 7871), E2E_TOKEN (default e2e-token-0123456789), E2E_WORKSPACE (default a new
temp dir; it is wiped and re-seeded on every start), E2E_EMPTY_WORKSPACE=1 (no labs or runs, for
onboarding tests). Always sets DECIDER_LAB_FAKE_CLOUD=1 and a temp DECIDER_LAB_CACHE, and keeps a
fake System One server running on loopback so the seeded lab can be run again from the UI. Serves the
built SPA from src/decider_lab/ui/static (run `npm run build` in ui/ first; `npm run test:e2e` does).
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "tests", "ui"))
sys.path.insert(0, os.path.join(REPO, "src"))


def main() -> int:
    port = int(os.environ.get("E2E_PORT", "7871"))
    token = os.environ.get("E2E_TOKEN", "e2e-token-0123456789")
    os.environ["DECIDER_LAB_FAKE_CLOUD"] = "1"
    os.environ.setdefault("DECIDER_LAB_E2E_SECRET_TOKEN", "sk-e2e-secret-value-0000000000")
    base = tempfile.mkdtemp(prefix="decider-lab-e2e-")
    cache = os.path.join(base, "cache")
    os.environ["DECIDER_LAB_CACHE"] = cache
    ws = os.environ.get("E2E_WORKSPACE") or os.path.join(base, "ws")
    if os.path.exists(ws):
        shutil.rmtree(ws)

    from decider_lab import suites

    suites.CACHE = cache  # read at import; keep the seed and the server on the temp cache
    from ui_seed import build_seeded_workspace, start_fake_system_one

    url, _stop = start_fake_system_one()
    if os.environ.get("E2E_EMPTY_WORKSPACE") == "1":
        os.makedirs(ws)
    else:
        build_seeded_workspace(ws, url, cache_dir=cache)

    import uvicorn

    from decider_lab.ui.server import create_app

    app = create_app(ws, token, port=port, shutdown_wait_s=10)
    print(f"[e2e] Studio on http://127.0.0.1:{port}/?token={token}\n[e2e] workspace {ws}\n[e2e] System One {url}",
          flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    shutil.rmtree(base, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
