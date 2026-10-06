"""The Studio server: `create_app(workspace, token)` and `decider-lab ui`.

    app = create_app("/Users/me/labs", token="...")      # FastAPI app: /api/* + the built SPA
    uvicorn.run(app, host="127.0.0.1", port=7861)

The SPA is the Vite build in `decider_lab/ui/static` (built from ui/ at the repo root). Unknown
non-API paths fall back to index.html so client-side routes reload; unknown /api paths are JSON 404s.
"""

from __future__ import annotations

import argparse
import contextlib
import ipaddress
import os
import secrets
import socket
import sys
import threading
import webbrowser
from collections.abc import AsyncIterator, Iterable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, Response

from . import (
    api_compute,
    api_data,
    api_jobs,
    api_labs,
    api_models,
    api_overview,
    api_results,
    api_system,
)
from .auth import is_authenticated, login_page, make_middleware
from .cloud_fake import FakeCloud, fake_cloud_enabled
from .errors import ApiError, install
from .jobs import EventBus, JobManager
from .redact import Redactor
from .state import SettingsStore, StudioState
from .workspace import Workspace

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
DEFAULT_PORT = 7861
ROUTERS = [api_system.router, api_overview.router, api_labs.router, api_jobs.router, api_results.router,
           api_models.router, api_data.router, api_compute.router]

NOT_BUILT = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>decider-lab Studio</title></head>
<body style="font:14px system-ui;background:#06140E;color:#E8F2EC;padding:40px">
<h1 style="font-size:20px">decider-lab Studio</h1>
<p>The web UI is not built. From the repository: <code>cd ui &amp;&amp; npm ci &amp;&amp; npm run build</code>.
The API is running at <code>/api</code>.</p></body></html>"""


def create_app(workspace: str, token: str, *, port: int | None = None, host: str = "127.0.0.1",
               static_dir: str | None = None, fake_cloud: bool | None = None, max_concurrent: int | None = None,
               extra_hosts: Iterable[str] = (), cancel_jobs_on_shutdown: bool = True,
               shutdown_wait_s: float = 60.0, jobs_kwargs: dict[str, Any] | None = None) -> FastAPI:
    """The Studio app for `workspace`, guarded by `token`.

    port: the served port, for the Host check (None accepts any port on loopback names, as in tests).
    fake_cloud: default from DECIDER_LAB_FAKE_CLOUD. extra_hosts: Host names besides loopback ("*"
    disables the check; only for an explicit non-loopback bind).
    """
    if not token or len(token) < 8:
        raise ValueError("the Studio token must be at least 8 characters")
    ws = Workspace(workspace)
    ws.ensure_state_dir()
    redact = Redactor(extra=[token])
    settings = SettingsStore(ws)
    bus = EventBus()
    conc = max_concurrent or int(settings.get()["max_concurrent_jobs"])
    jobs = JobManager(ws.state_dir, redact, max_concurrent=conc, bus=bus, redact_extra=[token],
                      **(jobs_kwargs or {}))
    use_fake = fake_cloud_enabled() if fake_cloud is None else fake_cloud
    static = static_dir if static_dir is not None else STATIC_DIR
    st = StudioState(workspace=ws, token=token, redact=redact, jobs=jobs, bus=bus, settings=settings,
                     cloud=FakeCloud() if use_fake else None, port=port, host=host, static_dir=static)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        if cancel_jobs_on_shutdown and jobs.active_count():
            print(f"[studio] cancelling {jobs.active_count()} active job(s); waiting up to "
                  f"{int(shutdown_wait_s)} s so remote machines are released", flush=True)
        left = jobs.shutdown(cancel_active=cancel_jobs_on_shutdown, wait_s=shutdown_wait_s)
        for job_id in left:
            j = jobs.get(job_id)
            machine = f" (machine {j['machine_id']})" if j.get("machine_id") else ""
            print(f"[studio] WARNING: job {job_id} {j['title']} is still {j['status']}{machine}", flush=True)

    app = FastAPI(title="decider-lab Studio", version="0.1.0", docs_url=None, redoc_url=None,
                  openapi_url="/api/openapi.json", lifespan=lifespan)
    app.state.studio = st
    install(app, redact)
    app.middleware("http")(make_middleware(lambda: st.token, lambda: st.port, set(extra_hosts)))
    for r in ROUTERS:
        app.include_router(r)
    api_jobs.install(st)  # job tracker: log parsers, progress and telemetry sampler (labs/jobs area)

    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"], include_in_schema=False)
    def api_not_found(rest: str) -> Response:
        raise ApiError(404, "not_found", "No such API endpoint.", detail={"what": "route"})

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str, request: Request) -> Response:
        return serve_static(st, path, request)

    return app


def serve_static(st: StudioState, path: str, request: Request) -> Response:
    root = os.path.realpath(st.static_dir or STATIC_DIR)
    if path and path != "index.html":
        candidate = os.path.realpath(os.path.join(root, path))
        if candidate.startswith(root + os.sep) and os.path.isfile(candidate):
            immutable = path.startswith("assets/")
            return FileResponse(candidate, headers={
                "Cache-Control": "public, max-age=31536000, immutable" if immutable else "no-cache"})
        if path.startswith("assets/") or os.path.splitext(path)[1] in (".js", ".css", ".map", ".woff2", ".png",
                                                                          ".svg", ".ico", ".txt", ".json"):
            return Response("Not found", status_code=404, media_type="text/plain")
    ok, _ = is_authenticated(request, st.token)
    if not ok:
        return login_page()
    index = os.path.join(root, "index.html")
    if not os.path.isfile(index):
        return HTMLResponse(NOT_BUILT, headers={"Cache-Control": "no-store"})
    return FileResponse(index, headers={"Cache-Control": "no-store"})


# ---- `decider-lab ui` ----------------------------------------------------------------------------


def is_loopback(host: str) -> bool:
    if host in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def _free_port(host: str) -> int:
    with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET) as s:
        s.bind((host.strip("[]"), 0))
        return s.getsockname()[1]


def _port_taken(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host.strip("[]"), port))
        except OSError:
            return True
    return False


def cmd_ui(a: argparse.Namespace) -> int:
    try:
        import uvicorn  # noqa: F401
    except ImportError:
        print("decider-lab ui needs the ui extra: pip install 'decider-lab[ui]'", file=sys.stderr)
        return 2
    host = a.host
    if not is_loopback(host) and not a.token:
        print(f"Studio binds to loopback only; to bind {host}, pass --token explicitly (anyone who can reach "
              "this address and has the token can run jobs on this machine).", file=sys.stderr)
        return 2
    if not os.path.isdir(a.workspace):
        print(f"workspace {a.workspace} is not a directory", file=sys.stderr)
        return 2
    token = a.token or os.environ.get("DECIDER_LAB_UI_TOKEN") or secrets.token_urlsafe(32)
    if len(token) < 8:
        print("the token must be at least 8 characters", file=sys.stderr)
        return 2
    port = a.port or _free_port(host)
    if _port_taken(host, port):
        print(f"port {port} on {host} is in use; pick another with --port (0 picks a free one)", file=sys.stderr)
        return 2
    extra = set() if is_loopback(host) else ({"*"} if host in ("0.0.0.0", "::") else {host.lower()})
    app = create_app(a.workspace, token, port=port, host=host, extra_hosts=extra)
    shown = "127.0.0.1" if host in ("0.0.0.0", "::") else (f"[{host}]" if ":" in host else host)
    url = f"http://{shown}:{port}/?token={token}"
    print(f"decider-lab Studio for {os.path.abspath(a.workspace)}\n\n    {url}\n\nOpen this link; it carries the "
          "access token. Press Ctrl+C to stop (active jobs are cancelled first).", flush=True)
    if not a.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    import uvicorn

    uvicorn.run(app, host=host.strip("[]"), port=port, log_level="warning", access_log=False)
    return 0
