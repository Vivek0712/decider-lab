"""REPORT.md and report.json for a run root: every model on every suite, and paired deltas."""

from __future__ import annotations

import json
import os
from typing import Any

from . import metrics
from .runner import load_run


def _runs(root: str) -> dict[str, dict[str, str]]:
    """{model: {suite: run dir}} for every scored run under root."""
    out: dict[str, dict[str, str]] = {}
    for model in sorted(os.listdir(root)):
        mdir = os.path.join(root, model)
        if model.startswith("_") or not os.path.isdir(mdir):
            continue
        for suite in sorted(os.listdir(mdir)):
            if os.path.exists(os.path.join(mdir, suite, "scores.json")):
                out.setdefault(model, {})[suite] = os.path.join(mdir, suite)
    return out


def _fmt(x: Any) -> str:
    return "-" if x is None else str(x)


def _scored(preds: list[dict[str, Any]], split: str | None) -> list[dict[str, Any]]:
    return [p for p in preds if not split or split == "all" or p.get("split") in (None, split)]


def write(root: str, *, baseline: str | None = None, title: str = "lab") -> str:
    runs = _runs(root)
    suites = sorted({s for m in runs.values() for s in m})
    data: dict[str, Any] = {"title": title, "runs": {}, "vs_baseline": {}}
    lines = [f"# {title}", "",
             "Intelligence is a local proxy computed with the JevBench v1.5 rules (yes/no between 0.2 and 0.8 "
             "counts wrong; chance-corrected; score graded by expected level). It ranks runs on these suites "
             "against each other; it is not a JevBench board score. `+cal` = per-kind temperature fitted on "
             "dev, scored on test.", ""]
    for suite in suites:
        lines += [f"## {suite}", "",
                  "| model | Intelligence (95% CI) | accuracy % | NLL | ECE | yes/no in band % | errors | median latency s |",
                  "|---|---|---|---|---|---|---|---|"]
        for model in runs:
            if suite not in runs[model]:
                continue
            s = json.load(open(os.path.join(runs[model][suite], "scores.json"), encoding="utf-8"))
            data["runs"].setdefault(model, {})[suite] = s
            if suite.startswith("jevbench"):
                lines.append(f"| {model} | {_fmt(s.get('intelligence_proxy'))} | {s.get('n_correct')}/231 right | - "
                             f"| - | {s.get('yes_no_in_band')}/{s.get('yes_no')} | - | - |")
                continue
            ci = s.get("intelligence_ci95") or [None, None]
            band = (s.get("by_kind", {}).get("noul") or {}).get("in_band")
            lines.append(f"| {model} | {_fmt(s['intelligence'])} ({_fmt(ci[0])} to {_fmt(ci[1])}) | {_fmt(s['accuracy'])} "
                         f"| {_fmt(s['nll'])} | {_fmt(s['ece'])} | {_fmt(band)} | {s['errors']} "
                         f"| {_fmt((s.get('latency_s') or {}).get('median'))} |")
        lines.append("")
        if baseline and baseline in runs and suite in runs[baseline] and not suite.startswith("jevbench"):
            b_preds, _ = load_run(runs[baseline][suite])
            split = json.load(open(os.path.join(runs[baseline][suite], "scores.json"))).get("scored_split")
            rows = []
            for model in runs:
                if model == baseline or suite not in runs[model]:
                    continue
                a_preds, _ = load_run(runs[model][suite])
                c = metrics.compare(_scored(a_preds, split), _scored(b_preds, split))
                data["vs_baseline"].setdefault(model, {})[suite] = c
                i, acc = c["intelligence"], c["accuracy"]
                rows.append(f"| {model} | {i['diff']:+} ({i['ci95'][0]} to {i['ci95'][1]}) "
                            f"| {acc['diff']:+} | {c['n_paired']} |")
            if rows:
                lines += [f"Paired against **{baseline}** (same rows, bootstrap 95% CI):", "",
                          "| model | delta Intelligence | delta accuracy | rows |", "|---|---|---|---|", *rows, ""]
        fam_models = [m for m in runs if suite in runs[m] and not suite.startswith("jevbench")]
        if fam_models:
            fams = sorted({f for m in fam_models
                           for f in data["runs"][m][suite].get("by_family", {})})
            if 1 < len(fams) <= 40:
                lines += ["<details><summary>by family (Intelligence)</summary>", "",
                          "| family | " + " | ".join(fam_models) + " |", "|---|" + "---|" * len(fam_models)]
                for f in fams:
                    vals = [_fmt(data["runs"][m][suite]["by_family"].get(f, {}).get("intelligence")) for m in fam_models]
                    lines.append(f"| {f} | " + " | ".join(vals) + " |")
                lines += ["", "</details>", ""]
    path = os.path.join(root, "REPORT.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    with open(os.path.join(root, "report.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    return path
