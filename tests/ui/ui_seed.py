"""A seeded Studio workspace with real results, shared by backend tests and the e2e server.

    url, stop = start_fake_system_one()           # the fake System One server from tests/conftest.py
    build_seeded_workspace("/tmp/ws", url)        # labs + a completed run root (via lab.run_lab)

Layout written:

    ws/.gitignore                          (so the state-dir rule can be checked)
    ws/labs/first/lab.yaml                 first-lab: fake (url) + majority + uniform; smoke and
                                           synthetic:per_kind=40; calibrate; baseline majority
    ws/labs/first/runs/first-lab/          the completed run root (lab.json, report.json, REPORT.md,
                                           <model>/<suite>/{predictions.jsonl,scores.json,run.json},
                                           fake/synthetic+cal)
    ws/labs/second/lab.yaml                second-lab: baselines only, never run
    ws/labs/broken/lab.yaml                broken-lab: invalid YAML with a models: key
    ws/data/notes.yaml                     YAML without models: (not a lab)
    ws/data/eval.jsonl                     a small JSONL suite file (rows from smoke)
    ws/node_modules/pkg/lab.yaml           ignored by the scan

No network beyond the loopback fake server; no GPU; takes a few seconds.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import threading
from http.server import ThreadingHTTPServer

import yaml

ROOT_CONFTEST = pathlib.Path(__file__).resolve().parents[1] / "conftest.py"
FIRST_LAB = "labs/first/lab.yaml"
FIRST_ROOT = "labs/first/runs/first-lab"


def _handler():
    spec = importlib.util.spec_from_file_location("_decider_lab_root_conftest", ROOT_CONFTEST)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod.Handler


def start_fake_system_one(port: int = 0) -> tuple[str, callable]:
    """Start the fake System One server on 127.0.0.1; returns (url, stop)."""
    handler = _handler()
    handler.calls = []
    handler.fail_next = 0
    srv = ThreadingHTTPServer(("127.0.0.1", port), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    def stop() -> None:
        srv.shutdown()
        srv.server_close()

    return f"http://127.0.0.1:{srv.server_address[1]}", stop


def first_lab(url: str) -> dict:
    return {"name": "first-lab", "workers": 4,
            "models": {"fake": {"url": url}, "majority": {"baseline": "majority"},
                       "uniform": {"baseline": "uniform"}},
            "suites": ["smoke", {"synthetic": {"per_kind": 40}}],
            "calibrate": True, "baseline": "majority"}


def build_seeded_workspace(dest: str, url: str, *, cache_dir: str | None = None) -> str:
    """Write the workspace and run first-lab in-process. Returns dest."""
    from decider_lab import suites
    from decider_lab.lab import run_lab

    ws = pathlib.Path(dest)
    (ws / "labs" / "first").mkdir(parents=True, exist_ok=True)
    (ws / ".gitignore").write_text("*.pyc\n")
    (ws / FIRST_LAB).write_text(yaml.safe_dump(first_lab(url), sort_keys=False))
    (ws / "labs" / "second").mkdir(parents=True, exist_ok=True)
    (ws / "labs" / "second" / "lab.yaml").write_text(yaml.safe_dump(
        {"name": "second-lab", "workers": 2,
         "models": {"majority": {"baseline": "majority"}, "random": {"baseline": "random"}},
         "suites": ["smoke"], "baseline": "majority"}, sort_keys=False))
    (ws / "labs" / "broken").mkdir(parents=True, exist_ok=True)
    (ws / "labs" / "broken" / "lab.yaml").write_text("name: broken-lab\nmodels:\n  a: {baseline: majority\n"
                                                     "suites: [smoke]\n")
    (ws / "data").mkdir(exist_ok=True)
    (ws / "data" / "notes.yaml").write_text("title: not a lab\nitems: [1, 2]\n")
    (ws / "node_modules" / "pkg").mkdir(parents=True, exist_ok=True)
    (ws / "node_modules" / "pkg" / "lab.yaml").write_text("models: {x: {baseline: uniform}}\n")

    old = suites.CACHE
    if cache_dir:
        suites.CACHE = cache_dir
    try:
        _name, rows, _params = suites.load_suite("smoke")
        with open(ws / "data" / "eval.jsonl", "w", encoding="utf-8") as fh:
            for r in rows[:30]:
                fh.write(json.dumps(r) + "\n")
        run_lab(str(ws / FIRST_LAB))
    finally:
        suites.CACHE = old
    assert (ws / FIRST_ROOT / "report.json").exists()
    assert (ws / FIRST_ROOT / "fake" / "synthetic+cal" / "scores.json").exists(), os.listdir(ws / FIRST_ROOT / "fake")
    return str(ws)
