"""JevBench's 231 public tasks, run by JevBench's own harness against any System One endpoint,
then scored with the v1.5 rules as a LOCAL proxy (never comparable with a board number: the
official v1.5 scorer, its tier weights and the sealed half are not public).

The harness is cloned at a pinned commit into the decider-lab cache and installed without
dependencies into this Python; the task file is checked to hold 231 tasks. The task text is
not redistributed: it stays in your cache, and the run directory is kept outside the clone.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

from .suites import CACHE

REPO = "https://github.com/fstandhartinger/jevbench"
COMMIT = "1bcc55eb6c8cffde2306b3db03ede39b61c6152a"
BAND = (0.2, 0.8)


def ensure_harness(python: str = sys.executable) -> str:
    path = os.path.join(CACHE, "jevbench")
    if not os.path.isdir(os.path.join(path, ".git")):
        subprocess.run(["git", "clone", "-q", REPO, path], check=True)
    if subprocess.run(["git", "-C", path, "cat-file", "-e", f"{COMMIT}^{{commit}}"], capture_output=True).returncode:
        subprocess.run(["git", "-C", path, "fetch", "-q", "origin"], check=True)
    subprocess.run(["git", "-C", path, "checkout", "-q", "--detach", COMMIT], check=True)
    dirty = subprocess.run(["git", "-C", path, "status", "--porcelain", "--untracked-files=no"],
                           capture_output=True, text=True).stdout.strip()
    if dirty:
        raise RuntimeError(f"{path} has local modifications; the benchmark must be unmodified")
    probe = subprocess.run([python, "-c", "import jevbench,os;print(os.path.dirname(os.path.dirname("
                            "os.path.abspath(jevbench.__file__))))"], capture_output=True, text=True)
    if probe.returncode or os.path.realpath(probe.stdout.strip()) != os.path.realpath(path):
        subprocess.run([python, "-m", "pip", "install", "-q", "--no-deps", "-e", path], check=True)
    return path


def run(url: str, out: str, *, label: str = "model", python: str = sys.executable) -> dict[str, Any]:
    """Run the public tasks against `url` into `out`; returns the proxy scores."""
    harness = ensure_harness(python)
    out = os.path.abspath(out)
    if out.startswith(os.path.realpath(harness) + os.sep):
        raise ValueError("the run directory must be outside the JevBench checkout")
    if os.path.exists(os.path.join(out, "results.jsonl")):
        raise FileExistsError(f"{out} already holds a JevBench run; use a fresh directory")
    os.makedirs(out, exist_ok=True)
    tasks = os.path.join(out, "all.jsonl")
    with open(tasks, "w", encoding="utf-8") as fh:
        for part in ("original", "easy", "hard"):
            with open(os.path.join(harness, "datasets", "public", f"{part}.jsonl"), encoding="utf-8") as src:
                fh.writelines(line for line in src if line.strip())
    n = sum(1 for _ in open(tasks, encoding="utf-8"))
    if n != 231:
        raise RuntimeError(f"the public task file has {n} tasks, expected 231")
    with open(os.path.join(out, "jevbench.log"), "w", encoding="utf-8") as log:
        subprocess.run([python, "-m", "jevbench.cli", "run", "--tasks", tasks, "--adapter", "typesafe",
                        "--endpoint", url, "--key-env", "", "--model", label, "--run-label", label,
                        "--cost-basis", "no_billable_account_public_endpoint", "--reserve-usd", "0",
                        "--results", os.path.join(out, "results.jsonl"), "--raw-dir", os.path.join(out, "raw"),
                        "--ledger", os.path.join(out, "ledger.jsonl"), "--manifest", os.path.join(out, "manifest.json")],
                       check=True, stdout=log, stderr=subprocess.STDOUT)
        subprocess.run([python, "-m", "jevbench.cli", "summarize", "--tasks", tasks,
                        "--results", os.path.join(out, "results.jsonl"), "--ledger", os.path.join(out, "ledger.jsonl"),
                        "--public-export", os.path.join(out, "summary.json")], check=True, stdout=log,
                       stderr=subprocess.STDOUT)
    scores = score_run(out)
    scores["jevbench_commit"] = COMMIT
    with open(os.path.join(out, "scores.json"), "w", encoding="utf-8") as fh:
        json.dump(scores, fh, indent=2)
    return scores


# ---- the v1.5-rule proxy over a JevBench results.jsonl ------------------------------------

def _kind(r: dict[str, Any]) -> str:
    if r.get("ordinal_ev") is not None:
        return "score"
    return "noul" if set(r.get("probs") or {}) == {"yes", "no"} else "choice"


def _in_band(r: dict[str, Any]) -> bool:
    return _kind(r) == "noul" and BAND[0] < (r.get("probs") or {}).get("yes", 0.5) < BAND[1]


def score_rows(rows: list[dict[str, Any]], tasks: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Competence per type and the proxy Intelligence for JevBench result rows."""
    by: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by.setdefault(_kind(r), []).append(r)
    per: dict[str, float] = {}
    for k, rs in by.items():
        if k == "score":
            err = chance = 0.0
            for r in rs:
                t = tasks[r["task_id"]]
                labels = [str(x) for x in t["labels"]]
                probs = [float((r.get("probs") or {}).get(x, 0.0)) for x in labels]
                gold, n = labels.index(str(t["expected"])), len(labels)
                exp = sum(i * p for i, p in enumerate(probs)) / (sum(probs) or 1.0)
                err += abs(exp - gold) / (n - 1)
                chance += sum(abs(i - gold) for i in range(n)) / n / (n - 1)
            per[k] = 100 * (1 - err / chance) if chance else 0.0
        else:
            tot = 0.0
            for r in rs:
                c = 1.0 / max(2, len(r.get("probs") or {}))
                right = bool(r.get("correct")) and not _in_band(r)
                tot += ((1.0 if right else 0.0) - c) / (1 - c)
            per[k] = 100 * tot / len(rs)
    yn = by.get("noul", [])
    return {"tasks": len(rows), "n_correct": sum(bool(r.get("correct")) for r in rows),
            "competence_by_type": {k: round(v, 1) for k, v in per.items()},
            "intelligence_proxy": round(sum(per.values()) / len(per), 1) if per else None,
            "yes_no_in_band": sum(map(_in_band, yn)), "yes_no": len(yn),
            "note": "local proxy of the JevBench v1.5 rules on the 231 public v1 tasks; not a board score"}


def score_run(run_dir: str) -> dict[str, Any]:
    with open(os.path.join(run_dir, "results.jsonl"), encoding="utf-8") as fh:
        rows = [json.loads(x) for x in fh if x.strip()]
    with open(os.path.join(run_dir, "all.jsonl"), encoding="utf-8") as fh:
        tasks = {t["id"]: t for t in map(json.loads, filter(str.strip, fh))}
    return score_rows(rows, tasks)
