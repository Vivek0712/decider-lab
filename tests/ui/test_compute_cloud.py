"""Compute endpoints: doctor, vast.ai, AWS, SSH hosts (API.md section 10).

Fake-cloud tests use the fixtures (DECIDER_LAB_FAKE_CLOUD=1, set for every test by conftest).
Real-mode tests (`fake_cloud=False`) replace the boto3 session, the vast module and ssh with
in-process fakes, so nothing here touches the network or a cloud account.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import time

import pytest

from decider_lab.ui import api_compute

SAMPLE_SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY00"


# ---- doctor ------------------------------------------------------------------------------------


def test_compute_doctor_uses_fixtures_and_marks_seen(client, app):
    d = client.get("/api/compute/doctor").json()
    items = {c["item"].strip(): c for c in d["checks"]}
    assert "(fixtures)" in items["vast.ai"]["detail"]
    assert "(fixtures)" in items["aws"]["detail"]
    assert {"torch", "strands-decider", "decider-lab"} <= set(items)
    assert d["counts"]["ok"] + d["counts"]["warn"] + d["counts"]["info"] == len(d["checks"])
    assert d["machine"]["cpus"] >= 1 and d["machine"]["accelerator"]
    assert d["fake"] is True
    assert app.state.studio.settings.flag("doctor_seen") is True


# ---- vast.ai (fixtures) ------------------------------------------------------------------------


def test_vast_status_and_offers(client):
    s = client.get("/api/compute/vast/status").json()
    assert s == {"fake": True, "cli": True, "api_key": True, "credit_usd": 25.4, "as_of": s["as_of"], "error": None}
    o = client.get("/api/compute/vast/offers", params={"gpu": "RTX_4090", "max_price": 0.8}).json()
    assert o["fake"] is True and o["credit_usd"] == 25.4 and "A100_SXM4" in o["gpu_names"]
    prices = [x["dph_total"] for x in o["items"]]
    assert prices == sorted(prices) and all(p <= 0.8 for p in prices)
    assert {x["gpu_name"] for x in o["items"]} == {"RTX_4090"}
    two = client.get("/api/compute/vast/offers", params={"gpu": "RTX_4090", "num_gpus": 2, "max_price": 1}).json()
    assert [x["id"] for x in two["items"]] == [1234503]
    assert client.get("/api/compute/vast/offers", params={"gpu": "H100_SXM", "max_price": 0.5}).json()["items"] == []


@pytest.mark.parametrize("params", [{"gpu": "RTX 4090; rm -rf /"}, {"num_gpus": 0}, {"max_price": -1},
                                    {"disk_gb": 1}])
def test_vast_offers_validation(client, params):
    r = client.get("/api/compute/vast/offers", params=params)
    assert r.status_code == 422 and r.json()["error"]["code"] == "bad_request"


def test_vast_instances_are_idle_without_a_job(client):
    body = client.get("/api/compute/vast/instances").json()
    (it,) = body["items"]
    assert it["id"] == 9876543 and it["label"].startswith("decider-lab")
    assert it["idle"] is True and it["job_id"] is None and it["job_title"] is None
    assert it["uptime_s"] >= 0 and it["cost_so_far_usd"] >= 0
    assert body["usd_per_hour"] == pytest.approx(0.612)


def test_vast_destroy_needs_the_typed_id(client):
    for body in ({}, {"confirm": "destroy"}, {"confirm": "987654"}):
        r = client.post("/api/compute/vast/instances/9876543/destroy", json=body)
        assert r.status_code == 409
        err = r.json()["error"]
        assert err["code"] == "confirm_mismatch" and err["detail"]["expected_hint"] == "type the instance id"
    assert len(client.get("/api/compute/vast/instances").json()["items"]) == 1
    r = client.post("/api/compute/vast/instances/1111111/destroy", json={"confirm": "1111111"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert client.post("/api/compute/vast/instances/abc/destroy", json={"confirm": "abc"}).status_code == 404
    r = client.post("/api/compute/vast/instances/9876543/destroy", json={"confirm": "9876543"})
    assert r.status_code == 200 and r.json() == {"destroyed": True}
    assert client.get("/api/compute/vast/instances").json()["items"] == []


def test_vast_destroy_all(make_client, workspace):
    c = make_client(workspace)
    r = c.post("/api/compute/vast/instances/destroy-all", json={"confirm": "destroy everything"})
    assert r.status_code == 409 and r.json()["error"]["detail"]["expected_hint"] == "type `destroy all`"
    r = c.post("/api/compute/vast/instances/destroy-all", json={"confirm": "destroy all"})
    assert r.json() == {"results": [{"id": 9876543, "destroyed": True}]}
    assert c.get("/api/compute/vast/instances").json()["items"] == []


def test_destroy_requires_x_studio_and_auth(app, anon_client, token):
    from fastapi.testclient import TestClient

    c = TestClient(app, base_url="http://127.0.0.1:7861", headers={"Authorization": f"Bearer {token}"})
    r = c.post("/api/compute/vast/instances/9876543/destroy", json={"confirm": "9876543"})
    assert r.status_code == 403
    assert anon_client.get("/api/compute/vast/instances").status_code == 401


def _sleep_job(st, backend: str, text: str) -> str:
    code = f"import time,sys; print({text!r}, flush=True); time.sleep(30)"
    job = st.jobs.start("run", [sys.executable, "-c", code], title="run first-lab", cwd=str(st.workspace.root),
                        backend=backend)
    return job["job_id"]


def _wait_log(st, job_id: str, needle: str) -> None:
    deadline = time.time() + 15
    while time.time() < deadline:
        if needle in st.jobs.log_text(job_id):
            return
        time.sleep(0.1)
    raise AssertionError(f"log never showed {needle!r}")


def test_instance_owned_by_an_active_job_is_not_idle(client, app):
    st = app.state.studio
    job_id = _sleep_job(st, "vast", "[vast] instance 9876543; waiting for it to boot (image pull)")
    try:
        _wait_log(st, job_id, "9876543")
        (it,) = client.get("/api/compute/vast/instances").json()["items"]
        assert it["job_id"] == job_id and it["idle"] is False and it["job_title"] == "run first-lab"
        # an aws job does not own a vast machine
        (ai,) = client.get("/api/compute/aws/instances").json()["items"]
        assert ai["idle"] is True
    finally:
        st.jobs.cancel(job_id)
        st.jobs.wait(job_id, timeout=30)
    (it,) = client.get("/api/compute/vast/instances").json()["items"]
    assert it["idle"] is True and it["job_id"] is None


def test_machine_id_marks_the_owner(client, app):
    st = app.state.studio
    job_id = _sleep_job(st, "aws", "[aws] starting")
    try:
        st.jobs.update(job_id, machine_id="i-0abc123def4567890")
        (it,) = client.get("/api/compute/aws/instances").json()["items"]
        assert it["job_id"] == job_id and it["idle"] is False
    finally:
        st.jobs.cancel(job_id)
        st.jobs.wait(job_id, timeout=30)


# ---- AWS (fixtures) ----------------------------------------------------------------------------


def test_aws_profiles_identity_quotas(client):
    p = client.get("/api/compute/aws/profiles").json()
    assert p["fake"] is True and "heisenberg" in p["items"] and p["boto3"] is True
    assert "us-east-1" in p["regions"] and p["default_region"]
    i = client.get("/api/compute/aws/identity", params={"profile": "heisenberg", "region": "us-west-2"}).json()
    assert i["ok"] is True and i["account"] == "123456789012" and i["arn"].startswith("arn:aws:sts::")
    assert i["profile"] == "heisenberg" and i["region"] == "us-west-2"
    q = client.get("/api/compute/aws/quotas", params={"profile": "heisenberg"}).json()
    fams = {x["family"]: x for x in q["items"]}
    assert set(fams) == {"g", "p", "standard"}
    assert fams["g"]["code"] == "L-DB2E81BA" and fams["p"]["limit_vcpus"] == 0 and fams["standard"]["used_vcpus"] == 2


@pytest.mark.parametrize("params", [{"region": "mars-1"}, {"profile": "bad profile"}, {"profile": "$(id)"},
                                    {"region": "us-east-1; ls"}])
def test_aws_params_are_validated(client, params):
    for path in ("identity", "quotas", "instances", "bedrock-models"):
        r = client.get(f"/api/compute/aws/{path}", params=params)
        assert r.status_code == 422, path


def test_aws_instance_types_static_table(client):
    t = client.get("/api/compute/aws/instance-types").json()
    by = {x["type"]: x for x in t["items"]}
    assert by["g6e.xlarge"]["usd_per_hour"] == 1.861 and by["g6e.xlarge"]["family"] == "g"
    assert t["as_of"] and "approximate" in t["region_note"]
    assert api_compute.aws_price("g6e.xlarge") == 1.861 and api_compute.aws_price("zz.huge") is None


def test_aws_instances_and_terminate(client):
    body = client.get("/api/compute/aws/instances").json()
    (it,) = body["items"]
    assert it["id"] == "i-0abc123def4567890" and it["idle"] is True and it["usd_per_hour"] == 1.861
    assert it["public_ip_masked"].endswith(".x.x")
    assert body["usd_per_hour"] == pytest.approx(1.861)
    path = "/api/compute/aws/instances/i-0abc123def4567890/terminate"
    for confirm in (None, "i-0abc", "terminate"):
        r = client.post(path, json={"confirm": confirm, "profile": "heisenberg", "region": "us-east-1"})
        assert r.status_code == 409 and r.json()["error"]["code"] == "confirm_mismatch"
    r = client.post("/api/compute/aws/instances/i-0000000000000000a/terminate", json={"confirm": "i-0000000000000000a"})
    assert r.status_code == 404
    assert client.post("/api/compute/aws/instances/sg-123/terminate", json={"confirm": "sg-123"}).status_code == 404
    r = client.post(path, json={"confirm": "i-0abc123def4567890", "profile": "heisenberg", "region": "us-east-1"})
    assert r.status_code == 200 and r.json() == {"terminating": ["i-0abc123def4567890"]}
    assert client.get("/api/compute/aws/instances").json()["items"] == []


def test_aws_terminate_all(make_client, workspace):
    c = make_client(workspace)
    r = c.post("/api/compute/aws/instances/terminate-all", json={"confirm": "terminate"})
    assert r.status_code == 409 and r.json()["error"]["detail"]["expected_hint"] == "type `terminate all`"
    r = c.post("/api/compute/aws/instances/terminate-all", json={"confirm": "terminate all", "region": "us-east-1"})
    assert r.json() == {"terminating": ["i-0abc123def4567890"]}


def test_bedrock_models_fixture_and_search(client):
    b = client.get("/api/compute/aws/bedrock-models", params={"region": "us-west-2"}).json()
    assert b["error"] is None and len(b["items"]) >= 5
    nova = next(m for m in b["items"] if m["model_id"] == "amazon.nova-pro-v1:0")
    assert nova["invoke_id"] == "us.amazon.nova-pro-v1:0"
    assert nova["spec_yaml"] == "{bedrock: us.amazon.nova-pro-v1:0, region: us-west-2}"
    q = client.get("/api/compute/aws/bedrock-models", params={"q": "llama"}).json()
    assert [m["provider"] for m in q["items"]] == ["Meta"]


def test_cloud_summary_fake(app):
    s = api_compute.cloud_summary(app.state.studio)
    assert s == {"instances": 2, "idle_instances": 2, "usd_per_hour": pytest.approx(0.612 + 1.861),
                 "known": True, "errors": []}


# ---- SSH hosts ---------------------------------------------------------------------------------


def test_ssh_hosts_crud_and_storage(client, workspace, tmp_path):
    key = tmp_path / "id_test"
    key.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\n" + SAMPLE_SECRET + "\n")
    assert client.get("/api/compute/ssh/hosts").json() == {"items": []}
    r = client.post("/api/compute/ssh/hosts", json={"name": "gpu-box", "target": "ubuntu@10.0.0.5:2222",
                                                    "key_path": str(key), "work_dir": None})
    assert r.status_code == 201
    h = r.json()
    assert h["host_id"].startswith("h_") and len(h["host_id"]) == 10
    assert (h["user"], h["address"], h["port"], h["key_exists"]) == ("ubuntu", "10.0.0.5", 2222, True)
    assert h["last_test"] is None
    assert SAMPLE_SECRET not in r.text
    stored = workspace / ".decider-lab-studio" / "ssh_hosts.json"
    data = json.loads(stored.read_text())
    assert data["hosts"][0]["name"] == "gpu-box" and SAMPLE_SECRET not in stored.read_text()

    dup = client.post("/api/compute/ssh/hosts", json={"name": "GPU-box", "target": "a@b"})
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "name_taken"

    r = client.put(f"/api/compute/ssh/hosts/{h['host_id']}",
                   json={"name": "gpu-box-2", "target": "root@rig.local", "key_path": "~/.ssh/none-here",
                         "work_dir": "/data/work"})
    assert r.status_code == 200
    u = r.json()
    assert (u["name"], u["port"], u["key_exists"], u["work_dir"]) == ("gpu-box-2", 22, False, "/data/work")
    assert [x["name"] for x in client.get("/api/compute/ssh/hosts").json()["items"]] == ["gpu-box-2"]

    assert client.delete(f"/api/compute/ssh/hosts/{h['host_id']}").json() == {"deleted": True}
    assert client.delete(f"/api/compute/ssh/hosts/{h['host_id']}").status_code == 404
    assert client.put("/api/compute/ssh/hosts/h_00000000", json={"name": "x", "target": "a@b"}).status_code == 404
    assert client.delete("/api/compute/ssh/hosts/../../etc").status_code == 404


@pytest.mark.parametrize("body,field", [
    ({"name": "", "target": "a@b"}, "name"),
    ({"name": "-x", "target": "a@b"}, "name"),
    ({"name": "ok", "target": "no-at-sign"}, "target"),
    ({"name": "ok", "target": "a@b;rm -rf /"}, "target"),
    ({"name": "ok", "target": "a@b c"}, "target"),
    ({"name": "ok", "target": "-oProxyCommand@host"}, "target"),
    ({"name": "ok", "target": "a@-host"}, "target"),
    ({"name": "ok", "target": "a@b:70000"}, "target"),
    ({"name": "ok", "target": "a@b", "key_path": "a\nb"}, "key_path"),
    ({"name": "ok", "target": "a@b", "work_dir": "/x; rm"}, "work_dir"),
])
def test_ssh_host_validation(client, body, field):
    r = client.post("/api/compute/ssh/hosts", json=body)
    assert r.status_code == 422
    assert field in [f["field"] for f in r.json()["error"]["detail"]["fields"]]


def test_ssh_test_fake_ok_and_invalid(client):
    ok = client.post("/api/compute/ssh/hosts", json={"name": "gpu-box", "target": "ubuntu@10.0.0.5"}).json()
    bad = client.post("/api/compute/ssh/hosts", json={"name": "old-rig", "target": "root@rig.invalid"}).json()
    r = client.post(f"/api/compute/ssh/hosts/{ok['host_id']}/test").json()
    assert r["ok"] is True and r["latency_ms"] == 42 and "L40S" in r["gpu"] and r["at"].endswith("Z")
    r = client.post(f"/api/compute/ssh/hosts/{bad['host_id']}/test").json()
    assert r["ok"] is False and "timed out" in r["error"] and r["latency_ms"] is None
    hosts = {h["name"]: h for h in client.get("/api/compute/ssh/hosts").json()["items"]}
    assert hosts["gpu-box"]["last_test"]["ok"] is True and hosts["old-rig"]["last_test"]["ok"] is False
    assert client.post("/api/compute/ssh/hosts/h_deadbeef/test").status_code == 404


class _Run:
    def __init__(self, rc: int, out: str = "", err: str = "") -> None:
        self.returncode, self.stdout, self.stderr = rc, out, err


def test_ssh_test_real_mode_runs_batchmode_ssh(make_client, workspace, monkeypatch):
    c = make_client(workspace, fake_cloud=False)
    h = c.post("/api/compute/ssh/hosts", json={"name": "box", "target": "ubuntu@10.0.0.5:2201",
                                               "key_path": "~/.ssh/id_x"}).json()
    seen = []

    def fake_run(argv, **kw):
        seen.append((argv, kw))
        return _Run(0, "NVIDIA L40S, 46068 MiB\nNVIDIA L40S, 46068 MiB\n")

    monkeypatch.setattr(api_compute.subprocess, "run", fake_run)
    r = c.post(f"/api/compute/ssh/hosts/{h['host_id']}/test").json()
    assert r["ok"] is True and r["gpu"] == "2x NVIDIA L40S, 46068 MiB" and r["latency_ms"] >= 0
    argv, kw = seen[0]
    assert argv[0] == "ssh" and "BatchMode=yes" in argv and "ubuntu@10.0.0.5" in argv
    assert argv[argv.index("-p") + 1] == "2201" and kw["timeout"] == api_compute.SSH_TIMEOUT_S
    assert kw["stdin"] == subprocess.DEVNULL

    monkeypatch.setattr(api_compute.subprocess, "run", lambda *a, **k: _Run(0, "no-gpu\n"))
    r = c.post(f"/api/compute/ssh/hosts/{h['host_id']}/test").json()
    assert r["ok"] is True and r["gpu"] is None

    monkeypatch.setattr(api_compute.subprocess, "run",
                        lambda *a, **k: _Run(255, "", "Warning: x\nubuntu@10.0.0.5: Permission denied (publickey).\n"))
    r = c.post(f"/api/compute/ssh/hosts/{h['host_id']}/test").json()
    assert r == r | {"ok": False, "error": "ubuntu@10.0.0.5: Permission denied (publickey)."}

    def timeout(*a, **k):
        raise subprocess.TimeoutExpired("ssh", 20)

    monkeypatch.setattr(api_compute.subprocess, "run", timeout)
    r = c.post(f"/api/compute/ssh/hosts/{h['host_id']}/test").json()
    assert r["ok"] is False and "timed out" in r["error"]


# ---- real mode: vast through decider_lab.compute.vast ------------------------------------------


def test_vast_real_mode_without_cli(make_client, workspace, monkeypatch):
    monkeypatch.setattr(api_compute.shutil, "which", lambda name: None)
    c = make_client(workspace, fake_cloud=False)
    s = c.get("/api/compute/vast/status").json()
    assert s["fake"] is False and s["cli"] is False and s["api_key"] is False and "decider-lab[vast]" in s["error"]
    for path in ("offers", "instances"):
        r = c.get(f"/api/compute/vast/{path}")
        assert r.status_code == 424
        err = r.json()["error"]
        assert err["code"] == "backend_unavailable" and err["detail"]["install"] == "pip install 'decider-lab[vast]'"


def test_vast_real_mode_maps_cli_output(make_client, workspace, monkeypatch):
    from decider_lab.compute import vast

    monkeypatch.setattr(api_compute.shutil, "which", lambda name: "/usr/bin/vastai")
    monkeypatch.setattr(vast, "credit", lambda: 12.345)
    started = time.time() - 600
    rows = [{"id": 555, "label": "decider-lab:demo", "actual_status": "running", "num_gpus": 1,
             "gpu_name": "RTX_4090", "dph_total": 0.4, "start_date": started, "ssh_host": "ssh1.vast.ai",
             "ssh_port": 4000}]
    monkeypatch.setattr(vast, "instances", lambda prefix="decider-lab": rows)
    monkeypatch.setattr(vast, "offers", lambda gpu, **kw: [
        {"id": 2, "gpu_name": gpu, "num_gpus": 1, "dph_total": 0.5, "gpu_ram": 24564, "cuda_max_good": 12.8,
         "reliability2": 0.99, "geolocation": "US", "inet_down": 900, "disk_space": 100},
        {"id": 1, "gpu_name": gpu, "num_gpus": 1, "dph_total": 0.3, "gpu_ram": 24564, "cuda_max_good": 12.8,
         "reliability2": 0.98, "geolocation": "CA", "inet_down": 700, "disk_space": 90}])
    destroyed = []
    monkeypatch.setattr(vast, "destroy", lambda iid, verify=True: destroyed.append(iid) or True)
    c = make_client(workspace, fake_cloud=False)
    s = c.get("/api/compute/vast/status").json()
    assert s["api_key"] is True and s["credit_usd"] == 12.35 and s["error"] is None
    o = c.get("/api/compute/vast/offers", params={"gpu": "RTX_4090"}).json()
    assert [x["id"] for x in o["items"]] == [1, 2] and o["items"][0]["gpu_ram_gb"] == 24 and o["fake"] is False
    (it,) = c.get("/api/compute/vast/instances").json()["items"]
    assert it["gpu"] == "1x RTX_4090" and it["ssh"] == "root@ssh1.vast.ai:4000" and it["status"] == "running"
    assert 590 <= it["uptime_s"] <= 700 and it["cost_so_far_usd"] == pytest.approx(0.07, abs=0.01)
    assert c.post("/api/compute/vast/instances/556/destroy", json={"confirm": "556"}).status_code == 404
    assert c.post("/api/compute/vast/instances/555/destroy", json={"confirm": "555"}).json() == {"destroyed": True}
    assert destroyed == [555]
    monkeypatch.setattr(vast, "destroy", lambda iid, verify=True: False)
    r = c.post("/api/compute/vast/instances/555/destroy", json={"confirm": "555"}).json()
    assert r["destroyed"] is False and "still listed" in r["message"]


def test_vast_real_mode_cli_error(make_client, workspace, monkeypatch, secret_value):
    from decider_lab.compute import vast

    monkeypatch.setattr(api_compute.shutil, "which", lambda name: "/usr/bin/vastai")

    def boom():
        raise RuntimeError(f"vastai show user failed: bad key {secret_value}")

    monkeypatch.setattr(vast, "credit", boom)
    monkeypatch.setattr(vast, "instances", lambda prefix="decider-lab": (_ for _ in ()).throw(RuntimeError("nope")))
    c = make_client(workspace, fake_cloud=False)
    s = c.get("/api/compute/vast/status")
    assert s.json()["api_key"] is False and secret_value not in s.text
    r = c.get("/api/compute/vast/instances")
    assert r.status_code == 502 and r.json()["error"]["code"] == "cloud_error"


# ---- real mode: AWS through boto3 (fake session) -----------------------------------------------


class _ClientError(Exception):
    def __init__(self, code: str, msg: str = "denied") -> None:
        super().__init__(f"An error occurred ({code}): {msg}")
        self.response = {"Error": {"Code": code, "Message": msg}}


class _Pager:
    def __init__(self, pages):
        self.pages = pages

    def paginate(self, **kw):
        return iter(self.pages)


class _FakeClient:
    def __init__(self, sess, name):
        self.s, self.name = sess, name

    def get_caller_identity(self):
        if self.s.sts_error:
            raise self.s.sts_error
        return {"Account": "210987654321", "Arn": "arn:aws:sts::210987654321:assumed-role/Dev/me", "UserId": "x"}

    def get_service_quota(self, ServiceCode, QuotaCode):
        if QuotaCode in self.s.denied_quotas:
            raise _ClientError("AccessDeniedException")
        return {"Quota": {"Value": {"L-DB2E81BA": 16.0, "L-417A185B": 0.0, "L-1216C47A": 128.0}[QuotaCode]}}

    def get_paginator(self, op):
        assert op == "describe_instances"
        return self

    def paginate(self, Filters):
        self.s.filters.append(Filters)
        tagged = any(f["Name"] == "tag-key" for f in Filters)
        insts = self.s.tagged if tagged else self.s.running
        return iter([{"Reservations": [{"Instances": insts}]}])

    def describe_instance_types(self, InstanceTypes):
        v = {"g6e.xlarge": 4, "m7i.2xlarge": 8, "p5.48xlarge": 192}
        return {"InstanceTypes": [{"InstanceType": t, "VCpuInfo": {"DefaultVCpus": v[t]}} for t in InstanceTypes]}

    def terminate_instances(self, InstanceIds):
        self.s.terminated += InstanceIds
        return {"TerminatingInstances": [{"InstanceId": i} for i in InstanceIds]}

    def list_foundation_models(self, byOutputModality):
        return {"modelSummaries": [
            {"modelId": "amazon.nova-pro-v1:0", "modelName": "Nova Pro", "providerName": "Amazon",
             "inputModalities": ["TEXT", "IMAGE"], "outputModalities": ["TEXT"],
             "inferenceTypesSupported": ["INFERENCE_PROFILE"], "modelLifecycle": {"status": "ACTIVE"}},
            {"modelId": "amazon.titan-text-lite-v1", "modelName": "Titan Text Lite", "providerName": "Amazon",
             "inputModalities": ["TEXT"], "outputModalities": ["TEXT"], "inferenceTypesSupported": ["ON_DEMAND"],
             "modelLifecycle": {"status": "LEGACY"}},
            {"modelId": "x.provisioned-only", "modelName": "Prov", "providerName": "X", "inputModalities": ["TEXT"],
             "outputModalities": ["TEXT"], "inferenceTypesSupported": ["PROVISIONED"]}]}

    def list_inference_profiles(self, **kw):
        arn = "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-pro-v1:0"
        return {"inferenceProfileSummaries": [
            {"inferenceProfileId": "global.amazon.nova-pro-v1:0", "models": [{"modelArn": arn}]},
            {"inferenceProfileId": "us.amazon.nova-pro-v1:0", "models": [{"modelArn": arn}]}]}


class _FakeSession:
    def __init__(self):
        self.sts_error = None
        self.denied_quotas: set[str] = set()
        launched = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=14)
        self.tagged = [{"InstanceId": "i-0123456789abcdef0", "InstanceType": "g6e.xlarge", "State": {"Name": "running"},
                        "LaunchTime": launched, "PublicIpAddress": "54.12.34.56",
                        "Tags": [{"Key": "decider-lab", "Value": "first-lab"}]}]
        self.running = [{"InstanceType": "g6e.xlarge"}, {"InstanceType": "g6e.xlarge"}, {"InstanceType": "m7i.2xlarge"}]
        self.filters: list = []
        self.terminated: list[str] = []
        self.calls: list[tuple] = []

    def client(self, name, config=None):
        return _FakeClient(self, name)


@pytest.fixture
def aws_real(make_client, workspace, monkeypatch):
    sess = _FakeSession()

    def session(profile, region):
        sess.calls.append((profile, region))
        return sess

    monkeypatch.setattr(api_compute, "_session", session)
    return make_client(workspace, fake_cloud=False), sess


def test_aws_real_identity(aws_real):
    c, sess = aws_real
    i = c.get("/api/compute/aws/identity", params={"profile": "heisenberg", "region": "us-east-1"}).json()
    assert i["ok"] is True and i["account"] == "210987654321" and i["fake"] is False
    assert sess.calls[-1] == ("heisenberg", "us-east-1")
    sess.sts_error = _ClientError("ExpiredToken", "The security token included in the request is expired")
    i = c.get("/api/compute/aws/identity", params={"profile": "heisenberg"}).json()
    assert i["ok"] is False and "aws sso login --profile heisenberg" in i["error"] and i["account"] is None


def test_aws_real_identity_no_credentials(aws_real):
    c, sess = aws_real

    class NoCredentialsError(Exception):
        pass

    sess.sts_error = NoCredentialsError("Unable to locate credentials")
    i = c.get("/api/compute/aws/identity").json()
    assert i["ok"] is False and i["error"].startswith("NoCredentials")


def test_aws_real_quotas_with_used_vcpus_and_denial(aws_real):
    c, sess = aws_real
    q = c.get("/api/compute/aws/quotas").json()
    fams = {x["family"]: x for x in q["items"]}
    assert fams["g"]["limit_vcpus"] == 16 and fams["g"]["used_vcpus"] == 8
    assert fams["standard"]["used_vcpus"] == 8 and fams["p"]["used_vcpus"] == 0 and q["error"] is None
    sess.denied_quotas = {"L-417A185B"}
    q = c.get("/api/compute/aws/quotas").json()
    p = next(x for x in q["items"] if x["family"] == "p")
    assert p["limit_vcpus"] is None and "servicequotas:GetServiceQuota" in p["error"]


def test_aws_real_instances_mask_ip_and_terminate(aws_real):
    c, sess = aws_real
    body = c.get("/api/compute/aws/instances", params={"region": "us-east-1"}).json()
    (it,) = body["items"]
    assert it["public_ip_masked"] == "54.12.x.x" and "54.12.34.56" not in json.dumps(body)
    assert it["lab"] == "first-lab" and it["usd_per_hour"] == 1.861 and 800 <= it["uptime_s"] <= 900
    assert {"Name": "tag-key", "Values": ["decider-lab"]} in sess.filters[-1]
    r = c.post("/api/compute/aws/instances/i-0fffffffffffffff0/terminate", json={"confirm": "i-0fffffffffffffff0"})
    assert r.status_code == 404 and sess.terminated == []
    r = c.post("/api/compute/aws/instances/i-0123456789abcdef0/terminate",
               json={"confirm": "i-0123456789abcdef0", "profile": "heisenberg"})
    assert r.json() == {"terminating": ["i-0123456789abcdef0"]} and sess.terminated == ["i-0123456789abcdef0"]
    r = c.post("/api/compute/aws/instances/terminate-all", json={"confirm": "terminate all"})
    assert r.json() == {"terminating": ["i-0123456789abcdef0"]}


def test_aws_real_bedrock_join_prefers_region_profile(aws_real):
    c, _ = aws_real
    b = c.get("/api/compute/aws/bedrock-models", params={"region": "us-east-1"}).json()
    by = {m["model_id"]: m for m in b["items"]}
    assert by["amazon.nova-pro-v1:0"]["invoke_id"] == "us.amazon.nova-pro-v1:0"
    assert by["amazon.titan-text-lite-v1"]["invoke_id"] == "amazon.titan-text-lite-v1"
    assert by["amazon.titan-text-lite-v1"]["status"] == "LEGACY"
    assert "x.provisioned-only" not in by
    assert by["amazon.nova-pro-v1:0"]["spec_yaml"] == "{bedrock: us.amazon.nova-pro-v1:0, region: us-east-1}"


def test_aws_real_bedrock_access_denied_is_a_panel_error(aws_real, monkeypatch):
    c, _ = aws_real

    def denied(self, byOutputModality):
        raise _ClientError("AccessDeniedException")

    monkeypatch.setattr(_FakeClient, "list_foundation_models", denied)
    b = c.get("/api/compute/aws/bedrock-models").json()
    assert b["items"] == [] and "bedrock:ListFoundationModels" in b["error"]


def test_aws_real_instances_error_names_the_action(aws_real, monkeypatch):
    c, _ = aws_real

    def denied(self, Filters):
        raise _ClientError("UnauthorizedOperation")

    monkeypatch.setattr(_FakeClient, "paginate", denied)
    r = c.get("/api/compute/aws/instances")
    assert r.status_code == 502
    err = r.json()["error"]
    assert err["code"] == "cloud_error" and "ec2:DescribeInstances" in err["message"]
    assert err["detail"]["aws_code"] == "UnauthorizedOperation"


def test_aws_real_timeout(aws_real, monkeypatch):
    c, _ = aws_real

    def slow(self):
        time.sleep(1.0)
        return {}

    monkeypatch.setattr(_FakeClient, "get_caller_identity", slow)
    monkeypatch.setattr(api_compute, "_cloud", _short_cloud)
    i = c.get("/api/compute/aws/identity").json()
    assert i["ok"] is False and "did not answer" in i["error"]


_orig_cloud = api_compute._cloud


def _short_cloud(fn, *, what, timeout=None):
    return _orig_cloud(fn, what=what, timeout=0.2)


def test_aws_without_boto3(make_client, workspace, monkeypatch):
    real = api_compute.importlib.util.find_spec
    monkeypatch.setattr(api_compute.importlib.util, "find_spec", lambda n: None if n == "boto3" else real(n))
    c = make_client(workspace, fake_cloud=False)
    for path in ("identity", "quotas", "instances", "bedrock-models"):
        r = c.get(f"/api/compute/aws/{path}")
        assert r.status_code == 424, path
        assert r.json()["error"]["detail"]["install"] == "pip install 'decider-lab[aws]'"
    assert c.get("/api/compute/aws/profiles").json()["boto3"] is False


def test_aws_profiles_reads_section_names_only(make_client, workspace, monkeypatch, tmp_path):
    cfg = tmp_path / "config"
    cfg.write_text("[default]\nregion = us-east-1\n[profile heisenberg]\nsso_start_url = https://x\n"
                   "[sso-session corp]\nsso_region = us-east-1\n")
    creds = tmp_path / "credentials"
    creds.write_text(f"[default]\naws_access_key_id = AKIAABCDEFGHIJKLMNOP\naws_secret_access_key = {SAMPLE_SECRET}\n"
                     "[ci-bot]\naws_access_key_id = AKIAABCDEFGHIJKLMNOQ\n")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(cfg))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(creds))
    monkeypatch.setenv("AWS_PROFILE", "heisenberg")
    c = make_client(workspace, fake_cloud=False)
    r = c.get("/api/compute/aws/profiles")
    body = r.json()
    assert body["items"] == ["default", "ci-bot", "heisenberg"] and body["env_profile"] == "heisenberg"
    assert SAMPLE_SECRET not in r.text and "AKIA" not in r.text


# ---- secrets -----------------------------------------------------------------------------------


def test_no_compute_response_carries_the_secret(client, secret_value):
    h = client.post("/api/compute/ssh/hosts", json={"name": "s", "target": "u@h.invalid"}).json()
    texts = [client.get(p).text for p in (
        "/api/compute/doctor", "/api/compute/vast/status", "/api/compute/vast/offers", "/api/compute/vast/instances",
        "/api/compute/aws/profiles", "/api/compute/aws/identity", "/api/compute/aws/quotas",
        "/api/compute/aws/instances", "/api/compute/aws/instance-types", "/api/compute/aws/bedrock-models",
        "/api/compute/ssh/hosts")]
    texts.append(client.post(f"/api/compute/ssh/hosts/{h['host_id']}/test").text)
    assert all(secret_value not in t for t in texts)
    assert os.environ.get("DECIDER_LAB_TEST_SECRET_TOKEN") == secret_value


def test_fake_reset_restores_fixtures(client, make_client, workspace):
    assert client.post("/api/compute/vast/instances/9876543/destroy", json={"confirm": "9876543"}).json()["destroyed"]
    assert client.post("/api/compute/aws/instances/terminate-all", json={"confirm": "terminate all"}).status_code == 200
    assert client.post("/api/compute/fake/reset").json() == {"reset": True}
    assert len(client.get("/api/compute/vast/instances").json()["items"]) == 1
    assert len(client.get("/api/compute/aws/instances").json()["items"]) == 1
    real = make_client(workspace, fake_cloud=False)
    assert real.post("/api/compute/fake/reset").status_code == 404
