"""Token auth like Jupyter, plus the request guards of API.md section 1.2.

- `/?token=<t>` (any non-API path) with the right token sets the HttpOnly cookie `dl_session` and
  redirects to the same path without the query, so the token leaves the address bar at once.
- `/api/*` needs one of: the cookie, `Authorization: Bearer <t>`, `X-Decider-Lab-Token: <t>`, or
  (GET only, for EventSource in dev) `?token=<t>`. Public: `/api/health`, `/api/system/health`.
- The SPA (`/`, `/labs`, ...) without a session shows a short page that says to open the link
  printed in the terminal. Static files (`/assets/*`, fonts, icons) need no auth: they hold no data.
- Host must be loopback with the server's port (DNS rebinding), else 421. Non-GET API requests must
  carry `X-Studio: 1` and, when `Origin` is sent, it must be this server's origin, else 403.
- Every response gets the security headers and an `X-Request-Id`.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from collections.abc import Awaitable, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit

from fastapi import Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from .errors import error_response

COOKIE = "dl_session"
TOKEN_HEADER = "X-Decider-Lab-Token"
PUBLIC_API = {"/api/health", "/api/system/health"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
LOOPBACK_NAMES = {"127.0.0.1", "localhost", "[::1]", "::1"}
SECURITY_HEADERS = {
    "Content-Security-Policy": ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                                "font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
                                "base-uri 'self'; form-action 'self'"),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


def session_value(token: str) -> str:
    """The cookie value: an HMAC of the token, so the token itself is never stored in the browser."""
    return hmac.new(token.encode(), b"decider-lab-studio-session-v1", hashlib.sha256).hexdigest()


def _eq(a: str | None, b: str) -> bool:
    return bool(a) and hmac.compare_digest(a.encode(), b.encode())


def is_authenticated(request: Request, token: str, *, allow_query: bool = False) -> tuple[bool, str | None]:
    """(ok, how): how is "cookie", "bearer", "header" or "query"."""
    if _eq(request.cookies.get(COOKIE), session_value(token)):
        return True, "cookie"
    auth = request.headers.get("authorization", "")
    if auth[:7].lower() == "bearer " and _eq(auth[7:].strip(), token):
        return True, "bearer"
    if _eq(request.headers.get(TOKEN_HEADER), token):
        return True, "header"
    if allow_query and _eq(request.query_params.get("token"), token):
        return True, "query"
    return False, None


def host_allowed(host_header: str | None, port: int | None, extra_hosts: set[str]) -> bool:
    """Loopback name with the server's port (any port when the port is not known, as in tests)."""
    if not host_header:
        return False
    host = host_header.strip().lower()
    if "*" in extra_hosts:
        return True
    if host.startswith("["):
        name, _, rest = host[1:].partition("]")
        name = f"[{name}]"
        hport = rest[1:] if rest.startswith(":") else None
    else:
        name, _, hport = host.partition(":")
        hport = hport or None
    if name not in LOOPBACK_NAMES and name not in extra_hosts:
        return False
    if port is None or hport is None:
        return True
    return hport == str(port)


def login_page(status: int = 401, reason: str | None = None) -> HTMLResponse:
    msg = reason or "Open the link printed in your terminal by <code>decider-lab ui</code>. It carries a one-time token."
    body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>decider-lab Studio</title>
<style>
:root{{color-scheme:dark light;--bg:#06140E;--s:#0B1F16;--t:#E8F2EC;--m:#A3BFB0;--a:#2FDC85;--b:#1F4030}}
@media (prefers-color-scheme: light){{:root{{--bg:#F5F8F4;--s:#FFFFFF;--t:#06140E;--m:#3D5747;--a:#0B7A45;--b:#D3DFD6}}}}
html,body{{margin:0;background:var(--bg);color:var(--t);font:14px/22px system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:520px;margin:12vh auto;padding:0 16px}}
.card{{background:var(--s);border:1px solid var(--b);border-radius:14px;padding:28px}}
h1{{font-size:20px;line-height:28px;margin:0 0 8px}} p{{color:var(--m);margin:8px 0}}
code{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px;color:var(--t)}}
.mark{{display:inline-block;width:28px;height:28px;border-radius:8px;background:#0B7A45;position:relative;vertical-align:middle;margin-right:10px}}
.mark:after{{content:"";position:absolute;left:9px;top:9px;width:10px;height:10px;background:var(--a);transform:rotate(45deg)}}
</style></head>
<body><main><div class="card" data-testid="login-page">
<h1><span class="mark" aria-hidden="true"></span>decider-lab Studio</h1>
<p>{msg}</p>
<p>Lost it? Stop the server and start it again: <code>decider-lab ui</code> prints a new link.</p>
</div></main></body></html>"""
    return HTMLResponse(body, status_code=status, headers={"Cache-Control": "no-store"})


def _strip_token(url: str) -> str:
    parts = urlsplit(url)
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "token"])
    return parts.path + (f"?{query}" if query else "")


def make_middleware(get_token: Callable[[], str], get_port: Callable[[], int | None],
                    extra_hosts: set[str]) -> Callable[[Request, Callable[[Request], Awaitable[Response]]],
                                                       Awaitable[Response]]:
    async def middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        rid = uuid.uuid4().hex
        request.state.request_id = rid
        token = get_token()
        path = request.url.path
        resp: Response
        if not host_allowed(request.headers.get("host"), get_port(), extra_hosts):
            resp = error_response(request, 421, "misdirected", "This server only answers on its loopback address.")
        elif path == "/api" or path.startswith("/api/"):
            resp = await _api(request, call_next, token)
        else:
            q = request.query_params.get("token")
            if q is not None and request.method in SAFE_METHODS:
                if _eq(q, token):
                    resp = RedirectResponse(_strip_token(str(request.url.path) + (
                        f"?{request.url.query}" if request.url.query else "")), status_code=302)
                    resp.set_cookie(COOKIE, session_value(token), httponly=True, samesite="strict", path="/")
                else:
                    resp = login_page(401, "This link's token is not valid for this server. Open the link printed "
                                           "in your terminal by <code>decider-lab ui</code>.")
            else:
                resp = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            resp.headers.setdefault(k, v)
        resp.headers["X-Request-Id"] = rid
        return resp

    return middleware


async def _api(request: Request, call_next: Callable[[Request], Awaitable[Response]], token: str) -> Response:
    path = request.url.path
    if path not in PUBLIC_API:
        ok, _how = is_authenticated(request, token, allow_query=request.method in SAFE_METHODS)
        if not ok:
            return error_response(request, 401, "unauthorized", "This request needs the Studio session.",
                                  hint="Open the link printed in your terminal by `decider-lab ui`.")
        if request.method not in SAFE_METHODS:
            if request.headers.get("x-studio") != "1":
                return error_response(request, 403, "forbidden_origin", "This request was refused.",
                                      hint="Send the header X-Studio: 1.")
            origin = request.headers.get("origin")
            if origin and origin != "null":
                o = urlsplit(origin)
                if o.netloc.lower() != (request.headers.get("host") or "").lower():
                    return error_response(request, 403, "forbidden_origin", "This request came from another site.")
            elif origin == "null":
                return error_response(request, 403, "forbidden_origin", "This request came from another site.")
    resp = await call_next(request)
    if request.method in SAFE_METHODS and "cache-control" not in resp.headers:
        resp.headers["Cache-Control"] = "no-store"
    return resp
