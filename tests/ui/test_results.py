"""Results API (API.md section 7) against the seeded workspace (tests/ui/ui_seed.py).

first-lab: fake (System One stub) + majority (baseline) + uniform; smoke and synthetic:per_kind=40;
calibrated synthetic for every model (smoke has too few dev rows for +cal).
"""

from __future__ import annotations

import csv
import io
import json
import shutil
from urllib.parse import quote

import pytest
from ui_seed import FIRST_ROOT

from decider_lab.ui.api_results import PROXY_NOTE, verdict
from decider_lab.ui.workspace import encode_id

ROOT = encode_id(FIRST_ROOT)


def _ok(r):
    assert r.status_code == 200, r.text
    return r.json()


def _err(r, status, code):
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code
    return r.json()["error"]


def test_list_run_roots(client):
    items = _ok(client.get("/api/runs"))["items"]
    root = next(i for i in items if i["root_id"] == ROOT)
    assert root["lab"] == "first-lab"
    assert root["path"] == FIRST_ROOT
    assert root["lab_id"] == encode_id("labs/first/lab.yaml")
    assert root["models"] == ["fake", "majority", "uniform"]
    assert root["suites"] == ["smoke", "synthetic"]
    assert root["has_calibrated"] is True and root["has_jevbench"] is False
    assert root["baseline"] == "majority" and root["failures"] == []
    assert root["best"]["suite"] == "synthetic"  # the suite with the most scored rows
    assert root["best"]["model"] != "majority"   # never the baseline
    assert len(root["best"]["ci95"]) == 2
    assert {b["suite"] for b in root["best_by_suite"]} == {"smoke", "synthetic"}
    assert root["proxy_note"] == PROXY_NOTE


def test_list_filters(client):
    assert _ok(client.get("/api/runs", params={"q": "first"}))["items"]
    assert _ok(client.get("/api/runs", params={"q": "zzz-nothing"}))["items"] == []
    assert _ok(client.get("/api/runs", params={"lab_id": encode_id("labs/second/lab.yaml")}))["items"] == []
    assert _ok(client.get("/api/runs", params={"lab_id": encode_id("labs/first/lab.yaml")}))["items"]


def test_root_detail(client, secret_value):
    d = _ok(client.get(f"/api/runs/{ROOT}"))
    assert d["title"] == "first-lab" and d["baseline"] == "majority"
    names = [m["name"] for m in d["models"]]
    assert names == ["fake", "majority", "uniform"]
    by = {m["name"]: m for m in d["models"]}
    assert by["fake"]["color_index"] == 0 and by["fake"]["kind"] == "url"
    assert by["majority"]["is_baseline"] and by["majority"]["color_index"] is None
    assert by["uniform"]["kind"] == "baseline"
    suites = {s["name"]: s for s in d["suites"]}
    assert suites["synthetic"]["has_calibrated"] and not suites["smoke"]["has_calibrated"]
    assert suites["synthetic"]["scored_split"] == "test"
    assert d["report_md_available"] is True
    assert d["lab_json"]["name"] == "first-lab"
    assert secret_value not in json.dumps(d)


def test_root_not_found_and_outside(client):
    _err(client.get(f"/api/runs/{encode_id('labs/nothing')}"), 404, "not_found")
    _err(client.get(f"/api/runs/{encode_id('labs/first')}"), 404, "not_found")  # a dir without results
    _err(client.get(f"/api/runs/{encode_id('../..')}"), 400, "path_outside_workspace")


def test_leaderboard_raw(client):
    lb = _ok(client.get(f"/api/runs/{ROOT}/leaderboard", params={"suite": "synthetic"}))
    assert lb["proxy_note"] == PROXY_NOTE
    assert lb["scored_split"] == "test" and lb["baseline"] == "majority"
    rows = lb["rows"]
    assert [r["variant"] for r in rows] == ["raw"] * 3
    vals = [r["intelligence"] for r in rows]
    assert vals == sorted(vals, reverse=True)
    for r in rows:
        assert r["status"] == "ok" and len(r["ci95"]) == 2 and r["ci95"][0] <= r["intelligence"] <= r["ci95"][1]
        assert r["n"] == 216 and r["errors"] == 0 and r["tie_group"] is not None
        assert r["latency_s"]["median"] >= 0
    assert lb["same_rows"]["consistent"] is True
    assert set(lb["same_rows"]["n_by_model"]) == {"fake", "majority", "uniform"}
    lo = min(min(r["ci95"][0], r["intelligence"]) for r in rows)
    assert lb["domain"][0] == pytest.approx(max(-100, lo - 5), abs=0.11)


def test_leaderboard_cal_and_both(client):
    cal = _ok(client.get(f"/api/runs/{ROOT}/leaderboard", params={"suite": "synthetic", "scores": "cal"}))
    assert {r["variant"] for r in cal["rows"]} == {"cal"}
    assert all(r["temperatures"] and set(r["temperatures"]) == {"noul", "choice", "score"} for r in cal["rows"])
    both = _ok(client.get(f"/api/runs/{ROOT}/leaderboard", params={"suite": "synthetic", "scores": "both"}))
    rows = both["rows"]
    assert len(rows) == 6
    for i in range(0, 6, 2):  # each +cal row sits right under its raw row
        assert rows[i]["variant"] == "raw" and rows[i + 1]["variant"] == "cal"
        assert rows[i]["model"] == rows[i + 1]["model"]
    # smoke has no +cal: cal rows are "missing" with a reason, never invented numbers
    smoke = _ok(client.get(f"/api/runs/{ROOT}/leaderboard", params={"suite": "smoke", "scores": "cal"}))
    assert all(r["status"] == "missing" and r["intelligence"] is None and r["message"] for r in smoke["rows"])


def test_leaderboard_errors(client):
    _err(client.get(f"/api/runs/{ROOT}/leaderboard", params={"suite": "jevbench"}), 400, "use_jevbench_endpoint")
    _err(client.get(f"/api/runs/{ROOT}/leaderboard", params={"suite": "nope"}), 404, "not_found")
    _err(client.get(f"/api/runs/{ROOT}/leaderboard", params={"suite": "smoke", "scores": "x"}), 422, "bad_request")
    _err(client.get(f"/api/runs/{ROOT}/leaderboard"), 422, "bad_request")


def test_leaderboard_failed_model_and_not_same_rows(client, workspace):
    root = workspace / FIRST_ROOT
    lab = json.loads((root / "lab.json").read_text())
    lab["models"]["nova"] = {"bedrock": "us.amazon.nova-pro-v1:0"}
    lab["failures"] = ["nova: ThrottlingException: Rate exceeded"]
    (root / "lab.json").write_text(json.dumps(lab))
    # uniform scored fewer rows (as after --limit): the same-rows check must say so
    preds = (root / "uniform" / "synthetic" / "predictions.jsonl").read_text().splitlines()
    scores = json.loads((root / "uniform" / "synthetic" / "scores.json").read_text())
    scores["n"] = 90
    (root / "uniform" / "synthetic" / "scores.json").write_text(json.dumps(scores))
    (root / "uniform" / "synthetic" / "predictions.jsonl").write_text("\n".join(preds[:120]) + "\n")
    lb = _ok(client.get(f"/api/runs/{ROOT}/leaderboard", params={"suite": "synthetic"}))
    last = lb["rows"][-1]
    assert last["model"] == "nova" and last["status"] == "failed" and "Throttling" in last["message"]
    assert last["intelligence"] is None
    assert lb["same_rows"]["consistent"] is False
    assert lb["same_rows"]["n_by_model"]["uniform"] == 90
    roots = _ok(client.get("/api/runs"))["items"]
    assert next(r for r in roots if r["root_id"] == ROOT)["failures"] == lab["failures"]


def test_leaderboard_exports(client):
    r = client.get(f"/api/runs/{ROOT}/leaderboard.csv", params={"suite": "synthetic", "scores": "both"})
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0][2] == "intelligence_local_proxy" and rows[0][3:5] == ["ci95_lo", "ci95_hi"]
    assert len(rows) == 7
    md = client.get(f"/api/runs/{ROOT}/leaderboard.md", params={"suite": "synthetic"})
    assert md.status_code == 200
    assert PROXY_NOTE in md.text and "95% CI" in md.text and "Intelligence (local proxy)" in md.text


def test_run_detail(client):
    d = _ok(client.get(f"/api/runs/{ROOT}/runs/fake/synthetic"))
    assert d["ref"] == {"root_id": ROOT, "model": "fake", "suite": "synthetic"}
    assert d["scores"]["n"] == 216 and d["has_calibrated"] is True and d["calibration"] is None
    assert d["run"]["suite_sha256"] and d["run"]["answerer"]["adapter"] == "systemone"
    assert d["files"]["predictions"].endswith("fake/synthetic/predictions.jsonl")
    cal = _ok(client.get(f"/api/runs/{ROOT}/runs/fake/{quote('synthetic+cal', safe='')}"))
    assert cal["calibration"]["fit_split"] == "dev" and set(cal["calibration"]["temperatures"]) == {"noul", "choice", "score"}
    _err(client.get(f"/api/runs/{ROOT}/runs/fake/nope"), 404, "not_found")
    _err(client.get(f"/api/runs/{ROOT}/runs/..%2F..%2Flabs/smoke"), 404, "not_found")


def test_predictions_paged_and_filtered(client):
    base = f"/api/runs/{ROOT}/runs/fake/synthetic/predictions"
    page = _ok(client.get(base, params={"limit": 10}))
    assert page["total"] == 360 and len(page["items"]) == 10 and page["limit"] == 10
    p = page["items"][0]
    for k in ("id", "task", "kind", "split", "label", "n", "probs", "top", "correct", "proxy_right", "in_band"):
        assert k in p
    noul = _ok(client.get(base, params={"kind": "noul", "split": "test", "limit": 500}))
    assert noul["total"] == 72 and all(i["kind"] == "noul" and i["in_band"] is not None for i in noul["items"])
    right = _ok(client.get(base, params={"correct": "1", "limit": 500}))
    wrong = _ok(client.get(base, params={"correct": "0", "limit": 500}))
    assert right["total"] + wrong["total"] == 360
    score = _ok(client.get(base, params={"kind": "score", "limit": 1}))["items"][0]
    assert score["proxy_right"] is None and score["expected_level"] is not None
    assert _ok(client.get(base, params={"error": "1"}))["total"] == 0
    _err(client.get(base, params={"limit": 501}), 422, "bad_request")
    csv_r = client.get(f"/api/runs/{ROOT}/runs/fake/synthetic/predictions.csv")
    assert csv_r.status_code == 200
    lines = list(csv.reader(io.StringIO(csv_r.text)))
    assert lines[0][:3] == ["id", "task", "kind"] and len(lines) == 361


def test_reliability(client):
    r = _ok(client.get(f"/api/runs/{ROOT}/runs/fake/synthetic/reliability"))
    assert r["kind"] == "all" and r["split"] == "test" and r["n"] == 216
    assert sum(b["n"] for b in r["bins"]) == 216
    for b in r["bins"]:
        assert 0 <= b["lo"] < b["hi"] <= 1 and 0 <= b["accuracy"] <= 1 and b["lo"] <= b["mean_conf"] <= b["hi"]
    scores = _ok(client.get(f"/api/runs/{ROOT}/runs/fake/synthetic"))["scores"]
    assert r["ece"] == pytest.approx(scores["ece"], abs=1e-3)  # same binning as metrics.ece
    assert r["calibrated"] is not None and r["calibrated"]["n"] == 216
    k = _ok(client.get(f"/api/runs/{ROOT}/runs/fake/synthetic/reliability", params={"kind": "noul", "bins": 5}))
    assert k["n"] == 72 and all(round((b["hi"] - b["lo"]) * 5, 3) == 1 for b in k["bins"])
    smoke = _ok(client.get(f"/api/runs/{ROOT}/runs/fake/smoke/reliability"))
    assert smoke["calibrated"] is None
    _err(client.get(f"/api/runs/{ROOT}/runs/fake/synthetic/reliability", params={"bins": 3}), 422, "bad_request")
    _err(client.get(f"/api/runs/{ROOT}/runs/fake/synthetic/reliability", params={"kind": "x"}), 422, "bad_request")


def test_latency(client):
    r = _ok(client.get(f"/api/runs/{ROOT}/runs/fake/synthetic/latency"))
    assert r["n"] == 216 and r["errors"] == 0 and r["p50"] <= r["p95"]
    assert sum(h["ok"] + h["failed"] for h in r["histogram"]) == 216
    assert len(r["histogram"]) in (1, 30)
    agg = _ok(client.get(f"/api/runs/{ROOT}/latency", params={"suite": "synthetic"}))
    assert [i["model"] for i in agg["items"]] == ["fake", "majority", "uniform"]
    assert agg["same_machine"] is True and agg["items"][0]["workers"] == 4


def test_vs_baseline(client):
    vb = _ok(client.get(f"/api/runs/{ROOT}/vs-baseline", params={"suite": "synthetic"}))
    assert vb["baseline"] == "majority" and vb["split"] == "test"
    assert [i["model"] for i in vb["items"]] == ["fake", "uniform"]
    for i in vb["items"]:
        assert i["n_paired"] == 216
        for m in ("intelligence", "accuracy", "nll"):
            assert len(i[m]["ci95"]) == 2 and i[m]["verdict"] in ("better", "worse", "unclear")
            assert i["verdict"][m] == i[m]["verdict"]
    cal = _ok(client.get(f"/api/runs/{ROOT}/vs-baseline", params={"suite": "synthetic", "variant": "cal"}))
    assert [i["model"] for i in cal["items"]] == ["fake", "uniform"]


def test_vs_baseline_computed_when_report_lacks_it_and_without_baseline(client, workspace):
    root = workspace / FIRST_ROOT
    rep = json.loads((root / "report.json").read_text())
    rep["vs_baseline"] = {}
    (root / "report.json").write_text(json.dumps(rep))
    vb = _ok(client.get(f"/api/runs/{ROOT}/vs-baseline", params={"suite": "smoke"}))
    assert [i["model"] for i in vb["items"]] == ["fake", "uniform"] and vb["items"][0]["n_paired"] > 0
    lab = json.loads((root / "lab.json").read_text())
    lab.pop("baseline")
    (root / "lab.json").write_text(json.dumps(lab))
    assert _ok(client.get(f"/api/runs/{ROOT}/vs-baseline", params={"suite": "smoke"})) == {
        "suite": "smoke", "variant": "raw", "baseline": None, "split": None, "items": []}


def test_verdict_rules():
    assert verdict({"ci95": [1, 2]}) == "better"
    assert verdict({"ci95": [-2, -1]}) == "worse"
    assert verdict({"ci95": [-1, 1]}) == "unclear"
    assert verdict({"ci95": [-0.2, -0.1]}, lower_is_better=True) == "better"
    assert verdict({"ci95": [None, None]}) == "unclear"


def test_families(client):
    f = _ok(client.get(f"/api/runs/{ROOT}/families", params={"suite": "synthetic"}))
    assert len(f["families"]) == 3 and f["models"] == ["fake", "majority", "uniform"]
    assert len(f["cells"]) == 9 and all(c["n"] == 72 for c in f["cells"])


def test_calibration(client):
    c = _ok(client.get(f"/api/runs/{ROOT}/calibration", params={"suite": "synthetic"}))
    assert [i["model"] for i in c["items"]] == ["fake", "majority", "uniform"]
    fake = c["items"][0]
    assert fake["fit_split"] == "dev" and fake["before"]["intelligence"] is not None
    d = fake["delta"]
    assert d["n_paired"] == 216 and d["intelligence"]["verdict"] in ("better", "worse", "unclear")
    smoke = _ok(client.get(f"/api/runs/{ROOT}/calibration", params={"suite": "smoke"}))
    assert smoke["items"] == [] and smoke["eligible"] == []
    assert all("too few dev rows" in i["reason"] for i in smoke["ineligible"])


def test_jevbench(client, workspace):
    assert _ok(client.get(f"/api/runs/{ROOT}/jevbench"))["items"] == []
    jb = workspace / FIRST_ROOT / "fake" / "jevbench"
    jb.mkdir()
    (jb / "scores.json").write_text(json.dumps({
        "tasks": 231, "n_correct": 151, "competence_by_type": {"noul": 45.1, "choice": 40.3, "score": 38.2},
        "intelligence_proxy": 41.2, "yes_no_in_band": 12, "yes_no": 77, "jevbench_commit": "1bcc55eb",
        "note": "local proxy of the JevBench v1.5 rules on the 231 public v1 tasks; not a board score"}))
    out = _ok(client.get(f"/api/runs/{ROOT}/jevbench"))
    assert out["label"] == "JevBench public tasks · local v1.5-rule proxy"
    item = out["items"][0]
    assert item["model"] == "fake" and item["intelligence_proxy"] == 41.2 and "not a board score" in item["note"]
    detail = _ok(client.get(f"/api/runs/{ROOT}"))
    assert detail["jevbench_models"] == ["fake"]
    assert "jevbench" not in [s["name"] for s in detail["suites"]]
    assert _ok(client.get("/api/runs"))["items"][0]["has_jevbench"] is True


def test_rows_explorer(client):
    base = f"/api/runs/{ROOT}/rows"
    page = _ok(client.get(base, params={"suite": "synthetic", "limit": 20}))
    assert page["models"] == ["fake", "majority", "uniform"]
    assert page["content_available"] is True and page["split"] == "test"
    assert page["total"] == 216 and len(page["items"]) == 20
    row = page["items"][0]
    assert row["state_excerpt"] and row["instructions_excerpt"] and row["gold_name"] is not None
    a = row["answers"]["fake"]
    assert set(a) >= {"top", "top_name", "p_top", "p_gold", "proxy_right", "in_band", "error"}
    assert set(page["kinds"]) == {"noul", "choice", "score"} and len(page["families"]) == 3
    all_rows = _ok(client.get(base, params={"suite": "synthetic", "split": "all", "limit": 1}))
    assert all_rows["total"] == 360
    noul = _ok(client.get(base, params={"suite": "synthetic", "kind": "noul", "limit": 200}))
    assert noul["total"] == 72 and all(i["kind"] == "noul" for i in noul["items"])
    fam = page["families"][0]
    by_fam = _ok(client.get(base, params={"suite": "synthetic", "family": fam, "limit": 200}))
    assert by_fam["total"] == 72 and all(i["task"] == fam for i in by_fam["items"])
    wrong = _ok(client.get(base, params={"suite": "synthetic", "show": "wrong", "wrong_for": "fake", "limit": 200}))
    assert wrong["total"] > 0
    for i in wrong["items"]:
        a = i["answers"]["fake"]
        assert (a["proxy_right"] is False) or (a["proxy_right"] is None and a["correct"] is False)
    dis = _ok(client.get(base, params={"suite": "synthetic", "show": "disagree", "limit": 200}))
    for i in dis["items"]:
        assert len({x["top"] for x in i["answers"].values() if x}) > 1
    assert _ok(client.get(base, params={"suite": "synthetic", "show": "errors"}))["total"] == 0
    word = row["state_excerpt"].split()[0]
    hits = _ok(client.get(base, params={"suite": "synthetic", "q": word, "limit": 200}))
    assert 0 < hits["total"] <= 216
    assert _ok(client.get(base, params={"suite": "synthetic", "q": "zzqqxx-nowhere"}))["total"] == 0
    p2 = _ok(client.get(base, params={"suite": "synthetic", "offset": 20, "limit": 20}))
    assert p2["items"][0]["id"] != row["id"] and p2["offset"] == 20
    one = _ok(client.get(base, params={"suite": "synthetic", "models": "fake", "limit": 1}))
    assert one["models"] == ["fake"]
    cal = _ok(client.get(base, params={"suite": "synthetic+cal", "limit": 1}))
    assert cal["models"] == ["fake", "majority", "uniform"] and cal["content_available"]
    _err(client.get(base, params={"suite": "synthetic", "show": "x"}), 422, "bad_request")
    _err(client.get(base, params={"suite": "nope"}), 404, "not_found")


def test_row_detail(client):
    rid = _ok(client.get(f"/api/runs/{ROOT}/rows", params={"suite": "synthetic", "kind": "choice", "limit": 1}))["items"][0]["id"]
    d = _ok(client.get(f"/api/runs/{ROOT}/rows/{rid}", params={"suite": "synthetic"}))
    assert d["id"] == rid and d["kind"] == "choice" and d["content_available"]
    assert d["state"] and d["instructions"] and len(d["options"]) >= 2 and 0 <= d["label"] < len(d["options"])
    assert d["models"] == ["fake", "majority", "uniform"]
    assert len(d["answers"]["fake"]["probs"]) == len(d["options"]) and d["answers"]["fake"]["latency_s"] is not None
    _err(client.get(f"/api/runs/{ROOT}/rows/nope", params={"suite": "synthetic"}), 404, "not_found")


def test_rows_without_rebuildable_content(client, workspace):
    root = workspace / FIRST_ROOT
    for m in ("fake", "majority", "uniform"):
        p = root / m / "smoke" / "run.json"
        meta = json.loads(p.read_text())
        meta["suite"] = "mine"
        meta["suite_params"] = {"file": str(workspace / "data" / "gone.jsonl")}
        p.write_text(json.dumps(meta))
    page = _ok(client.get(f"/api/runs/{ROOT}/rows", params={"suite": "smoke", "limit": 5}))
    assert page["content_available"] is False and page["content_reason"]
    assert page["items"][0]["state_excerpt"] is None and page["total"] > 0


def test_rows_from_a_file_suite_with_images(client, workspace):
    root = workspace / FIRST_ROOT
    rows = [json.loads(x) for x in (workspace / "data" / "eval.jsonl").read_text().splitlines()]
    (workspace / "data" / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    rows[0]["images"] = ["pic.png", "/etc/hosts.png", "data:image/png;base64,AAAA"]
    (workspace / "data" / "eval.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    for m in ("fake", "majority", "uniform"):
        p = root / m / "smoke" / "run.json"
        meta = json.loads(p.read_text())
        meta["suite_params"] = {"file": str(workspace / "data" / "eval.jsonl")}
        p.write_text(json.dumps(meta))
    from decider_lab.rows import row_id

    rid = row_id({k: v for k, v in rows[0].items() if k != "id"}) if not rows[0].get("id") else rows[0]["id"]
    for m in ("fake", "majority", "uniform"):  # the run's first prediction answers the row with images
        pp = root / m / "smoke" / "predictions.jsonl"
        preds = [json.loads(x) for x in pp.read_text().splitlines()]
        preds[0]["id"] = rid
        pp.write_text("\n".join(json.dumps(x) for x in preds) + "\n")
    d = _ok(client.get(f"/api/runs/{ROOT}/rows/{rid}", params={"suite": "smoke"}))
    imgs = d["images"]
    assert imgs[0]["src"].startswith("/api/files/raw?file_id=")
    assert imgs[1]["src"] is None and "outside the workspace" in imgs[1]["alt"]
    assert imgs[2]["src"].startswith("data:image/png")


def test_provenance(client, secret_value, workspace):
    p = workspace / FIRST_ROOT / "fake" / "synthetic" / "run.json"
    meta = json.loads(p.read_text())
    meta["answerer"]["url"] = f"http://user:{secret_value}@127.0.0.1:1"
    p.write_text(json.dumps(meta))
    prov = _ok(client.get(f"/api/runs/{ROOT}/provenance"))
    text = json.dumps(prov)
    assert secret_value not in text
    item = next(i for i in prov["items"] if i["model"] == "fake" and i["suite"] == "synthetic")
    assert item["suite_sha256"] and item["host"] and item["python"] and item["workers"] == 4
    cal = next(i for i in prov["items"] if i["model"] == "fake" and i["suite"] == "synthetic+cal")
    assert cal["calibration"]["fit_split"] == "dev" and cal["calibrated_from"].endswith("fake/synthetic")
    assert prov["same_rows"]["synthetic"] == {"consistent": True, "sha256": item["suite_sha256"], "mismatched_models": []}
    assert prov["job_id"] is None


def _ref(model, suite, root=ROOT):
    return ":".join(quote(x, safe="") for x in (root, model, suite))


def test_compare(client, workspace):
    c = _ok(client.get("/api/compare", params={"a": _ref("fake", "synthetic"), "b": _ref("majority", "synthetic")}))
    assert c["a"]["label"] == "first-lab / fake / synthetic" and c["b"]["model"] == "majority"
    assert c["n_paired"] == 216 and c["only_a"] == 0 and c["split"] == "test"
    assert c["same_suite_sha256"] is True and c["has_splits"] is True and c["bootstrap"] == 2000
    for m in ("intelligence", "accuracy", "nll"):
        assert len(c[m]["ci95"]) == 2 and c[m]["verdict"] in ("better", "worse", "unclear")
    dev = _ok(client.get("/api/compare", params={"a": _ref("fake", "synthetic"), "b": _ref("majority", "synthetic"),
                                                 "split": "dev"}))
    assert dev["n_paired"] == 144
    allr = _ok(client.get("/api/compare", params={"a": _ref("fake", "synthetic"), "b": _ref("fake", "synthetic+cal"),
                                                  "split": "all"}))
    assert allr["n_paired"] == 360
    other = workspace / "evals" / "other" / "fake" / "smoke"
    shutil.copytree(workspace / FIRST_ROOT / "fake" / "smoke", other)
    preds = [json.loads(x) for x in (other / "predictions.jsonl").read_text().splitlines()]
    (other / "predictions.jsonl").write_text("\n".join(json.dumps({**x, "id": "x" + x["id"]}) for x in preds) + "\n")
    err = _err(client.get("/api/compare", params={"a": _ref("fake", "smoke"),
                                                  "b": _ref("fake", "smoke", encode_id("evals/other"))}),
               422, "no_shared_rows")
    assert "share no rows" in err["message"]
    _err(client.get("/api/compare", params={"a": "x", "b": "y"}), 422, "bad_request")
    _err(client.get("/api/compare", params={"a": _ref("fake", "nope"), "b": _ref("fake", "smoke")}), 404, "not_found")
    _err(client.get("/api/compare", params={"a": _ref("fake", "smoke"), "b": _ref("fake", "smoke"), "split": "x"}),
         422, "bad_request")


def test_refs(client):
    items = _ok(client.get("/api/runs/refs"))["items"]
    keys = {(i["model"], i["suite"]) for i in items if i["root_id"] == ROOT}
    assert ("fake", "synthetic+cal") in keys and ("majority", "smoke") in keys and len(keys) == 9
    cal = next(i for i in items if i["suite"] == "synthetic+cal")
    assert cal["calibrated"] is True and cal["has_splits"] is True and cal["lab"] == "first-lab"


def test_report_exports_and_rebuild(client, workspace):
    md = client.get(f"/api/runs/{ROOT}/report.md")
    assert md.status_code == 200 and "attachment" in md.headers["content-disposition"] and "# first-lab" in md.text
    js = client.get(f"/api/runs/{ROOT}/report.json")
    assert js.status_code == 200 and "vs_baseline" in js.json()
    (workspace / FIRST_ROOT / "REPORT.md").unlink()
    _err(client.get(f"/api/runs/{ROOT}/report.md"), 404, "not_found")
    assert _ok(client.get(f"/api/runs/{ROOT}"))["report_md_available"] is False
    out = _ok(client.post(f"/api/runs/{ROOT}/report", json={}))
    assert out["report_md"] == f"{FIRST_ROOT}/REPORT.md"
    assert (workspace / FIRST_ROOT / "REPORT.md").exists()
    _err(client.post(f"/api/runs/{ROOT}/report", json={"baseline": "nobody"}), 422, "bad_request")
    assert _ok(client.post(f"/api/runs/{ROOT}/report", json={"baseline": "uniform"}))
    rep = json.loads((workspace / FIRST_ROOT / "report.json").read_text())
    assert "majority" in rep["vs_baseline"]


def test_writes_need_x_studio(client, token):
    from fastapi.testclient import TestClient

    bare = TestClient(client.app, base_url="http://127.0.0.1:7861", headers={"Authorization": f"Bearer {token}"})
    assert bare.post(f"/api/runs/{ROOT}/report", json={}).status_code == 403


def test_delete_root(client, workspace):
    _err(client.request("DELETE", f"/api/runs/{ROOT}", json={"confirm": "delete"}), 409, "confirm_mismatch")
    _err(client.request("DELETE", f"/api/runs/{ROOT}"), 409, "confirm_mismatch")
    assert (workspace / FIRST_ROOT).exists()
    out = _ok(client.request("DELETE", f"/api/runs/{ROOT}", json={"confirm": "delete first-lab"}))
    assert out["deleted"] is True and out["freed_mb"] >= 0
    assert not (workspace / FIRST_ROOT).exists()
    assert (workspace / "labs" / "first" / "lab.yaml").exists()  # never the lab file
    assert all(i["root_id"] != ROOT for i in _ok(client.get("/api/runs"))["items"])
    _err(client.get(f"/api/runs/{ROOT}"), 404, "not_found")


def test_delete_refused_while_a_job_writes(client, workspace):
    jm = client.app.state.studio.jobs
    real = jm.list
    jm.list = lambda **kw: [{"id": "j_000000000001", "root_id": ROOT, "status": "running"}]
    try:
        _err(client.request("DELETE", f"/api/runs/{ROOT}", json={"confirm": "delete first-lab"}), 409, "job_active")
    finally:
        jm.list = real
    assert (workspace / FIRST_ROOT).exists()


def test_eval_root_without_lab_json(client, workspace):
    src = workspace / FIRST_ROOT
    dest = workspace / "evals" / "quick"
    for m in ("fake", "majority"):
        shutil.copytree(src / m / "smoke", dest / m / "smoke")
    rid = encode_id("evals/quick")
    items = _ok(client.get("/api/runs"))["items"]
    ev = next(i for i in items if i["root_id"] == rid)
    assert ev["lab"] == "quick" and ev["kind"] == "eval" and ev["baseline"] is None and ev["lab_id"] is None
    lb = _ok(client.get(f"/api/runs/{rid}/leaderboard", params={"suite": "smoke"}))
    assert [r["model"] for r in lb["rows"]] and lb["baseline"] is None
    assert _ok(client.get(f"/api/runs/{rid}/vs-baseline", params={"suite": "smoke"}))["items"] == []
    d = _ok(client.get(f"/api/runs/{rid}"))
    assert d["report_md_available"] is False
    _err(client.get(f"/api/runs/{rid}/report.md"), 404, "not_found")


def test_secret_never_in_results_responses(client, secret_value, workspace):
    root = workspace / FIRST_ROOT
    pp = root / "fake" / "smoke" / "predictions.jsonl"
    preds = [json.loads(x) for x in pp.read_text().splitlines()]
    preds[0]["error"] = f"HTTPError: 401 with Authorization: Bearer {secret_value}"
    pp.write_text("\n".join(json.dumps(x) for x in preds) + "\n")
    lab = json.loads((root / "lab.json").read_text())
    lab["failures"] = [f"nova: token {secret_value} rejected"]
    (root / "lab.json").write_text(json.dumps(lab))
    urls = [f"/api/runs/{ROOT}", "/api/runs", f"/api/runs/{ROOT}/runs/fake/smoke",
            f"/api/runs/{ROOT}/runs/fake/smoke/predictions?error=1",
            f"/api/runs/{ROOT}/runs/fake/smoke/predictions.csv",
            f"/api/runs/{ROOT}/rows?suite=smoke&show=errors", f"/api/runs/{ROOT}/rows/{preds[0]['id']}?suite=smoke",
            f"/api/runs/{ROOT}/leaderboard?suite=smoke", f"/api/runs/{ROOT}/provenance"]
    for u in urls:
        r = client.get(u)
        assert r.status_code == 200, (u, r.text)
        assert secret_value not in r.text, u
    errs = _ok(client.get(f"/api/runs/{ROOT}/runs/fake/smoke/predictions", params={"error": "1"}))
    assert errs["total"] == 1 and "••••" in errs["items"][0]["error"]
