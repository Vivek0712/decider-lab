from __future__ import annotations

import json
import os

import pytest
import yaml

from decider_lab import finetune, jevbench
from decider_lab.cli import main
from decider_lab.gpu import vast


def write_lab(tmp_path, url, **extra):
    lab = {"name": "t", "workers": 2, "models": {"fake": {"url": url}, "majority": {"baseline": "majority"}},
           "suites": ["smoke"], "calibrate": True, "baseline": "majority", **extra}
    p = tmp_path / "lab.yaml"
    p.write_text(yaml.safe_dump(lab))
    return p


def test_lab_end_to_end_writes_report(fake_server, tmp_path):
    url, _ = fake_server
    p = write_lab(tmp_path, url)
    assert main(["run", str(p)]) == 0
    root = tmp_path / "runs" / "t"
    for d in ("fake/smoke", "fake/smoke+cal", "majority/smoke"):
        assert (root / d / "scores.json").exists(), d
    report = (root / "REPORT.md").read_text()
    assert "| fake |" in report and "Paired against **majority**" in report
    rep = json.loads((root / "report.json").read_text())
    assert rep["vs_baseline"]["fake"]["smoke"]["n_paired"] == 54  # the test split of smoke


def test_lab_rejects_unknown_baseline(tmp_path):
    p = write_lab(tmp_path, "http://127.0.0.1:9", baseline="nobody")
    assert main(["run", str(p)]) == 2


def test_eval_and_compare_cli(fake_server, tmp_path, capsys):
    url, _ = fake_server
    a, b = str(tmp_path / "a"), str(tmp_path / "b")
    assert main(["eval", "--model", url, "--suite", "smoke", "--out", a]) == 0
    assert main(["eval", "--model", "uniform", "--suite", "smoke", "--out", b]) == 0
    capsys.readouterr()
    assert main(["compare", a, b]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["n_paired"] == 54


def test_init_templates(tmp_path):
    for t in ("eval", "finetune"):
        d = tmp_path / t
        assert main(["init", str(d), "--template", t]) == 0
        lab = yaml.safe_load((d / "lab.yaml").read_text())
        assert lab["models"] and lab["suites"]
    assert main(["init", str(tmp_path / "eval")]) == 1  # refuses a non-empty directory


# ---- finetune config -------------------------------------------------------------------------

FIELDS = set(finetune.RECIPE) | {"train_files", "output_dir", "max_steps", "init_from"}


def test_finetune_prefers_continue_from(tmp_path):
    (tmp_path / "train.jsonl").write_text(json.dumps({"kind": "noul", "state": "x", "instructions": "q",
                                                      "options": [["false", "no"], ["true", "yes"]], "label": 1}) + "\n")
    spec = {"from": "org/ckpt", "train": str(tmp_path / "train.jsonl"), "steps": 10}
    path, ckpt, notes = finetune.make_config("m", spec, str(tmp_path / "w"), fields=FIELDS | {"continue_from"})
    cfg = yaml.safe_load(open(path))
    assert cfg["continue_from"] == "org/ckpt" and "init_from" not in cfg and cfg["max_steps"] == 10
    assert cfg["head_type"] == "pointer"


def test_finetune_falls_back_to_init_from_and_says_so(tmp_path):
    (tmp_path / "train.jsonl").write_text(json.dumps({"kind": "noul", "state": "x", "instructions": "q",
                                                      "options": [["false", "no"], ["true", "yes"]], "label": 1}) + "\n")
    spec = {"from": "org/ckpt", "train": str(tmp_path / "train.jsonl")}
    path, _, notes = finetune.make_config("m", spec, str(tmp_path / "w"), fields=FIELDS)
    cfg = yaml.safe_load(open(path))
    assert cfg["init_from"] == "org/ckpt" and cfg["head_type"] == "slot"
    assert any("FROZEN" in n for n in notes)


def test_finetune_rejects_bad_rows_and_unknown_options(tmp_path):
    (tmp_path / "bad.jsonl").write_text(json.dumps({"kind": "noul", "state": "x", "instructions": "q",
                                                    "options": [["no", "a"], ["yes", "b"]], "label": 1}) + "\n")
    with pytest.raises(ValueError, match="row 0"):
        finetune.make_config("m", {"train": str(tmp_path / "bad.jsonl")}, str(tmp_path / "w"), fields=FIELDS)
    (tmp_path / "ok.jsonl").write_text(json.dumps({"kind": "noul", "state": "x", "instructions": "q",
                                                   "options": [["false", "a"], ["true", "b"]], "label": 1}) + "\n")
    with pytest.raises(ValueError, match="no training option"):
        finetune.make_config("m", {"train": str(tmp_path / "ok.jsonl"), "config": {"warp_drive": 1}},
                             str(tmp_path / "w"), fields=FIELDS)


# ---- jevbench proxy --------------------------------------------------------------------------

def test_jevbench_proxy_rules():
    tasks = {"s1": {"labels": ["low", "mid", "high"], "expected": "high"}}
    rows = [{"task_id": "y1", "probs": {"yes": 0.9, "no": 0.1}, "correct": True},
            {"task_id": "y2", "probs": {"yes": 0.6, "no": 0.4}, "correct": True},  # in band -> wrong
            {"task_id": "c1", "probs": {"a": 0.8, "b": 0.1, "c": 0.1}, "correct": True},
            {"task_id": "s1", "probs": {"low": 0, "mid": 0, "high": 1}, "ordinal_ev": 2.0}]
    s = jevbench.score_rows(rows, tasks)
    assert s["competence_by_type"] == {"noul": 0.0, "choice": 100.0, "score": 100.0}
    assert s["yes_no_in_band"] == 1


# ---- vast: the instance is always destroyed --------------------------------------------------

def test_gpu_run_destroys_on_failure(monkeypatch, tmp_path):
    lab = tmp_path / "lab.yaml"
    lab.write_text("name: x\nmodels: {m: uniform}\n")
    destroyed = []
    monkeypatch.setattr(vast, "credit", lambda: 50.0)
    monkeypatch.setattr(vast, "offers", lambda *a, **k: [{"id": 1, "gpu_name": "RTX_4090", "dph_total": 0.4}])
    monkeypatch.setattr(vast, "create", lambda *a, **k: 777)
    monkeypatch.setattr(vast, "destroy", lambda iid, **k: destroyed.append(iid) or True)

    def no_ssh(*a, **k):
        raise TimeoutError("unreachable")

    monkeypatch.setattr(vast, "wait_ssh", no_ssh)
    with pytest.raises(TimeoutError):
        vast.run_remote(str(lab), log=lambda *_: None)
    assert destroyed == [777]


def test_gpu_run_refuses_without_credit(monkeypatch, tmp_path):
    lab = tmp_path / "lab.yaml"
    lab.write_text("name: x\nmodels: {m: uniform}\n")
    monkeypatch.setattr(vast, "credit", lambda: 1.0)
    with pytest.raises(RuntimeError, match="does not cover"):
        vast.run_remote(str(lab), max_price=1.0, max_hours=2, log=lambda *_: None)


def test_package_root_is_the_checkout():
    assert os.path.exists(os.path.join(vast.package_root(), "pyproject.toml"))
