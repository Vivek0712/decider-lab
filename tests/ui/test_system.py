"""System endpoints: health, meta, doctor, about, settings, env (API.md 3, 4, 10)."""

from __future__ import annotations

import json


def test_meta(client, workspace):
    m = client.get("/api/meta").json()
    assert m["workspace"] == str(workspace.resolve())
    assert m["state_dir"].endswith(".decider-lab-studio")
    assert m["fake_cloud"] is True
    assert m["decider_lab_version"] == "0.1.0" and m["server_time"].endswith("Z")
    assert set(m["features"]) == {"vast_cli", "boto3", "strands_decider", "heldout_extra", "chess_extra", "nvidia_smi"}
    assert client.get("/api/system/meta").json()["workspace"] == m["workspace"]


def test_doctor_uses_fixtures_in_fake_mode(client, app):
    d = client.get("/api/system/doctor").json()
    items = {c["item"]: c for c in d["checks"]}
    assert "(fixtures)" in items["vast.ai"]["detail"] and "(fixtures)" in items["aws"]["detail"]
    assert items["decider-lab"]["status"] == "ok"
    assert d["counts"]["ok"] + d["counts"]["warn"] + d["counts"]["info"] == len(d["checks"])
    assert d["machine"]["cpus"] >= 1 and d["machine"]["python"]
    assert d["fake"] is True
    assert app.state.studio.settings.flag("doctor_seen") is True


def test_doctor_without_fake_cloud_skips_nothing(make_client, workspace, monkeypatch):
    from decider_lab import doctor

    calls = []
    monkeypatch.setattr(doctor, "checks", lambda cloud=True: calls.append(cloud) or [("ok", "decider-lab", "x")])
    c = make_client(workspace, fake_cloud=False)
    assert c.get("/api/system/doctor").json()["fake"] is False
    assert calls == [True]


def test_settings_roundtrip_and_validation(client):
    s = client.get("/api/settings").json()
    assert s["theme"] == "system" and s["max_concurrent_jobs"] == 2
    s = client.put("/api/settings", json={"theme": "light", "max_concurrent_jobs": 3}).json()
    assert s["theme"] == "light" and s["max_concurrent_jobs"] == 3
    assert client.get("/api/settings").json()["theme"] == "light"
    r = client.put("/api/settings", json={"theme": "pink", "max_concurrent_jobs": 99, "nope": 1})
    assert r.status_code == 422
    fields = {f["field"] for f in r.json()["error"]["detail"]["fields"]}
    assert fields == {"theme", "max_concurrent_jobs", "nope"}
    assert client.put("/api/settings", json={"runs_dir": "../outside"}).status_code == 400
    assert client.app.state.studio.jobs.max_concurrent == 3


def test_settings_env_lists_names_never_values(client, workspace, secret_value, monkeypatch):
    lab = workspace / "labs" / "chat" / "lab.yaml"
    lab.parent.mkdir()
    lab.write_text("name: chat-lab\nmodels:\n  gpt: {chat: gpt-4o, api_key_env: MY_CHAT_KEY}\n"
                   "compute: {env: [HF_TOKEN, EXTRA_NAME]}\n")
    monkeypatch.setenv("MY_CHAT_KEY", "chat-secret-value-123456")
    r = client.get("/api/settings/env?names=ADDED_ONE")
    items = {i["name"]: i for i in r.json()["items"]}
    assert items["MY_CHAT_KEY"] == {"name": "MY_CHAT_KEY", "set": True, "referenced_by": ["chat-lab"],
                                    "purpose": "referenced by api_key_env in a lab"}
    assert items["HF_TOKEN"]["referenced_by"] == ["chat-lab"]
    assert items["EXTRA_NAME"]["set"] is False and items["ADDED_ONE"]["purpose"] == "added by you"
    assert items["DECIDER_LAB_FAKE_CLOUD"]["set"] is True
    assert "chat-secret-value-123456" not in r.text and secret_value not in r.text
    assert client.get("/api/settings/env?names=bad-name").status_code == 422


def test_about(client):
    a = client.get("/api/about").json()
    assert a["telemetry"] == "none" and a["license"] == "Apache-2.0"


def test_no_response_leaks_secret_or_token(client, token, secret_value):
    for path in ("/api/meta", "/api/system/doctor", "/api/about", "/api/settings", "/api/settings/env"):
        body = client.get(path).text
        assert secret_value not in body and token not in body, path
    assert json.dumps(client.get("/api/meta").json())
