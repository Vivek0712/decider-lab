"""vast.ai: rent the cheapest matching GPU for the run, destroy it afterwards.

    compute: {backend: vast, vast: {gpu: A100_SXM4, max_price: 0.8}}

Needs the `vastai` CLI with an API key (`vastai set api-key ...`) and an ssh key registered in
your vast.ai account (`ssh_key`, default ~/.ssh/id_ed25519). Instances are labelled
`decider-lab:<lab>`; `decider-lab compute ls|down --on vast` finds them.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from typing import Any

from .base import Host, Provider, wait_reachable

LABEL_PREFIX = "decider-lab"
DEFAULT_IMAGE = "pytorch/pytorch:2.7.1-cuda12.6-cudnn9-runtime"


def _vast(*args: str, raw: bool = True) -> Any:
    exe = shutil.which("vastai")
    if not exe:
        raise RuntimeError("the vastai CLI is not installed: pip install vastai, then vastai set api-key <key>")
    out = subprocess.run([exe, *args, *(["--raw"] if raw else [])], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"vastai {' '.join(args)} failed: {out.stderr.strip() or out.stdout.strip()}")
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


BAD_HOSTS_TTL_S = 24 * 3600


def _bad_hosts_path() -> str:
    from .. import suites

    return os.path.join(suites.CACHE, "vast_bad_machines.json")


def bad_machines() -> dict[str, float]:
    """machine_id -> when it failed to boot; entries older than a day are forgotten."""
    try:
        with open(_bad_hosts_path(), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    now = time.time()
    return {k: v for k, v in data.items() if now - float(v) < BAD_HOSTS_TTL_S}


def remember_bad_machine(machine_id: Any) -> None:
    if machine_id in (None, ""):
        return
    data = bad_machines()
    data[str(machine_id)] = time.time()
    os.makedirs(os.path.dirname(_bad_hosts_path()), exist_ok=True)
    with open(_bad_hosts_path(), "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def destroy(iid: int, *, verify: bool = True) -> bool:
    """Destroy with -y: without it the CLI asks, aborts, and the instance keeps billing."""
    subprocess.run([shutil.which("vastai") or "vastai", "destroy", "instance", str(iid), "-y"],
                   capture_output=True, text=True)
    if not verify:
        return True
    for _ in range(12):
        if iid not in {int(r["id"]) for r in (_vast("show", "instances") or [])}:
            return True
        time.sleep(5)
    return False


class VastProvider(Provider):
    name = "vast"

    def __init__(self, gpu: str = "RTX_4090", num_gpus: int = 1, max_price: float = 0.8, disk_gb: int = 80,
                 ssh_key: str = "~/.ssh/id_ed25519", image: str = DEFAULT_IMAGE, min_gpu_ram_gb: int = 0,
                 offer: int | None = None, boot_timeout: float = 1800, label: str = "lab") -> None:
        self.gpu, self.num_gpus, self.max_price, self.disk_gb = gpu, num_gpus, max_price, disk_gb
        self.ssh_key, self.image, self.min_gpu_ram_gb = ssh_key, image, min_gpu_ram_gb
        self.offer, self.boot_timeout, self.label = offer, boot_timeout, label
        self.iid: int | None = None
        self.machine_id: Any = None

    def check(self, max_hours: float) -> None:
        budget = self.max_price * max_hours
        have = credit()
        if have < budget + 0.5:
            raise RuntimeError(f"vast.ai credit ${have:.2f} does not cover max_price x max_hours = ${budget:.2f} "
                               "(+$0.50 margin); top up or lower max_hours / max_price")

    def acquire(self, log: Any) -> Host:
        found = offers(self.gpu, num_gpus=self.num_gpus, max_price=self.max_price, disk_gb=self.disk_gb,
                       min_gpu_ram_gb=self.min_gpu_ram_gb)
        if self.offer is not None:
            found = [o for o in found if int(o["id"]) == int(self.offer)]
        else:
            # a machine that failed to boot recently is skipped for a day (cheapest is not always best)
            bad = bad_machines()
            skipped = [o for o in found if str(o.get("machine_id")) in bad]
            if skipped and len(skipped) < len(found):
                log(f"[vast] skipping {len(skipped)} offer(s) on machines that failed to boot in the last 24 h")
                found = [o for o in found if str(o.get("machine_id")) not in bad]
        if not found:
            raise RuntimeError(f"no vast.ai offer for {self.num_gpus}x {self.gpu} under ${self.max_price}/h"
                               + (f" with id {self.offer}" if self.offer else "")
                               + "; see `decider-lab compute offers --on vast --gpu ...`")
        o = found[0]
        self.machine_id = o.get("machine_id")
        log(f"[vast] renting {self.num_gpus}x {o.get('gpu_name')} at ${o.get('dph_total', 0):.3f}/h (offer {o['id']})")
        res = _vast("create", "instance", str(o["id"]), "--image", self.image, "--disk", str(self.disk_gb), "--ssh",
                    "--direct", "--label", f"{LABEL_PREFIX}:{self.label}")
        iid = (res or {}).get("new_contract") if isinstance(res, dict) else None
        if not iid:
            raise RuntimeError(f"vast.ai did not create an instance: {res}")
        self.iid = int(iid)
        log(f"[vast] instance {self.iid}; waiting for it to boot (image pull)")
        t0 = time.time()
        while time.time() - t0 < self.boot_timeout:
            info = _vast("show", "instance", str(self.iid))
            if isinstance(info, dict) and info.get("actual_status") == "running" and info.get("ssh_host"):
                host = Host(info["ssh_host"], int(info["ssh_port"]), "root", self.ssh_key)
                return wait_reachable(host, max(60.0, self.boot_timeout - (time.time() - t0)), f"vast instance {iid}")
            time.sleep(15)
        remember_bad_machine(self.machine_id)
        raise TimeoutError(f"vast instance {self.iid} did not boot within {self.boot_timeout:.0f}s "
                           f"(machine {self.machine_id} is skipped for the next 24 h)")

    def release(self, log: Any) -> None:
        if self.iid is None:
            return
        try:
            gone = destroy(self.iid)
        except Exception as e:  # release must not raise; say what to do instead
            gone = False
            log(f"[vast] destroy failed: {e}")
        log(f"[vast] instance {self.iid} destroyed" if gone else
            f"[vast] WARNING: instance {self.iid} may still be running: vastai destroy instance {self.iid} -y")
        self.iid = None if gone else self.iid
