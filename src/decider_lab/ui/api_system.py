"""System API: health, meta, doctor, about and settings (API.md sections 3, 4 and 10).

    GET  /api/health, /api/system/health   {"ok": true}            (no auth)
    GET  /api/meta (alias /api/system/meta) versions, workspace, fake_cloud, features
    GET  /api/system/doctor                 doctor checks + machine (compute's /api/compute/doctor reuses
                                            `doctor_report(st)`)
    GET  /api/about
    GET, PUT /api/settings
    GET  /api/settings/env                  names of relevant environment variables, set or not; never values
"""

from __future__ import annotations

import importlib.util
import os
import platform
import re
import shutil
import subprocess
from typing import Any

import yaml
from fastapi import APIRouter, Body, Depends, Query

from .. import __version__
from ..suites import CACHE
from .errors import ApiError
from .state import StudioState, get_state
from .workspace import now_iso

router = APIRouter(prefix="/api", tags=["system"])

FIXED_ENV = {
    "HF_TOKEN": "private or gated Hugging Face models",
    "HF_HUB_OFFLINE": "use only the local Hugging Face cache",
    "AWS_PROFILE": "default AWS profile",
    "AWS_REGION": "default AWS region",
    "AWS_DEFAULT_REGION": "default AWS region (older name)",
    "OPENAI_API_KEY": "OpenAI-compatible chat models",
    "DECIDER_LAB_CACHE": "where decider-lab caches suites and models",
    "STRANDS_DECIDER_PYTHON": "the Python that serves and trains strands-decider",
    "DECIDER_LAB_FAKE_CLOUD": "cloud fixtures for testing",
}
ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")


def _features() -> dict[str, bool]:
    return {"vast_cli": shutil.which("vastai") is not None,
            "boto3": importlib.util.find_spec("boto3") is not None,
            "strands_decider": importlib.util.find_spec("strands_decider") is not None,
            "heldout_extra": importlib.util.find_spec("datasets") is not None,
            "chess_extra": importlib.util.find_spec("chess") is not None,
            "nvidia_smi": shutil.which("nvidia-smi") is not None}


def _studio_build(st: StudioState) -> str | None:
    if st.static_dir:
        p = os.path.join(st.static_dir, "build.txt")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                return fh.read().strip()[:40] or None
    return None


@router.get("/health")
@router.get("/system/health")
def health() -> dict[str, Any]:
    return {"ok": True}


@router.get("/meta")
@router.get("/system/meta")
def meta(st: StudioState = Depends(get_state)) -> dict[str, Any]:
    return {
        "studio_version": __version__,
        "decider_lab_version": __version__,
        "studio_build": _studio_build(st),
        "python": platform.python_version(),
        "platform": platform.platform(terse=True),
        "workspace": st.workspace.root,
        "state_dir": st.workspace.state_dir,
        "cache_dir": CACHE,
        "fake_cloud": st.fake_cloud,
        "features": _features(),
        "server_time": now_iso(),
    }


def _memory_gb() -> float | None:
    try:
        if platform.system() == "Darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5).stdout
            return round(int(out.strip()) / 1024**3, 1)
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) / 1024**2, 1)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return None


def _machine() -> dict[str, Any]:
    gpus: list[dict[str, Any]] = []
    accelerator = "CPU"
    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            out = subprocess.run([smi, "--query-gpu=index,name,memory.total", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=10).stdout
            for line in out.strip().splitlines():
                idx, name, mem = (x.strip() for x in line.split(",", 2))
                gpus.append({"index": int(idx), "name": name, "memory_total_gb": round(float(mem) / 1024, 1)})
            if gpus:
                accelerator = "NVIDIA CUDA"
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    elif platform.system() == "Darwin" and platform.machine() == "arm64":
        accelerator = "Apple MPS"
    system = platform.system()
    os_name = f"macOS {platform.mac_ver()[0]}" if system == "Darwin" else f"{system} {platform.release()}"
    return {"os": os_name, "python": platform.python_version(), "cpus": os.cpu_count(), "memory_gb": _memory_gb(),
            "accelerator": accelerator, "gpus": gpus}


def doctor_report(st: StudioState) -> dict[str, Any]:
    """`decider-lab doctor` as API.md's doctor response. Fake cloud: vast/aws rows come from fixtures."""
    from .. import doctor

    rows = doctor.checks(cloud=not st.fake_cloud)
    if st.cloud is not None:
        hf = next((i for i, r in enumerate(rows) if r[1] == "HF_TOKEN"), len(rows))
        rows[hf:hf] = st.cloud.doctor_rows()
    checks = [{"status": s, "item": item, "detail": st.redact(detail)} for s, item, detail in rows]
    counts = {k: sum(1 for c in checks if c["status"] == k) for k in ("ok", "warn", "info")}
    st.settings.set_flag("doctor_seen", True)
    return {"checks": checks, "machine": _machine(), "counts": counts, "fake": st.fake_cloud}


@router.get("/system/doctor")
def system_doctor(st: StudioState = Depends(get_state)) -> dict[str, Any]:
    return doctor_report(st)


@router.get("/about")
def about(st: StudioState = Depends(get_state)) -> dict[str, Any]:
    sd = None
    if importlib.util.find_spec("strands_decider"):
        try:
            from importlib.metadata import version

            sd = version("strands-decider")
        except Exception:
            sd = "installed"
    return {"decider_lab_version": __version__, "studio_build": _studio_build(st),
            "python": platform.python_version(), "strands_decider": sd, "license": "Apache-2.0",
            "cache_dir": CACHE, "state_dir": st.workspace.state_dir, "telemetry": "none"}


@router.get("/settings")
def get_settings(st: StudioState = Depends(get_state)) -> dict[str, Any]:
    return st.settings.get()


@router.put("/settings")
def put_settings(patch: dict[str, Any] = Body(...), st: StudioState = Depends(get_state)) -> dict[str, Any]:
    out = st.settings.update(patch)
    st.jobs.max_concurrent = int(out["max_concurrent_jobs"])
    return out


def _env_refs(value: Any, lab: str, refs: dict[str, set[str]], purposes: dict[str, str]) -> None:
    """Collect values of keys ending in `_env` and names in `compute.env`."""
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(k, str) and k.endswith("_env") and isinstance(v, str) and ENV_NAME.match(v):
                refs.setdefault(v, set()).add(lab)
                purposes.setdefault(v, f"referenced by {k} in a lab")
            else:
                _env_refs(v, lab, refs, purposes)
    elif isinstance(value, list):
        for v in value:
            _env_refs(v, lab, refs, purposes)


@router.get("/settings/env")
def settings_env(names: str | None = Query(None), st: StudioState = Depends(get_state)) -> dict[str, Any]:
    refs: dict[str, set[str]] = {}
    purposes: dict[str, str] = {}
    for lab in st.workspace.labs():
        try:
            with open(lab.abspath, encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(data, dict):
            continue
        _env_refs(data.get("models"), lab.name, refs, purposes)
        _env_refs(data.get("finetune"), lab.name, refs, purposes)
        compute = data.get("compute") or {}
        if isinstance(compute, dict):
            _env_refs({k: v for k, v in compute.items() if k != "env"}, lab.name, refs, purposes)
            for n in compute.get("env") or []:
                if isinstance(n, str) and ENV_NAME.match(n):
                    refs.setdefault(n, set()).add(lab.name)
                    purposes.setdefault(n, "passed to remote runs (compute.env)")
    extra = []
    if names:
        extra = [n.strip() for n in names.split(",") if n.strip()]
        bad = [n for n in extra if not ENV_NAME.match(n)]
        if bad:
            raise ApiError(422, "bad_request", "Environment variable names use A-Z, 0-9 and _.",
                           detail={"fields": [{"field": "names", "message": f"not a variable name: {b[:64]}"}
                                              for b in bad]})
    ordered = list(FIXED_ENV) + sorted(n for n in refs if n not in FIXED_ENV) + [
        n for n in extra if n not in FIXED_ENV and n not in refs]
    items = [{"name": n, "set": bool(os.environ.get(n)), "referenced_by": sorted(refs.get(n, ())),
              "purpose": FIXED_ENV.get(n) or purposes.get(n) or "added by you"} for n in dict.fromkeys(ordered)]
    return {"items": items}
