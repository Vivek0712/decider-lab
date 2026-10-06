"""Telemetry while a lab runs: GPU, load and rows done, sampled every 10 s.

    with telemetry.sampling(run_root):      # lab.run_lab does this for you
        ...                                 # every model on every suite

A daemon thread appends one JSON object per sample to `<run root>/telemetry.jsonl` and prints it
as a `[telemetry] {...}` line, so a remote run's samples reach the local log with the rest of the
output (Studio parses those lines). A sample:

    {"ts": "2026-10-06T10:52:00Z", "interval_s": 10,
     "gpus": [{"index": 0, "name": "NVIDIA L40S", "util_pct": 87.0, "mem_used_gb": 31.2,
               "mem_total_gb": 45.0, "temp_c": 64.0, "power_w": 241.5, "power_limit_w": 350.0}],
     "load": [3.1, 2.8, 2.2],
     "rows": {"v19/smoke": {"done": 90, "errors": 0}, "v19/synthetic": {"done": 410, "errors": 2}}}

GPUs come from `nvidia-smi` when it is installed (an empty list otherwise: Apple MPS and CPU are
not sampled). Rows are the lines of each `<model>/<suite>/predictions.jsonl` under the run root.

Telemetry never fails a run: every error inside the sampler is swallowed, the thread stops when
the block exits (or the process does), and `DECIDER_LAB_TELEMETRY=0` turns it off.
`DECIDER_LAB_TELEMETRY_INTERVAL` changes the period (seconds); `DECIDER_LAB_NVIDIA_SMI` names
the nvidia-smi executable to use.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

DEFAULT_INTERVAL_S = 10.0
QUERY = "index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,power.limit"
PREFIX = "[telemetry] "


def _num(value: str) -> float | None:
    try:
        return float(value.strip())
    except (TypeError, ValueError):
        return None  # "[N/A]", "[Not Supported]", ""


def nvidia_smi_path() -> str | None:
    override = os.environ.get("DECIDER_LAB_NVIDIA_SMI")
    if override:
        return override if os.path.exists(override) else shutil.which(override)
    return shutil.which("nvidia-smi")


def gpu_sample(nvidia_smi: str | None = None, timeout: float = 5.0) -> list[dict[str, Any]]:
    """One reading per GPU from nvidia-smi; [] when there is none or it fails."""
    exe = nvidia_smi or nvidia_smi_path()
    if not exe:
        return []
    try:
        out = subprocess.run([exe, f"--query-gpu={QUERY}", "--format=csv,noheader,nounits"], capture_output=True,
                             text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    gpus = []
    for line in out.stdout.strip().splitlines():
        f = [x.strip() for x in line.split(",")]
        if len(f) < 8:
            continue
        idx = _num(f[0])
        used, total = _num(f[3]), _num(f[4])
        gpus.append({"index": int(idx) if idx is not None else len(gpus), "name": f[1],
                     "util_pct": _num(f[2]),
                     "mem_used_gb": round(used / 1024, 2) if used is not None else None,
                     "mem_total_gb": round(total / 1024, 2) if total is not None else None,
                     "temp_c": _num(f[5]), "power_w": _num(f[6]), "power_limit_w": _num(f[7])})
    return gpus


def rows_done(root: str) -> dict[str, dict[str, int]]:
    """{"<model>/<suite>": {"done": n, "errors": e}} from the predictions files under a run root."""
    out: dict[str, dict[str, int]] = {}
    try:
        models = sorted(os.listdir(root))
    except OSError:
        return out
    for m in models:
        md = os.path.join(root, m)
        if m.startswith((".", "_")) or not os.path.isdir(md):
            continue
        try:
            suites = sorted(os.listdir(md))
        except OSError:
            continue
        for s in suites:
            p = os.path.join(md, s, "predictions.jsonl")
            if not os.path.isfile(p):
                continue
            done = errors = 0
            try:
                with open(p, encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        if not line.strip():
                            continue
                        done += 1
                        if '"error": null' not in line and '"error":null' not in line:
                            errors += 1
            except OSError:
                continue
            out[f"{m}/{s}"] = {"done": done, "errors": errors}
    return out


def load_average() -> list[float] | None:
    try:
        return [round(x, 2) for x in os.getloadavg()]
    except (OSError, AttributeError):
        return None


def sample(root: str, interval_s: float = DEFAULT_INTERVAL_S, nvidia_smi: str | None = None) -> dict[str, Any]:
    return {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "interval_s": interval_s,
            "gpus": gpu_sample(nvidia_smi), "load": load_average(), "rows": rows_done(root)}


class Sampler:
    """The sampling thread. `start()`, then `stop()` (idempotent; waits for the thread)."""

    def __init__(self, root: str, interval_s: float = DEFAULT_INTERVAL_S, *, nvidia_smi: str | None = None,
                 emit: Callable[[str], None] | None = None) -> None:
        self.root = root
        self.interval_s = max(0.05, float(interval_s))
        self.nvidia_smi = nvidia_smi
        self.emit = emit or (lambda line: print(line, flush=True))
        self.path = os.path.join(root, "telemetry.jsonl")
        self.samples = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def tick(self) -> dict[str, Any] | None:
        """Take one sample, append it and print it; None (and nothing raised) on any failure."""
        try:
            s = sample(self.root, self.interval_s, self.nvidia_smi)
            text = json.dumps(s, separators=(",", ":"))
            os.makedirs(self.root, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(text + "\n")
            self.emit(PREFIX + text)
            self.samples += 1
            return s
        except Exception:  # noqa: BLE001 - telemetry must never fail a run
            return None

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            self.tick()

    def start(self) -> Sampler:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="decider-lab-telemetry", daemon=True)
            self._thread.start()
        return self

    def stop(self, timeout: float = 15.0) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout)


def enabled(environ: dict[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return env.get("DECIDER_LAB_TELEMETRY", "1").strip().lower() not in ("0", "false", "no", "off")


def interval_from_env(default: float = DEFAULT_INTERVAL_S) -> float:
    try:
        return max(0.05, float(os.environ.get("DECIDER_LAB_TELEMETRY_INTERVAL", default)))
    except ValueError:
        return default


@contextlib.contextmanager
def sampling(root: str, interval_s: float | None = None, **kw: Any) -> Iterator[Sampler | None]:
    """Sample `root` in the background for the duration of the block (None when disabled)."""
    sampler = None
    if enabled():
        try:
            sampler = Sampler(root, interval_s if interval_s is not None else interval_from_env(), **kw).start()
        except Exception as e:  # noqa: BLE001 - e.g. no threads left: run without telemetry
            print(f"[telemetry] off: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
            sampler = None
    try:
        yield sampler
    finally:
        if sampler is not None:
            with contextlib.suppress(Exception):
                sampler.stop()
