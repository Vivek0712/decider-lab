"""vast.ai: rent the cheapest matching GPU for the run, destroy it afterwards.

    compute: {backend: vast, vast: {gpu: A100_SXM4, max_price: 0.8}}

Needs the `vastai` CLI with an API key (`vastai set api-key ...`) and an ssh key registered in
your vast.ai account (`ssh_key`, default ~/.ssh/id_ed25519). Instances are labelled
`decider-lab:<lab>`; `decider-lab compute ls|down --on vast` finds them.
"""

from __future__ import annotations

import json
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
        if not found:
            raise RuntimeError(f"no vast.ai offer for {self.num_gpus}x {self.gpu} under ${self.max_price}/h"
                               + (f" with id {self.offer}" if self.offer else "")
                               + "; see `decider-lab compute offers --on vast --gpu ...`")
        o = found[0]
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
        raise TimeoutError(f"vast instance {self.iid} did not boot within {self.boot_timeout:.0f}s")

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
