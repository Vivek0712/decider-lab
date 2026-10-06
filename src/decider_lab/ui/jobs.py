"""Jobs: `decider-lab` subprocesses that Studio starts, tracks, streams and cancels (API.md section 6).

    jm = JobManager(state_dir, redactor, max_concurrent=2)
    job = jm.start("run", JobManager.cli_argv("run", "labs/first/lab.yaml"), title="run first-lab",
                   cwd="/ws/labs/first", env={"PYTHONUNBUFFERED": "1"}, env_names=["HF_TOKEN"])
    jm.get(job["job_id"])            -> Job (API shape)
    jm.read_log(job_id, offset=0)    -> {"lines": [...], "next_offset", "total", "truncated"}
    jm.cancel(job_id)                -> "cancelling" | "cancelled"
    StreamingResponse(job_event_stream(jm, job_id, ...))   SSE, see `sse_response`

Each job lives in <state_dir>/jobs/<job_id>/: job.json (the record), spawn.json (argv, cwd),
log.txt (`<ISO ts>\\t<redacted text>` per line), child.json (the command's pid/pgid) and exit.json.
The command runs under `decider_lab.ui.jobrunner`, a detached supervisor, so jobs survive a server
restart and the next server reattaches to them by pid; a job whose supervisor is gone without an
exit.json becomes `lost`.

Cancel sends SIGINT to the command's process group (decider-lab releases remote machines on SIGINT
and SIGTERM), then SIGTERM after `cancel_grace_s`, then SIGKILL after `kill_after_s`.

Area teams extend behaviour with hooks instead of editing this file:
    jm.add_line_handler(fn(job: dict, line: dict) -> dict | None)   parse a log line; return field
                                                                     updates (stages, progress, ...)
    jm.add_finish_handler(fn(job: dict) -> dict | None)              adjust the final record
                                                                     (e.g. status "partial", result)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterable
from datetime import datetime
from typing import Any

from .errors import ApiError
from .jobrunner import REDACT_ENV
from .redact import Redactor
from .workspace import now_iso

ACTIVE = ("queued", "running", "cancelling")
TERMINAL = ("succeeded", "partial", "failed", "cancelled", "lost")
JOB_ID = re.compile(r"^j_[0-9a-f]{12}$")

STAGES: dict[str, list[tuple[str, str]]] = {
    "run/local": [("prepare", "Prepare"), ("run", "Run"), ("report", "Report")],
    "run/remote": [("check", "Check"), ("acquire", "Acquire machine"), ("copy", "Copy lab"),
                   ("bootstrap", "Bootstrap"), ("run", "Run"), ("fetch", "Fetch results"),
                   ("release", "Release machine")],
    "eval": [("prepare", "Prepare"), ("run", "Run")],
    "pull": [("resolve", "Resolve"), ("download", "Download"), ("verify", "Verify"), ("extract", "Extract"),
             ("done", "Done")],
    "jevbench": [("harness", "Harness"), ("run", "Run"), ("score", "Score")],
    "calibrate": [("fit", "Fit"), ("score", "Score")],
    "suite_build": [("build", "Build")],
}

ERROR_LINE = re.compile(r"FAILED|Traceback|Error|failed|WARNING: instance")
WARN_LINE = re.compile(r"WARNING|WARN|too few|⚠")

LineHandler = Callable[[dict[str, Any], dict[str, Any]], "dict[str, Any] | None"]
FinishHandler = Callable[[dict[str, Any]], "dict[str, Any] | None"]


def default_stages(kind: str, backend: str = "local") -> list[dict[str, Any]]:
    key = f"run/{'local' if backend == 'local' else 'remote'}" if kind == "run" else kind
    return [{"name": n, "label": label, "status": "pending", "started_at": None, "ended_at": None, "detail": None}
            for n, label in STAGES.get(key, [])]


def default_progress() -> dict[str, Any]:
    return {"fraction": None, "label": "", "runs": [], "bytes": None, "finetune": None, "machine": None}


def line_level(text: str) -> str:
    if ERROR_LINE.search(text):
        return "error"
    if WARN_LINE.search(text):
        return "warn"
    return "info"


def parse_log_line(seq: int, raw: str) -> dict[str, Any]:
    ts, sep, text = raw.rstrip("\n").partition("\t")
    if not sep:
        ts, text = "", raw.rstrip("\n")
    return {"seq": seq, "ts": ts, "text": text, "level": line_level(text)}


def _parse_iso(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:  # a zombie child of this process counts as gone
        done, _ = os.waitpid(pid, os.WNOHANG)
        return done == 0
    except ChildProcessError:
        return True
    except OSError:
        return True


def _is_our_runner(pid: int | None, job_dir: str) -> bool:
    """True if pid is alive and is the jobrunner for job_dir (guards against pid reuse)."""
    if not _pid_alive(pid):
        return False
    proc = f"/proc/{pid}"
    if os.path.isdir(proc):  # Linux: no dependency on ps (slim containers have none)
        try:
            with open(f"{proc}/stat", encoding="utf-8") as fh:
                if fh.read().rsplit(")", 1)[-1].split()[0] == "Z":
                    return False  # a zombie has finished; its exit.json (written first) says how
            with open(f"{proc}/cmdline", "rb") as fh:
                out = fh.read().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError:
            return True  # cannot tell; assume it is ours rather than declaring a live job lost
    else:
        try:
            out = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True,
                                 timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            return True
    return "decider_lab.ui.jobrunner" in out and os.path.basename(job_dir) in out


def _write_json(path: str, data: dict[str, Any]) -> None:
    tmp = f"{path}.tmp{os.getpid()}.{threading.get_ident()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
    os.replace(tmp, path)


def _read_json(path: str) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _order(rec: dict[str, Any]) -> tuple[str, int]:
    return rec["created_at"], int((rec.get("_internal") or {}).get("created_ns") or 0)


class EventBus:
    """Thread-safe fan-out of global events (`GET /api/events`) to asyncio subscribers."""

    def __init__(self) -> None:
        self._subs: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self._lock = threading.Lock()

    def publish(self, event: str, data: Any) -> None:
        with self._lock:
            subs = list(self._subs)
        for loop, q in subs:
            with contextlib.suppress(RuntimeError):  # loop closed
                loop.call_soon_threadsafe(q.put_nowait, (event, data))

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue]:
        q: asyncio.Queue = asyncio.Queue()
        entry = (asyncio.get_running_loop(), q)
        with self._lock:
            self._subs.append(entry)
        try:
            yield q
        finally:
            with self._lock:
                self._subs.remove(entry)


class JobManager:
    def __init__(self, state_dir: str, redactor: Redactor | None = None, *, max_concurrent: int = 2,
                 bus: EventBus | None = None, cancel_grace_s: float = 30.0, kill_after_s: float = 60.0,
                 poll_s: float = 0.2, redact_extra: Iterable[str] = (), start_monitor: bool = True) -> None:
        self.dir = os.path.join(state_dir, "jobs")
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        self.redact = redactor or Redactor(extra=redact_extra)
        self._redact_extra = [v for v in redact_extra if v]
        self.max_concurrent = max_concurrent
        self.bus = bus or EventBus()
        self.cancel_grace_s = cancel_grace_s
        self.kill_after_s = kill_after_s
        self.poll_s = poll_s
        self._jobs: dict[str, dict[str, Any]] = {}
        self._popen: dict[str, subprocess.Popen] = {}  # runners started by this server (not persisted)
        self._lock = threading.RLock()
        self._line_handlers: list[LineHandler] = []
        self._finish_handlers: list[FinishHandler] = []
        self._stop = threading.Event()
        self._changed = threading.Condition(self._lock)
        self._load()
        self._thread: threading.Thread | None = None
        if start_monitor:
            self._thread = threading.Thread(target=self._monitor, name="studio-jobs", daemon=True)
            self._thread.start()

    # ---- argv helpers --------------------------------------------------------------------------

    @staticmethod
    def cli_argv(*args: str) -> list[str]:
        """argv that runs the decider-lab CLI with this interpreter (works without the console script)."""
        return [sys.executable, "-m", "decider_lab", *[str(a) for a in args]]

    @staticmethod
    def display_argv(argv: list[str]) -> list[str]:
        if len(argv) >= 3 and argv[1:3] == ["-m", "decider_lab"]:
            return ["decider-lab", *argv[3:]]
        return list(argv)

    # ---- hooks ---------------------------------------------------------------------------------

    def add_line_handler(self, fn: LineHandler) -> None:
        self._line_handlers.append(fn)

    def add_finish_handler(self, fn: FinishHandler) -> None:
        self._finish_handlers.append(fn)

    # ---- records -------------------------------------------------------------------------------

    def job_dir(self, job_id: str) -> str:
        if not JOB_ID.match(job_id or ""):
            raise ApiError(404, "not_found", "No such job.", detail={"what": "job"})
        return os.path.join(self.dir, job_id)

    def log_path(self, job_id: str) -> str:
        return os.path.join(self.job_dir(job_id), "log.txt")

    def _load(self) -> None:
        for name in sorted(os.listdir(self.dir)):
            rec = _read_json(os.path.join(self.dir, name, "job.json"))
            if not rec or not JOB_ID.match(rec.get("job_id", "")):
                continue
            self._jobs[rec["job_id"]] = rec
            jd = os.path.join(self.dir, name)
            if rec["status"] in ("running", "cancelling"):
                internal = rec.setdefault("_internal", {})
                if os.path.exists(os.path.join(jd, "exit.json")):
                    continue  # finished while no server watched; the monitor finalizes it
                if _is_our_runner(internal.get("runner_pid"), jd):
                    internal["reattached"] = True
                    continue
                rec["status"] = "lost"
                rec["ended_at"] = rec.get("ended_at") or now_iso()
                msg = ("Studio stopped while this job ran; a remote machine may still be running"
                       if rec.get("backend") in ("ssh", "aws", "vast") else
                       "Studio stopped while this job ran; its process is gone")
                rec.setdefault("failures", []).append(msg)
                self._persist(rec)

    def _persist(self, rec: dict[str, Any]) -> None:
        _write_json(os.path.join(self.dir, rec["job_id"], "job.json"), rec)

    def _public(self, rec: dict[str, Any]) -> dict[str, Any]:
        out = {k: v for k, v in rec.items() if not k.startswith("_")}
        start = _parse_iso(rec.get("started_at"))
        end = _parse_iso(rec.get("ended_at"))
        out["duration_s"] = round((end or time.time()) - start, 1) if start else None
        out["failure_count"] = len(rec.get("failures") or [])
        out["queue_position"] = self._queue_position(rec["job_id"]) if rec["status"] == "queued" else None
        prog = rec.get("progress") or default_progress()
        machine = prog.get("machine") or {}
        out["cost_so_far_usd"] = machine.get("cost_so_far_usd")
        return out

    def _summary(self, rec: dict[str, Any]) -> dict[str, Any]:
        pub = self._public(rec)
        keys = ("job_id", "kind", "title", "status", "backend", "lab_id", "created_at", "started_at", "ended_at",
                "duration_s", "failure_count", "root_id", "queue_position", "cost_so_far_usd", "machine_id")
        s = {k: pub.get(k) for k in keys}
        prog = rec.get("progress") or {}
        s["progress"] = {"fraction": prog.get("fraction"), "label": prog.get("label", "")}
        return s

    def _queue_position(self, job_id: str) -> int | None:
        queued = sorted((r for r in self._jobs.values() if r["status"] == "queued"), key=_order)
        for i, r in enumerate(queued):
            if r["job_id"] == job_id:
                return i + 1
        return None

    def _record(self, job_id: str) -> dict[str, Any]:
        rec = self._jobs.get(job_id)
        if rec is None:
            raise ApiError(404, "not_found", "No such job.", detail={"what": "job"})
        return rec

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            return self._public(self._record(job_id))

    def summary(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            return self._summary(self._record(job_id))

    def list(self, *, status: str | None = None, kind: str | None = None, lab_id: str | None = None,
             q: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            recs = sorted(self._jobs.values(), key=_order, reverse=True)
            if status == "active":
                recs = [r for r in recs if r["status"] in ACTIVE]
            elif status == "finished":
                recs = [r for r in recs if r["status"] in TERMINAL]
            elif status:
                recs = [r for r in recs if r["status"] == status]
            if kind:
                recs = [r for r in recs if r["kind"] == kind]
            if lab_id:
                recs = [r for r in recs if r.get("lab_id") == lab_id]
            if q:
                ql = q.lower()
                recs = [r for r in recs if ql in r["title"].lower() or ql in r.get("command", "").lower()]
            return [self._summary(r) for r in recs[:limit]]

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for r in self._jobs.values() if r["status"] in ACTIVE)

    def update(self, job_id: str, **fields: Any) -> dict[str, Any]:
        """Merge fields into a job record (redacted), persist it and publish `job.updated`."""
        with self._lock:
            rec = self._record(job_id)
            rec.update(self.redact.obj(fields))
            self._persist(rec)
            summary = self._summary(rec)
            self._changed.notify_all()
        self.bus.publish("job.updated", summary)
        return summary

    # ---- lifecycle -----------------------------------------------------------------------------

    def start(self, kind: str, argv: list[str], *, title: str, cwd: str, env: dict[str, str] | None = None,
              env_names: Iterable[str] = (), backend: str = "local", lab_id: str | None = None,
              root_id: str | None = None, options: dict[str, Any] | None = None,
              stages: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Create a job and start it now, or queue it when `max_concurrent` jobs are running.

        `argv` is a list (never a shell string); `env` adds variables to the server's environment
        for this job (PYTHONUNBUFFERED=1 is always added); `env_names` are the names shown to the
        person (never values).
        """
        if not argv or not all(isinstance(a, str) for a in argv):
            raise ValueError("argv must be a non-empty list of strings")
        if not os.path.isdir(cwd):
            raise ApiError(422, "bad_request", "The working directory does not exist.", detail={"cwd": cwd})
        job_id = "j_" + secrets.token_hex(6)
        jd = os.path.join(self.dir, job_id)
        os.makedirs(jd, mode=0o700)
        # paths inside the working directory are shown relative to it: shorter, and the same command
        # works for anyone with the same workspace
        base = os.path.realpath(cwd) + os.sep
        shown = [os.path.relpath(a, cwd) if os.path.isabs(a) and os.path.realpath(a).startswith(base) else a
                 for a in self.display_argv(argv)]
        rec: dict[str, Any] = {
            "job_id": job_id, "kind": kind, "title": title, "status": "queued", "backend": backend,
            "lab_id": lab_id, "created_at": now_iso(), "started_at": None, "ended_at": None,
            "root_id": root_id, "machine_id": None, "progress": default_progress(),
            "argv": shown, "command": shlex.join(shown), "cwd": cwd, "env_names": sorted(set(env_names)),
            "exit_code": None, "stages": stages if stages is not None else default_stages(kind, backend),
            "failures": [], "result": None, "log": {"lines": 0, "bytes": 0},
            "telemetry": {"source": "none", "reason": "no telemetry sampler for this job"},
            "options": options or {},
            "_internal": {"env": dict(env or {}), "log_offset": 0, "created_ns": time.time_ns()},
        }
        rec = self.redact.obj(rec) | {"_internal": rec["_internal"]}
        _write_json(os.path.join(jd, "spawn.json"), {"argv": list(argv), "cwd": cwd})
        open(os.path.join(jd, "log.txt"), "a").close()
        with self._lock:
            self._jobs[job_id] = rec
            self._persist(rec)
            summary = self._summary(rec)
        self.bus.publish("job.created", summary)
        self._start_queued()
        return self.get(job_id)

    def _start_queued(self) -> None:
        started = []
        with self._lock:
            running = sum(1 for r in self._jobs.values() if r["status"] in ("running", "cancelling"))
            queued = sorted((r for r in self._jobs.values() if r["status"] == "queued"), key=_order)
            for rec in queued:
                if running >= self.max_concurrent:
                    break
                self._spawn(rec)
                running += 1
                started.append(self._summary(rec))
            if started:
                self._changed.notify_all()
        for s in started:
            self.bus.publish("job.updated", s)

    def _spawn(self, rec: dict[str, Any]) -> None:
        jd = os.path.join(self.dir, rec["job_id"])
        internal = rec["_internal"]
        env = dict(os.environ)
        env.update(internal.get("env") or {})
        env["PYTHONUNBUFFERED"] = "1"
        env[REDACT_ENV] = "\n".join(dict.fromkeys([*self._redact_extra, *self.redact.values]))
        err = open(os.path.join(jd, "runner.err"), "ab")  # noqa: SIM115 - handed to the child
        try:
            p = subprocess.Popen([sys.executable, "-m", "decider_lab.ui.jobrunner", jd], cwd=rec["cwd"], env=env,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err,
                                 start_new_session=True)
        finally:
            err.close()
        internal["runner_pid"] = p.pid
        self._popen[rec["job_id"]] = p
        rec["status"] = "running"
        rec["started_at"] = now_iso()
        self._persist(rec)

    def _child(self, rec: dict[str, Any]) -> dict[str, Any] | None:
        return _read_json(os.path.join(self.dir, rec["job_id"], "child.json"))

    def _signal(self, rec: dict[str, Any], sig: int) -> bool:
        child = self._child(rec)
        if not child:
            return False
        try:
            os.killpg(int(child["pgid"]), sig)
            return True
        except (ProcessLookupError, PermissionError, OSError):
            return False

    def cancel(self, job_id: str) -> str:
        with self._lock:
            rec = self._record(job_id)
            if rec["status"] == "queued":
                rec["status"] = "cancelled"
                rec["ended_at"] = now_iso()
                self._persist(rec)
                summary = self._summary(rec)
                self._changed.notify_all()
                result = "cancelled"
            elif rec["status"] in ("running", "cancelling"):
                internal = rec["_internal"]
                if rec["status"] == "running":
                    rec["status"] = "cancelling"
                    internal["cancel_at"] = time.time()
                    internal["signals"] = ["SIGINT"]
                    if not self._signal(rec, signal.SIGINT):
                        internal["signal_pending"] = True  # the child has not written child.json yet
                    self._persist(rec)
                summary = self._summary(rec)
                self._changed.notify_all()
                result = "cancelling"
            else:
                raise ApiError(409, "job_not_active", "This job is not running.")
        self.bus.publish("job.updated" if result == "cancelling" else "job.finished", summary)
        return result

    def delete(self, job_id: str) -> None:
        with self._lock:
            rec = self._record(job_id)
            if rec["status"] in ACTIVE:
                raise ApiError(409, "job_active", "This job is still active.", detail={"job_id": job_id})
            del self._jobs[job_id]
        shutil.rmtree(os.path.join(self.dir, job_id), ignore_errors=True)

    def wait(self, job_id: str, timeout: float = 60.0) -> dict[str, Any]:
        """Block until the job is terminal (tests and scripts)."""
        deadline = time.time() + timeout
        with self._lock:
            while self._record(job_id)["status"] not in TERMINAL:
                left = deadline - time.time()
                if left <= 0:
                    raise TimeoutError(f"job {job_id} still {self._record(job_id)['status']}")
                self._changed.wait(min(left, 0.5))
            return self._public(self._record(job_id))

    def shutdown(self, *, cancel_active: bool = True, wait_s: float = 60.0) -> list[str]:
        """Stop the monitor; with cancel_active, cancel active jobs and wait for them. Returns ids still active."""
        if cancel_active:
            for s in self.list(status="active"):
                with contextlib.suppress(ApiError):
                    self.cancel(s["job_id"])
            deadline = time.time() + wait_s
            while self.active_count() and time.time() < deadline:
                if self._thread is None:
                    self._tick()
                time.sleep(self.poll_s)
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        return [s["job_id"] for s in self.list(status="active")]

    # ---- the monitor ---------------------------------------------------------------------------

    def _monitor(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:  # never let the monitor die
                print(f"[studio] job monitor: {type(e).__name__}: {self.redact(str(e))}", file=sys.stderr)
            self._stop.wait(self.poll_s)

    def _tick(self) -> None:
        with self._lock:
            active = [r for r in self._jobs.values() if r["status"] in ("running", "cancelling")]
        for rec in active:
            self._tail(rec)
            self._check_exit(rec)
        self._start_queued()

    def _tail(self, rec: dict[str, Any]) -> None:
        path = os.path.join(self.dir, rec["job_id"], "log.txt")
        internal = rec["_internal"]
        offset = int(internal.get("log_offset") or 0)
        try:
            size = os.path.getsize(path)
        except OSError:
            return
        if size <= offset:
            return
        with open(path, "rb") as fh:
            fh.seek(offset)
            data = fh.read(size - offset)
        end = data.rfind(b"\n")
        if end < 0:
            return
        data = data[: end + 1]
        lines = data.decode("utf-8", errors="replace").splitlines()
        updates: dict[str, Any] = {}
        with self._lock:
            seq0 = rec["log"]["lines"]
            snapshot = self._public(rec)
        for i, raw in enumerate(lines):
            line = parse_log_line(seq0 + i, raw)
            for fn in self._line_handlers:
                try:
                    out = fn(snapshot | updates, line)
                except Exception as e:  # a parser bug must not stop the job
                    print(f"[studio] line handler: {type(e).__name__}: {e}", file=sys.stderr)
                    out = None
                if out:
                    updates.update(out)
        with self._lock:
            internal["log_offset"] = offset + len(data)
            rec["log"] = {"lines": seq0 + len(lines), "bytes": offset + len(data)}
            if updates:
                rec.update(self.redact.obj(updates))
            self._persist(rec)
            summary = self._summary(rec)
            self._changed.notify_all()
        if updates:
            self.bus.publish("job.updated", summary)

    def _check_exit(self, rec: dict[str, Any]) -> None:
        jd = os.path.join(self.dir, rec["job_id"])
        internal = rec["_internal"]
        if rec["status"] == "cancelling":
            elapsed = time.time() - float(internal.get("cancel_at") or time.time())
            sent = internal.setdefault("signals", [])
            if internal.pop("signal_pending", False) and not self._signal(rec, signal.SIGINT):
                internal["signal_pending"] = True
            if elapsed >= self.kill_after_s and "SIGKILL" not in sent:
                self._signal(rec, signal.SIGKILL)
                sent.append("SIGKILL")
                if rec.get("backend") in ("ssh", "aws", "vast"):
                    rec.setdefault("failures", []).append("the machine may still be running: check Compute")
            elif elapsed >= self.cancel_grace_s and "SIGTERM" not in sent:
                self._signal(rec, signal.SIGTERM)
                sent.append("SIGTERM")
        ex = _read_json(os.path.join(jd, "exit.json"))
        if ex is None:
            popen = self._popen.get(rec["job_id"])
            alive = popen.poll() is None if popen is not None else _pid_alive(internal.get("runner_pid"))
            if alive:
                return
            time.sleep(0.05)  # the runner may be writing exit.json right now
            ex = _read_json(os.path.join(jd, "exit.json"))
            if ex is None:
                ex = {"exit_code": None, "ended_at": now_iso(), "error": "the job runner stopped unexpectedly"}
        popen = self._popen.pop(rec["job_id"], None)
        if popen is not None:
            with contextlib.suppress(Exception):
                popen.wait(timeout=1)
        self._tail(rec)
        self._finalize(rec, ex)

    def _finalize(self, rec: dict[str, Any], ex: dict[str, Any]) -> None:
        code = ex.get("exit_code")
        with self._lock:
            rec["exit_code"] = code
            rec["ended_at"] = ex.get("ended_at") or now_iso()
            if rec["status"] == "cancelling":
                rec["status"] = "cancelled"
            elif code == 0:
                rec["status"] = "succeeded"
            else:
                rec["status"] = "failed"
                if not rec.get("failures"):
                    rec["failures"] = [self._last_error_line(rec) or ex.get("error") or f"exit code {code}"]
            snapshot = self._public(rec)
        for fn in self._finish_handlers:
            try:
                out = fn(snapshot)
            except Exception as e:
                print(f"[studio] finish handler: {type(e).__name__}: {e}", file=sys.stderr)
                out = None
            if out:
                snapshot.update(out)
                with self._lock:
                    rec.update(self.redact.obj(out))
        with self._lock:
            self._persist(rec)
            summary = self._summary(rec)
            self._changed.notify_all()
        self.bus.publish("job.finished", summary)

    def _last_error_line(self, rec: dict[str, Any]) -> str | None:
        try:
            with open(os.path.join(self.dir, rec["job_id"], "log.txt"), encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()[-200:]
        except OSError:
            return None
        for raw in reversed(lines):
            text = parse_log_line(0, raw)["text"].strip()
            if text and line_level(text) == "error":
                return text[:500]
        for raw in reversed(lines):
            text = parse_log_line(0, raw)["text"].strip()
            if text:
                return text[:500]
        return None

    # ---- logs ----------------------------------------------------------------------------------

    def read_log(self, job_id: str, *, offset: int = 0, limit: int = 5000, q: str | None = None,
                 level: str = "all") -> dict[str, Any]:
        """API.md `GET /api/jobs/:id/log`: lines from `offset`, optionally filtered (on redacted text)."""
        self.summary(job_id)  # 404 for unknown ids
        limit = max(1, min(int(limit), 20000))
        offset = max(0, int(offset))
        try:
            with open(self.log_path(job_id), encoding="utf-8", errors="replace") as fh:
                raw = fh.read().splitlines()
        except OSError:
            raw = []
        total = len(raw)
        out: list[dict[str, Any]] = []
        ql = q.lower() if q else None
        i = offset
        while i < total and len(out) < limit:
            line = parse_log_line(i, raw[i])
            i += 1
            if ql and ql not in line["text"].lower():
                continue
            if level == "error" and line["level"] != "error":
                continue
            if level == "warn" and line["level"] not in ("warn", "error"):
                continue
            out.append(line)
        return {"lines": out, "next_offset": i, "total": total, "truncated": i < total}

    def log_text(self, job_id: str) -> str:
        """The redacted log as plain text (no timestamps), for downloads."""
        self.summary(job_id)
        try:
            with open(self.log_path(job_id), encoding="utf-8", errors="replace") as fh:
                return "".join(parse_log_line(0, x)["text"] + "\n" for x in fh)
        except OSError:
            return ""


# ---- server-sent events ------------------------------------------------------------------------


def format_sse(event: str, data: Any, event_id: str | int | None = None) -> str:
    out = f"event: {event}\n"
    if event_id is not None:
        out += f"id: {event_id}\n"
    payload = json.dumps(data, separators=(",", ":"))
    return out + "".join(f"data: {chunk}\n" for chunk in payload.split("\n")) + "\n"


async def job_event_stream(jm: JobManager, job_id: str, *, last_event_id: str | None = None,
                           from_start: bool = False, is_disconnected: Callable[[], Any] | None = None,
                           poll_s: float = 0.25, ping_s: float = 15.0) -> AsyncIterator[str]:
    """SSE for one job (API.md `GET /api/jobs/:id/events`): snapshot, log, stage, progress, status, end.

    Follows log.txt from the line after `last_event_id` (numeric log ids), from line 0 with
    `from_start`, else from the current end. Ends after a terminal status.
    """
    job = jm.get(job_id)
    yield format_sse("snapshot", {"job": job}, "s-0")
    if last_event_id is not None and str(last_event_id).isdigit():
        seq = int(last_event_id) + 1
    elif from_start:
        seq = 0
    else:
        seq = int(job["log"]["lines"])
    path = jm.log_path(job_id)
    offset = 0
    skipped = 0
    with contextlib.suppress(OSError), open(path, "rb") as fh:  # byte offset of line `seq`
        while skipped < seq:
            line = fh.readline()
            if not line or not line.endswith(b"\n"):
                break
            offset += len(line)
            skipped += 1
    seq = skipped
    last_status, last_stages, last_progress = job["status"], job["stages"], job["progress"]
    counters = {"st": 0, "p": 0, "x": 0}
    last_ping = time.monotonic()
    while True:
        if is_disconnected is not None:
            res = is_disconnected()
            if asyncio.iscoroutine(res):
                res = await res
            if res:
                return
        try:
            with open(path, "rb") as fh:
                fh.seek(offset)
                data = fh.read()
        except OSError:
            data = b""
        end = data.rfind(b"\n")
        got = end >= 0
        if got:
            chunk = data[: end + 1]
            offset += len(chunk)
            for raw in chunk.decode("utf-8", errors="replace").splitlines():
                line = parse_log_line(seq, raw)
                yield format_sse("log", line, seq)
                seq += 1
        try:
            job = jm.get(job_id)
        except ApiError:
            yield format_sse("end", {}, "e-0")
            return
        if job["stages"] != last_stages:
            old = {s["name"]: s for s in last_stages}
            for st in job["stages"]:
                if old.get(st["name"]) != st:
                    counters["st"] += 1
                    yield format_sse("stage", {"stage": st}, f"st-{counters['st']}")
            last_stages = job["stages"]
        if job["progress"] != last_progress:
            counters["p"] += 1
            yield format_sse("progress", {"progress": job["progress"]}, f"p-{counters['p']}")
            last_progress = job["progress"]
        if job["status"] != last_status or job["status"] in TERMINAL:
            if job["status"] in TERMINAL and got:
                await asyncio.sleep(0)  # drain the remaining lines before the final status
                continue
            counters["x"] += 1
            yield format_sse("status", {"status": job["status"], "exit_code": job["exit_code"],
                                        "ended_at": job["ended_at"], "result": job["result"]}, f"x-{counters['x']}")
            last_status = job["status"]
            if job["status"] in TERMINAL:
                yield format_sse("end", {}, "e-0")
                return
        if time.monotonic() - last_ping >= ping_s:
            last_ping = time.monotonic()
            yield ": ping\n\n"
        await asyncio.sleep(poll_s)


async def bus_event_stream(bus: EventBus, *, is_disconnected: Callable[[], Any] | None = None,
                           ping_s: float = 15.0) -> AsyncIterator[str]:
    """SSE for `GET /api/events`: every event published on the bus from now on (no replay)."""
    async with bus.subscribe() as q:
        yield ": connected\n\n"
        last_ping = time.monotonic()
        while True:
            try:
                event, data = await asyncio.wait_for(q.get(), timeout=1.0)
                yield format_sse(event, data)
            except asyncio.TimeoutError:
                pass
            if is_disconnected is not None:
                res = is_disconnected()
                if asyncio.iscoroutine(res):
                    res = await res
                if res:
                    return
            if time.monotonic() - last_ping >= ping_s:
                last_ping = time.monotonic()
                yield ": ping\n\n"


def sse_response(stream: AsyncIterator[str]):  # noqa: ANN201 - starlette type
    """Wrap an SSE generator in a StreamingResponse with the right headers."""
    from fastapi.responses import StreamingResponse

    async def body() -> AsyncIterator[bytes]:
        async for chunk in stream:
            if chunk:
                yield chunk.encode("utf-8")

    return StreamingResponse(body(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})
