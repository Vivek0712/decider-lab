"""Start `strands-decider serve` for the length of a run, and prove it is the server you asked for.

The traps this closes, each of which cost us a measurement once:
  - a stale server already on the port answers instead of the new one (refused up front);
  - the server dies while loading and the client waits forever (the process is watched);
  - /health names another checkpoint, or no vision when vision was asked for (refused);
  - a stop that leaves the process running (process group terminated, then killed).
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def get_health(url: str, timeout: float = 5) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(url + "/health", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


WARM_UP = {"state": "The sky is blue today.",
           "questions": {"q": {"type": "noul", "instructions": "Is this text about the weather?"}}}


def tail(path: str | None, n: int = 25) -> str:
    if not path or not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8", errors="replace") as fh:
        return "".join(fh.readlines()[-n:])


def warm_up(url: str, *, timeout: float = 600, log_path: str | None = None) -> float:
    """One real request; returns its latency or raises with the server log's tail."""
    req = urllib.request.Request(url + "/v1/systemone", data=json.dumps(WARM_UP).encode(),
                                 headers={"Content-Type": "application/json"})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            answer = json.loads(r.read())["answers"]["q"]
        if "noul" not in answer:
            raise RuntimeError(f"warm-up answer has no noul: {answer}")
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        detail = e.read().decode("utf-8", "replace")[:300] if isinstance(e, urllib.error.HTTPError) else str(e)
        raise RuntimeError(f"the server is up but cannot answer a question ({detail}). Server log tail:\n"
                           + tail(log_path)) from e
    print(f"[decider-lab] warm-up answer in {time.time() - t:.1f}s", flush=True)
    return time.time() - t


def serve_command(checkpoint: str, port: int, *, vision: bool = False, device: str | None = None,
                  model_name: str | None = None, cli: str | None = None, extra: list[str] | None = None) -> list[str]:
    exe = cli or shutil.which("strands-decider") or os.path.join(os.path.dirname(sys.executable), "strands-decider")
    cmd = [exe, "serve", checkpoint, "--host", "127.0.0.1", "--port", str(port)]
    if vision:
        cmd.append("--vision")
    if device:
        cmd += ["--device", device]
    if model_name:
        cmd += ["--model-name", model_name]
    return cmd + list(extra or [])


@contextlib.contextmanager
def served(checkpoint: str, *, port: int | None = None, vision: bool = False, device: str | None = None,
           model_name: str | None = None, log_path: str | None = None, timeout: float = 1800,
           gpu: str | None = None, cli: str | None = None, extra: list[str] | None = None) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yields (url, /health) for a server running `checkpoint`; always stops it afterwards."""
    port = port or free_port()
    url = f"http://127.0.0.1:{port}"
    if get_health(url, timeout=2) is not None:
        raise RuntimeError(f"something already answers on {url}; refusing to measure a stale server")
    env = dict(os.environ)
    if gpu is not None:
        env["CUDA_VISIBLE_DEVICES"] = str(gpu)
    cmd = serve_command(checkpoint, port, vision=vision, device=device, model_name=model_name, cli=cli, extra=extra)
    if not os.path.exists(cmd[0]) and not shutil.which(cmd[0]):
        raise RuntimeError("strands-decider is not installed here: pip install 'decider-lab[strands]' "
                           "(or point `cli` at it)")
    log = open(log_path or os.devnull, "a", encoding="utf-8")
    print(f"[decider-lab] serving {checkpoint} on {url}", flush=True)
    proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=True)
    try:
        t0 = time.time()
        health: dict[str, Any] | None = None
        while health is None or health.get("status") != "ok":
            if proc.poll() is not None:
                raise RuntimeError(f"the server exited with code {proc.returncode} before /health answered"
                                   + (f"; see {log_path}" if log_path else ""))
            if time.time() - t0 > timeout:
                raise RuntimeError(f"/health did not answer within {timeout:.0f}s")
            time.sleep(3)
            health = get_health(url)
        if vision and not health.get("vision"):
            raise RuntimeError(f"asked for --vision but /health reports {health.get('vision')!r}")
        # /health only says the weights loaded. Ask one real question before measuring: kernels
        # compile on the first request, and a server that cannot answer must fail here, loudly.
        warm_up(url, timeout=max(60.0, timeout / 2), log_path=log_path)
        print(f"[decider-lab] /health ok after {time.time() - t0:.0f}s: model={health.get('model')} "
              f"device={health.get('device')} vision={health.get('vision')}", flush=True)
        yield url, health
        after = get_health(url)
        if after is None or after.get("checkpoint") != health.get("checkpoint"):
            raise RuntimeError("the server changed or died during the run; its answers cannot be trusted")
    finally:
        if proc.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=10)
        log.close()
