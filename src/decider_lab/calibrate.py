"""Post-hoc temperature per question kind, fitted on the dev split, judged on test.

For a prediction p over options, the calibrated answer is p_T proportional to p ** (1 / T),
which is temperature scaling of the model's logits (log p differs from them by a constant).
T is fitted per kind by minimising NLL on dev rows, a proper scoring rule, so the fit makes
probabilities honest rather than moving answers out of the abstention band on purpose.

Works on any adapter's predictions: no model access needed. For a Strands Decider checkpoint,
`strands-decider calibrate` writes temperatures into the checkpoint itself; this is the
model-agnostic equivalent, and the two can be compared.
"""

from __future__ import annotations

import json
import math
import os
from typing import Any

from . import metrics
from .runner import load_run

T_RANGE = (0.05, 20.0)


def apply_t(probs: list[float], t: float) -> list[float]:
    logs = [math.log(max(p, 1e-12)) / t for p in probs]
    m = max(logs)
    ex = [math.exp(x - m) for x in logs]
    s = sum(ex)
    return [x / s for x in ex]


def nll(preds: list[dict[str, Any]], t: float) -> float:
    return sum(-math.log(max(apply_t(metrics.probs_of(p), t)[int(p["label"])], 1e-12)) for p in preds) / len(preds)


def fit_t(preds: list[dict[str, Any]]) -> float:
    """Golden-section search over log T (NLL is unimodal in T for temperature scaling)."""
    lo, hi = math.log(T_RANGE[0]), math.log(T_RANGE[1])
    g = (math.sqrt(5) - 1) / 2
    a, b = hi - g * (hi - lo), lo + g * (hi - lo)
    fa, fb = nll(preds, math.exp(a)), nll(preds, math.exp(b))
    for _ in range(60):
        if fa < fb:
            hi, b, fb = b, a, fa
            a = hi - g * (hi - lo)
            fa = nll(preds, math.exp(a))
        else:
            lo, a, fa = a, b, fb
            b = lo + g * (hi - lo)
            fb = nll(preds, math.exp(b))
    return round(math.exp((lo + hi) / 2), 4)


def calibrate_run(run_dir: str, out_dir: str | None = None, *, fit_split: str = "dev",
                  score_split: str = "test", min_rows: int = 30) -> dict[str, Any]:
    """Fit per-kind temperatures on `fit_split` rows of a run and write the calibrated run to
    `out_dir` (default `<run_dir>+cal`). Kinds with fewer than `min_rows` dev rows keep T = 1."""
    preds, meta = load_run(run_dir)
    fit = [p for p in preds if p.get("split") == fit_split and p.get("probs")]
    temps: dict[str, float] = {}
    for kind in metrics.KINDS:
        rows = [p for p in fit if p["kind"] == kind]
        temps[kind] = fit_t(rows) if len(rows) >= min_rows else 1.0
    cal = [{**p, "probs": [round(x, 6) for x in apply_t(p["probs"], temps[p["kind"]])]} if p.get("probs") else p
           for p in preds]
    out_dir = out_dir or run_dir.rstrip("/") + "+cal"
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "predictions.jsonl"), "w", encoding="utf-8") as fh:
        fh.writelines(json.dumps(p) + "\n" for p in cal)
    scored = [p for p in cal if p.get("split") in (None, score_split)]
    scores = metrics.summarize(scored)
    scores["scored_split"] = score_split
    before = metrics.summarize([p for p in preds if p.get("split") in (None, score_split)], bootstrap=0)
    info = {"temperatures": temps, "fit_split": fit_split, "fit_rows": len(fit), "score_split": score_split,
            "before": {"intelligence": before["intelligence"], "nll": before["nll"], "ece": before["ece"]},
            "after": {"intelligence": scores["intelligence"], "nll": scores["nll"], "ece": scores["ece"]}}
    with open(os.path.join(out_dir, "calibration.json"), "w", encoding="utf-8") as fh:
        json.dump(info, fh, indent=2)
    with open(os.path.join(out_dir, "scores.json"), "w", encoding="utf-8") as fh:
        json.dump(scores, fh, indent=2)
    with open(os.path.join(out_dir, "run.json"), "w", encoding="utf-8") as fh:
        json.dump({**meta, "calibrated_from": os.path.abspath(run_dir), "calibration": info}, fh, indent=2)
    return info
