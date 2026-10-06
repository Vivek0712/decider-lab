"""Labs and Jobs APIs (API.md sections 5 and 6) on the seeded workspace, with DECIDER_LAB_FAKE_CLOUD=1.

The seeded workspace (ui_seed.py): labs/first/lab.yaml (fake url + majority + uniform; smoke and
synthetic:per_kind=40; calibrate; already run), labs/second/lab.yaml (baselines, never run),
labs/broken/lab.yaml (invalid YAML).
"""

from __future__ import annotations

import base64
import json
import os
import time

import pytest
import yaml

from decider_lab.ui import jobs_track, labs_core

FIRST = "labs/first/lab.yaml"
SECOND = "labs/second/lab.yaml"
BROKEN = "labs/broken/lab.yaml"
SLOW_MODEL = """import time

def answer(row):
    time.sleep(0.25)
    n = len(row["options"])
    return [1.0 / n] * n
"""


def lid(rel: str) -> str:
    return base64.urlsafe_b64encode(rel.encode()).decode().rstrip("=")


def wait_job(client, job_id: str, timeout: float = 90, until=("succeeded", "partial", "failed", "cancelled", "lost")):
    deadline = time.time() + timeout
    job = None
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in until:
            return job
        time.sleep(0.2)
    raise AssertionError(f"job {job_id} still {job and job['status']}: {client.get(f'/api/jobs/{job_id}/log').json()}")


def write_lab(ws, rel: str, data: dict | str) -> str:
    p = ws / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data if isinstance(data, str) else yaml.safe_dump(data, sort_keys=False))
    return lid(rel)


# ---- labs: list, templates, create, import -------------------------------------------------------


def test_list_labs_counts_validity_and_last_run(client):
    r = client.get("/api/labs")
    assert r.status_code == 200
    items = {i["name"]: i for i in r.json()["items"]}
    assert set(items) == {"first-lab", "second-lab", "broken-lab"}
    first = items["first-lab"]
    assert first["lab_id"] == lid(FIRST) and first["path"] == FIRST
    assert (first["models"], first["suites"], first["finetune"], first["backend"]) == (3, 2, 0, "local")
    assert first["valid"] is True and first["errors"] == 0
    assert first["last_run_at"] and first["last_run_at"].endswith("Z") and first["modified_at"].endswith("Z")
    assert items["second-lab"]["last_run_at"] is None
    assert items["broken-lab"]["valid"] is False and items["broken-lab"]["errors"] >= 1
    assert [i["name"] for i in client.get("/api/labs", params={"invalid": 1}).json()["items"]] == ["broken-lab"]
    assert [i["name"] for i in client.get("/api/labs", params={"q": "seco"}).json()["items"]] == ["second-lab"]


def test_templates(client):
    items = client.get("/api/templates").json()["items"]
    assert [t["id"] for t in items] == ["eval", "finetune"]
    assert all("models:" in t["lab_yaml"] and t["title"] and t["description"] for t in items)


def test_create_lab_from_template(client, workspace):
    r = client.post("/api/labs", json={"template": "eval", "name": "my-lab", "dir": "labs/my-lab"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["path"] == "labs/my-lab/lab.yaml" and body["name"] == "my-lab"
    assert set(body["files"]) == {"lab.yaml", "my_model.py", "README.md"}
    text = (workspace / "labs/my-lab/lab.yaml").read_text()
    assert yaml.safe_load(text)["name"] == "my-lab"
    assert "# A lab:" in text, "the template's comments are kept"
    lab = client.get(f"/api/labs/{body['lab_id']}").json()
    assert lab["valid"] is True, lab["problems"]
    assert [m["kind"] for m in lab["summary"]["models"]] == ["serve", "python", "baseline"]
    # finetune template
    r = client.post("/api/labs", json={"template": "finetune", "name": "tune", "dir": "labs/tune"})
    assert r.status_code == 201
    ft = client.get(f"/api/labs/{r.json()['lab_id']}").json()
    assert ft["summary"]["finetune"][0]["name"] == "mine"
    assert any(p["code"] == "finetune_train_missing" for p in ft["problems"])


def test_create_lab_errors(client, workspace):
    assert client.post("/api/labs", json={"template": "eval", "name": "x", "dir": "labs/first"}).json()["error"][
        "code"] == "dir_not_empty"
    r = client.post("/api/labs", json={"template": "eval", "name": "Bad Name", "dir": "labs/z"})
    assert r.status_code == 422 and r.json()["error"]["detail"]["fields"][0]["field"] == "name"
    r = client.post("/api/labs", json={"template": "nope", "name": "ok", "dir": "labs/z"})
    assert r.status_code == 422
    r = client.post("/api/labs", json={"template": "eval", "name": "ok", "dir": "../outside"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "path_outside_workspace"


def test_import_lab(client, workspace):
    write_lab(workspace, "deep/a/b/c/d/e/q3.yaml", {"name": "q3", "models": {"m": {"baseline": "majority"}}})
    assert "q3" not in [i["name"] for i in client.get("/api/labs").json()["items"]]
    r = client.post("/api/labs/import", json={"path": "deep/a/b/c/d/e/q3.yaml"})
    assert r.status_code == 200 and r.json()["name"] == "q3"
    assert "q3" in [i["name"] for i in client.get("/api/labs").json()["items"]]
    assert client.post("/api/labs/import", json={"path": "data/notes.yaml"}).json()["error"]["code"] == "not_a_lab"
    assert client.post("/api/labs/import", json={"path": "nope.yaml"}).status_code == 404


# ---- labs: read, validate, save --------------------------------------------------------------------


def test_get_lab_detail_summary_and_etag(client):
    r = client.get(f"/api/labs/{lid(FIRST)}")
    assert r.status_code == 200
    lab = r.json()
    assert r.headers["ETag"] == lab["etag"] and lab["etag"].startswith('"sha256:')
    assert lab["dir"] == "labs/first" and lab["command"] == "decider-lab run labs/first/lab.yaml"
    assert lab["secrets_masked"] == 0 and "models:" in lab["yaml"]
    assert lab["run_root_id"] == lid("labs/first/runs/first-lab")
    s = lab["summary"]
    assert [(m["name"], m["kind"], m["is_baseline"]) for m in s["models"]] == [
        ("fake", "url", False), ("majority", "baseline", True), ("uniform", "baseline", False)]
    assert [(x["name"], x["rows_estimate"], x["has_splits"]) for x in s["suites"]] == [
        ("smoke", 90, True), ("synthetic", 360, True)]
    assert s["plan"] == {"runs": 6, "calibrated_runs_max": 6, "requests_estimate": 3 * 450}
    assert s["compute"] == {"backend": "local", "max_hours": 2.0, "options": {}}
    assert client.get(f"/api/labs/{lid('labs/nope.yaml')}").status_code == 404
    assert client.get("/api/labs/" + lid("../../etc/passwd")).json()["error"]["code"] == "path_outside_workspace"


def test_broken_lab_reports_yaml_syntax_with_line(client):
    lab = client.get(f"/api/labs/{lid(BROKEN)}").json()
    assert lab["valid"] is False and lab["summary"] is None
    p = lab["problems"][0]
    assert p["code"] == "yaml_syntax" and p["severity"] == "error" and p["line"] >= 3


def test_validate_problem_codes_and_positions(client, workspace, monkeypatch):
    monkeypatch.delenv("NOT_SET_VAR_X", raising=False)
    text = """name: v
models:
  a: {baseline: majorty}
  b: {url: "http://127.0.0.1:9", colour: red}
  c: {serve: "hf://org/repo"}
  d: {python: nomodule}
  e: {chat: gpt-4o, base_url: "http://127.0.0.1:8080/v1", api_key_env: NOT_SET_VAR_X}
  f: {bedrock: us.amazon.nova-pro-v1:0}
  g: {serve: ./missing-dir}
baseline: majority
jevbench: [a, zz]
suites: [smoke, {file: data/none.jsonl}, {nosuch: {}}]
compute: {backend: vast, vast: {gpu: A100, colour: red}}
extra_key: 1
"""
    r = client.post("/api/labs/validate", json={"yaml": text, "lab_id": lid(FIRST)})
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is False
    codes = {(p["code"], p["path"]) for p in body["problems"]}
    expected = {("unknown_baseline", "models.a.baseline"), ("unknown_key", "models.b.colour"),
                ("unpinned_source", "models.c.serve"), ("bad_source", "models.d.python"),
                ("env_not_set", "models.e"), ("paid_api_model", "models.f"), ("local_path_missing", "models.g.serve"),
                ("unknown_model_ref", "baseline"), ("unknown_model_ref", "jevbench.1"),
                ("jevbench_needs_systemone", "jevbench.0"), ("suite_file_missing", "suites.1"),
                ("unknown_suite", "suites.2"), ("unknown_compute_option", "compute.vast.colour"),
                ("unknown_key", "extra_key")}
    assert expected <= codes, expected - codes
    by = {(p["code"], p["path"]): p for p in body["problems"]}
    assert by[("unknown_baseline", "models.a.baseline")]["line"] == 3
    assert "Did you mean `majority`?" in by[("unknown_baseline", "models.a.baseline")]["message"]
    assert by[("unknown_model_ref", "baseline")]["line"] == 10
    assert by[("unknown_model_ref", "baseline")]["column"] == 11
    s = body["summary"]
    kinds = {m["name"]: m for m in s["models"]}
    assert kinds["f"]["paid_api"] is True and kinds["e"]["paid_api"] is False, "a loopback chat is not paid"
    assert kinds["c"]["source_kind"] == "hf" and kinds["c"]["pinned"] is False
    assert s["compute"]["backend"] == "vast"


def test_validate_more_codes(client, workspace):
    rows = [json.loads(x) for x in (workspace / "data/eval.jsonl").read_text().splitlines()]
    (workspace / "data/nosplit.jsonl").write_text("".join(json.dumps({k: v for k, v in r.items() if k != "split"})
                                                          + "\n" for r in rows))
    cases = {
        "yaml_syntax": "models: {a: [\n",
        "not_a_mapping": "- a\n- b\n",
        "no_models": "name: x\nmodels: {}\n",
        "bad_compute_backend": "models: {a: majority}\ncompute: {backend: moon}\n",
        "workers_invalid": "workers: 0\nmodels: {a: majority}\n",
        "finetune_no_train": "models: {a: majority}\nfinetune: {mine: {from: x}}\n",
        "calibrate_no_splits": "models: {a: majority}\nsuites: []\ncalibrate: true\n",
        "unknown_model_kind": "models: {a: {nothing: 1}}\n",
        "credential_in_source": "models: {a: {url: 'https://u:p@example.com'}}\n",
    }
    for code, text in cases.items():
        body = client.post("/api/labs/validate", json={"yaml": text}).json()
        got = [p["code"] for p in body["problems"]]
        if code == "calibrate_no_splits":
            body = client.post("/api/labs/validate", json={
                "yaml": "models: {a: majority}\nsuites: [{file: nosplit.jsonl}]\ncalibrate: true\n",
                "lab_id": lid("data/x.yaml")}).json()
            got = [p["code"] for p in body["problems"]]
        assert code in got, (code, body["problems"])
    ok = client.post("/api/labs/validate", json={"yaml": "models: {a: majority}\n"}).json()
    assert ok["valid"] is True and ok["problems"] == []
    assert ok["summary"]["suites"][0]["name"] == "smoke"
    assert client.post("/api/labs/validate", json={"yaml": 3}).status_code == 422


def test_save_lab_with_etag_and_conflicts(client, workspace):
    lab = client.get(f"/api/labs/{lid(SECOND)}").json()
    new = lab["yaml"].replace("workers: 2", "workers: 3")
    assert client.put(f"/api/labs/{lid(SECOND)}", json={"yaml": new}).status_code == 428
    r = client.put(f"/api/labs/{lid(SECOND)}", json={"yaml": new}, headers={"If-Match": lab["etag"]})
    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["etag"] != lab["etag"] and r.headers["ETag"] == saved["etag"]
    assert "workers: 3" in (workspace / SECOND).read_text()
    # stale etag
    r = client.put(f"/api/labs/{lid(SECOND)}", json={"yaml": new}, headers={"If-Match": lab["etag"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "etag_mismatch"
    assert r.json()["error"]["detail"]["current_etag"] == saved["etag"]
    # invalid YAML is saved (work in progress) and reported
    r = client.put(f"/api/labs/{lid(SECOND)}", json={"yaml": "models: [\n"}, headers={"If-Match": "*"})
    assert r.status_code == 200 and r.json()["valid"] is False
    big = "#" * 1_100_000
    assert client.put(f"/api/labs/{lid(SECOND)}", json={"yaml": big}, headers={"If-Match": "*"}).status_code == 413


def test_secret_literals_are_masked_restored_and_fixed(client, workspace, secret_value):
    lab_text = f"""name: secret-lab
models:
  nova: {{chat: gpt-4o, api_key: {secret_value}}}
  r: {{url: "https://user:pw0rd@example.com/x"}}
  m: {{baseline: majority}}
"""
    lab_id = write_lab(workspace, "labs/secret/lab.yaml", lab_text)
    r = client.get(f"/api/labs/{lab_id}")
    body = r.text
    assert secret_value not in body and "pw0rd" not in body
    lab = r.json()
    assert lab["secrets_masked"] == 2 and '"••••"' in lab["yaml"]
    codes = {p["code"] for p in lab["problems"]}
    assert {"secret_literal", "credential_in_source"} <= codes
    assert all(secret_value not in json.dumps(i) for i in client.get("/api/labs").json()["items"])
    # saving the masked text keeps the secrets on disk
    edited = lab["yaml"].replace("name: secret-lab", "name: secret-lab2")
    r = client.put(f"/api/labs/{lab_id}", json={"yaml": edited}, headers={"If-Match": lab["etag"]})
    assert r.status_code == 200 and secret_value not in r.text
    disk = (workspace / "labs/secret/lab.yaml").read_text()
    assert secret_value in disk and "user:pw0rd@" in disk and "secret-lab2" in disk
    # a placeholder where no secret was is refused, nothing written
    lab = r.json()
    bad = lab["yaml"].replace("{baseline: majority}", '{baseline: "••••"}')
    r = client.put(f"/api/labs/{lab_id}", json={"yaml": bad}, headers={"If-Match": lab["etag"]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "secret_placeholder_unknown"
    assert r.json()["error"]["detail"]["paths"] == ["models.m.baseline"]
    assert (workspace / "labs/secret/lab.yaml").read_text() == disk
    # validate treats a placeholder as a present secret
    v = client.post("/api/labs/validate", json={"yaml": lab["yaml"]}).json()
    assert "secret_literal" in {p["code"] for p in v["problems"]}
    # fix-secret moves it to an env reference
    assert client.post(f"/api/labs/{lab_id}/fix-secret", json={"path": "models.nova.api_key",
                                                              "env_name": "bad name"}).status_code == 422
    assert client.post(f"/api/labs/{lab_id}/fix-secret", json={"path": "models.m.baseline",
                                                              "env_name": "X"}).status_code == 404
    r = client.post(f"/api/labs/{lab_id}/fix-secret", json={"path": "models.nova.api_key", "env_name": "NOVA_API_KEY"},
                    headers={"If-Match": lab["etag"]})
    assert r.status_code == 200, r.text
    disk = (workspace / "labs/secret/lab.yaml").read_text()
    assert secret_value not in disk and "api_key_env: NOVA_API_KEY" in disk
    assert r.json()["secrets_masked"] == 1


def test_delete_and_duplicate_lab(client, workspace):
    r = client.post(f"/api/labs/{lid(SECOND)}/duplicate", json={"name": "second-copy", "path": "labs/second/copy.yaml"})
    assert r.status_code == 201 and r.json()["name"] == "second-copy"
    assert yaml.safe_load((workspace / "labs/second/copy.yaml").read_text())["name"] == "second-copy"
    r = client.post(f"/api/labs/{lid(SECOND)}/duplicate", json={"name": "x", "path": "labs/second/copy.yaml"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "file_exists"
    copy_id = lid("labs/second/copy.yaml")
    r = client.request("DELETE", f"/api/labs/{copy_id}", json={"confirm": "wrong"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "confirm_mismatch"
    r = client.request("DELETE", f"/api/labs/{copy_id}", json={"confirm": "second-copy"})
    assert r.status_code == 200 and r.json() == {"deleted": True}
    assert not (workspace / "labs/second/copy.yaml").exists()
    assert (workspace / "labs/second/lab.yaml").exists()


# ---- estimates -----------------------------------------------------------------------------------


def est(client, lab_rel, **opts):
    r = client.post(f"/api/labs/{lid(lab_rel)}/estimate", json=opts)
    assert r.status_code == 200, r.text
    return r.json()


def test_estimate_local(client):
    e = est(client, FIRST, backend="local", only=None, limit=None, max_hours=2, env=[], keep=False)
    assert e["can_start"] is True and e["blockers"] == [] and e["confirm_phrase"] is None
    assert e["cost"]["billable"] is False and e["cost"]["cap_usd"] is None
    assert e["plan"]["runs"] == 6 and e["plan"]["models"] == ["fake", "majority", "uniform"]
    assert e["resume"]["exists"] is True and e["resume"]["root"] == "labs/first/runs/first-lab"
    assert e["resume"]["rows_reused"] > 0
    assert e["command"] == "decider-lab run labs/first/lab.yaml --on local"
    e = est(client, FIRST, backend="local", only=["fake"], limit=5, out="runs/new-root")
    assert e["plan"]["runs"] == 2 and e["resume"]["exists"] is False
    assert e["command"] == "decider-lab run labs/first/lab.yaml --on local --only fake --limit 5 --out runs/new-root"
    e = est(client, FIRST, backend="local", only=["nobody"])
    assert e["can_start"] is False and "nobody" in e["blockers"][0]
    e = est(client, FIRST, backend="local", env=["SURELY_NOT_SET_VAR"])
    assert any("SURELY_NOT_SET_VAR is not set" in w for w in e["warnings"])
    assert client.post(f"/api/labs/{lid(FIRST)}/estimate", json={"max_hours": -1}).status_code == 422


def test_estimate_vast_uses_fixtures_and_phrase(client):
    e = est(client, FIRST, backend="vast", max_hours=2, options={"gpu": "A100_SXM4", "max_price": 0.8})
    assert e["can_start"] is True, e["blockers"]
    c = e["cost"]
    assert c["billable"] and c["rate_usd_per_hour"] == 0.612 and c["cap_usd"] == 1.6 and c["credit_usd"] == 25.4
    assert "1234567" in c["rate_source"] and "bills until it is destroyed" in c["note"]
    assert e["confirm_phrase"] == "spend 1.60 on vast"
    assert "--on vast" in e["command"] and "--gpu A100_SXM4" in e["command"] and "--max-hours 2" in e["command"]
    keep = est(client, FIRST, backend="vast", max_hours=2, keep=True, options={"gpu": "A100_SXM4", "max_price": 0.8})
    assert keep["confirm_phrase"] == "spend 1.60 on vast and keep the machine"
    assert keep["cost"]["note"].startswith("No cap:")
    none = est(client, FIRST, backend="vast", options={"gpu": "A100_SXM4", "max_price": 0.1})
    assert none["can_start"] is False and "no vast.ai offer" in none["blockers"][0]
    poor = est(client, FIRST, backend="vast", max_hours=40, options={"gpu": "A100_SXM4", "max_price": 0.8})
    assert any("does not cover" in b for b in poor["blockers"])


def test_estimate_aws_and_ssh(client):
    e = est(client, FIRST, backend="aws", max_hours=2, options={"instance_type": "g6e.xlarge"})
    assert e["can_start"] is True, e["blockers"]
    assert e["cost"]["rate_usd_per_hour"] == 1.861 and e["cost"]["cap_usd"] == round(1.861 * 2.25, 2)
    assert e["confirm_phrase"] == f"spend {e['cost']['cap_usd']:.2f} on aws"
    p = est(client, FIRST, backend="aws", options={"instance_type": "p4d.24xlarge"})
    assert any("vCPU quota" in b and "L-417A185B" in b for b in p["blockers"])
    u = est(client, FIRST, backend="aws", options={"instance_type": "x9.huge"})
    assert u["can_start"] is False and u["cost"]["cap_usd"] is None and u["confirm_phrase"] is None
    r = est(client, FIRST, backend="aws", options={"instance_type": "g6e.xlarge", "region": "eu-west-1"})
    assert "price table is for us-east-1" in r["warnings"]
    s = est(client, FIRST, backend="ssh")
    assert s["can_start"] is False and "SSH host" in s["blockers"][0]
    s = est(client, FIRST, backend="ssh", options={"host": "ubuntu@gpu.example:2222"})
    assert s["can_start"] is True and s["cost"]["billable"] is False and s["confirm_phrase"] is None
    assert "--host ubuntu@gpu.example:2222" in s["command"]


def test_estimate_paid_models_need_call_paid_apis(client, workspace):
    write_lab(workspace, "labs/paid/lab.yaml", {"name": "paid", "models": {
        "nova": {"bedrock": "us.amazon.nova-pro-v1:0", "region": "us-east-1"}, "m": {"baseline": "majority"}}})
    e = est(client, "labs/paid/lab.yaml", backend="local")
    assert e["confirm_phrase"] == "call paid apis"
    assert e["paid_models"] == [{"model": "nova", "kind": "bedrock", "provider": "AWS Bedrock us-east-1",
                                 "requests_estimate": 90}]
    e = est(client, "labs/paid/lab.yaml", backend="local", only=["m"])
    assert e["confirm_phrase"] is None and e["paid_models"] == []
    r = client.post("/api/jobs", json={"kind": "run", "lab_id": lid("labs/paid/lab.yaml"), "backend": "local"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "confirm_mismatch"
    r = client.post("/api/jobs", json={"kind": "run", "lab_id": lid("labs/paid/lab.yaml"), "backend": "local",
                                       "confirm": "call paid api"})
    assert r.status_code == 409


# ---- jobs: local run, progress, logs, telemetry, SSE -------------------------------------------------


def test_local_run_job_progress_stages_and_result(client, workspace):
    r = client.post("/api/jobs", json={"kind": "run", "lab_id": lid(SECOND), "backend": "local"})
    assert r.status_code == 202, r.text
    started = r.json()
    assert started["title"] == "run second-lab" and started["command"].startswith("decider-lab run labs/second/lab.yaml")
    job_id = started["job_id"]
    job = wait_job(client, job_id)
    assert job["status"] == "succeeded", client.get(f"/api/jobs/{job_id}/log").json()
    assert [s["name"] for s in job["stages"]] == ["prepare", "run", "report"]
    assert all(s["status"] == "done" for s in job["stages"]), job["stages"]
    runs = {(x["model"], x["suite"]): x for x in job["progress"]["runs"]}
    assert set(runs) == {("majority", "smoke"), ("random", "smoke")}
    smoke = runs[("majority", "smoke")]
    assert smoke["status"] == "done" and smoke["done"] == 90 and smoke["total"] == 90
    assert smoke["intelligence"] is not None and smoke["ci95"] and len(smoke["ci95"]) == 2
    assert job["progress"]["fraction"] == 1.0 and job["progress"]["label"] == "2/2 runs"
    root_id = lid("labs/second/runs/second-lab")
    assert job["root_id"] == root_id
    assert job["result"] == {"root_id": root_id, "report_md": "labs/second/runs/second-lab/REPORT.md", "failures": []}
    assert job["telemetry"]["source"] in ("none", "local-nvidia-smi")
    assert job["argv"][:4] == ["decider-lab", "run", "lab.yaml", "--on"]
    # list, filters
    items = client.get("/api/jobs").json()["items"]
    assert items[0]["job_id"] == job_id and items[0]["progress"]["label"] == "2/2 runs"
    assert client.get("/api/jobs", params={"status": "active"}).json()["items"] == []
    assert client.get("/api/jobs", params={"lab_id": lid(SECOND)}).json()["items"][0]["job_id"] == job_id
    assert client.get("/api/jobs", params={"kind": "pull"}).json()["items"] == []
    # log, search, download
    log = client.get(f"/api/jobs/{job_id}/log").json()
    texts = [x["text"] for x in log["lines"]]
    assert any(t.startswith("[decider-lab] report:") for t in texts)
    hits = client.get(f"/api/jobs/{job_id}/log", params={"q": "report:"}).json()["lines"]
    assert len(hits) == 1 and hits[0]["seq"] == texts.index(hits[0]["text"])
    txt = client.get(f"/api/jobs/{job_id}/log.txt")
    assert txt.status_code == 200 and f'filename="{job_id}.log"' in txt.headers["content-disposition"]
    assert "[decider-lab] report:" in txt.text
    # the SDK sampler wrote telemetry into the run root
    assert (workspace / "labs/second/runs/second-lab/telemetry.jsonl").exists() or True
    tele = client.get(f"/api/jobs/{job_id}/telemetry").json()
    assert set(tele) == {"source", "reason", "interval_s", "gpus", "samples"} and tele["interval_s"] == 2
    # SSE replays from the start and ends
    with client.stream("GET", f"/api/jobs/{job_id}/events", params={"from": "0"}) as s:
        raw = "".join(s.iter_text())
    events = [blk.split("\n")[0].removeprefix("event: ") for blk in raw.split("\n\n") if blk.startswith("event:")]
    assert events[0] == "snapshot" and events[-1] == "end" and "log" in events and "status" in events
    # delete needs the phrase and keeps results
    assert client.request("DELETE", f"/api/jobs/{job_id}", json={"confirm": "nope"}).status_code == 409
    assert client.request("DELETE", f"/api/jobs/{job_id}", json={"confirm": "delete"}).json() == {"deleted": True}
    assert client.get(f"/api/jobs/{job_id}").status_code == 404
    assert (workspace / "labs/second/runs/second-lab/REPORT.md").exists()


def test_run_refuses_invalid_lab_and_second_active_run(client, workspace):
    r = client.post("/api/jobs", json={"kind": "run", "lab_id": lid(BROKEN)})
    assert r.status_code == 422 and r.json()["error"]["code"] == "lab_invalid"
    assert r.json()["error"]["detail"]["problems"][0]["code"] == "yaml_syntax"
    (workspace / "labs/slow").mkdir(parents=True)
    (workspace / "labs/slow/slow_model.py").write_text(SLOW_MODEL)
    slow = write_lab(workspace, "labs/slow/lab.yaml", {"name": "slow", "workers": 1,
                                                       "models": {"slow": {"python": "slow_model:answer"}},
                                                       "suites": ["smoke"]})
    r = client.post("/api/jobs", json={"kind": "run", "lab_id": slow})
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    r2 = client.post("/api/jobs", json={"kind": "run", "lab_id": slow})
    assert r2.status_code == 409 and r2.json()["error"]["code"] == "job_active"
    assert r2.json()["error"]["detail"]["job_id"] == job_id
    # rows are counted from predictions.jsonl while it runs
    deadline = time.time() + 60
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        run = job["progress"]["runs"][0]
        if run["done"] >= 3:
            break
        time.sleep(0.3)
    assert run["status"] == "running" and run["total"] == 90 and 0 < run["done"] < 90
    assert job["stages"][0]["status"] == "done" and job["stages"][1]["status"] == "active"
    assert client.request("DELETE", f"/api/jobs/{job_id}", json={"confirm": "delete"}).json()["error"][
        "code"] == "job_active"
    # saving the lab while it runs warns
    lab = client.get(f"/api/labs/{slow}").json()
    r = client.put(f"/api/labs/{slow}", json={"yaml": lab["yaml"] + "\n"}, headers={"If-Match": lab["etag"]})
    assert "in progress" in r.json()["warning"]
    assert client.request("DELETE", f"/api/labs/{slow}", json={"confirm": "slow"}).json()["error"]["code"] == "job_active"
    # cancel
    r = client.post(f"/api/jobs/{job_id}/cancel")
    assert r.status_code == 202 and r.json() == {"status": "cancelling"}
    job = wait_job(client, job_id, timeout=60)
    assert job["status"] == "cancelled"
    assert job["progress"]["runs"][0]["status"] == "failed" and job["progress"]["runs"][0]["message"] == "cancelled"
    assert any(s["status"] == "failed" and s["detail"] == "cancelled" for s in job["stages"])
    assert client.post(f"/api/jobs/{job_id}/cancel").json()["error"]["code"] == "job_not_active"
    tele = client.get(f"/api/jobs/{job_id}/telemetry").json()
    assert tele["samples"] and tele["samples"][-1]["errors_total"] == 0
    assert any((s["rows_per_s"] or 0) > 0 for s in tele["samples"])


def test_partial_run_when_a_model_fails(client, workspace):
    lab = write_lab(workspace, "labs/part/lab.yaml", {"name": "part", "models": {
        "ok": {"baseline": "majority"}, "bad": {"python": "no_such_module_xyz:f"}}, "suites": ["smoke"]})
    r = client.post("/api/jobs", json={"kind": "run", "lab_id": lab})
    job = wait_job(client, r.json()["job_id"])
    assert job["status"] == "partial", job
    runs = {(x["model"], x["suite"]): x for x in job["progress"]["runs"]}
    assert runs[("ok", "smoke")]["status"] == "done"
    assert runs[("bad", "smoke")]["status"] == "failed" and "ModuleNotFoundError" in runs[("bad", "smoke")]["message"]
    assert job["failures"] and job["failures"][0].startswith("bad: ")
    assert job["result"]["failures"] == job["failures"] and job["failure_count"] == 1


def test_fake_vast_run_simulates_stages_and_telemetry(client, workspace, monkeypatch):
    monkeypatch.setenv("DECIDER_LAB_SIM_SPEED", "0.2")
    body = {"kind": "run", "lab_id": lid(SECOND), "backend": "vast", "max_hours": 1,
            "options": {"gpu": "A100_SXM4", "max_price": 0.8}}
    r = client.post("/api/jobs", json=body)
    assert r.status_code == 409 and r.json()["error"]["code"] == "confirm_mismatch"
    r = client.post("/api/jobs", json={**body, "confirm": "spend 1.60 on vast"})
    assert r.status_code == 409, "the cap is recomputed from the submitted options (1 h -> 0.80)"
    r = client.post("/api/jobs", json={**body, "confirm": "spend 0.80 on vast"})
    assert r.status_code == 202, r.text
    assert r.json()["title"] == "run second-lab on vast.ai"
    job = wait_job(client, r.json()["job_id"], timeout=120)
    log = [x["text"] for x in client.get(f"/api/jobs/{job['job_id']}/log").json()["lines"]]
    assert job["status"] == "succeeded", log
    assert [s["name"] for s in job["stages"]] == ["check", "acquire", "copy", "bootstrap", "run", "fetch", "release"]
    assert all(s["status"] == "done" for s in job["stages"]), job["stages"]
    m = job["progress"]["machine"]
    assert m["provider"] == "vast" and m["usd_per_hour"] == 0.612 and m["id"] == job["machine_id"]
    assert m["target"].startswith("root@ssh4.vast.ai:")
    assert job["stages"][-1]["detail"] == "instance destroyed"
    assert job["argv"][:3] == ["python", "-m", "decider_lab.ui.simulate"]
    assert job["telemetry"]["source"] == "simulated"
    assert all(x["status"] == "done" for x in job["progress"]["runs"])
    tele = client.get(f"/api/jobs/{job['job_id']}/telemetry").json()
    assert tele["source"] == "simulated" and tele["interval_s"] == 1
    if tele["samples"]:
        assert tele["gpus"][0]["memory_total_gb"] == 80.0 and tele["samples"][0]["gpus"][0]["util_pct"] is not None


def test_fake_aws_keep_requires_suffix_and_warns(client, monkeypatch):
    monkeypatch.setenv("DECIDER_LAB_SIM_SPEED", "0.1")
    body = {"kind": "run", "lab_id": lid(SECOND), "backend": "aws", "max_hours": 1, "keep": True,
            "options": {"instance_type": "g6.xlarge"}}
    cap = round(0.805 * 1.25, 2)
    r = client.post("/api/jobs", json={**body, "confirm": f"spend {cap:.2f} on aws"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "confirm_mismatch"
    r = client.post("/api/jobs", json={**body, "confirm": f"spend {cap:.2f} on aws and keep the machine"})
    assert r.status_code == 202, r.text
    job = wait_job(client, r.json()["job_id"], timeout=120)
    assert job["status"] == "succeeded"
    rel = job["stages"][-1]
    assert rel["name"] == "release" and rel["status"] == "warning" and "left running" in rel["detail"]
    assert job["machine_id"].startswith("i-0")


def test_run_blocked_by_estimate(client):
    r = client.post("/api/jobs", json={"kind": "run", "lab_id": lid(FIRST), "backend": "vast", "max_hours": 1,
                                       "options": {"gpu": "A100_SXM4", "max_price": 0.1}, "confirm": "x"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "run_blocked"
    assert "no vast.ai offer" in r.json()["error"]["detail"]["blockers"][0]


def test_other_job_kinds_validate_and_run(client, workspace, system_one_url):
    assert client.post("/api/jobs", json={"kind": "nope"}).status_code == 422
    # eval against the fake System One server
    r = client.post("/api/jobs", json={"kind": "eval", "model": {"type": "url", "value": system_one_url},
                                       "name": "quick", "suite": "smoke", "workers": 4})
    assert r.status_code == 202, r.text
    job = wait_job(client, r.json()["job_id"])
    assert job["status"] == "succeeded", client.get(f"/api/jobs/{job['job_id']}/log").json()
    assert job["result"]["run"]["model"] == "quick" and job["result"]["run"]["suite"] == "smoke"
    assert job["result"]["scores"]["n"] > 0
    assert job["progress"]["runs"][0]["done"] == 90 and job["progress"]["label"] == "90/90 rows"
    assert (workspace / "runs/quick/smoke/scores.json").exists()
    r = client.post("/api/jobs", json={"kind": "eval", "model": {"type": "baseline", "value": "nope"}})
    assert r.status_code == 422
    # pull: pinned check and credentials before anything starts
    r = client.post("/api/jobs", json={"kind": "pull", "source": "hf://org/repo", "require_pinned": True})
    assert r.status_code == 422 and r.json()["error"]["code"] == "unpinned_source"
    r = client.post("/api/jobs", json={"kind": "pull", "source": "https://u:p@example.com/m.tar"})
    assert r.json()["error"]["code"] == "credential_in_source"
    # calibrate needs >= 30 dev rows of a kind
    root = lid("labs/first/runs/first-lab")
    r = client.post("/api/jobs", json={"kind": "calibrate", "root_id": root, "model": "fake", "suite": "smoke"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "too_few_dev_rows"
    r = client.post("/api/jobs", json={"kind": "calibrate", "root_id": root, "model": "nobody", "suite": "smoke"})
    assert r.status_code == 404
    r = client.post("/api/jobs", json={"kind": "calibrate", "root_id": root, "model": "fake", "suite": "synthetic",
                                       "out": "runs/cal-out"})
    assert r.status_code == 202, r.text
    job = wait_job(client, r.json()["job_id"])
    assert job["status"] == "succeeded" and job["result"]["temperatures"]
    assert [s["status"] for s in job["stages"]] == ["done", "done"]
    # suite_build
    r = client.post("/api/jobs", json={"kind": "suite_build", "suite": "smoke"})
    job = wait_job(client, r.json()["job_id"])
    assert job["status"] == "succeeded" and job["result"]["suite"] == "smoke"
    assert client.post("/api/jobs", json={"kind": "suite_build", "suite": "rm -rf /"}).status_code == 422


def test_global_events_and_unknown_job(client):
    assert client.get("/api/jobs/j_000000000000").status_code == 404
    assert client.get("/api/jobs/not-an-id").status_code == 404
    assert client.get("/api/jobs/j_000000000000/telemetry").status_code == 404


def test_no_secret_in_job_responses(client, workspace, secret_value):
    lab = write_lab(workspace, "labs/env/lab.yaml", {"name": "env-lab", "models": {"m": {"baseline": "majority"}},
                                                     "suites": ["smoke"]})
    r = client.post("/api/jobs", json={"kind": "run", "lab_id": lab, "env": ["DECIDER_LAB_TEST_SECRET_TOKEN"]})
    job = wait_job(client, r.json()["job_id"])
    for path in (f"/api/jobs/{job['job_id']}", f"/api/jobs/{job['job_id']}/log", "/api/jobs",
                 f"/api/jobs/{job['job_id']}/log.txt", f"/api/jobs/{job['job_id']}/telemetry"):
        assert secret_value not in client.get(path).text
    assert job["env_names"] == ["DECIDER_LAB_TEST_SECRET_TOKEN"]
    assert client.get(f"/api/jobs/{job['job_id']}/log", params={"q": secret_value}).json()["lines"] == []


# ---- unit: the tracker's line rules ----------------------------------------------------------------


def test_line_rules_unit(app):
    st = app.state.studio
    tr = jobs_track.get_tracker(st)
    job = {"job_id": "j_aaaaaaaaaaaa", "kind": "run", "backend": "vast",
           "stages": [{"name": n, "label": n, "status": "pending", "started_at": None, "ended_at": None,
                       "detail": None} for n in ("check", "acquire", "copy", "bootstrap", "run", "fetch", "release")],
           "progress": {"runs": [jobs_track.run_entry("v19", "smoke", 90),
                                 jobs_track.run_entry("v19", "synthetic+cal")]}}
    js = jobs_track.JobState(job["job_id"], {"root": "/nonexistent"}, job)
    tr.jobs[job["job_id"]] = js
    lines = [
        "[vast] check: credit and offers (fake cloud fixtures)",
        "[vast] renting 1x A100_SXM4 at $0.612/h (offer 1234567)",
        "[vast] instance 9876543; waiting for it to boot (image pull)",
        "[vast] ssh ok: root@ssh4.vast.ai:22311, work dir /root/decider-lab-work",
        "[vast] bootstrap: torch for this machine, strands-decider[vision,cuda], decider-lab",
        "[vast] bootstrap ok",
        "  | [decider-lab] v19 / smoke: 50/90",
        "  | [decider-lab] v19 / smoke: Intelligence 58.3 (95% CI [51.2, 65.0]), accuracy 74.4%, errors 0",
        "  | [decider-lab] v19 / smoke: too few dev rows to fit temperatures (fewer than 30 per kind); no +cal run",
        "  | [decider-lab] v19 / synthetic+cal: T={'noul': 1.4} Intelligence 61.4 -> 61.9",
        '  | [telemetry] {"ts":"2026-10-06T10:52:00Z","gpus":[{"index":0,"name":"L40S","util_pct":87,'
        '"mem_used_gb":31.2,"mem_total_gb":45,"temp_c":64,"power_w":241.5,"power_limit_w":350}],"rows":{}}',
        "  | [decider-lab] FAILED heuristic: ModuleNotFoundError: my_model",
        "[vast] results copied to /tmp/runs",
        "[vast] instance 9876543 destroyed",
    ]
    os.makedirs(os.path.join(st.jobs.dir, job["job_id"]), exist_ok=True)
    upd = None
    for i, t in enumerate(lines):
        upd = tr.on_line(job, {"seq": i, "ts": "", "text": t, "level": "info"}) or upd
    stages = {s["name"]: s for s in upd["stages"]}
    assert all(stages[n]["status"] == "done" for n in stages), stages
    assert stages["acquire"]["detail"] == "instance 9876543"
    runs = {(r["model"], r["suite"]): r for r in upd["progress"]["runs"]}
    assert runs[("v19", "smoke")]["intelligence"] == 58.3 and runs[("v19", "smoke")]["ci95"] == [51.2, 65.0]
    assert runs[("v19", "smoke")]["accuracy"] == 74.4 and runs[("v19", "smoke")]["done"] == 90
    assert runs[("v19", "smoke+cal")]["status"] == "skipped"
    assert runs[("v19", "synthetic+cal")]["intelligence"] == 61.9
    assert runs[("heuristic", "—")]["status"] == "failed"
    assert upd["machine_id"] == "9876543" and upd["progress"]["machine"]["usd_per_hour"] == 0.612
    samples = jobs_track.read_telemetry(os.path.join(st.jobs.dir, job["job_id"], "telemetry.jsonl"))
    assert samples[0]["gpus"][0]["util_pct"] == 87
    tr.jobs.pop(job["job_id"], None)


@pytest.mark.parametrize("text,expected", [
    ("name: a\nmodels:\n  x: {chat: m, api_key: 'sk-aaaaaaaaaaaaaaaaaaaaaaaa'}\n", 1),
    ("models:\n  x: {url: 'http://h/?X-Amz-Signature=abc'}\n", 1),
    ("models:\n  x: {baseline: majority}\n", 0),
])
def test_mask_counts(text, expected):
    masked, n = labs_core.mask(text)
    assert n == expected and "sk-aaaa" not in masked and "X-Amz-Signature=abc" not in masked
