"""Run a lab on a rented vast.ai GPU, and always give the GPU back.

    decider-lab gpu run lab.yaml --gpu RTX_4090 --max-price 0.6 --max-hours 2

  1. checks your vast.ai credit covers max-price x max-hours, and refuses otherwise;
  2. rents the cheapest matching offer, labelled `decider-lab:<lab>` so `gpu ls`/`gpu down` find it;
  3. copies decider-lab and the lab directory (not its runs/) to the host over ssh;
  4. runs bootstrap.sh (torch matched to the driver, strands-decider, decider-lab);
  5. runs `decider-lab run` there in the background and streams its log;
  6. copies runs/ back;
  7. destroys the instance (with -y, then checks it is gone), on success, on error and on Ctrl-C.
     `--keep` skips 7 for debugging; `decider-lab gpu down --all` cleans up afterwards.

Credentials: nothing from your machine is copied to the host. Environment variables you list
with --env (e.g. HF_TOKEN) are passed to the remote command for that run only.
Needs the `vastai` CLI with an API key set (`vastai set api-key ...`) and an ssh key added to
your vast.ai account (`--ssh-key`, default ~/.ssh/id_ed25519).
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from typing import Any

import decider_lab

LABEL_PREFIX = "decider-lab"
DEFAULT_IMAGE = "pytorch/pytorch:2.7.1-cuda12.6-cudnn9-runtime"


def _vast(*args: str, raw: bool = True) -> Any:
    exe = shutil.which("vastai")
    if not exe:
        raise RuntimeError("the vastai CLI is not installed: pip install vastai, then vastai set api-key <key>")
    cmd = [exe, *args, *(["--raw"] if raw else [])]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"vastai {' '.join(args)} failed: {out.stderr.strip() or out.stdout.strip()}")
    if not raw:
        return out.stdout
    text = out.stdout.strip()
    try:
        return json.loads(text) if text else None
    except json.JSONDecodeError:
        return text


def credit() -> float:
    u = _vast("show", "user")
    return float(u.get("credit") or 0) if isinstance(u, dict) else 0.0


def instances(label_prefix: str = LABEL_PREFIX) -> list[dict[str, Any]]:
    rows = _vast("show", "instances") or []
    return [r for r in rows if str(r.get("label") or "").startswith(label_prefix)]


def offers(gpu: str, *, num_gpus: int = 1, max_price: float = 1.0, disk_gb: int = 80,
           min_reliability: float = 0.98, min_gpu_ram_gb: int = 0) -> list[dict[str, Any]]:
    q = (f"num_gpus={num_gpus} gpu_name={gpu} rentable=true verified=true reliability>{min_reliability} "
         f"disk_space>{disk_gb} dph_total<{max_price} cuda_max_good>=12.6 inet_down>500")
    if min_gpu_ram_gb:
        q += f" gpu_ram>={min_gpu_ram_gb * 1024}"
    rows = _vast("search", "offers", q, "-o", "dph_total") or []
    return rows if isinstance(rows, list) else []


def create(offer_id: int, *, label: str, image: str = DEFAULT_IMAGE, disk_gb: int = 80) -> int:
    res = _vast("create", "instance", str(offer_id), "--image", image, "--disk", str(disk_gb), "--ssh", "--direct",
                "--label", label)
    iid = (res or {}).get("new_contract") if isinstance(res, dict) else None
    if not iid:
        raise RuntimeError(f"vast.ai did not create an instance: {res}")
    return int(iid)


def destroy(iid: int, *, verify: bool = True) -> bool:
    """Destroy (with -y: without it the CLI asks, aborts, and the instance keeps billing)."""
    subprocess.run([shutil.which("vastai") or "vastai", "destroy", "instance", str(iid), "-y"],
                   capture_output=True, text=True)
    if not verify:
        return True
    for _ in range(12):
        ids = {int(r["id"]) for r in (_vast("show", "instances") or [])}
        if iid not in ids:
            return True
        time.sleep(5)
    return False


class Host:
    def __init__(self, host: str, port: int, key: str) -> None:
        self.host, self.port, self.key = host, port, os.path.expanduser(key)
        self.opts = ["-p", str(port), "-i", self.key, "-o", "StrictHostKeyChecking=no", "-o",
                     "UserKnownHostsFile=/dev/null", "-o", "ServerAliveInterval=30", "-o", "ConnectTimeout=20",
                     "-o", "LogLevel=ERROR"]

    def ssh(self, cmd: str, *, check: bool = True, capture: bool = False, timeout: float | None = None) -> str:
        out = subprocess.run(["ssh", *self.opts, f"root@{self.host}", cmd], capture_output=capture, text=True,
                             timeout=timeout)
        if check and out.returncode != 0:
            raise RuntimeError(f"remote command failed ({out.returncode}): {cmd[:120]}"
                               + (f"\n{out.stderr[-1500:]}" if capture else ""))
        return out.stdout if capture else ""

    def rsync(self, src: str, dst: str, *, excludes: tuple[str, ...] = ()) -> None:
        ssh = "ssh " + " ".join(shlex.quote(o) for o in self.opts)
        cmd = ["rsync", "-az", "--delete", "-e", ssh, *[f"--exclude={e}" for e in excludes], src, dst]
        subprocess.run(cmd, check=True)

    def remote(self, path: str) -> str:
        return f"root@{self.host}:{path}"


def wait_ssh(iid: int, key: str, timeout: float = 900) -> Host:
    t0 = time.time()
    while time.time() - t0 < timeout:
        info = _vast("show", "instance", str(iid))
        if isinstance(info, dict) and info.get("actual_status") == "running" and info.get("ssh_host"):
            host = Host(info["ssh_host"], int(info["ssh_port"]), key)
            try:
                host.ssh("true", timeout=30)
                return host
            except (RuntimeError, subprocess.TimeoutExpired):
                pass
        time.sleep(10)
    raise TimeoutError(f"instance {iid} not reachable over ssh after {timeout:.0f}s")


def package_root() -> str:
    """The decider-lab source tree to copy (the checkout this module was imported from)."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(decider_lab.__file__))))
    if not os.path.exists(os.path.join(root, "pyproject.toml")):
        raise RuntimeError("gpu run copies decider-lab's source; install it from a checkout (pip install -e .)")
    return root


def run_remote(lab_path: str, *, gpu: str = "RTX_4090", num_gpus: int = 1, max_price: float = 0.8,
               max_hours: float = 2.0, disk_gb: int = 80, strands_spec: str | None = None,
               ssh_key: str = "~/.ssh/id_ed25519", env: list[str] | None = None, keep: bool = False,
               run_args: str = "", image: str = DEFAULT_IMAGE, min_gpu_ram_gb: int = 0,
               fast_kernels: bool = False, boot_timeout: float = 1800, log=print) -> str:
    lab_path = os.path.abspath(lab_path)
    lab_dir, lab_file = os.path.dirname(lab_path), os.path.basename(lab_path)
    name = os.path.splitext(lab_file)[0]
    budget = max_price * max_hours
    have = credit()
    if have < budget + 0.5:
        raise RuntimeError(f"vast.ai credit ${have:.2f} does not cover max-price x max-hours = ${budget:.2f} "
                           "(+$0.50 margin); top up or lower --max-hours/--max-price")
    found = offers(gpu, num_gpus=num_gpus, max_price=max_price, disk_gb=disk_gb, min_gpu_ram_gb=min_gpu_ram_gb)
    if not found:
        raise RuntimeError(f"no vast.ai offer for {num_gpus}x {gpu} under ${max_price}/h; try another --gpu "
                           "or a higher --max-price (decider-lab gpu offers --gpu ...)")
    offer = found[0]
    log(f"[gpu] renting {num_gpus}x {offer.get('gpu_name')} at ${offer.get('dph_total', 0):.3f}/h "
        f"(offer {offer['id']}, credit ${have:.2f}, cap ${budget:.2f})")
    iid = create(int(offer["id"]), label=f"{LABEL_PREFIX}:{name}", image=image, disk_gb=disk_gb)
    log(f"[gpu] instance {iid}")
    deadline = time.time() + max_hours * 3600
    cleaned = {"done": False}

    def cleanup(*_: Any) -> None:
        if cleaned["done"] or keep:
            return
        cleaned["done"] = True
        gone = destroy(iid)
        log(f"[gpu] instance {iid} destroyed" if gone else
            f"[gpu] WARNING: instance {iid} may still be running; run: vastai destroy instance {iid} -y")

    old = signal.signal(signal.SIGINT, lambda *a: (cleanup(), sys.exit(130)))
    try:
        host = wait_ssh(iid, ssh_key, timeout=boot_timeout)
        log(f"[gpu] ssh ok: {host.host}:{host.port}")
        host.rsync(package_root() + "/", host.remote("/root/decider-lab/"),
                   excludes=(".git", "runs", ".venv", "__pycache__", "*.egg-info", ".pytest_cache"))
        host.rsync(lab_dir + "/", host.remote("/root/lab/"), excludes=("runs", ".git", ".venv", "__pycache__"))
        spec = strands_spec or ""
        fk = "FAST_KERNELS=1 " if fast_kernels else ""
        log("[gpu] bootstrapping (torch for the driver, strands-decider, decider-lab)")
        host.ssh(f"{fk}bash /root/decider-lab/src/decider_lab/gpu/bootstrap.sh {shlex.quote(spec) if spec else ''}"
                 " > /root/bootstrap.log 2>&1 || (tail -40 /root/bootstrap.log; exit 1)", timeout=3600)
        log("[gpu] bootstrap ok")
        envs = " ".join(f"{k}={shlex.quote(os.environ[k])}" for k in (env or []) if k in os.environ)
        # setsid + </dev/null: otherwise ssh keeps the session open until the job ends, nothing
        # streams, and the --max-hours deadline cannot fire.
        launch = (f"cd /root/lab && rm -f /root/lab/EXIT && {envs} setsid nohup bash -c '. /root/venv/bin/activate && "
                  f"decider-lab run {shlex.quote(lab_file)} {run_args}; echo $? > /root/lab/EXIT' "
                  "> /root/lab/run.log 2>&1 < /dev/null &")
        host.ssh(launch, timeout=60)
        shown, code = 0, None
        while code is None:
            if time.time() > deadline:
                raise TimeoutError(f"--max-hours {max_hours} reached; stopping")
            time.sleep(20)
            try:
                out = host.ssh(f"tail -n +{shown + 1} /root/lab/run.log; echo __END__; cat /root/lab/EXIT 2>/dev/null "
                               "|| true", capture=True, check=False, timeout=90)
            except subprocess.TimeoutExpired:
                continue  # a slow poll is not a failed run; the deadline above still applies
            body, _, tail = out.partition("__END__\n")
            for line in body.splitlines():
                log(f"  | {line}")
            shown += len(body.splitlines())
            code = int(tail.strip()) if tail.strip().isdigit() else None
        local_runs = os.path.join(lab_dir, "runs")
        os.makedirs(local_runs, exist_ok=True)
        host.rsync(host.remote("/root/lab/runs/"), local_runs + "/")
        for f in ("run.log",):
            subprocess.run(["rsync", "-az", "-e", "ssh " + " ".join(shlex.quote(o) for o in host.opts),
                            host.remote(f"/root/lab/{f}"), os.path.join(local_runs, f"remote-{f}")], check=False)
        subprocess.run(["rsync", "-az", "-e", "ssh " + " ".join(shlex.quote(o) for o in host.opts),
                        host.remote("/root/bootstrap.log"), os.path.join(local_runs, "remote-bootstrap.log")],
                       check=False)
        if code != 0:
            raise RuntimeError(f"the remote run exited with {code}; logs copied to {local_runs}")
        log(f"[gpu] results copied to {local_runs}")
        return local_runs
    finally:
        cleanup()
        signal.signal(signal.SIGINT, old)
