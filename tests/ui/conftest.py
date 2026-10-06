"""Fixtures for Studio backend tests (tests/ui/).

    def test_something(client, workspace):          # authed TestClient on a fresh copy of the seed
        r = client.get("/api/meta")

    seeded_template   session: the seeded workspace, built once (see ui_seed.py for its layout)
    system_one_url    session: URL of the fake System One server the seeded lab points at
    workspace         a fresh copy of the seeded workspace (pathlib.Path); mutate freely
    empty_workspace   an empty directory (pathlib.Path)
    token             the test token
    app               create_app(workspace, token) with DECIDER_LAB_FAKE_CLOUD=1 and a temp cache
    client            TestClient(app) with Authorization: Bearer <token> and X-Studio: 1
    anon_client       the same app with no credentials
    make_client       factory: make_client(ws_path, **create_app_kwargs) -> authed TestClient
    secret_value      a secret set in the server env (DECIDER_LAB_TEST_SECRET_TOKEN); assert it never
                      appears in responses

Every test runs with DECIDER_LAB_FAKE_CLOUD=1, DECIDER_LAB_CACHE in a temp dir and no network
except loopback.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import sys

import pytest

pytest.importorskip("fastapi", reason="Studio tests need the ui extra: pip install -e '.[ui,dev]'")
sys.path.insert(0, os.path.dirname(__file__))

from ui_seed import build_seeded_workspace, start_fake_system_one  # noqa: E402

TOKEN = "studio-test-token-0123456789"
SECRET = "sk-studio-test-secret-value-00000000"
BASE_URL = "http://127.0.0.1:7861"


@pytest.fixture(scope="session")
def system_one_url():
    url, stop = start_fake_system_one()
    yield url
    stop()


@pytest.fixture(scope="session")
def seeded_template(tmp_path_factory, system_one_url) -> pathlib.Path:
    base = tmp_path_factory.mktemp("seeded")
    ws = base / "ws"
    build_seeded_workspace(str(ws), system_one_url, cache_dir=str(base / "cache"))
    return ws


@pytest.fixture
def workspace(seeded_template, tmp_path) -> pathlib.Path:
    dest = tmp_path / "ws"
    shutil.copytree(seeded_template, dest, ignore=shutil.ignore_patterns(".decider-lab-studio"))
    return dest


@pytest.fixture
def empty_workspace(tmp_path) -> pathlib.Path:
    d = tmp_path / "empty-ws"
    d.mkdir()
    return d


@pytest.fixture
def token() -> str:
    return TOKEN


@pytest.fixture(autouse=True)
def studio_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DECIDER_LAB_FAKE_CLOUD", "1")
    monkeypatch.setenv("DECIDER_LAB_CACHE", str(tmp_path / "dl-cache"))
    monkeypatch.setenv("DECIDER_LAB_TEST_SECRET_TOKEN", SECRET)
    monkeypatch.delenv("DECIDER_LAB_UI_TOKEN", raising=False)


@pytest.fixture
def secret_value() -> str:
    return SECRET


@pytest.fixture
def make_client():
    from fastapi.testclient import TestClient

    from decider_lab.ui.server import create_app

    made = []

    def make(ws, *, authed: bool = True, **kwargs):
        kwargs.setdefault("shutdown_wait_s", 10)
        app = create_app(str(ws), TOKEN, **kwargs)
        headers = {"Authorization": f"Bearer {TOKEN}", "X-Studio": "1"} if authed else {}
        c = TestClient(app, base_url=BASE_URL, headers=headers)
        made.append(c)
        return c

    yield make
    for c in made:
        c.app.state.studio.jobs.shutdown(cancel_active=True, wait_s=10)


@pytest.fixture
def app(make_client, workspace):
    return make_client(workspace).app


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient

    return TestClient(app, base_url=BASE_URL, headers={"Authorization": f"Bearer {TOKEN}", "X-Studio": "1"})


@pytest.fixture
def anon_client(app):
    from fastapi.testclient import TestClient

    return TestClient(app, base_url=BASE_URL)
