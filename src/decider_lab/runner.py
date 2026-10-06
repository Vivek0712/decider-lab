"""Run one adapter over one suite, into a run directory that a later step (or person) can trust.

    <out>/predictions.jsonl   one line per row: id, task, kind, split, label, n, probs, latency_s, error
    <out>/run.json            who answered (adapter description, /health), the suite fingerprint,
                              host, versions, wall time
    <out>/scores.json         metrics.summarize over the scored split

A run resumes: rows already answered without error are not asked again. A row that fails is
recorded with its error and scored as a uniform answer, so failures cost score instead of
disappearing from it.
"""

from __future__ import annotations

import json
import os
import platform
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from . import __version__, metrics
from .adapters import Adapter, AdapterError
from .rows import Row, read_jsonl, rows_sha256


def gpu_name() -> str | None:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip().splitlines()[0] if out.returncode == 0 and out.stdout.strip() else None
    except (OSError, subprocess.SubprocessError):
        return None


def select(rows: list[Row], split: str | None = None, limit: int | None = None) -> list[Row]:
    """`split` keeps rows of that split (rows without one are kept); `limit` keeps the first
    N per kind, so a limited run still covers every question type."""
    if split and split != "all":
        rows = [r for r in rows if r.get("split", split) == split]
    if limit:
        seen: dict[str, int] = {}
        kept = []
        for r in rows:
            if seen.get(r["kind"], 0) < limit:
                seen[r["kind"]] = seen.get(r["kind"], 0) + 1
                kept.append(r)
        rows = kept
    return rows


def record(row: Row, probs: list[float] | None, latency: float | None, error: str | None) -> dict[str, Any]:
    return {"id": row["id"], "task": row.get("task"), "kind": row["kind"], "split": row.get("split"),
            "label": row["label"], "n": len(row["options"]),
            "probs": [round(p, 6) for p in probs] if probs else None,
            "latency_s": None if latency is None else round(latency, 4), "error": error}


def run(adapter: Adapter, rows: list[Row], out: str, *, suite: str = "suite", suite_params: dict[str, Any] | None = None,
        workers: int = 4, model: str = "model", score_split: str | None = None, resume: bool = True,
        progress: bool = True, fail_fast: int = 20) -> dict[str, Any]:
    """Answer `rows` with `adapter` into `out`; returns the scores."""
    os.makedirs(out, exist_ok=True)
    pred_path = os.path.join(out, "predictions.jsonl")
    done: dict[str, dict[str, Any]] = {}
    if resume and os.path.exists(pred_path):
        for p in read_jsonl(pred_path):
            if not p.get("error"):
                done[p["id"]] = p
    todo = [r for r in rows if r["id"] not in done]
    if hasattr(adapter, "fit"):
        adapter.fit(rows)
    t0 = time.time()
    lock = threading.Lock()
    finished = len(done)
    new: dict[str, dict[str, Any]] = {}

    def one(r: Row) -> dict[str, Any]:
        t = time.perf_counter()
        try:
            res = adapter.predict([r])[0]
        except Exception as e:  # an adapter bug on one row must not lose the others
            res = AdapterError(f"{type(e).__name__}: {e}")
        dt = time.perf_counter() - t
        if isinstance(res, AdapterError):
            return record(r, None, dt, str(res))
        return record(r, res, dt, None)

    with open(pred_path, "a" if resume else "w", encoding="utf-8") as fh, \
            ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(one, r) for r in todo]
        streak = 0
        for f in as_completed(futures):
            rec = f.result()
            # the first `fail_fast` answers all failing means the model cannot answer at all:
            # stop now, instead of recording a whole suite of errors as scores
            streak = streak + 1 if rec["error"] else -10**9
            if fail_fast and streak >= fail_fast:
                for g in futures:
                    g.cancel()
                raise RuntimeError(f"{model} / {suite}: the first {fail_fast} rows all failed; last error: "
                                   f"{rec['error']}")
            with lock:
                fh.write(json.dumps(rec) + "\n")
                fh.flush()
                new[rec["id"]] = rec
                finished += 1
                if progress and (finished % 50 == 0 or finished == len(rows)):
                    print(f"[decider-lab] {model} / {suite}: {finished}/{len(rows)}", flush=True)
    wall = time.time() - t0
    by_id = {**done, **new}
    preds = [by_id[r["id"]] for r in rows if r["id"] in by_id]
    # rewrite once, in suite order, keeping only the latest answer per row
    with open(pred_path, "w", encoding="utf-8") as fh:
        fh.writelines(json.dumps(p) + "\n" for p in preds)
    scored = [p for p in preds if not score_split or score_split == "all" or p.get("split") in (None, score_split)]
    scores = metrics.summarize(scored)
    scores["scored_split"] = score_split or "all"
    meta = {"model": model, "suite": suite, "suite_params": suite_params or {}, "suite_sha256": rows_sha256(rows),
            "n_rows": len(rows), "answered_this_call": len(new), "wall_s": round(wall, 2),
            "answerer": {**adapter.describe(), **({"source": adapter.source} if getattr(adapter, "source", None) else {})}, "decider_lab": __version__, "host": socket.gethostname(),
            "platform": platform.platform(), "python": platform.python_version(), "gpu": gpu_name(),
            "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    with open(os.path.join(out, "run.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)
    with open(os.path.join(out, "scores.json"), "w", encoding="utf-8") as fh:
        json.dump(scores, fh, indent=2)
    return scores


def load_run(path: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """(predictions, run.json) of a run directory."""
    preds = list(read_jsonl(os.path.join(path, "predictions.jsonl")))
    meta_path = os.path.join(path, "run.json")
    meta = json.load(open(meta_path, encoding="utf-8")) if os.path.exists(meta_path) else {}
    return preds, meta
