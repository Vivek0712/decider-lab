"""Scoring: the JevBench v1.5 rules applied to any suite, plus proper scoring rules.

The headline is a *local* Intelligence proxy, computed with the published JevBench v1.5 rules
(not the official scorer, and never comparable with a board number):

  - yes/no (noul): 0.2 < P(yes) < 0.8 is an abstention and counts as wrong; a decisive answer
    on the gold side is right. Credit is chance-corrected: (right - 1/2) / (1 - 1/2).
  - choice: right = top option is gold; credit (right - 1/n) / (1 - 1/n).
  - score: graded by the expected level: 100 * (1 - sum nMAE / sum nMAE_chance).
  - competence per kind is the mean credit in %, and Intelligence is the mean over the kinds present.

Beside it, per kind: accuracy (top option), NLL, Brier, ranked probability score (score
kind), top-probability ECE, and the share of yes/no answers in the abstention band. A row the
model failed to answer is scored as a uniform answer and counted in `errors`, never dropped.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence
from typing import Any

BAND = (0.2, 0.8)
KINDS = ("noul", "choice", "score")
Pred = dict[str, Any]


def probs_of(p: Pred) -> list[float]:
    n = int(p["n"])
    pr = p.get("probs")
    return list(pr) if pr and len(pr) == n else [1.0 / n] * n


def row_stats(p: Pred) -> dict[str, float]:
    """Everything one prediction contributes. `credit` is None for score rows (graded together)."""
    probs, label, kind, n = probs_of(p), int(p["label"]), p["kind"], int(p["n"])
    top = max(range(n), key=lambda i: probs[i])
    out: dict[str, Any] = {
        "correct": float(top == label),
        "nll": -math.log(max(probs[label], 1e-12)),
        "brier": sum((q - (1.0 if i == label else 0.0)) ** 2 for i, q in enumerate(probs)),
        "conf": probs[top],
        "in_band": 0.0, "credit": None, "nmae": 0.0, "nmae_chance": 0.0, "rps": 0.0,
    }
    if kind == "noul":
        p_yes = probs[1]
        band = BAND[0] < p_yes < BAND[1]
        right = (not band) and ((p_yes >= 0.5) == (label == 1))
        out["in_band"] = float(band)
        out["credit"] = ((1.0 if right else 0.0) - 0.5) / 0.5
    elif kind == "choice":
        chance = 1.0 / n
        out["credit"] = (out["correct"] - chance) / (1 - chance)
    else:
        expected = sum(i * q for i, q in enumerate(probs))
        out["nmae"] = abs(expected - label) / (n - 1)
        out["nmae_chance"] = sum(abs(i - label) for i in range(n)) / n / (n - 1)
        cp = cy = rps = 0.0
        for i in range(n - 1):
            cp += probs[i]
            cy += 1.0 if i == label else 0.0
            rps += (cp - cy) ** 2
        out["rps"] = rps / (n - 1)
    return out


def competence(kind: str, stats: Sequence[dict[str, Any]]) -> float:
    if not stats:
        return float("nan")
    if kind == "score":
        chance = sum(s["nmae_chance"] for s in stats)
        return 100 * (1 - sum(s["nmae"] for s in stats) / chance) if chance else 0.0
    return 100 * sum(s["credit"] for s in stats) / len(stats)


def intelligence(by_kind: dict[str, list[dict[str, Any]]]) -> float:
    vals = [competence(k, v) for k, v in by_kind.items() if v]
    return sum(vals) / len(vals) if vals else float("nan")


def ece(stats: Sequence[dict[str, Any]], bins: int = 10) -> float:
    cells: list[list[dict[str, Any]]] = [[] for _ in range(bins)]
    for s in stats:
        cells[min(bins - 1, int(s["conf"] * bins))].append(s)
    n = sum(map(len, cells))
    return sum(abs(sum(s["conf"] for s in c) - sum(s["correct"] for s in c)) for c in cells if c) / max(n, 1)


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def _r(x: float, d: int = 2) -> float | None:
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(x, d)


def summarize(preds: Sequence[Pred], *, bootstrap: int = 1000, seed: int = 0) -> dict[str, Any]:
    """The full report for one run's predictions."""
    stats = [row_stats(p) for p in preds]
    by_kind: dict[str, list[dict[str, Any]]] = {k: [] for k in KINDS}
    for p, s in zip(preds, stats, strict=True):
        by_kind[p["kind"]].append(s)
    kinds: dict[str, Any] = {}
    for k, ss in by_kind.items():
        if not ss:
            continue
        kinds[k] = {"n": len(ss), "competence": _r(competence(k, ss), 1),
                    "accuracy": _r(100 * _mean([s["correct"] for s in ss]), 1),
                    "nll": _r(_mean([s["nll"] for s in ss]), 4), "brier": _r(_mean([s["brier"] for s in ss]), 4),
                    "ece": _r(ece(ss), 4)}
        if k == "noul":
            kinds[k]["in_band"] = _r(100 * _mean([s["in_band"] for s in ss]), 1)
        if k == "score":
            kinds[k]["rps"] = _r(_mean([s["rps"] for s in ss]), 4)
    fams: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for p, s in zip(preds, stats, strict=True):
        fams.setdefault(str(p.get("task")), {k: [] for k in KINDS})[p["kind"]].append(s)
    families = {f: {"n": sum(map(len, v.values())), "intelligence": _r(intelligence(v), 1),
                    "accuracy": _r(100 * _mean([s["correct"] for ss in v.values() for s in ss]), 1)}
                for f, v in sorted(fams.items())}
    out: dict[str, Any] = {
        "n": len(preds), "errors": sum(1 for p in preds if p.get("error")),
        "intelligence": _r(intelligence(by_kind), 1),
        "accuracy": _r(100 * _mean([s["correct"] for s in stats]), 1),
        "nll": _r(_mean([s["nll"] for s in stats]), 4), "ece": _r(ece(stats), 4),
        "by_kind": kinds, "by_family": families,
    }
    lat = sorted(float(p["latency_s"]) for p in preds if p.get("latency_s") is not None)
    if lat:
        out["latency_s"] = {"median": round(lat[len(lat) // 2], 3), "p95": round(lat[int(0.95 * (len(lat) - 1))], 3)}
    if bootstrap and preds:
        out["intelligence_ci95"] = _ci([[(p["kind"], s) for p, s in zip(preds, stats, strict=True)]],
                                       lambda runs: _intel(runs[0]), bootstrap, seed)
    return out


def _intel(pairs: Sequence[tuple[str, dict[str, Any]]]) -> float:
    by: dict[str, list[dict[str, Any]]] = {}
    for k, s in pairs:
        by.setdefault(k, []).append(s)
    return intelligence(by)


def _ci(runs: list[list[Any]], stat: Callable[[list[list[Any]]], float], resamples: int,
        seed: int, digits: int = 1) -> list[float | None]:
    """Paired percentile bootstrap over row positions (every run resampled with the same draw)."""
    rng = random.Random(seed)
    n = len(runs[0])
    draws = []
    for _ in range(resamples):
        idx = [rng.randrange(n) for _ in range(n)]
        draws.append(stat([[run[i] for i in idx] for run in runs]))
    draws.sort()
    return [_r(draws[int(0.025 * resamples)], digits), _r(draws[int(0.975 * resamples) - 1], digits)]


def compare(a: Sequence[Pred], b: Sequence[Pred], *, bootstrap: int = 2000, seed: int = 0) -> dict[str, Any]:
    """`a` minus `b` on the rows both answered (paired by id): Intelligence, accuracy and NLL,
    each with a paired bootstrap 95% interval."""
    bi = {p["id"]: p for p in b}
    common = [p for p in a if p["id"] in bi]
    if not common:
        raise ValueError("the two runs share no rows")
    pa = [(p["kind"], row_stats(p)) for p in common]
    pb = [(bi[p["id"]]["kind"], row_stats(bi[p["id"]])) for p in common]
    measures: dict[str, Callable[[list[list[Any]]], float]] = {
        "intelligence": lambda r: _intel(r[0]) - _intel(r[1]),
        "accuracy": lambda r: 100 * (_mean([s["correct"] for _, s in r[0]]) - _mean([s["correct"] for _, s in r[1]])),
        "nll": lambda r: _mean([s["nll"] for _, s in r[0]]) - _mean([s["nll"] for _, s in r[1]]),
    }
    out: dict[str, Any] = {"n_paired": len(common), "only_a": len(a) - len(common),
                           "only_b": len(b) - len(common)}
    for name, f in measures.items():
        point = f([pa, pb])
        ci = _ci([pa, pb], f, bootstrap, seed, 4 if name == "nll" else 2) if bootstrap else [None, None]
        out[name] = {"diff": _r(point, 2 if name != "nll" else 4), "ci95": ci}
    return out
