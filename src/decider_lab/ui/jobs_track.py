"""Job tracking: stages, per model/suite progress, telemetry and results from what a job prints.

    tracker = get_tracker(st)          # installed once per server (line/finish handlers + a sampler thread)
    with tracker.lock:                 # hold it across start + register so no log line is parsed early
        job = st.jobs.start(...)
        tracker.register(job["job_id"], ctx)

The log rules are API.md 6.1 ("Log-line → state rules"). Local run and eval jobs also refresh
row counts every 2 s from `<root>/<model>/<suite>/predictions.jsonl` (append-only during a run)
and record a telemetry sample (rows/s over the last 30 s, errors, and nvidia-smi when this machine
has one) into `<job dir>/telemetry.jsonl`. Remote jobs get their samples from the `[telemetry]`
lines the SDK sampler prints on the remote machine (simulated ones in fake-cloud mode).

The tracker keeps the authoritative stages/progress per job in memory (rebuilt from job.json and
track.json after a restart) so the line parser and the sampler never overwrite each other with
stale copies.
"""

from __future__ import annotations

import contextlib
import copy
import json
import os
import re
import sys
import threading
import time
from collections import deque
from typing import Any

from .. import telemetry
from .jobs import ACTIVE, TERMINAL, _parse_iso
from .state import StudioState
from .workspace import iso, now_iso

PIPE = "  | "
RE_PROGRESS = re.compile(r"^\[decider-lab\] (?P<m>.+?) / (?P<s>[^\s:]+): (?P<d>\d+)/(?P<t>\d+)\s*$")
RE_DONE = re.compile(r"^\[decider-lab\] (?P<m>.+?) / (?P<s>[^\s:]+): Intelligence (?P<i>\S+) \(95% CI (?P<ci>.*?)\), "
                     r"accuracy (?P<a>\S+)%, errors (?P<e>\d+)")
RE_CAL = re.compile(r"^\[decider-lab\] (?P<m>.+?) / (?P<s>[^\s:]+)\+cal: T=.* Intelligence (?P<b>\S+) -> (?P<a>\S+)\s*$")
RE_FEW = re.compile(r"^\[decider-lab\] (?P<m>.+?) / (?P<s>[^\s:]+): too few dev rows")
RE_JEV = re.compile(r"^\[decider-lab\] (?P<m>.+?) / jevbench-public: proxy Intelligence (?P<i>\S+) \((?P<n>\d+)/231 right\)")
RE_JEV_FAIL = re.compile(r"^\[decider-lab\] (?P<m>.+?) / jevbench: (?P<msg>.+)$")
RE_FAILED = re.compile(r"^\[decider-lab\] FAILED (?P<m>[^:]+): (?P<msg>.*)$")
RE_REPORT = re.compile(r"^\[decider-lab\] report: (?P<p>.+)$")
RE_FINETUNE = re.compile(r"^\[decider-lab\] finetune\.(?P<m>[^:]+): (?P<msg>.*)$")
RE_RENT = re.compile(r"^\[(?P<p>vast)\] renting (?P<n>\d+)x (?P<gpu>\S+) at \$(?P<r>[\d.]+)/h \(offer (?P<o>\d+)\)")
RE_VAST_ID = re.compile(r"^\[vast\] instance (?P<id>\d+); waiting")
RE_AWS = re.compile(r"^\[aws\] (?P<t>\S+) (?P<id>i-[0-9a-f]+) from (?P<ami>\S+) in (?P<region>\S+)")
RE_SSH_OK = re.compile(r"^\[(?P<p>vast|aws|ssh)\] ssh ok: (?P<target>\S+?):(?P<port>\d+), work dir")
RE_BOOT = re.compile(r"^\[(?P<p>vast|aws|ssh)\] bootstrap: ")
RE_BOOT_OK = re.compile(r"^\[(?P<p>vast|aws|ssh)\] bootstrap ok")
RE_COPIED = re.compile(r"^\[(?P<p>vast|aws|ssh)\] results copied to ")
RE_RELEASED = re.compile(r"^\[(?P<p>vast|aws)\] instance \S+ (destroyed|terminated)\s*$|^\[ssh\] done; ")
RE_KEEP = re.compile(r"--keep: the machine is left running")
RE_STILL = re.compile(r"WARNING: instance .* may still be running|WARNING: could not confirm .* terminated")
RE_REMOTE_EXIT = re.compile(r"the remote run exited with (?P<c>-?\d+)")
RE_CHECK = re.compile(r"^\[(?P<p>vast|aws|ssh)\] check: ")
RE_PULL_BYTES = re.compile(r"^\[pull\] (?P<gb>[\d.]+) GB(?: \((?P<pct>[\d.]+)%\))?")
RE_TELEMETRY = re.compile(r"^\[telemetry\] (?P<j>\{.*\})\s*$")

STAGE_LABELS = {"prepare": "Prepare", "finetune": "Fine-tune", "run": "Run", "report": "Report", "check": "Check",
                "acquire": "Acquire machine", "copy": "Copy lab", "bootstrap": "Bootstrap", "fetch": "Fetch results",
                "release": "Release machine"}
SAMPLE_EVERY_S = 2.0
RATE_WINDOW_S = 30.0
LAB_POLL_S = 3.0


def _num(x: str | None) -> float | None:
    try:
        return None if x is None or x == "None" else float(x)
    except ValueError:
        return None


def _ci(text: str) -> list[float | None] | None:
    m = re.match(r"\[\s*([-\d.]+|None)\s*,\s*([-\d.]+|None)\s*\]", text.strip())
    return [_num(m.group(1)), _num(m.group(2))] if m else None


def run_entry(model: str, suite: str, total: int | None = None, path: str | None = None) -> dict[str, Any]:
    return {"model": model, "suite": suite, "status": "pending", "done": 0, "total": total, "errors": 0,
            "rows_per_s": None, "intelligence": None, "ci95": None, "accuracy": None, "message": None,
            "reused": None, **({"_path": path} if path else {})}


def count_predictions(path: str) -> tuple[int, int]:
    done = errors = 0
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.strip():
                    continue
                done += 1
                if '"error": null' not in line and '"error":null' not in line:
                    errors += 1
    except OSError:
        pass
    return done, errors


class JobState:
    """What the tracker knows about one job (the persisted part is track.json)."""

    def __init__(self, job_id: str, ctx: dict[str, Any], job: dict[str, Any]) -> None:
        self.job_id = job_id
        self.ctx = ctx
        self.kind = job.get("kind", ctx.get("kind", "run"))
        self.backend = job.get("backend", "local")
        self.stages: list[dict[str, Any]] = copy.deepcopy(job.get("stages") or [])
        self.progress: dict[str, Any] = copy.deepcopy(job.get("progress") or {})
        for k, v in (("fraction", None), ("label", ""), ("runs", []), ("bytes", None), ("finetune", None),
                     ("machine", None)):
            self.progress.setdefault(k, v)
        self.root_id: str | None = job.get("root_id")
        self.machine_id: str | None = job.get("machine_id")
        self.telemetry: dict[str, Any] = dict(job.get("telemetry") or {"source": "none", "reason": None})
        self.history: dict[str, deque] = {}
        self.total_history: deque = deque(maxlen=64)
        self.last_sample = 0.0
        self.last_remote_rows: tuple[float, int] | None = None
        self.seen_pipe = False
        self.acquire_at: float | None = ctx.get("acquire_at")
        self.dirty = False

    # ---- helpers -------------------------------------------------------------------------------

    def stage(self, name: str) -> dict[str, Any] | None:
        return next((s for s in self.stages if s["name"] == name), None)

    def set_stage(self, name: str, status: str, detail: str | None = None, *, keep_detail: bool = False) -> None:
        s = self.stage(name)
        if s is None:
            return
        now = now_iso()
        if status == "active" and s["status"] in ("done", "failed", "skipped"):
            return
        if status == "active" and s["status"] != "active":
            s["started_at"] = s["started_at"] or now
        if status in ("done", "failed", "warning", "skipped"):
            if s["status"] == "pending" and status != "skipped":
                s["started_at"] = s["started_at"] or now
            if status != "skipped":
                s["ended_at"] = s["ended_at"] or now
        s["status"] = status
        if detail is not None and not (keep_detail and s.get("detail")):
            s["detail"] = detail[:300]
        self.dirty = True

    def activate(self, name: str) -> None:
        """Mark every earlier active/pending stage done and `name` active (a stage starting implies the
        ones before it finished)."""
        names = [s["name"] for s in self.stages]
        if name not in names:
            return
        for s in self.stages[: names.index(name)]:
            if s["status"] in ("active", "pending"):
                self.set_stage(s["name"], "done")
        self.set_stage(name, "active")

    def run(self, model: str, suite: str) -> dict[str, Any]:
        for r in self.progress["runs"]:
            if r["model"] == model and r["suite"] == suite:
                return r
        r = run_entry(model, suite)
        self.progress["runs"].append(r)
        self.dirty = True
        return r

    def machine(self) -> dict[str, Any]:
        if self.progress.get("machine") is None:
            self.progress["machine"] = {"provider": self.backend, "id": None, "target": None, "gpu": None,
                                        "usd_per_hour": self.ctx.get("rate"), "cost_so_far_usd": None}
        return self.progress["machine"]

    def recompute(self) -> None:
        runs = [r for r in self.progress["runs"] if r["status"] != "skipped"]
        if self.kind == "pull":
            b = self.progress.get("bytes")
            if b and b.get("total"):
                self.progress["fraction"] = min(1.0, b["done"] / b["total"])
            return
        if runs:
            parts = []
            for r in runs:
                if r["status"] in ("done", "failed"):
                    parts.append(1.0)
                elif r["status"] == "running" and r.get("total"):
                    parts.append(min(1.0, r["done"] / max(1, r["total"])) * 0.99)
                else:
                    parts.append(0.0)
            done = sum(1 for r in runs if r["status"] in ("done", "failed"))
            frac = sum(parts) / len(parts)
            failed = sum(1 for r in runs if r["status"] == "failed")
            label = f"{done}/{len(runs)} runs" + (f" · {failed} failed" if failed else "")
            if self.backend != "local" and self.kind == "run":
                pre = [s for s in self.stages if s["name"] in ("check", "acquire", "copy", "bootstrap")]
                pre_done = sum(1 for s in pre if s["status"] == "done") / max(1, len(pre))
                frac = 0.1 * pre_done + 0.85 * frac + (0.05 if (self.stage("release") or {}).get("status") == "done"
                                                       else 0.0)
                run_st = (self.stage("run") or {}).get("status")
                if run_st == "pending":
                    stages_done = sum(1 for s in self.stages if s["status"] == "done")
                    label = f"{stages_done}/{len(self.stages)} stages"
            if len(runs) == 1 and self.kind == "eval":
                r = runs[0]
                label = f"{r['done']}/{r['total'] if r['total'] is not None else '?'} rows"
            self.progress["fraction"] = round(min(1.0, frac), 4)
            self.progress["label"] = label
        elif self.stages:
            done = sum(1 for s in self.stages if s["status"] in ("done", "skipped"))
            self.progress["fraction"] = round(done / len(self.stages), 4)
            self.progress["label"] = f"{done}/{len(self.stages)} stages"

    def public_progress(self) -> dict[str, Any]:
        p = copy.deepcopy(self.progress)
        for r in p["runs"]:
            r.pop("_path", None)
        return p

    def updates(self) -> dict[str, Any]:
        self.recompute()
        out: dict[str, Any] = {"stages": copy.deepcopy(self.stages), "progress": self.public_progress(),
                               "telemetry": dict(self.telemetry)}
        if self.root_id:
            out["root_id"] = self.root_id
        if self.machine_id:
            out["machine_id"] = self.machine_id
        return out


class Tracker:
    def __init__(self, st: StudioState) -> None:
        self.st = st
        self.jm = st.jobs
        self.lock = threading.RLock()
        self.jobs: dict[str, JobState] = {}
        self._stop = threading.Event()
        self._lab_mtimes: dict[str, float] | None = None
        self._last_lab_poll = 0.0
        self.jm.add_line_handler(self.on_line)
        self.jm.add_finish_handler(self.on_finish)
        self._thread = threading.Thread(target=self._loop, name="studio-jobs-track", daemon=True)
        self._thread.start()

    # ---- registration --------------------------------------------------------------------------

    def ctx_path(self, job_id: str) -> str:
        return os.path.join(self.jm.dir, job_id, "track.json")

    def register(self, job_id: str, ctx: dict[str, Any], *, progress: dict[str, Any] | None = None,
                 stages_extra: dict[str, Any] | None = None) -> None:
        with contextlib.suppress(OSError):
            with open(self.ctx_path(job_id), "w", encoding="utf-8") as fh:
                json.dump(ctx, fh)
        job = self.jm.get(job_id)
        js = JobState(job_id, ctx, job)
        if progress:
            js.progress.update(progress)
        if js.backend == "local" and js.kind in ("run", "eval"):
            js.telemetry = {"source": "local-nvidia-smi" if telemetry.nvidia_smi_path() else "none",
                            "reason": None if telemetry.nvidia_smi_path() else
                            "no NVIDIA GPU on this machine (Apple MPS and CPU are not sampled)"}
        elif js.kind == "run":
            js.telemetry = {"source": "simulated" if self.st.fake_cloud else "remote-ssh",
                            "reason": None if self.st.fake_cloud else "samples arrive every 10 s from the machine"}
        else:
            js.telemetry = {"source": "none", "reason": f"{js.kind} jobs have no telemetry"}
        with self.lock:
            self.jobs[job_id] = js
            first = js.stages[0]["name"] if js.stages else None
            if first and job["status"] in ("running", "queued"):
                if job["status"] == "running":
                    js.set_stage(first, "active")
            upd = js.updates()
        self.jm.update(job_id, **upd)

    def state(self, job: dict[str, Any]) -> JobState:
        js = self.jobs.get(job["job_id"])
        if js is None:
            ctx: dict[str, Any] = {}
            with contextlib.suppress(OSError, ValueError):
                with open(self.ctx_path(job["job_id"]), encoding="utf-8") as fh:
                    ctx = json.load(fh)
            js = JobState(job["job_id"], ctx, job)
            self.jobs[job["job_id"]] = js
        return js

    # ---- log lines -----------------------------------------------------------------------------

    def on_line(self, job: dict[str, Any], line: dict[str, Any]) -> dict[str, Any] | None:
        text = line["text"]
        with self.lock:
            js = self.state(job)
            js.dirty = False
            piped = text.startswith(PIPE) or text.startswith("  |")
            body = text[len(PIPE):] if text.startswith(PIPE) else (text[3:].lstrip() if text.startswith("  |") else text)
            self._parse(js, body.strip(), piped, line)
            if not js.dirty:
                return None
            return js.updates()

    def _first_stage_active(self, js: JobState) -> None:
        if js.stages and all(s["status"] == "pending" for s in js.stages):
            js.set_stage(js.stages[0]["name"], "active")

    def _parse(self, js: JobState, text: str, piped: bool, line: dict[str, Any]) -> None:
        self._first_stage_active(js)
        m = RE_TELEMETRY.match(text)
        if m:
            if js.backend != "local":
                self._remote_sample(js, m.group("j"))
            return
        if js.kind == "run" and js.backend != "local":
            if self._parse_remote(js, text, piped):
                return
        if js.kind == "pull":
            self._parse_pull(js, text)
            return
        m = RE_FINETUNE.match(text)
        if m:
            name = m.group("m")
            if js.stage("finetune"):
                js.activate("finetune")
                js.set_stage("finetune", "active", f"{name}: {m.group('msg')}")
            ft = js.progress.get("finetune") or []
            row = next((f for f in ft if f["model"] == name), None)
            if row is None:
                row = {"model": name, "step": None, "total_steps": None, "loss": None, "status": "running"}
                ft.append(row)
            row["status"] = "running"
            js.progress["finetune"] = ft
            js.dirty = True
            return
        m = RE_PROGRESS.match(text)
        if m:
            r = js.run(m.group("m"), m.group("s"))
            self._enter_run(js, r["model"])
            r["status"] = "running" if r["status"] in ("pending", "running") else r["status"]
            r["done"] = max(r["done"], int(m.group("d")))
            r["total"] = int(m.group("t"))
            js.dirty = True
            self._rate(js, r)
            return
        m = RE_DONE.match(text)
        if m:
            r = js.run(m.group("m"), m.group("s"))
            self._enter_run(js, r["model"])
            r.update(status="done", intelligence=_num(m.group("i")), ci95=_ci(m.group("ci")),
                     accuracy=_num(m.group("a")), errors=int(m.group("e")), rows_per_s=None)
            if r["total"] is not None:
                r["done"] = max(r["done"], r["total"])
            js.dirty = True
            return
        m = RE_CAL.match(text)
        if m:
            r = js.run(m.group("m"), m.group("s") + "+cal")
            r.update(status="done", intelligence=_num(m.group("a")), ci95=None,
                     message=f"Intelligence {m.group('b')} → {m.group('a')} after calibration")
            js.dirty = True
            return
        m = RE_FEW.match(text)
        if m:
            r = js.run(m.group("m"), m.group("s") + "+cal")
            r.update(status="skipped", message="too few dev rows to fit temperatures (fewer than 30 per kind)")
            js.dirty = True
            return
        m = RE_JEV.match(text)
        if m:
            r = js.run(m.group("m"), "jevbench")
            r.update(status="done", intelligence=_num(m.group("i")), done=int(m.group("n")), total=231)
            js.dirty = True
            return
        m = RE_JEV_FAIL.match(text)
        if m:
            r = js.run(m.group("m"), "jevbench")
            r.update(status="failed", message=m.group("msg")[:300])
            js.dirty = True
            return
        m = RE_FAILED.match(text)
        if m:
            model = m.group("m").strip()
            msg = m.group("msg")[:300]
            hit = False
            for r in js.progress["runs"]:
                if r["model"] == model and r["status"] in ("pending", "running"):
                    r.update(status="failed", message=msg)
                    hit = True
            if not hit:
                js.run(model, "—").update(status="failed", message=msg)
            for f in js.progress.get("finetune") or []:
                if f["model"] == model and f["status"] != "done":
                    f["status"] = "failed"
            js.dirty = True
            return
        m = RE_REPORT.match(text)
        if m:
            if js.backend == "local":
                js.activate("report")
                js.set_stage("report", "done", "REPORT.md written")
                root = os.path.dirname(m.group("p").strip())
                with contextlib.suppress(Exception):
                    js.root_id = self.st.workspace.encode_id(os.path.realpath(root))
            js.dirty = True
            return
        if js.kind in ("eval", "calibrate", "jevbench", "suite_build") and text:
            if js.kind == "calibrate" and text.startswith("{"):
                js.activate("score")
            elif js.kind == "jevbench" and js.stage("harness") and js.stage("harness")["status"] == "active" \
                    and "jevbench" not in text.lower():
                pass

    def _enter_run(self, js: JobState, model: str) -> None:
        if js.kind == "eval":
            js.activate("run")
            return
        if js.kind != "run":
            return
        if js.backend == "local":
            ft = js.progress.get("finetune") or []
            for f in ft:
                if f["model"] == model and f["status"] == "running":
                    f["status"] = "done"
            if js.stage("finetune") and ft and all(f["status"] in ("done", "failed") for f in ft):
                js.set_stage("finetune", "done")
            if (js.stage("run") or {}).get("status") != "active":
                if js.stage("finetune") and (js.stage("finetune") or {}).get("status") == "active":
                    js.set_stage("prepare", "done")
                    js.set_stage("run", "active")
                else:
                    js.activate("run")
        else:
            js.activate("run")

    def _parse_remote(self, js: JobState, text: str, piped: bool) -> bool:
        """Remote stage lines; True if consumed."""
        if piped and not js.seen_pipe:
            js.seen_pipe = True
            js.activate("run")
        if piped:
            return False
        m = RE_CHECK.match(text)
        if m:
            js.set_stage("check", "active", text.split(": ", 1)[1][:200])
            return True
        m = RE_RENT.match(text)
        if m:
            js.activate("acquire")
            js.set_stage("acquire", "active", f"offer {m.group('o')}")
            mach = js.machine()
            mach.update(provider="vast", gpu=f"{m.group('n')}x {m.group('gpu')}", usd_per_hour=float(m.group("r")))
            js.acquire_at = js.acquire_at or time.time()
            js.ctx["acquire_at"] = js.acquire_at
            self._save_ctx(js)
            return True
        m = RE_VAST_ID.match(text)
        if m:
            js.machine_id = m.group("id")
            js.machine()["id"] = m.group("id")
            js.set_stage("acquire", "active", f"instance {m.group('id')}")
            self._save_ctx(js)
            return True
        m = RE_AWS.match(text)
        if m:
            js.activate("acquire")
            js.machine_id = m.group("id")
            mach = js.machine()
            mach.update(provider="aws", id=m.group("id"), gpu=m.group("t"))
            js.set_stage("acquire", "active", f"{m.group('t')} {m.group('id')} in {m.group('region')}")
            js.acquire_at = js.acquire_at or time.time()
            js.ctx["acquire_at"] = js.acquire_at
            self._save_ctx(js)
            return True
        m = RE_SSH_OK.match(text)
        if m:
            js.activate("copy")
            mach = js.machine()
            mach["target"] = f"{m.group('target')}:{m.group('port')}"
            if js.backend == "ssh":
                mach["id"] = mach["id"] or m.group("target")
            return True
        if text.startswith("[remote]") and "presigned" in text:
            js.set_stage("copy", "active", text[len("[remote] "):])
            return True
        if RE_BOOT.match(text):
            js.activate("bootstrap")
            return True
        if RE_BOOT_OK.match(text):
            js.set_stage("bootstrap", "done")
            js.activate("run")
            return True
        if RE_COPIED.match(text):
            js.activate("fetch")
            js.set_stage("fetch", "done")
            js.set_stage("release", "active")
            self._root_from_ctx(js)
            return True
        m = RE_REMOTE_EXIT.search(text)
        if m:
            js.set_stage("run", "failed", f"the remote run exited with {m.group('c')}")
            js.set_stage("fetch", "done", "results and logs copied")
            js.set_stage("release", "active")
            self._root_from_ctx(js)
            return True
        if RE_KEEP.search(text):
            js.set_stage("release", "warning", "machine left running: release it in Compute")
            return True
        if RE_STILL.search(text):
            js.set_stage("release", "failed", text[:300])
            return True
        if RE_RELEASED.match(text):
            for s in js.stages:
                if s["name"] != "release" and s["status"] == "active":
                    js.set_stage(s["name"], "failed" if s["name"] != "fetch" else "done",
                                 "stopped before it finished", keep_detail=True)
            js.set_stage("release", "done", "your machine; nothing to release" if js.backend == "ssh"
                         else ("instance destroyed" if js.backend == "vast" else "instance terminated"))
            return True
        return False

    def _parse_pull(self, js: JobState, text: str) -> None:
        m = RE_PULL_BYTES.match(text)
        if m:
            js.activate("download")
            done = int(float(m.group("gb")) * 1e9)
            total = int(done / (float(m.group("pct")) / 100)) if m.group("pct") and float(m.group("pct")) > 0 else None
            js.progress["bytes"] = {"done": done, "total": total}
            js.progress["label"] = f"{m.group('gb')} GB" + (f" ({m.group('pct')}%)" if m.group("pct") else "")
            js.dirty = True
        elif text.startswith("[pull]"):
            if "sha256" in text or "verif" in text.lower():
                js.activate("verify")
            elif "extract" in text.lower():
                js.activate("extract")
            else:
                js.activate("download")
            js.set_stage(next((s["name"] for s in js.stages if s["status"] == "active"), "download"), "active",
                         text[len("[pull] "):][:200])

    def _save_ctx(self, js: JobState) -> None:
        with contextlib.suppress(OSError):
            with open(self.ctx_path(js.job_id), "w", encoding="utf-8") as fh:
                json.dump(js.ctx, fh)

    def _root_from_ctx(self, js: JobState) -> None:
        root = js.ctx.get("root")
        if root and os.path.isdir(root):
            with contextlib.suppress(Exception):
                js.root_id = self.st.workspace.encode_id(root)

    # ---- telemetry -----------------------------------------------------------------------------

    def tele_path(self, job_id: str) -> str:
        return os.path.join(self.jm.dir, job_id, "telemetry.jsonl")

    def _append_sample(self, job_id: str, sample: dict[str, Any]) -> None:
        with contextlib.suppress(OSError):
            with open(self.tele_path(job_id), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(sample, separators=(",", ":")) + "\n")

    def _remote_sample(self, js: JobState, raw: str) -> None:
        try:
            s = json.loads(raw)
        except ValueError:
            return
        if not isinstance(s, dict):
            return
        rows = s.get("rows") or {}
        total_done = sum(int(v.get("done") or 0) for v in rows.values() if isinstance(v, dict))
        errors = sum(int(v.get("errors") or 0) for v in rows.values() if isinstance(v, dict))
        now = time.time()
        rps = None
        if js.last_remote_rows is not None:
            t0, d0 = js.last_remote_rows
            if now > t0:
                rps = round(max(0, total_done - d0) / (now - t0), 2)
        js.last_remote_rows = (now, total_done)
        gpus = [g for g in (s.get("gpus") or []) if isinstance(g, dict)]
        sample = {"ts": s.get("ts") or now_iso(), "gpus": gpus, "rows_per_s": rps, "errors_total": errors}
        self._append_sample(js.job_id, sample)
        if gpus:
            js.machine()["gpu"] = js.machine().get("gpu") or f"{len(gpus)}x {gpus[0].get('name')}"
            if js.telemetry.get("reason"):
                js.telemetry = {"source": js.telemetry.get("source", "remote-ssh"), "reason": None}
                js.dirty = True
        for key, v in rows.items():
            if not isinstance(v, dict) or "/" not in key:
                continue
            model, _, suite = key.partition("/")
            r = next((x for x in js.progress["runs"] if x["model"] == model and x["suite"] == suite), None)
            if r and r["status"] in ("pending", "running") and int(v.get("done") or 0) > r["done"]:
                r["done"] = int(v.get("done") or 0)
                r["errors"] = int(v.get("errors") or 0)
                if r["status"] == "pending":
                    r["status"] = "running"
                self._rate(js, r)
                js.dirty = True

    def _rate(self, js: JobState, r: dict[str, Any]) -> None:
        key = f"{r['model']}/{r['suite']}"
        h = js.history.setdefault(key, deque(maxlen=64))
        now = time.time()
        h.append((now, r["done"]))
        while h and now - h[0][0] > RATE_WINDOW_S and len(h) > 2:
            h.popleft()
        if len(h) >= 2 and h[-1][0] > h[0][0]:
            r["rows_per_s"] = round((h[-1][1] - h[0][1]) / (h[-1][0] - h[0][0]), 2)

    # ---- the sampler ---------------------------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.wait(0.5) and not self.jm._stop.is_set():
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001 - never let the sampler die
                print(f"[studio] jobs tracker: {type(e).__name__}: {self.st.redact(str(e))}", file=sys.stderr)

    def stop(self) -> None:
        self._stop.set()

    def tick(self) -> None:
        now = time.time()
        if now - self._last_lab_poll >= LAB_POLL_S:
            self._last_lab_poll = now
            self._poll_labs()
        try:
            active = self.jm.list(status="active", limit=1000)
        except Exception:  # noqa: BLE001
            return
        for s in active:
            if s["status"] != "running":
                continue
            job = self.jm.get(s["job_id"])
            with self.lock:
                js = self.state(job)
                if now - js.last_sample < SAMPLE_EVERY_S:
                    continue
                js.last_sample = now
                js.dirty = False
                self._sample(js, job)
                upd = js.updates() if js.dirty or self._differs(js, job) else None
            if upd:
                with contextlib.suppress(Exception):
                    self.jm.update(js.job_id, **upd)

    def _differs(self, js: JobState, job: dict[str, Any]) -> bool:
        return job.get("stages") != js.stages or job.get("progress", {}).get("runs") != js.public_progress()["runs"]

    def _sample(self, js: JobState, job: dict[str, Any]) -> None:
        if js.backend != "local" and js.kind == "run":
            mach = js.progress.get("machine")
            if mach and mach.get("usd_per_hour") and js.acquire_at:
                rel = (js.stage("release") or {}).get("status")
                if rel not in ("done",):
                    cost = round(float(mach["usd_per_hour"]) * (time.time() - js.acquire_at) / 3600, 2)
                    if cost != mach.get("cost_so_far_usd"):
                        mach["cost_so_far_usd"] = cost
                        js.dirty = True
            return
        if js.kind not in ("run", "eval") or js.backend != "local":
            return
        total_done = 0
        total_err = 0
        any_rows = False
        for r in js.progress["runs"]:
            path = r.get("_path")
            if not path:
                root = js.ctx.get("root")
                if not root:
                    continue
                path = os.path.join(root, r["model"], r["suite"], "predictions.jsonl")
            if r["status"] not in ("pending", "running"):
                d, e = (r["done"], r["errors"])
            elif os.path.exists(path):
                d, e = count_predictions(path)
                if r.get("reused") is None:
                    r["reused"] = (js.ctx.get("reused") or {}).get(f"{r['model']}/{r['suite']}")
                if d != r["done"] or e != r["errors"]:
                    if d > (r.get("reused") or 0) and r["status"] == "pending":
                        r["status"] = "running"
                        self._enter_run(js, r["model"])
                    r["done"], r["errors"] = d, e
                    self._rate(js, r)
                    js.dirty = True
                elif r["status"] == "running" and r.get("rows_per_s"):
                    self._rate(js, r)
            else:
                d, e = 0, 0
            if r["status"] in ("pending", "running", "done", "failed"):
                total_done += d if r["status"] in ("pending", "running") else 0
                total_err += e
                any_rows = any_rows or d > 0
        now = time.time()
        js.total_history.append((now, total_done))
        while len(js.total_history) > 2 and now - js.total_history[0][0] > RATE_WINDOW_S:
            js.total_history.popleft()
        h = js.total_history
        rps = None
        if len(h) >= 2 and h[-1][0] > h[0][0]:
            rps = round(max(0, h[-1][1] - h[0][1]) / (h[-1][0] - h[0][0]), 2)
        gpus = telemetry.gpu_sample() if js.telemetry.get("source") == "local-nvidia-smi" else []
        self._append_sample(js.job_id, {"ts": now_iso(), "gpus": gpus, "rows_per_s": rps, "errors_total": total_err})

    def _poll_labs(self) -> None:
        try:
            labs = {lab.lab_id: os.path.getmtime(lab.abspath) for lab in self.st.workspace.labs()}
        except Exception:  # noqa: BLE001
            return
        if self._lab_mtimes is not None:
            changed = [k for k in set(labs) | set(self._lab_mtimes) if labs.get(k) != self._lab_mtimes.get(k)]
            if changed:
                self.st.bus.publish("labs.changed", {"lab_ids": sorted(changed)})
        self._lab_mtimes = labs

    # ---- finish --------------------------------------------------------------------------------

    def on_finish(self, job: dict[str, Any]) -> dict[str, Any] | None:
        with self.lock:
            js = self.state(job)
            status = job["status"]
            ok = status == "succeeded"
            js.last_sample = 0
            if js.kind in ("run", "eval") and js.backend == "local":
                with contextlib.suppress(Exception):
                    self._sample(js, job)
            out: dict[str, Any] = {}
            result: dict[str, Any] | None = None
            failures = list(job.get("failures") or [])
            if js.kind == "run":
                root = js.ctx.get("root")
                lab_json = None
                if root and os.path.isfile(os.path.join(root, "lab.json")):
                    started = _parse_iso(job.get("started_at")) or 0
                    if os.path.getmtime(os.path.join(root, "lab.json")) >= started - 1:
                        with contextlib.suppress(OSError, ValueError):
                            with open(os.path.join(root, "lab.json"), encoding="utf-8") as fh:
                                lab_json = json.load(fh)
                lab_failures = [str(x) for x in (lab_json or {}).get("failures") or []]
                if lab_failures:
                    failures = lab_failures + [f for f in failures if f not in lab_failures and status == "lost"]
                scored = any(r["status"] == "done" for r in js.progress["runs"])
                if status == "failed" and lab_failures and scored:
                    status = "partial"
                    out["status"] = "partial"
                if root and os.path.isdir(root):
                    with contextlib.suppress(Exception):
                        js.root_id = self.st.workspace.encode_id(root)
                report = os.path.join(root, "REPORT.md") if root else None
                result = {"root_id": js.root_id,
                          "report_md": self.st.workspace.rel(report) if report and os.path.isfile(report) else None,
                          "failures": lab_failures}
                if js.root_id:
                    self.st.bus.publish("runs.changed", {"root_ids": [js.root_id]})
            elif js.kind == "eval":
                out_dir = js.ctx.get("out")
                if out_dir and os.path.isfile(os.path.join(out_dir, "scores.json")):
                    with contextlib.suppress(OSError, ValueError):
                        with open(os.path.join(out_dir, "scores.json"), encoding="utf-8") as fh:
                            sc = json.load(fh)
                        root = os.path.dirname(out_dir)
                        with contextlib.suppress(Exception):
                            js.root_id = self.st.workspace.encode_id(root)
                        result = {"run": {"root_id": js.root_id, "model": js.ctx.get("model_name"),
                                          "suite": os.path.basename(out_dir)},
                                  "scores": {k: sc.get(k) for k in ("n", "errors", "intelligence", "intelligence_ci95",
                                                                    "accuracy", "nll", "ece")}}
                        r = js.progress["runs"][0] if js.progress["runs"] else None
                        if r:
                            r.update(status="done", intelligence=sc.get("intelligence"),
                                     ci95=sc.get("intelligence_ci95"), accuracy=sc.get("accuracy"),
                                     errors=sc.get("errors") or 0)
                        if js.root_id:
                            self.st.bus.publish("runs.changed", {"root_ids": [js.root_id]})
            elif js.kind in ("pull", "calibrate", "suite_build", "jevbench"):
                data = self._last_json(job["job_id"])
                if js.kind == "pull" and data:
                    path = str(data.get("path") or "")
                    result = {"path": path, "model_key": _model_key(path),
                              "info": {k: v for k, v in data.items() if k != "path"}}
                    self.st.bus.publish("models.changed", {})
                elif js.kind == "calibrate" and data:
                    out_dir = js.ctx.get("out")
                    with contextlib.suppress(Exception):
                        js.root_id = self.st.workspace.encode_id(os.path.dirname(out_dir)) if out_dir else js.root_id
                    result = {"run": {"root_id": js.root_id, "model": js.ctx.get("model_name"),
                                      "suite": os.path.basename(out_dir) if out_dir else None}, **data}
                elif js.kind == "jevbench" and data:
                    result = {"out": js.ctx.get("out_rel"), "scores": data}
                elif js.kind == "suite_build" and data:
                    result = {"suite": js.ctx.get("suite"), "rows": data.get("n_rows") or data.get("rows"),
                              "sha256": data.get("sha256"), "stats": data}
            # close the stages
            final_ok = ok or status == "partial"
            for s in js.stages:
                if s["status"] == "active":
                    if final_ok:
                        js.set_stage(s["name"], "done")
                    else:
                        js.set_stage(s["name"], "failed", "cancelled" if status == "cancelled" else
                                     (failures[0] if failures else None), keep_detail=status != "failed")
                elif s["status"] == "pending":
                    if final_ok and js.kind != "run":
                        js.set_stage(s["name"], "done")
                    elif final_ok:
                        js.set_stage(s["name"], "skipped")
                    elif s["name"] == "release" and js.machine_id and status in ("cancelled", "failed", "lost"):
                        js.set_stage("release", "failed", "the machine may still be running: check Compute")
                    else:
                        js.set_stage(s["name"], "skipped")
            for r in js.progress["runs"]:
                if r["status"] == "running":
                    r["status"] = "failed" if not final_ok else "done"
                    if not final_ok:
                        r["message"] = r["message"] or ("cancelled" if status == "cancelled" else "stopped")
                    r["rows_per_s"] = None
                elif r["status"] == "pending":
                    r["status"] = "skipped"
                    r["message"] = r["message"] or ("not run" if final_ok else
                                                    ("cancelled" if status == "cancelled" else "not run"))
            for f in js.progress.get("finetune") or []:
                if f["status"] in ("pending", "running"):
                    f["status"] = "done" if final_ok else "failed"
            if js.kind == "pull" and ok:
                b = js.progress.get("bytes")
                if b:
                    b["total"] = b.get("total") or b["done"]
                js.progress["fraction"] = 1.0
            js.telemetry = dict(js.telemetry)
            out.update(js.updates())
            if js.kind != "pull" and not js.progress["runs"]:
                out["progress"]["fraction"] = 1.0 if final_ok else out["progress"].get("fraction")
            if failures != list(job.get("failures") or []):
                out["failures"] = failures
            if result is not None:
                out["result"] = result
            if js.root_id:
                out["root_id"] = js.root_id
            self.jobs.pop(job["job_id"], None)
            return out

    def _last_json(self, job_id: str) -> dict[str, Any] | None:
        """The last top-level JSON object the CLI printed (`_print`), from the job log."""
        try:
            with open(os.path.join(self.jm.dir, job_id, "log.txt"), encoding="utf-8", errors="replace") as fh:
                lines = [ln.rstrip("\n").partition("\t")[2] for ln in fh]
        except OSError:
            return None
        end = None
        for i in range(len(lines) - 1, -1, -1):
            if end is None and lines[i] == "}":
                end = i
            elif end is not None and lines[i] == "{":
                with contextlib.suppress(ValueError):
                    data = json.loads("\n".join(lines[i:end + 1]))
                    if isinstance(data, dict):
                        return data
                end = None
        return None


def _model_key(path: str) -> str | None:
    """The cache directory name under ~/.cache/decider-lab/models/ for a pulled path."""
    parts = os.path.normpath(path).split(os.sep)
    if "models" in parts:
        i = len(parts) - 1 - parts[::-1].index("models")
        if i + 1 < len(parts):
            return parts[i + 1]
    return None


_INSTALL_LOCK = threading.Lock()


def get_tracker(st: StudioState) -> Tracker:
    """The server's tracker, created (and its handlers installed) on first use."""
    t = st.extra.get("jobs_tracker")
    if t is None:
        with _INSTALL_LOCK:
            t = st.extra.get("jobs_tracker")
            if t is None:
                t = Tracker(st)
                st.extra["jobs_tracker"] = t
    return t


def read_telemetry(path: str, since: str | None = None, limit: int = 900) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                with contextlib.suppress(ValueError):
                    s = json.loads(line)
                    if isinstance(s, dict):
                        out.append(s)
    except OSError:
        return []
    if since:
        t = _parse_iso(since)
        if t is not None:
            out = [s for s in out if (_parse_iso(s.get("ts")) or 0) > t]
    return out[-max(1, limit):]


__all__ = ["ACTIVE", "TERMINAL", "Tracker", "get_tracker", "iso", "read_telemetry"]
