"""One way to run a lab anywhere: acquire a machine, copy, bootstrap, run, fetch, release.

A provider only knows how to get a machine and how to give it back (`acquire`, `release`);
everything between is shared, so a lab behaves the same on a rented GPU, an AWS instance,
your own server over ssh, or this laptop.

`release` runs on success, on failure, at the `max_hours` deadline, on Ctrl-C and on SIGTERM.
Providers that can also enforce a deadline on the machine itself (AWS: the instance shuts
itself down and terminates) do so, in case this process dies first.
"""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import time
from typing import Any

import decider_lab

DEFAULT_STRANDS = ("strands-decider[vision,cuda] @ git+https://github.com/strands-labs/strands-decider"
                   "@3e94e9d84c620ed5a95f1a3310c3decb971e261c")
CPU_STRANDS = DEFAULT_STRANDS.replace("[vision,cuda]", "[vision]")


class Host:
    """A machine reachable over ssh. `work` is the remote working directory."""

    def __init__(self, host: str, port: int = 22, user: str = "root", key: str | None = None,
                 work: str | None = None) -> None:
        self.host, self.port, self.user = host, int(port), user
        self.key = os.path.expanduser(key) if key else None
        self.work = work or ("/root/decider-lab-work" if user == "root" else f"/home/{user}/decider-lab-work")
        self.opts = ["-p", str(self.port), "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
                     "-o", "ServerAliveInterval=30", "-o", "ConnectTimeout=20", "-o", "LogLevel=ERROR",
                     "-o", "BatchMode=yes"]
        if self.key:
            self.opts += ["-i", self.key]

    @property
    def target(self) -> str:
        return f"{self.user}@{self.host}"

    def ssh(self, cmd: str, *, check: bool = True, capture: bool = False, timeout: float | None = None) -> str:
        out = subprocess.run(["ssh", *self.opts, self.target, cmd], capture_output=capture, text=True,
                             timeout=timeout, stdin=subprocess.DEVNULL)
        if check and out.returncode != 0:
            raise RuntimeError(f"remote command failed ({out.returncode}): {cmd[:160]}"
                               + (f"\n{(out.stderr or '')[-1500:]}" if capture else ""))
        return out.stdout if capture else ""

    def rsync(self, src: str, dst: str, *, excludes: tuple[str, ...] = (), delete: bool = True,
              check: bool = True) -> int:
        ssh = "ssh " + " ".join(shlex.quote(o) for o in self.opts)
        cmd = ["rsync", "-az", *(["--delete"] if delete else []), "-e", ssh,
               *[f"--exclude={e}" for e in excludes], src, dst]
        return subprocess.run(cmd, check=check).returncode

    def remote(self, path: str) -> str:
        return f"{self.target}:{path}"

    def reachable(self, timeout: float = 30) -> bool:
        try:
            self.ssh("true", timeout=timeout)
            return True
        except (RuntimeError, subprocess.TimeoutExpired):
            return False


class Provider:
    """Where a lab runs. Subclasses implement `check`, `acquire` and `release`."""

    name = "provider"
    # the host is a fresh machine decider-lab set up (True) or someone's own machine (False)
    ephemeral = True

    def check(self, max_hours: float) -> None:
        """Refuse early, with the fix, if this cannot work (credentials, quota, budget)."""

    def acquire(self, log: Any) -> Host:
        raise NotImplementedError

    def release(self, log: Any) -> None:
        """Give the machine back. Idempotent; must not raise."""

    def default_strands_spec(self) -> str:
        return DEFAULT_STRANDS


def wait_reachable(host: Host, timeout: float, what: str = "the machine") -> Host:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if host.reachable():
            return host
        time.sleep(10)
    raise TimeoutError(f"{what} did not accept ssh within {timeout:.0f}s")


def package_root() -> str:
    """The decider-lab source tree to copy (the checkout this module was imported from)."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(decider_lab.__file__))))
    if not os.path.exists(os.path.join(root, "pyproject.toml")):
        raise RuntimeError("remote runs copy decider-lab's source; install it from a checkout (pip install -e .)")
    return root


def presign_sources(lab_path: str, log: Any = print) -> str | None:
    """A copy of the lab with every s3:// model source replaced by a presigned https URL made
    here, so the remote host can pull it without AWS credentials; None if there is none."""
    import yaml

    from ..sources import presign

    with open(lab_path, encoding="utf-8") as fh:
        lab = yaml.safe_load(fh) or {}
    changed = 0
    targets = [(m, "serve") for m in (lab.get("models") or {}).values() if isinstance(m, dict)]
    targets += [(f, "from") for f in (lab.get("finetune") or {}).values() if isinstance(f, dict)]
    for spec, key in targets:
        src = spec.get(key)
        if isinstance(src, str) and src.startswith("s3://"):
            if not spec.get("sha256"):
                log(f"[remote] WARNING: {src} has no sha256; the remote pull cannot be verified")
            spec[key] = presign(src, spec)
            for k in ("profile", "region"):
                spec.pop(k, None)
            changed += 1
    if not changed:
        return None
    log(f"[remote] {changed} s3:// source(s) presigned for the remote host (valid 6 h; no credentials copied)")
    out = os.path.join(os.path.dirname(lab_path), f".remote-{os.path.basename(lab_path)}")
    with open(out, "w", encoding="utf-8") as fh:
        yaml.safe_dump(lab, fh, sort_keys=False)
    return out


def run_on(provider: Provider, lab_path: str, *, max_hours: float = 2.0, strands_spec: str | None = None,
           env: list[str] | None = None, keep: bool = False, run_args: str = "", fast_kernels: bool = False,
           log: Any = print) -> str:
    """Run a lab on `provider`'s machine and copy runs/ back; returns the local runs directory."""
    lab_path = os.path.abspath(lab_path)
    lab_dir, lab_file = os.path.dirname(lab_path), os.path.basename(lab_path)
    provider.check(max_hours)
    deadline = time.time() + max_hours * 3600
    state = {"released": False}

    def release(*_: Any) -> None:
        if state["released"]:
            return
        state["released"] = True
        if keep:
            log(f"[{provider.name}] --keep: the machine is left running; release it yourself")
            return
        provider.release(log)

    def on_signal(signum: int, _frame: Any) -> None:
        release()
        sys.exit(128 + signum)

    old = {s: signal.signal(s, on_signal) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        host = provider.acquire(log)
        w = host.work
        log(f"[{provider.name}] ssh ok: {host.target}:{host.port}, work dir {w}")
        host.ssh(f"mkdir -p {w}/lab {w}/decider-lab", timeout=60)
        host.rsync(package_root() + "/", host.remote(f"{w}/decider-lab/"),
                   excludes=(".git", "runs", ".venv", "__pycache__", "*.egg-info", ".pytest_cache", ".ruff_cache"))
        host.rsync(lab_dir + "/", host.remote(f"{w}/lab/"),
                   excludes=("runs", ".git", ".venv", "__pycache__", "gpu-run.log", ".remote-*"))
        remote_lab = presign_sources(lab_path, log)
        if remote_lab:
            try:
                host.rsync(remote_lab, host.remote(f"{w}/lab/{lab_file}"), delete=False)
            finally:
                os.remove(remote_lab)  # holds presigned URLs: never left on disk
        spec = strands_spec or provider.default_strands_spec()
        log(f"[{provider.name}] bootstrap: torch for this machine, {spec.split(' @ ')[0]}, decider-lab")
        host.ssh(f"WORK={w} {'FAST_KERNELS=1 ' if fast_kernels else ''}bash {w}/decider-lab/src/decider_lab/compute/"
                 f"bootstrap.sh {shlex.quote(spec)} > {w}/bootstrap.log 2>&1 || (tail -40 {w}/bootstrap.log; exit 1)",
                 timeout=3600)
        log(f"[{provider.name}] bootstrap ok")
        envs = " ".join(f"{k}={shlex.quote(os.environ[k])}" for k in (env or []) if k in os.environ)
        # setsid + </dev/null: otherwise ssh holds the session until the job ends, nothing streams,
        # and the deadline cannot fire. `--on local`: the remote run must not dispatch again.
        # Only the job goes in the background, inside { ...; }: a trailing `&` after an `&&` chain
        # backgrounds the whole chain, whose subshell keeps the ssh channel open.
        host.ssh(f"cd {w}/lab && rm -f {w}/lab/EXIT && {{ {envs} $(command -v setsid) nohup bash -c '. {w}/venv/bin/activate && "
                 f"decider-lab run {shlex.quote(lab_file)} --on local {run_args}; echo $? > {w}/lab/EXIT' "
                 f"> {w}/lab/run.log 2>&1 < /dev/null & }}", timeout=60)
        shown, code = 0, None
        while code is None:
            if time.time() > deadline:
                raise TimeoutError(f"max_hours {max_hours} reached; stopping")
            time.sleep(20)
            try:
                out = host.ssh(f"tail -n +{shown + 1} {w}/lab/run.log; echo __END__; cat {w}/lab/EXIT 2>/dev/null "
                               "|| true", capture=True, check=False, timeout=90)
            except subprocess.TimeoutExpired:
                continue  # a slow poll is not a failed run; the deadline still applies
            body, _, tail = out.partition("__END__\n")
            for line in body.splitlines():
                log(f"  | {line}")
            shown += len(body.splitlines())
            code = int(tail.strip()) if tail.strip().isdigit() else None
        local_runs = os.path.join(lab_dir, "runs")
        os.makedirs(local_runs, exist_ok=True)
        host.rsync(host.remote(f"{w}/lab/runs/"), local_runs + "/", delete=False)
        for src, dst in ((f"{w}/lab/run.log", "remote-run.log"), (f"{w}/bootstrap.log", "remote-bootstrap.log")):
            host.rsync(host.remote(src), os.path.join(local_runs, dst), delete=False, check=False)
        if code != 0:
            raise RuntimeError(f"the remote run exited with {code}; results and logs copied to {local_runs}")
        log(f"[{provider.name}] results copied to {local_runs}")
        return local_runs
    finally:
        release()
        for s, h in old.items():
            signal.signal(s, h)
