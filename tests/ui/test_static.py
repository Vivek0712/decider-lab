"""The built SPA: static files without auth, index.html fallback behind the session."""

from __future__ import annotations

import pytest


@pytest.fixture
def static_dir(tmp_path):
    d = tmp_path / "static"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<!doctype html><title>Studio</title><div id=root></div>")
    (d / "assets" / "app-abc123.js").write_text("console.log('studio')")
    (d / "theme-init.js").write_text("// theme")
    return d


def test_assets_are_public_and_immutable(make_client, workspace, static_dir):
    c = make_client(workspace, authed=False, static_dir=str(static_dir))
    r = c.get("/assets/app-abc123.js")
    assert r.status_code == 200 and "studio" in r.text
    assert "immutable" in r.headers["Cache-Control"]
    assert c.get("/theme-init.js").status_code == 200
    assert c.get("/assets/missing.js").status_code == 404


def test_spa_fallback_needs_session(make_client, workspace, static_dir, token):
    c = make_client(workspace, authed=False, static_dir=str(static_dir))
    assert c.get("/results/abc/model/suite").status_code == 401
    c.get(f"/?token={token}")
    for path in ("/", "/labs", "/results/abc/model/suite", "/index.html"):
        r = c.get(path)
        assert r.status_code == 200 and "<div id=root>" in r.text, path
        assert r.headers["Cache-Control"] == "no-store"


def test_static_paths_cannot_escape(make_client, workspace, static_dir, token):
    c = make_client(workspace, authed=False, static_dir=str(static_dir))
    c.get(f"/?token={token}")
    r = c.get("/..%2F..%2Fpyproject.toml")
    assert "[project]" not in r.text


def test_unbuilt_ui_explains_itself(make_client, workspace, tmp_path, token):
    c = make_client(workspace, authed=False, static_dir=str(tmp_path / "nothing"))
    c.get(f"/?token={token}")
    r = c.get("/")
    assert r.status_code == 200 and "npm run build" in r.text
