"""Auth, request guards and security headers (API.md 1.2)."""

from __future__ import annotations

from decider_lab.ui.auth import COOKIE, host_allowed, session_value


def test_health_is_public(anon_client):
    assert anon_client.get("/api/health").json() == {"ok": True}
    assert anon_client.get("/api/system/health").json() == {"ok": True}


def test_api_requires_token(anon_client):
    r = anon_client.get("/api/meta")
    assert r.status_code == 401
    err = r.json()["error"]
    assert err["code"] == "unauthorized" and err["request_id"] == r.headers["X-Request-Id"]


def test_all_token_forms_work(anon_client, token):
    assert anon_client.get("/api/meta", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert anon_client.get("/api/meta", headers={"X-Decider-Lab-Token": token}).status_code == 200
    assert anon_client.get(f"/api/meta?token={token}").status_code == 200  # for EventSource
    assert anon_client.get("/api/meta", headers={"Authorization": "Bearer wrong-token"}).status_code == 401
    assert anon_client.get("/api/meta?token=wrong").status_code == 401


def test_query_token_not_accepted_for_writes(anon_client, token):
    r = anon_client.put(f"/api/settings?token={token}", json={"theme": "dark"}, headers={"X-Studio": "1"})
    assert r.status_code == 401


def test_root_without_token_shows_open_the_link_page(anon_client):
    r = anon_client.get("/")
    assert r.status_code == 401
    assert "printed in your terminal" in r.text and "decider-lab ui" in r.text
    assert anon_client.get("/labs/some/deep/link").status_code == 401


def test_first_load_sets_httponly_cookie_and_strips_token(anon_client, token):
    r = anon_client.get(f"/jobs?tab=logs&token={token}", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/jobs?tab=logs"
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(f"{COOKIE}={session_value(token)}")
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie.replace("Strict", "strict")
    assert token not in cookie
    # the cookie now authenticates both the SPA and the API
    assert anon_client.get("/").status_code == 200
    assert anon_client.get("/api/meta").status_code == 200


def test_wrong_link_token_is_refused(anon_client):
    r = anon_client.get("/?token=nope", follow_redirects=False)
    assert r.status_code == 401 and "not valid" in r.text


def test_cookie_writes_need_x_studio_and_same_origin(anon_client, token):
    anon_client.get(f"/?token={token}")
    r = anon_client.put("/api/settings", json={"theme": "dark"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden_origin"
    r = anon_client.put("/api/settings", json={"theme": "dark"},
                        headers={"X-Studio": "1", "Origin": "http://evil.example"})
    assert r.status_code == 403
    r = anon_client.put("/api/settings", json={"theme": "dark"},
                        headers={"X-Studio": "1", "Origin": "http://127.0.0.1:7861"})
    assert r.status_code == 200 and r.json()["theme"] == "dark"


def test_bearer_writes_still_need_x_studio(anon_client, token):
    r = anon_client.put("/api/settings", json={"theme": "light"}, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_bad_host_is_misdirected(client):
    r = client.get("/api/meta", headers={"Host": "evil.example:7861"})
    assert r.status_code == 421 and r.json()["error"]["code"] == "misdirected"


def test_host_allowed_rules():
    assert host_allowed("127.0.0.1:7861", 7861, set())
    assert host_allowed("localhost:7861", 7861, set())
    assert host_allowed("[::1]:7861", 7861, set())
    assert not host_allowed("127.0.0.1:9999", 7861, set())
    assert not host_allowed("attacker.example:7861", 7861, set())
    assert host_allowed("127.0.0.1:1234", None, set())
    assert host_allowed("10.0.0.5:7861", 7861, {"10.0.0.5"})
    assert not host_allowed(None, 7861, set())


def test_security_headers_on_every_response(client, anon_client):
    for r in (client.get("/api/meta"), anon_client.get("/"), anon_client.get("/api/meta"), client.get("/api/nope")):
        assert "default-src 'self'" in r.headers["Content-Security-Policy"]
        assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
        assert r.headers["Referrer-Policy"] == "no-referrer"
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["X-Frame-Options"] == "DENY"
        assert len(r.headers["X-Request-Id"]) == 32
        assert "access-control-allow-origin" not in r.headers


def test_api_gets_are_not_cached(client):
    assert client.get("/api/meta").headers["Cache-Control"] == "no-store"


def test_unknown_api_route_is_json_404(client):
    r = client.post("/api/does/not/exist", json={})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"
