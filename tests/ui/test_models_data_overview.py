"""Backend tests for the Models, Data and Overview areas (API.md sections 3, 8 and 9).

Every test runs on a fresh copy of the seeded workspace (or an empty one) with
DECIDER_LAB_FAKE_CLOUD=1, a temp decider-lab cache, a temp Hugging Face cache and no network beyond
loopback fixture servers.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import pathlib
import tarfile
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

from decider_lab import suites

SHA_A = "bb282d786bc251fd4e3068de3ada9ddbb38127cd"


def fid(rel: str) -> str:
    return base64.urlsafe_b64encode(rel.encode()).decode().rstrip("=")


@pytest.fixture(autouse=True)
def isolated_caches(monkeypatch, tmp_path):
    cache = tmp_path / "dl-cache"
    cache.mkdir(exist_ok=True)
    monkeypatch.setattr(suites, "CACHE", str(cache))
    hf = tmp_path / "hf-hub"
    hf.mkdir()
    monkeypatch.setenv("HF_HUB_CACHE", str(hf))
    return cache


def no_secret(resp, secret: str) -> None:
    assert secret not in resp.text


# ---- fixtures: a cached model, an HF cache entry, a fixture HTTP server ----------------------------


def put_cached(cache: pathlib.Path, source: str, sha: str | None = None, *, requested: bool = True,
               commit: str | None = None) -> str:
    want = sha if requested else None
    key = hashlib.sha256(json.dumps([source, want], sort_keys=True).encode()).hexdigest()[:16]
    d = cache / "models" / key
    d.mkdir(parents=True)
    (d / "weights.bin").write_bytes(b"x" * 2048)
    info = {"source": source, "kind": "url" if source.startswith("http") else "s3", "pulled_utc": "2026-10-01T00:00:00Z"}
    if sha:
        info["sha256"] = sha
    if commit:
        info.update(kind="hf", resolved_commit=commit, pinned=True)
    (d / ".decider-lab-source.json").write_text(json.dumps(info))
    return key


def put_hf(hf: pathlib.Path, repo: str, commit: str, ref: str | None = "main") -> None:
    base = hf / ("models--" + repo.replace("/", "--"))
    (base / "blobs").mkdir(parents=True)
    (base / "snapshots" / commit).mkdir(parents=True)
    blob = base / "blobs" / "abc123"
    blob.write_bytes(b"{}" * 100)
    os.symlink(os.path.relpath(blob, base / "snapshots" / commit), base / "snapshots" / commit / "config.json")
    if ref:
        (base / "refs").mkdir()
        (base / "refs" / ref).write_text(commit)


@pytest.fixture
def archive_server(tmp_path):
    root = tmp_path / "srv"
    root.mkdir()
    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    (ckpt / "strands_decider_config.json").write_text("{}")
    (ckpt / "model.bin").write_bytes(os.urandom(4096))
    with tarfile.open(root / "model.tar.gz", "w:gz") as t:
        t.add(ckpt, arcname="ckpt")
    sha = hashlib.sha256((root / "model.tar.gz").read_bytes()).hexdigest()

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(root)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/model.tar.gz", sha
    srv.shutdown()
    srv.server_close()


def wait_job(client, job_id: str, timeout: float = 60.0) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] not in ("queued", "running", "cancelling"):
            return j
        time.sleep(0.2)
    raise AssertionError(f"job {job_id} did not finish")


# ---- models --------------------------------------------------------------------------------------


def test_models_empty(client, isolated_caches):
    r = client.get("/api/models")
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert body["total_gb"] == 0
    assert body["cache_dir"] == str(isolated_caches / "models")
    assert "HF cache" in body["hf_cache_note"]


def test_models_lists_cache_and_hf_entries(client, workspace, isolated_caches, tmp_path):
    url = "https://example.com/weights/mine.tar"
    sha = "9a1f" + "0" * 60
    key = put_cached(isolated_caches, url, sha)
    key2 = put_cached(isolated_caches, "s3://bucket/w/other.tar", "03be" + "1" * 60, requested=False)
    (workspace / "labs" / "serve").mkdir()
    (workspace / "labs" / "serve" / "lab.yaml").write_text(
        f"name: serve-lab\nmodels:\n  mine: {{serve: {url}, sha256: {sha}}}\n  b: {{baseline: majority}}\n")
    put_hf(tmp_path / "hf-hub", "StrandsAgents/strands-decider-2B-hobson-v19", SHA_A)
    put_hf(tmp_path / "hf-hub", "someone/unrelated-model", "c" * 40)
    items = {i["model_key"]: i for i in client.get("/api/models").json()["items"]}
    mine = items[key]
    assert mine["source"] == url and mine["kind"] == "url"
    assert mine["ref"] == sha and mine["ref_kind"] == "sha256" and mine["pinned"] is True
    assert mine["used_by_labs"] == ["serve-lab"] and mine["in_use_by_job"] is None
    assert mine["size_gb"] >= 0 and mine["pulled_at"].endswith("Z") and mine["deletable"] is True
    assert items[key2]["pinned"] is False  # sha256 recorded, not requested
    hf = [i for i in items.values() if i["location"] == "hf"]
    assert len(hf) == 1
    assert hf[0]["source"] == f"hf://StrandsAgents/strands-decider-2B-hobson-v19@{SHA_A}"
    assert hf[0]["ref_kind"] == "commit" and hf[0]["hf_refs"] == ["main"] and hf[0]["deletable"] is False


def test_inspect_hf_policy(client):
    r = client.post("/api/models/inspect", json={"source": "hf://StrandsAgents/strands-decider-2B-hobson-v19@main",
                                                 "require_pinned": True})
    body = r.json()
    assert r.status_code == 200
    assert body["kind"] == "hf" and body["pinned"] is False
    assert body["problems"][0]["code"] == "unpinned_source" and body["problems"][0]["severity"] == "error"
    assert body["command"] == "decider-lab pull hf://StrandsAgents/strands-decider-2B-hobson-v19@main --require-pinned"
    assert body["needs"] == {"env": ["HF_TOKEN?"], "profile": False}
    body = client.post("/api/models/inspect", json={"source": "StrandsAgents/strands-decider-2B-hobson-v19",
                                                    "require_pinned": False}).json()
    assert body["kind"] == "hf" and body["normalized"] == "hf://StrandsAgents/strands-decider-2B-hobson-v19"
    assert [p["severity"] for p in body["problems"]] == ["warning"]
    body = client.post("/api/models/inspect", json={"source": f"hf://StrandsAgents/x-decider@{SHA_A}",
                                                    "require_pinned": True}).json()
    assert body["pinned"] is True and body["problems"] == []


def test_inspect_hf_revision_field_and_cached(client, tmp_path):
    put_hf(tmp_path / "hf-hub", "StrandsAgents/strands-decider-2B-hobson-v19", SHA_A, ref=None)
    body = client.post("/api/models/inspect", json={"source": "hf://StrandsAgents/strands-decider-2B-hobson-v19",
                                                    "revision": SHA_A, "require_pinned": True}).json()
    assert body["pinned"] is True and body["normalized"].endswith("@" + SHA_A)
    assert body["cached"] is True and body["cached_info"]["ref"] == SHA_A


def test_inspect_url_s3_and_sha_policy(client, isolated_caches):
    body = client.post("/api/models/inspect", json={"source": "https://example.com/m.tar"}).json()
    assert body["kind"] == "url" and body["problems"][0]["code"] == "unverified_source"
    assert body["problems"][0]["severity"] == "warning"
    sha = "a" * 64
    key = put_cached(isolated_caches, "https://example.com/m.tar", sha)
    body = client.post("/api/models/inspect", json={"source": "https://example.com/m.tar", "sha256": sha}).json()
    assert body["problems"] == [] and body["pinned"] is True
    assert body["cached"] is True and body["model_key"] == key
    assert body["command"].endswith(f"--sha256 {sha}")
    body = client.post("/api/models/inspect", json={"source": "s3://bucket/w/model.tar", "profile": "heisenberg",
                                                    "region": "us-east-1"}).json()
    assert body["kind"] == "s3" and body["needs"]["profile"] is True
    assert "--profile heisenberg --region us-east-1" in body["command"]
    assert client.put("/api/settings", json={"require_sha256": True}).status_code == 200
    body = client.post("/api/models/inspect", json={"source": "s3://bucket/w/model.tar"}).json()
    assert body["problems"][0]["severity"] == "error"
    body = client.post("/api/models/inspect", json={"source": "s3://bucket/w/prefix/"}).json()
    assert body["problems"] == []  # a prefix records ETags; sha256 does not apply
    body = client.post("/api/models/inspect", json={"source": "https://e.com/m.tar", "sha256": "xyz"}).json()
    assert any(p["path"] == "sha256" and p["severity"] == "error" for p in body["problems"])


def test_inspect_refuses_credentials_without_echoing_them(client, secret_value):
    for src in ("https://user:pw-secret-123@example.com/m.tar",
                "https://bucket.s3.amazonaws.com/m.tar?X-Amz-Signature=deadbeefcafe&X-Amz-Credential=AKIAXX",
                f"https://example.com/m.tar?token={secret_value}"):
        r = client.post("/api/models/inspect", json={"source": src})
        body = r.json()
        assert body["problems"][0]["code"] == "credential_in_source"
        assert "pw-secret-123" not in r.text and "deadbeefcafe" not in r.text
        no_secret(r, secret_value)
        r = client.post("/api/jobs", json={"kind": "pull", "source": src, "require_pinned": False})
        assert r.status_code == 422 and r.json()["error"]["code"] == "credential_in_source"
        assert "pw-secret-123" not in r.text


def test_inspect_local(client, workspace):
    (workspace / "ckpts" / "run-7").mkdir(parents=True)
    body = client.post("/api/models/inspect", json={"source": "ckpts/run-7"}).json()
    assert body["kind"] == "local" and all(p["severity"] == "warning" for p in body["problems"])
    body = client.post("/api/models/inspect", json={"source": "./ckpts/nope"}).json()
    assert body["problems"][0]["code"] == "local_path_missing"
    body = client.post("/api/models/inspect", json={"source": "/etc"}).json()
    assert body["problems"][0]["code"] == "path_outside_workspace"
    body = client.post("/api/models/inspect", json={"source": ""}).json()
    assert body["problems"][0]["code"] == "bad_source"
    assert client.post("/api/models/inspect", json={"source": 3}).status_code == 422


def test_delete_model_typed_confirmation(client, isolated_caches):
    sha = "9a1f03be" + "2" * 56
    key = put_cached(isolated_caches, "https://example.com/a.tar", sha)
    r = client.request("DELETE", f"/api/models/{key}", json={"confirm": "wrong"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "confirm_mismatch"
    r = client.request("DELETE", f"/api/models/{key}", json={})
    assert r.status_code == 409
    r = client.request("DELETE", f"/api/models/{key}", json={"confirm": "9a1f03be"})
    assert r.status_code == 200 and r.json()["deleted"] is True and r.json()["freed_gb"] >= 0
    assert not (isolated_caches / "models" / key).exists()
    assert client.request("DELETE", f"/api/models/{key}", json={"confirm": "9a1f03be"}).status_code == 404
    assert client.request("DELETE", "/api/models/..%2F..", json={"confirm": "x"}).status_code in (404, 400)
    assert client.request("DELETE", "/api/models/hf-0123456789abcdef", json={"confirm": "x"}).status_code == 400


def test_pull_local_directory_job(client, workspace):
    ck = workspace / "ckpts" / "run-7"
    ck.mkdir(parents=True)
    (ck / "strands_decider_config.json").write_text("{}")
    r = client.post("/api/jobs", json={"kind": "pull", "source": "ckpts/run-7", "revision": None, "sha256": None,
                                       "require_pinned": True, "profile": None, "region": None})
    assert r.status_code == 202, r.text
    started = r.json()
    assert started["job_id"].startswith("j_") and started["command"] == "decider-lab pull ckpts/run-7"
    j = wait_job(client, started["job_id"])
    assert j["status"] == "succeeded", j
    assert j["kind"] == "pull"
    assert j["result"]["info"]["kind"] == "local"
    assert [s["status"] for s in j["stages"]][-1] == "done"


def test_pull_https_archive_with_sha256(client, archive_server):
    url, sha = archive_server
    r = client.post("/api/jobs", json={"kind": "pull", "source": url, "sha256": sha, "require_pinned": True})
    assert r.status_code == 202, r.text
    j = wait_job(client, r.json()["job_id"])
    assert j["status"] == "succeeded", j
    assert j["result"]["model_key"] and j["result"]["info"]["sha256"] == sha
    assert {s["name"]: s["status"] for s in j["stages"]} == {"resolve": "done", "download": "done", "verify": "done",
                                                             "extract": "done", "done": "done"}
    items = client.get("/api/models").json()["items"]
    hit = [i for i in items if i["model_key"] == j["result"]["model_key"]]
    assert hit and hit[0]["pinned"] is True and hit[0]["ref"] == sha and hit[0]["source"] == url
    body = client.post("/api/models/inspect", json={"source": url, "sha256": sha}).json()
    assert body["cached"] is True


def test_pull_sha256_mismatch_fails_with_message(client, archive_server):
    url, _sha = archive_server
    r = client.post("/api/jobs", json={"kind": "pull", "source": url, "sha256": "0" * 64})
    j = wait_job(client, r.json()["job_id"])
    assert j["status"] == "failed"
    assert any("sha256 does not match" in f for f in j["failures"])
    assert any(s["status"] == "failed" for s in j["stages"])
    assert client.get("/api/models").json()["items"] == []


def test_pull_validation(client):
    r = client.post("/api/jobs", json={"kind": "pull", "source": "hf://StrandsAgents/x@main", "require_pinned": True})
    assert r.status_code == 422 and r.json()["error"]["code"] == "unpinned_source"
    r = client.post("/api/jobs", json={"kind": "pull", "source": "./missing-dir"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "source_invalid"
    r = client.post("/api/jobs", json={"kind": "pull", "source": "s3://b/k.tar", "profile": "bad profile; rm"})
    assert r.status_code == 422


# ---- data: suites --------------------------------------------------------------------------------


def test_suites_list(client, workspace):
    (workspace / "data" / "bad.jsonl").write_text(json.dumps(
        {"kind": "choice", "state": "s", "instructions": "q", "options": [["a", "a"], ["b", "b"]], "label": 4}) + "\n")
    (workspace / "labs" / "first" / "runs" / "first-lab" / "extra.jsonl").write_text("{}\n")  # under runs/: skipped
    body = client.get("/api/suites").json()
    names = [b["name"] for b in body["builtins"]]
    assert names == ["smoke", "synthetic", "heldout"]
    smoke = body["builtins"][0]
    assert smoke["rows_estimate"] == 90 and smoke["available"] is True
    synth = body["builtins"][1]
    assert synth["param_schema"]["per_kind"] == {"type": "int", "min": 1, "max": 5000}
    assert "chess" in synth["param_schema"]["families"]["options"]
    files = {f["path"]: f for f in body["files"]}
    assert set(files) == {"data/eval.jsonl", "data/bad.jsonl"}
    assert files["data/eval.jsonl"]["valid"] is True and files["data/eval.jsonl"]["rows"] == 30
    assert files["data/eval.jsonl"]["ref"] == "file:" + fid("data/eval.jsonl")
    assert files["data/bad.jsonl"]["valid"] is False and files["data/bad.jsonl"]["problem"].startswith("row 0: label")
    assert "smoke" in body["used_by_labs"] and "synthetic:per_kind=40" in body["used_by_labs"]


def test_suite_stats_builtin_and_file(client):
    r = client.get("/api/suites/smoke/stats")
    body = r.json()
    assert r.status_code == 200
    assert body["rows"] == 90 and body["by_kind"] == {"noul": 30, "choice": 30, "score": 30}
    assert len(body["sha256"]) == 64 and set(body["label_balance"]) == {"noul", "choice", "score"}
    assert body["by_split"] == {"dev": 36, "test": 54}
    body = client.get("/api/suites/synthetic%3Aper_kind%3D5%2Cseed%3D3/stats").json()
    assert body["rows"] == 45 and body["params"]["seed"] == 3
    body = client.get("/api/suites/" + "file%3A" + fid("data/eval.jsonl") + "/stats").json()
    assert body["rows"] == 30 and body["name"] == "eval" and body["params"] == {"file": "data/eval.jsonl"}
    assert client.get("/api/suites/nope/stats").status_code == 404
    assert client.get("/api/suites/synthetic%3Aper_kind%3D0/stats").status_code == 422
    assert client.get("/api/suites/synthetic%3Afamilies%3Dpoker/stats").status_code == 422
    r = client.get("/api/suites/file%3A" + fid("../outside.jsonl") + "/stats")
    assert r.status_code == 400 and r.json()["error"]["code"] == "path_outside_workspace"


def test_suite_stats_invalid_file(client, workspace):
    (workspace / "data" / "bad.jsonl").write_text('{"kind": "x"}\n')
    r = client.get("/api/suites/file%3A" + fid("data/bad.jsonl") + "/stats")
    assert r.status_code == 422 and r.json()["error"]["code"] == "rows_invalid"
    assert r.json()["error"]["detail"]["problem"].startswith("row 0: kind")
    assert str(workspace) not in r.text


def test_heldout_unavailable_and_build_job(client, monkeypatch):
    from decider_lab.ui import api_data

    monkeypatch.setattr(api_data, "_heldout_available", lambda: False)
    r = client.get("/api/suites/heldout/stats")
    assert r.status_code == 422 and r.json()["error"]["code"] == "suite_unavailable"
    assert client.get("/api/suites").json()["builtins"][2]["available"] is False
    monkeypatch.setattr(api_data, "_heldout_available", lambda: True)
    st = client.app.state.studio
    started = {}

    def fake_start(kind, argv, **kw):
        started.update(kind=kind, argv=argv)
        return {"job_id": "j_000000000001"}

    monkeypatch.setattr(st.jobs, "start", fake_start)
    r = client.get("/api/suites/heldout/stats")
    assert r.status_code == 202 and r.json() == {"job_id": "j_000000000001"}
    assert started["kind"] == "suite_build" and started["argv"][-3:] == ["suites", "--build", "heldout"]


def test_suite_rows_paging_filters_and_search(client):
    page = client.get("/api/suites/smoke/rows", params={"limit": 10}).json()
    assert page["total"] == 90 and page["offset"] == 0 and page["limit"] == 10 and len(page["items"]) == 10
    assert page["items"][0]["index"] == 0 and page["facets"]["kinds"] == ["choice", "noul", "score"]
    nxt = client.get("/api/suites/smoke/rows", params={"limit": 10, "offset": 85}).json()
    assert len(nxt["items"]) == 5 and nxt["items"][0]["index"] == 85
    noul = client.get("/api/suites/smoke/rows", params={"kind": "noul", "split": "dev"}).json()
    assert noul["total"] == 12 and all(r["kind"] == "noul" and r["split"] == "dev" for r in noul["items"])
    task = page["facets"]["tasks"][0]
    by_task = client.get("/api/suites/smoke/rows", params={"task": task}).json()
    assert by_task["total"] == 30
    some = page["items"][3]
    word = some["instructions"].split()[0]
    hits = client.get("/api/suites/smoke/rows", params={"q": some["id"]}).json()
    assert hits["total"] == 1 and hits["items"][0]["id"] == some["id"]
    assert client.get("/api/suites/smoke/rows", params={"q": word.upper()}).json()["total"] >= 1
    assert client.get("/api/suites/smoke/rows", params={"limit": 500}).status_code == 422


def test_suite_rows_truncate_long_state(client, workspace):
    row = {"kind": "noul", "state": "x" * 5000, "instructions": "q?",
           "options": [["false", "no"], ["true", "yes"]], "label": 1, "task": "t"}
    (workspace / "data" / "long.jsonl").write_text(json.dumps(row) + "\n")
    item = client.get("/api/suites/file%3A" + fid("data/long.jsonl") + "/rows").json()["items"][0]
    assert item["state_truncated"] is True and len(item["state"]) == 2000


# ---- data: tools ---------------------------------------------------------------------------------


def test_export_suite(client, workspace):
    r = client.post("/api/data/export-suite", json={"ref": "smoke", "out": "data/smoke-copy.jsonl"})
    assert r.status_code == 200 and r.json()["rows"] == 90 and r.json()["file_id"] == fid("data/smoke-copy.jsonl")
    assert len((workspace / "data" / "smoke-copy.jsonl").read_text().splitlines()) == 90
    r = client.post("/api/data/export-suite", json={"ref": "smoke", "out": "data/smoke-copy.jsonl"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "file_exists"
    r = client.post("/api/data/export-suite", json={"ref": "synthetic:per_kind=2", "out": "data/smoke-copy.jsonl",
                                                    "overwrite": True})
    assert r.status_code == 200 and r.json()["rows"] == 18
    assert client.post("/api/data/export-suite", json={"ref": "smoke", "out": "../x.jsonl"}).status_code == 400
    assert client.post("/api/data/export-suite", json={"ref": "smoke", "out": "/tmp/x.jsonl"}).status_code == 400
    assert client.post("/api/data/export-suite", json={"ref": "smoke", "out": "data/x.txt"}).status_code == 422


CSV_OK = ("state,question,answer,options,kind,notes\n"
          "The sky is green.,Is this true?,no,,,n1\n"
          "Pick a fruit,Which is a fruit?,apple,apple|rock|car,,\n"
          "Review: great product,How positive?,good,bad|ok|good,score,\n")


def test_csv_preview_and_convert(client, workspace):
    files = {"file": ("my rows.csv", b"\xef\xbb\xbf" + CSV_OK.encode(), "text/csv")}
    r = client.post("/api/data/csv/preview", files=files, data={"task": "custom", "delimiter": ","})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["upload_id"].startswith("u_") and body["filename"] == "my rows.csv"
    assert body["suggested_out"] == "data/my-rows.jsonl"
    assert body["rows_total"] == 3 and body["errors"] == []
    assert body["columns"] == {"found": ["state", "question", "answer", "options", "kind"], "missing_required": [],
                               "ignored": ["notes"]}
    assert [p["kind"] for p in body["preview"]] == ["noul", "choice", "score"]
    assert body["preview"][2]["label"] == 2 and body["stats"]["rows"] == 3
    r = client.post("/api/data/csv/convert", json={"upload_id": body["upload_id"], "out": "data/my.jsonl",
                                                   "task": "custom", "delimiter": ","})
    assert r.status_code == 200, r.text
    assert r.json()["rows"] == 3 and r.json()["path"] == "data/my.jsonl"
    lines = (workspace / "data" / "my.jsonl").read_text().splitlines()
    assert json.loads(lines[1])["options"] == [["apple", "apple"], ["rock", "rock"], ["car", "car"]]
    r = client.post("/api/data/csv/convert", json={"upload_id": body["upload_id"], "out": "data/my2.jsonl"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "upload_not_found"


def test_csv_preview_errors_and_missing_columns(client):
    bad = CSV_OK + "s,q,maybe,,,\n,q,yes,,,\n"
    body = client.post("/api/data/csv/preview", files={"file": ("x.csv", bad.encode(), "text/csv")}).json()
    assert body["rows_total"] == 5 and [e["row"] for e in body["errors"]] == [5, 6]
    assert "yes or no" in body["errors"][0]["message"] and "missing state" in body["errors"][1]["message"]
    assert body["stats"] is None
    r = client.post("/api/data/csv/convert", json={"upload_id": body["upload_id"], "out": "data/bad.jsonl"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "rows_invalid"
    body = client.post("/api/data/csv/preview", files={"file": ("y.csv", b"text;label\na;b\n", "text/csv")},
                       data={"delimiter": ";"}).json()
    assert body["columns"]["missing_required"] == ["state", "question", "answer"]
    assert body["columns"]["ignored"] == ["text", "label"]
    r = client.post("/api/data/csv/preview", files={"file": ("y.csv", b"a,b\n", "text/csv")}, data={"delimiter": "x"})
    assert r.status_code == 422
    r = client.post("/api/data/csv/preview", files={"file": ("z.csv", b"\xff\xfe\x00bad", "text/csv")})
    assert r.status_code == 422 and r.json()["error"]["code"] == "rows_invalid"


def test_generate_with_exclusions(client, workspace):
    r = client.post("/api/data/generate", json={"out": "data/train.jsonl", "families": ["arithmetic", "calendar"],
                                                "per_kind": 20, "seed": 1, "exclude_suites": ["smoke"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rows"] + body["dropped_overlapping"] == 20 * 2 * 3
    assert body["stats"]["rows"] == body["rows"] and body["file_id"] == fid("data/train.jsonl")
    assert "--exclude-suite smoke" in body["command"]
    smoke_ids = {r["id"] for r in suites.load_suite("smoke")[1]}
    written = [json.loads(x) for x in (workspace / "data" / "train.jsonl").read_text().splitlines()]
    assert not smoke_ids & {w["id"] for w in written}
    assert client.post("/api/data/generate", json={"out": "data/train.jsonl"}).status_code == 409
    for bad in ({"families": ["poker"]}, {"per_kind": 0}, {"per_kind": "3"}, {"families": []}, {"seed": 1.5}):
        assert client.post("/api/data/generate", json={"out": "data/g2.jsonl", **bad}).status_code == 422


def test_generate_large_becomes_job(client, monkeypatch):
    st = client.app.state.studio
    seen = {}

    def fake_start(kind, argv, **kw):
        seen.update(kind=kind, argv=argv)
        return {"job_id": "j_000000000002"}

    monkeypatch.setattr(st.jobs, "start", fake_start)
    r = client.post("/api/data/generate", json={"out": "data/big.jsonl", "families": ["arithmetic", "calendar",
                                                                                      "seating"], "per_kind": 4000})
    assert r.status_code == 202 and r.json()["job_id"] == "j_000000000002"
    assert seen["argv"][-9:] == ["generate", "--out", "data/big.jsonl", "--families", "arithmetic,calendar,seating",
                                 "--per-kind", "4000", "--seed", "1"]


def test_split_and_check(client, workspace):
    client.post("/api/data/export-suite", json={"ref": "synthetic:per_kind=10", "out": "data/s.jsonl"})
    r = client.post("/api/data/split", json={"file_id": fid("data/s.jsonl"), "out": "data/s-split.jsonl",
                                             "dev_fraction": 0.5, "seed": 0})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["by_split"] == {"dev": 45, "test": 45} and body["dev_by_kind"]["noul"] == 15
    assert "data/s-split.jsonl" in body["snippet"]
    for frac in (0, 1, 1.5, "x"):
        r = client.post("/api/data/split", json={"file_id": fid("data/s.jsonl"), "out": "data/z.jsonl",
                                                 "dev_fraction": frac})
        assert r.status_code == 422
    assert client.post("/api/data/check", json={"file_id": fid("data/s-split.jsonl")}).json()["valid"] is True
    (workspace / "data" / "bad.jsonl").write_text("not json\n")
    body = client.post("/api/data/check", json={"file_id": fid("data/bad.jsonl")}).json()
    assert body["valid"] is False and body["problem"].startswith("line 1") and body["stats"] is None
    assert client.post("/api/data/check", json={"file_id": fid("data/none.jsonl")}).status_code == 404


def test_leakcheck_detects_overlap_and_writes_clean_copy(client, workspace):
    body = client.post("/api/data/leakcheck", json={"train_file_id": fid("data/eval.jsonl"),
                                                    "against": ["smoke"], "drop_to": "data/clean.jsonl"}).json()
    assert body["train_rows"] == 30 and body["eval_rows"] == 90
    assert body["overlapping"] == 30 and body["passed"] is False and len(body["examples"]) == 5
    assert body["clean"]["rows"] == 0 and body["clean"]["path"] == "data/clean.jsonl"
    assert "overlapping_indices" not in body
    row = {"kind": "noul", "state": "A completely unique state 8812", "instructions": "q?",
           "options": [["false", "no"], ["true", "yes"]], "label": 0, "task": "t"}
    (workspace / "data" / "uniq.jsonl").write_text(json.dumps(row) + "\n")
    body = client.post("/api/data/leakcheck", json={"train_file_id": fid("data/uniq.jsonl"),
                                                    "against": ["smoke", "file:" + fid("data/eval.jsonl")]}).json()
    assert body["overlapping"] == 0 and body["passed"] is True and body["clean"] is None
    assert body["eval_rows"] == 120
    assert client.post("/api/data/leakcheck", json={"train_file_id": fid("data/uniq.jsonl"),
                                                    "against": []}).status_code == 422
    r = client.post("/api/data/leakcheck", json={"train_file_id": fid("data/uniq.jsonl"), "against": ["smoke"],
                                                 "drop_to": "data/eval.jsonl"})
    assert r.status_code == 409


def test_files_raw_serves_images_only(client, workspace):
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    (workspace / "data" / "a.png").write_bytes(png)
    (workspace / "data" / "evil.png").write_bytes(b"<svg onload=alert(1)>")
    r = client.get("/api/files/raw", params={"file_id": fid("data/a.png")})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content == png
    r = client.get("/api/files/raw", params={"file_id": fid("data/evil.png")})
    assert r.status_code == 415 and r.json()["error"]["code"] == "unsupported_media"
    assert client.get("/api/files/raw", params={"file_id": fid("data/none.png")}).status_code == 404


# ---- overview ------------------------------------------------------------------------------------


def test_overview_seeded(client, secret_value):
    r = client.get("/api/overview")
    assert r.status_code == 200
    no_secret(r, secret_value)
    body = r.json()
    k = body["kpis"]
    assert k["labs"] == 3 and k["labs_invalid"] == 1 and k["run_roots"] == 1
    assert k["runs_scored"] == 9  # fake/majority/uniform x smoke, synthetic and synthetic+cal
    best = k["best"]
    assert best["suite"] == "synthetic" and best["model"] == "fake" and best["lab"] == "first-lab"
    assert best["tied_with"] == 0 and best["suite_rows"] > 0 and len(best["ci95"]) == 2
    assert k["jobs_active"] == 0 and k["jobs_active_remote"] == 0
    cloud = k["cloud"]
    assert cloud["known"] is True and cloud["instances"] == 2 and cloud["idle_instances"] == 2
    assert cloud["usd_per_hour"] == pytest.approx(0.612 + 1.861) and cloud["errors"] == []
    assert cloud["vast"]["credit_usd"] == 25.4 and cloud["aws"]["account"] == "123456789012"
    recent = body["recent_results"]
    assert {(x["model"], x["suite"]) for x in recent} == {(m, s) for m in ("fake", "majority", "uniform")
                                                          for s in ("smoke", "synthetic")}
    assert all(x["status"] == "ok" and not x["calibrated"] for x in recent)
    assert next(x for x in recent if x["model"] == "majority")["is_baseline"] is True
    roots = body["recent_roots"]
    assert roots[0]["title"] == "first-lab"
    syn = next(s for s in roots[0]["suites"] if s["suite"] == "synthetic")
    assert syn["top"]["model"] == "fake" and syn["baseline"]["model"] == "majority"
    assert body["onboarding"]["has_lab"] and body["onboarding"]["has_run"] and body["onboarding"]["has_results"]
    assert body["active_jobs"] == []


def test_overview_failures_and_ties(client, workspace):
    root = workspace / "labs" / "first" / "runs" / "first-lab"
    lab = json.loads((root / "lab.json").read_text())
    lab["failures"] = ["nova: AccessDeniedException: not authorized"]
    (root / "lab.json").write_text(json.dumps(lab))
    # a second non-baseline model on synthetic whose CI overlaps the top
    import shutil

    shutil.copytree(root / "fake" / "synthetic", root / "twin" / "synthetic")
    body = client.get("/api/overview").json()
    failed = [x for x in body["recent_results"] if x["status"] == "failed"]
    assert failed and failed[0]["model"] == "nova" and "AccessDenied" in failed[0]["message"]
    assert failed[0]["intelligence"] is None
    assert body["kpis"]["best"]["tied_with"] == 1


def test_overview_empty_workspace_and_onboarding(make_client, empty_workspace):
    c = make_client(empty_workspace)
    body = c.get("/api/overview").json()
    k = body["kpis"]
    assert k["labs"] == 0 and k["run_roots"] == 0 and k["runs_scored"] == 0 and k["best"] is None
    assert body["recent_results"] == [] and body["recent_roots"] == []
    assert body["onboarding"] == {"doctor_seen": False, "has_lab": False, "has_run": False, "has_results": False,
                                  "dismissed": False}
    c.get("/api/system/doctor")
    c.put("/api/settings", json={"onboarding_dismissed": True})
    ob = c.get("/api/overview").json()["onboarding"]
    assert ob["doctor_seen"] is True and ob["dismissed"] is True


def test_overview_counts_active_jobs_and_owned_machines(client, workspace):
    st = client.app.state.studio
    job = st.jobs.start("run", ["/bin/sleep", "30"], title="run first-lab on vast.ai", cwd=str(workspace),
                        backend="vast")
    try:
        st.jobs.update(job["job_id"], machine_id="9876543")
        body = client.get("/api/overview").json()
        assert body["kpis"]["jobs_active"] == 1 and body["kpis"]["jobs_active_remote"] == 1
        assert body["active_jobs"][0]["job_id"] == job["job_id"]
        assert body["kpis"]["cloud"]["idle_instances"] == 1  # the vast one is owned by the job
    finally:
        st.jobs.cancel(job["job_id"])


def test_overview_cached_models_kpi(client, isolated_caches):
    put_cached(isolated_caches, "https://example.com/z.tar", "d" * 64)
    k = client.get("/api/overview").json()["kpis"]
    assert k["cached_models"] == 1 and k["cached_models_gb"] >= 0


def test_area_endpoints_need_auth(anon_client):
    for path in ("/api/models", "/api/suites", "/api/overview", "/api/suites/smoke/rows"):
        assert anon_client.get(path).status_code == 401
    r = anon_client.post("/api/data/check", json={"file_id": "x"}, headers={"Authorization": "Bearer nope"})
    assert r.status_code in (401, 403)


def test_upload_is_multipart_only(client):
    r = client.post("/api/data/csv/preview", json={"file": "x"})
    assert r.status_code == 422


def test_quick_eval_job_runs_and_shows_on_overview(client, workspace):
    r = client.post("/api/jobs", json={"kind": "eval", "model": {"type": "baseline", "value": "majority"},
                                       "name": "maj", "suite": "smoke", "split": None, "limit": 5, "workers": 2,
                                       "vision": False, "out": None})
    assert r.status_code == 202, r.text
    assert r.json()["command"] == ("decider-lab eval --model majority --suite smoke --name maj --workers 2 "
                                   "--out runs/maj/smoke --limit 5")
    j = wait_job(client, r.json()["job_id"])
    assert j["status"] == "succeeded", j
    assert (workspace / "runs" / "maj" / "smoke" / "scores.json").exists()
    body = client.get("/api/overview").json()
    assert any(x["model"] == "maj" and x["suite"] == "smoke" for x in body["recent_results"])
    assert any(lab["name"] == "first-lab" and lab["valid"] for lab in body["labs"])


def test_quick_eval_validation(client):
    base = {"kind": "eval", "model": {"type": "baseline", "value": "majority"}, "name": "m", "suite": "smoke"}
    for patch in ({"model": {"type": "baseline", "value": "best"}}, {"model": {"type": "url", "value": "ftp://x"}},
                  {"model": {"type": "python", "value": "no colon"}}, {"model": {"type": "bedrock", "value": "x"}},
                  {"name": "bad name"}, {"workers": 0}, {"limit": -1}, {"split": "train"}):
        r = client.post("/api/jobs", json={**base, **patch})
        assert r.status_code == 422, patch
    assert client.post("/api/jobs", json={**base, "suite": "nope"}).status_code == 404
    assert client.post("/api/jobs", json={**base, "out": "/tmp/x"}).status_code == 400
    r = client.post("/api/jobs", json={**base, "model": {"type": "url", "value": "http://u:p@127.0.0.1:9/"}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "credential_in_source"
    assert client.post("/api/jobs", json={"kind": "jevbench"}).status_code == 422
