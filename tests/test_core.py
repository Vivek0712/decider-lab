from __future__ import annotations

import json
import math
import os

import pytest

from decider_lab import calibrate, data, metrics, runner
from decider_lab.adapters import AdapterError, MajorityBaseline, SystemOneAdapter, UniformBaseline, make_adapter
from decider_lab.rows import YES_NO, check, problems, row_id
from decider_lab.suites import load_suite, synthetic


def noul(state="x", label=1, task="t"):
    return {"kind": "noul", "state": state, "instructions": "q?", "options": YES_NO, "label": label, "task": task}


def choice(label=0, n=4, task="t", state="c"):
    return {"kind": "choice", "state": state, "instructions": "which?", "options": [[f"o{i}", f"o{i}"] for i in range(n)],
            "label": label, "task": task}


def score(label=2, n=5, task="t", state="s"):
    return {"kind": "score", "state": state, "instructions": "how?", "options": [[str(i), f"l{i}"] for i in range(n)],
            "label": label, "task": task}


def pred(row, probs, split=None):
    r = check([row])[0]
    return {"id": r["id"], "task": r["task"], "kind": r["kind"], "label": r["label"], "n": len(r["options"]),
            "probs": probs, "split": split}


# ---- rows -----------------------------------------------------------------------------------

def test_valid_rows_pass_and_get_stable_ids():
    rows = check([noul(), choice(), score()])
    assert all(r["id"] for r in rows)
    assert row_id(noul()) == row_id({**noul(), "label": 0}), "the id must not depend on the label"


@pytest.mark.parametrize("bad, msg", [
    ({**noul(), "kind": "maybe"}, "kind"),
    ({**noul(), "options": [["yes", "a"], ["no", "b"]]}, "false"),
    ({**choice(), "label": 9}, "label"),
    ({**score(), "options": [["a", "x"], ["b", "y"]]}, "level indices"),
    ({**noul(), "state": "  "}, "state"),
])
def test_invalid_rows_are_named(bad, msg):
    assert any(msg in p for p in problems(bad))
    with pytest.raises(ValueError, match="row 0"):
        check([bad])


# ---- metrics: the v1.5 rules ---------------------------------------------------------------

def test_yes_no_band_counts_as_wrong():
    s = metrics.row_stats(pred(noul(label=1), [0.3, 0.7]))
    assert s["in_band"] == 1.0 and s["credit"] == -1.0 and s["correct"] == 1.0
    s = metrics.row_stats(pred(noul(label=1), [0.15, 0.85]))
    assert s["credit"] == 1.0


def test_choice_credit_is_chance_corrected():
    assert metrics.row_stats(pred(choice(0, 4), [0.7, 0.1, 0.1, 0.1]))["credit"] == pytest.approx(1.0)
    assert metrics.row_stats(pred(choice(1, 4), [0.7, 0.1, 0.1, 0.1]))["credit"] == pytest.approx(-1 / 3)


def test_score_competence_uses_expected_level():
    exact = pred(score(2, 5), [0, 0, 1, 0, 0])
    uniform = pred(score(2, 5), [0.2] * 5)
    assert metrics.competence("score", [metrics.row_stats(exact)]) == pytest.approx(100.0)
    # uniform's expected level is 2 == gold: right on average, so full credit by this rule
    assert metrics.competence("score", [metrics.row_stats(uniform)]) == pytest.approx(100.0)
    far = pred(score(0, 5), [0, 0, 0, 0, 1])
    assert metrics.competence("score", [metrics.row_stats(far)]) < 0


def test_summary_intelligence_is_mean_of_kinds_and_errors_score_as_uniform():
    preds = [pred(noul(label=1), [0.1, 0.9]), pred(choice(0, 2), [0.9, 0.1]),
             {**pred(choice(1, 2), [0.5, 0.5]), "probs": None, "error": "timeout"}]
    s = metrics.summarize(preds, bootstrap=0)
    assert s["errors"] == 1
    assert s["by_kind"]["noul"]["competence"] == 100.0
    # choice: one right (credit 1), one failed -> uniform -> top index 0 != 1 -> credit -1
    assert s["by_kind"]["choice"]["competence"] == 0.0
    assert s["intelligence"] == 50.0


def test_compare_is_paired_and_signed():
    rows = [noul(state=f"s{i}", label=i % 2) for i in range(40)]
    good = [pred(r, [0.05, 0.95] if r["label"] else [0.95, 0.05]) for r in rows]
    bad = [pred(r, [0.5, 0.5]) for r in rows]
    c = metrics.compare(good, bad, bootstrap=200)
    assert c["n_paired"] == 40 and c["intelligence"]["diff"] == 200.0
    assert c["intelligence"]["ci95"][0] > 0


# ---- calibration -----------------------------------------------------------------------------

def test_temperature_fit_recovers_overconfidence(tmp_path):
    import random

    rng = random.Random(0)
    rows, preds = [], []
    for i in range(400):
        true_p = rng.uniform(0.05, 0.95)
        label = int(rng.random() < true_p)
        sharp = calibrate.apply_t([1 - true_p, true_p], 1 / 3)  # overconfident by T=3
        r = noul(state=f"q{i}", label=label)
        rows.append(r)
        preds.append(pred(r, sharp, split="dev" if i % 2 else "test"))
    d = tmp_path / "run"
    d.mkdir()
    (d / "predictions.jsonl").write_text("".join(json.dumps(p) + "\n" for p in preds))
    info = calibrate.calibrate_run(str(d), min_rows=10)
    assert 2.0 < info["temperatures"]["noul"] < 4.5
    assert info["after"]["nll"] < info["before"]["nll"]
    assert os.path.exists(str(d) + "+cal/predictions.jsonl")


# ---- suites ----------------------------------------------------------------------------------

def test_smoke_suite_is_deterministic_balanced_and_split():
    name, rows, _ = load_suite("smoke")
    assert name == "smoke" and len(rows) == 90
    again = synthetic(10, ("arithmetic", "calendar", "seating"), 1234)
    assert [row_id(r) for r in rows] == [row_id(r) for r in again]
    assert {r["split"] for r in rows} == {"dev", "test"}
    assert {r["kind"] for r in rows} == {"noul", "choice", "score"}
    yes = sum(r["label"] for r in rows if r["kind"] == "noul")
    assert 10 <= yes <= 20  # 30 yes/no rows, balanced by construction


def test_file_suite_and_errors(tmp_path):
    p = tmp_path / "mine.jsonl"
    p.write_text(json.dumps(noul()) + "\n" + json.dumps(choice()) + "\n")
    name, rows, _ = load_suite(str(p))
    assert name == "mine" and len(rows) == 2
    p.write_text(json.dumps({**noul(), "label": 5}) + "\n")
    with pytest.raises(ValueError, match="label"):
        load_suite(str(p))


# ---- adapters --------------------------------------------------------------------------------

def test_systemone_adapter_reads_every_kind(fake_server):
    url, handler = fake_server
    a = SystemOneAdapter(url, retries=0)
    assert a.predict_one(noul(state="say yes")) == pytest.approx([0.1, 0.9])
    assert a.predict_one(choice(n=3))[0] == pytest.approx(0.7)
    assert a.predict_one(score(n=5)) == pytest.approx([0.2] * 5)
    q = handler.calls[0]["questions"]["q"]
    assert q["type"] == "noul" and set(q["criteria"]) == {"false", "true"}
    assert a.describe()["health"]["model"] == "fake-decider"


def test_systemone_adapter_retries_5xx_but_not_4xx(fake_server):
    url, handler = fake_server
    a = SystemOneAdapter(url, retries=1)
    handler.fail_next = 1
    assert a.predict_one(noul(state="yes")) == pytest.approx([0.1, 0.9])
    with pytest.raises(AdapterError, match="HTTP 422"):
        a.predict_one(noul(state="bad"))


def test_images_are_sent_base64(fake_server, tmp_path):
    url, handler = fake_server
    img = tmp_path / "a.png"
    img.write_bytes(b"\x89PNG fake")
    a = SystemOneAdapter(url, base_dir=str(tmp_path))
    a.predict_one({**noul(), "images": ["a.png"]})
    assert handler.calls[-1]["images"][0].startswith("data:image/png;base64,")


def test_make_adapter_specs(tmp_path):
    assert isinstance(make_adapter("uniform"), UniformBaseline)
    assert isinstance(make_adapter({"baseline": "majority"}), MajorityBaseline)
    (tmp_path / "m.py").write_text("def f(row):\n    return [1] * len(row['options'])\n")
    a = make_adapter({"python": "m:f"}, base_dir=str(tmp_path))
    assert a.predict_one(choice(n=4)) == [0.25] * 4
    with pytest.raises(ValueError):
        make_adapter("nonsense")


# ---- runner ----------------------------------------------------------------------------------

def test_runner_writes_run_and_resumes(fake_server, tmp_path):
    url, handler = fake_server
    _, rows, _ = load_suite("smoke")
    out = tmp_path / "r"
    handler.fail_next = 3  # with retries=0 the first rows fail and are recorded
    s = runner.run(SystemOneAdapter(url, retries=0), rows, str(out), suite="smoke", workers=1, progress=False)
    assert s["errors"] == 3 and s["n"] == 90
    meta = json.loads((out / "run.json").read_text())
    assert meta["suite_sha256"] and meta["answerer"]["health"]["model"] == "fake-decider"
    n_before = len(handler.calls)
    s2 = runner.run(SystemOneAdapter(url, retries=0), rows, str(out), suite="smoke", workers=4, progress=False)
    assert s2["errors"] == 0
    assert len(handler.calls) - n_before == 3, "only the failed rows are asked again"
    assert sum(1 for _ in open(out / "predictions.jsonl")) == 90


def test_select_limit_keeps_every_kind():
    _, rows, _ = load_suite("smoke")
    sel = runner.select(rows, limit=2)
    assert len(sel) == 6 and {r["kind"] for r in sel} == {"noul", "choice", "score"}
    assert all(r["split"] == "dev" for r in runner.select(rows, split="dev"))


# ---- data ------------------------------------------------------------------------------------

def test_from_csv_builds_every_kind(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("state,question,answer,options,kind\n"
                 "the sky,is it blue?,yes,,\n"
                 "a fruit,which?,apple,apple|rock|car,\n"
                 "a review,how positive?,good,bad|ok|good,score\n")
    rows = data.from_csv(str(p))
    assert [r["kind"] for r in rows] == ["noul", "choice", "score"]
    assert [r["label"] for r in rows] == [1, 0, 2]
    p.write_text("state,question,answer\nx,y,perhaps\n")
    with pytest.raises(ValueError, match="yes or no"):
        data.from_csv(str(p))


def test_generate_excludes_suite_rows_and_leakcheck_finds_overlap():
    _, smoke, _ = load_suite("smoke")
    train = data.generate(10, ["arithmetic", "calendar", "seating"], 1234, {r["id"] for r in smoke})
    assert not {row_id(r) for r in train} & {r["id"] for r in smoke}
    res = data.leakcheck(smoke[:5] + train[:5], smoke)
    assert res["overlapping"] >= 5


def test_uniform_scores_zero_on_choice_and_band_on_noul():
    _, rows, _ = load_suite("smoke")
    preds = [runner.record(r, UniformBaseline().predict_one(r), 0.0, None) for r in rows]
    s = metrics.summarize(preds, bootstrap=0)
    assert s["by_kind"]["noul"]["in_band"] == 100.0
    assert s["by_kind"]["noul"]["competence"] == -100.0
    assert math.isfinite(s["intelligence"])
