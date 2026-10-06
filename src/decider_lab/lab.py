"""A lab: one YAML file that says which models answer which suites, and what to compare.

    name: first-lab
    workers: 8                  # concurrent requests per model
    models:
      v19:                      # started and stopped for you
        serve: StrandsAgents/strands-decider-2B-hobson-v19
      remote: {url: "http://10.0.0.5:8000"}
      majority: {baseline: majority}
    finetune:                   # optional: train first, then evaluate as a model of the same name
      mine:
        from: StrandsAgents/strands-decider-2B-hobson-v19
        train: data/train.jsonl
        steps: 300
    suites: [smoke, {synthetic: {per_kind: 100}}, {file: data/my_eval.jsonl}]
    calibrate: true             # per-kind temperature fitted on dev, scored on test
    baseline: majority          # every model is compared with this one, row by row
    jevbench: [v19]             # optional: JevBench public tasks for these models

`decider-lab run lab.yaml` writes runs/<name>/<model>/<suite>/ for every pair and
runs/<name>/REPORT.md with the table and the paired comparisons.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Iterator
from typing import Any

import yaml

from . import calibrate, jevbench, report, runner, serve
from .adapters import make_adapter
from .suites import load_suite


def load_lab(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        lab = yaml.safe_load(fh) or {}
    lab.setdefault("name", os.path.splitext(os.path.basename(path))[0])
    lab.setdefault("models", {})
    lab.setdefault("suites", ["smoke"])
    lab["_dir"] = os.path.dirname(os.path.abspath(path))
    for name in lab.get("finetune") or {}:
        lab["models"].setdefault(name, {"finetuned": name})
    if not lab["models"]:
        raise ValueError(f"{path}: no models")
    bad = [m for m in lab.get("jevbench") or [] if m not in lab["models"]]
    if bad or (lab.get("baseline") and lab["baseline"] not in lab["models"]):
        raise ValueError(f"{path}: baseline/jevbench name models not in `models`: {bad or lab['baseline']}")
    return lab


@contextlib.contextmanager
def answerer(name: str, spec: Any, lab: dict[str, Any], run_root: str) -> Iterator[Any]:
    """An adapter for a model spec, starting (and later stopping) a server when needed."""
    base_dir = lab["_dir"]
    if isinstance(spec, dict) and ("serve" in spec or "finetuned" in spec):
        if "finetuned" in spec:
            from .finetune import run_finetune

            ckpt = run_finetune(spec["finetuned"], lab["finetune"][spec["finetuned"]],
                                os.path.join(run_root, "_finetune"))
            spec = {**spec, "serve": ckpt}
        with serve.served(spec["serve"], vision=bool(spec.get("vision")), device=spec.get("device"),
                          model_name=spec.get("model_name"), gpu=spec.get("gpu"),
                          log_path=os.path.join(run_root, name, "server.log"),
                          timeout=float(spec.get("health_timeout", 1800))) as (url, _health):
            yield make_adapter({"url": url, "timeout": spec.get("timeout", 300)}, base_dir=base_dir)
        return
    yield make_adapter(spec, base_dir=base_dir)


def run_lab(path: str, *, only: list[str] | None = None, limit: int | None = None, out: str | None = None) -> str:
    """Run every (model, suite) of a lab; returns the run root."""
    lab = load_lab(path)
    root = os.path.abspath(out or os.path.join(lab["_dir"], lab.get("out", "runs"), lab["name"]))
    os.makedirs(os.path.join(root), exist_ok=True)
    suites = [load_suite(s, base_dir=lab["_dir"]) for s in lab["suites"]]
    t0 = time.time()
    failures: list[str] = []
    for name, spec in lab["models"].items():
        if only and name not in only:
            continue
        os.makedirs(os.path.join(root, name), exist_ok=True)
        try:
            run_model(name, spec, lab, root, suites, limit, failures)
        except Exception as e:  # one model failing must not lose the others' results
            failures.append(f"{name}: {type(e).__name__}: {e}")
            print(f"[decider-lab] FAILED {failures[-1]}", flush=True)
    rep = report.write(root, baseline=lab.get("baseline"), title=lab["name"])
    with open(os.path.join(root, "lab.json"), "w", encoding="utf-8") as fh:
        json.dump({k: v for k, v in lab.items() if not k.startswith("_")}
                  | {"wall_s": round(time.time() - t0, 1), "failures": failures},
                  fh, indent=2)
    print(f"[decider-lab] report: {rep}", flush=True)
    if failures:
        raise RuntimeError("finished with failures:\n  " + "\n  ".join(failures))
    return root


def run_model(name: str, spec: Any, lab: dict[str, Any], root: str, suites: list[Any], limit: int | None,
              failures: list[str]) -> None:
    """Every suite (and JevBench, when asked) for one model."""
    workers = int((spec.get("workers") if isinstance(spec, dict) else None) or lab.get("workers", 4))
    with answerer(name, spec, lab, root) as adapter:
        for sname, rows, params in suites:
            rows = runner.select(rows, limit=limit)
            has_splits = any(r.get("split") for r in rows)
            d = os.path.join(root, name, sname)
            s = runner.run(adapter, rows, d, suite=sname, suite_params=params, workers=workers, model=name,
                           score_split="test" if has_splits else None)
            print(f"[decider-lab] {name} / {sname}: Intelligence {s['intelligence']} "
                  f"(95% CI {s.get('intelligence_ci95')}), accuracy {s['accuracy']}%, errors {s['errors']}",
                  flush=True)
            if lab.get("calibrate") and has_splits:
                info = calibrate.calibrate_run(d)
                print(f"[decider-lab] {name} / {sname}+cal: T={info['temperatures']} "
                      f"Intelligence {info['before']['intelligence']} -> {info['after']['intelligence']}",
                      flush=True)
        if name in (lab.get("jevbench") or []):
            url = getattr(adapter, "url", None)
            if not url:
                raise ValueError(f"jevbench needs a System One model; {name} is {adapter.name}")
            try:
                jb = jevbench.run(url, os.path.join(root, name, "jevbench"), label=name)
                print(f"[decider-lab] {name} / jevbench-public: proxy Intelligence {jb['intelligence_proxy']} "
                      f"({jb['n_correct']}/231 right)", flush=True)
            except Exception as e:  # the suites above are already scored; keep them
                failures.append(f"{name} / jevbench: {type(e).__name__}: {e}")
                print(f"[decider-lab] {failures[-1]}", flush=True)
