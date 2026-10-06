"""Compute API: doctor, vast.ai, AWS and SSH hosts (API.md section 10).

    GET  /api/compute/doctor                              doctor checks + this machine
    GET  /api/compute/vast/status|offers|instances
    POST /api/compute/vast/instances/{id}/destroy         typed confirm: the instance id
    POST /api/compute/vast/instances/destroy-all          typed confirm: "destroy all"
    GET  /api/compute/aws/profiles|identity|quotas|instance-types|instances|bedrock-models
    POST /api/compute/aws/instances/{id}/terminate        typed confirm: the instance id
    POST /api/compute/aws/instances/terminate-all         typed confirm: "terminate all"
    GET, POST /api/compute/ssh/hosts
    PUT, DELETE /api/compute/ssh/hosts/{host_id}
    POST /api/compute/ssh/hosts/{host_id}/test

With DECIDER_LAB_FAKE_CLOUD=1 every vast/aws call goes to `st.cloud` (FakeCloud) and no `vastai`
or boto3 call is made. Otherwise vast uses `decider_lab.compute.vast` (the `vastai` CLI) and AWS
uses boto3 with the chosen profile; AWS calls are read-only except terminate. Studio never reads
key material: AWS profiles are names from config section headers, SSH keys are paths.

Other areas may import: `AWS_INSTANCE_TYPES` / `aws_price(type)` (static price table for run
estimates) and `cloud_summary(st)` (instances, idle count and burn rate for the Overview).
"""

from __future__ import annotations

import concurrent.futures
import importlib.util
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, TypeVar

from fastapi import APIRouter, Body, Depends, Query

from . import api_system
from .errors import ApiError
from .state import StudioState, get_state
from .workspace import iso, now_iso

router = APIRouter(prefix="/api", tags=["compute"])

T = TypeVar("T")

CLOUD_TIMEOUT_S = 15.0
VAST_LABEL_PREFIX = "decider-lab"
AWS_TAG = "decider-lab"
DEFAULT_REGION = "us-east-1"
GPU_NAMES = ["RTX_4090", "RTX_3090", "RTX_A6000", "RTX_6000Ada", "L40S", "L40", "A40", "A100_SXM4", "A100_PCIE",
             "H100_SXM", "H100_PCIE", "H200"]

VAST_INSTALL = "pip install 'decider-lab[vast]'"
AWS_INSTALL = "pip install 'decider-lab[aws]'"

# Static on-demand list prices (us-east-1, Linux), used for estimates and the instance table.
# Approximate: no AWS pricing call is made. Keep `AWS_PRICES_AS_OF` in step with edits.
AWS_PRICES_AS_OF = "2026-09-01"
AWS_REGION_NOTE = "us-east-1 on-demand list prices; approximate"
AWS_INSTANCE_TYPES: list[dict[str, Any]] = [
    {"type": "g4dn.xlarge", "gpu": "1x T4 16 GB", "vcpus": 4, "family": "g", "usd_per_hour": 0.526},
    {"type": "g4dn.2xlarge", "gpu": "1x T4 16 GB", "vcpus": 8, "family": "g", "usd_per_hour": 0.752},
    {"type": "g5.xlarge", "gpu": "1x A10G 24 GB", "vcpus": 4, "family": "g", "usd_per_hour": 1.006},
    {"type": "g5.2xlarge", "gpu": "1x A10G 24 GB", "vcpus": 8, "family": "g", "usd_per_hour": 1.212},
    {"type": "g5.12xlarge", "gpu": "4x A10G 24 GB", "vcpus": 48, "family": "g", "usd_per_hour": 5.672},
    {"type": "g6.xlarge", "gpu": "1x L4 24 GB", "vcpus": 4, "family": "g", "usd_per_hour": 0.805},
    {"type": "g6.2xlarge", "gpu": "1x L4 24 GB", "vcpus": 8, "family": "g", "usd_per_hour": 0.978},
    {"type": "g6e.xlarge", "gpu": "1x L40S 48 GB", "vcpus": 4, "family": "g", "usd_per_hour": 1.861},
    {"type": "g6e.2xlarge", "gpu": "1x L40S 48 GB", "vcpus": 8, "family": "g", "usd_per_hour": 2.242},
    {"type": "g6e.12xlarge", "gpu": "4x L40S 48 GB", "vcpus": 48, "family": "g", "usd_per_hour": 10.493},
    {"type": "p4d.24xlarge", "gpu": "8x A100 40 GB", "vcpus": 96, "family": "p", "usd_per_hour": 21.958},
    {"type": "p5.48xlarge", "gpu": "8x H100 80 GB", "vcpus": 192, "family": "p", "usd_per_hour": 55.04},
    {"type": "c7i.xlarge", "gpu": None, "vcpus": 4, "family": "standard", "usd_per_hour": 0.179},
    {"type": "c7i.2xlarge", "gpu": None, "vcpus": 8, "family": "standard", "usd_per_hour": 0.357},
    {"type": "m7i.2xlarge", "gpu": None, "vcpus": 8, "family": "standard", "usd_per_hour": 0.403},
]
_PRICE = {t["type"]: t["usd_per_hour"] for t in AWS_INSTANCE_TYPES}
QUOTA_FAMILIES = [("g", "L-DB2E81BA", "Running On-Demand G and VT instances"),
                  ("p", "L-417A185B", "Running On-Demand P instances"),
                  ("standard", "L-1216C47A", "Running On-Demand Standard (A, C, D, H, I, M, R, T, Z) instances")]
STANDARD_LETTERS = set("acdhimrtz")

PROFILE_RE = re.compile(r"^[A-Za-z0-9_.+@=,-]{1,64}$")
REGION_RE = re.compile(r"^[a-z]{2}(-gov|-iso[a-z]?)?-[a-z]+-\d{1,2}$")
AWS_ID_RE = re.compile(r"^i-[0-9a-f]{8,17}$")
GPU_RE = re.compile(r"^[A-Za-z0-9_]{1,40}$")
HOST_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
TARGET_RE = re.compile(r"^(?P<user>[A-Za-z0-9_][A-Za-z0-9._-]{0,63})@"
                       r"(?P<host>[A-Za-z0-9][A-Za-z0-9.-]{0,252}|\[[0-9A-Fa-f:.]{2,45}\])"
                       r"(?::(?P<port>\d{1,5}))?$")
WORK_DIR_RE = re.compile(r"^[A-Za-z0-9_./~-]{1,512}$")
HOST_ID_RE = re.compile(r"^h_[0-9a-f]{8}$")
SSH_TEST_CMD = "nvidia-smi --query-gpu=name,memory.total --format=csv,noheader || echo no-gpu"
SSH_TIMEOUT_S = 20.0

_pool = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="studio-cloud")


# ---- shared helpers ----------------------------------------------------------------------------



VAST_NOT_BILLING = ("exited", "stopped", "offline", "destroyed")  # every other status bills

def _cloud(fn: Callable[[], T], *, what: str, timeout: float = CLOUD_TIMEOUT_S) -> T:
    """Run a cloud read with a deadline; a slow provider becomes `504 cloud_timeout`."""
    fut = _pool.submit(fn)
    try:
        return fut.result(timeout=timeout)
    except concurrent.futures.TimeoutError as e:
        raise ApiError(504, "cloud_timeout", f"{what} did not answer within {int(timeout)} seconds.",
                       hint="Check your network connection, then refresh.") from e


def _confirm(body: dict[str, Any], expected: str, hint: str) -> None:
    got = body.get("confirm")
    if not isinstance(got, str) or got.strip() != expected:
        raise ApiError(409, "confirm_mismatch", "The confirmation text does not match.",
                       hint=hint[:1].upper() + hint[1:] + ".", detail={"expected_hint": hint})


def _bad(field: str, message: str) -> ApiError:
    return ApiError(422, "bad_request", "The request is not valid.", detail={"fields": [{"field": field,
                                                                                         "message": message}]})


def _owners(st: StudioState) -> list[tuple[dict[str, Any], str]]:
    """Active remote jobs and the text that can name their machine (machine_id plus the log)."""
    out = []
    for j in st.jobs.list(status="active", limit=200):
        if j.get("backend") not in ("vast", "aws"):
            continue
        try:
            text = st.jobs.log_text(j["job_id"])[-500_000:]
        except ApiError:
            text = ""
        out.append((j, f"{j.get('machine_id') or ''}\n{text}"))
    return out


def _overlay(st: StudioState, items: list[dict[str, Any]], provider: str) -> list[dict[str, Any]]:
    """Set `job_id`, `job_title` and `idle` (no active Studio job owns the machine)."""
    owners = [(j, t) for j, t in _owners(st) if j.get("backend") == provider]
    for it in items:
        sid = str(it["id"])
        pat = re.compile(rf"(?<![\w-]){re.escape(sid)}(?![\w-])")
        owner = next((j for j, text in owners if str(j.get("machine_id") or "") == sid or pat.search(text)), None)
        it["job_id"] = owner["job_id"] if owner else None
        it["job_title"] = owner["title"] if owner else None
        it["idle"] = owner is None
    return items


def _publish(st: StudioState, provider: str) -> None:
    st.bus.publish("compute.changed", {"provider": provider})


# ---- doctor ------------------------------------------------------------------------------------


@router.get("/compute/doctor")
def compute_doctor(st: StudioState = Depends(get_state)) -> dict[str, Any]:
    return _cloud(lambda: api_system.doctor_report(st), what="The doctor checks", timeout=45.0)


# ---- vast.ai -----------------------------------------------------------------------------------


def _vast_mod() -> Any:
    from ..compute import vast

    return vast


def _vast_cli() -> bool:
    return shutil.which("vastai") is not None


def _vast_unavailable() -> ApiError:
    return ApiError(424, "backend_unavailable", "The vastai CLI is not installed.",
                    hint=f"Install it with {VAST_INSTALL}, then run `vastai set api-key <key>` in a terminal.",
                    detail={"install": VAST_INSTALL, "provider": "vast"})


def _vast_error(st: StudioState, e: Exception) -> ApiError:
    msg = st.redact(str(e))[:400]
    if "not installed" in msg:
        return _vast_unavailable()
    return ApiError(502, "cloud_error", "vast.ai returned an error.", hint=msg, detail={"provider": "vast"})


def _ts_iso(value: Any) -> str | None:
    try:
        return iso(float(value)) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _vast_instance_row(r: dict[str, Any], now: float) -> dict[str, Any]:
    start = r.get("start_date")
    try:
        start_f = float(start) if start is not None else None
    except (TypeError, ValueError):
        start_f = None
    up = int(max(0.0, now - start_f)) if start_f else 0
    dph = float(r.get("dph_total") or 0.0)
    ssh = f"root@{r['ssh_host']}:{r['ssh_port']}" if r.get("ssh_host") and r.get("ssh_port") else None
    return {"id": int(r["id"]), "label": str(r.get("label") or ""),
            "status": str(r.get("actual_status") or r.get("cur_state") or "unknown"),
            "gpu": f"{r.get('num_gpus') or 1}x {r.get('gpu_name') or 'GPU'}", "dph_total": round(dph, 4),
            "started_at": _ts_iso(start_f), "uptime_s": up, "ssh": ssh,
            "cost_so_far_usd": round(dph * up / 3600, 2)}


def _vast_offer_row(o: dict[str, Any]) -> dict[str, Any]:
    ram = o.get("gpu_ram") or 0
    return {"id": int(o["id"]), "gpu_name": str(o.get("gpu_name") or ""), "num_gpus": int(o.get("num_gpus") or 1),
            "dph_total": round(float(o.get("dph_total") or 0.0), 4),
            "gpu_ram_gb": round(float(ram) / 1024) if float(ram) > 512 else float(ram),
            "cuda_max_good": o.get("cuda_max_good"), "reliability": o.get("reliability2", o.get("reliability")),
            "geolocation": o.get("geolocation"), "inet_down_mbps": o.get("inet_down"),
            "disk_space_gb": o.get("disk_space")}


@router.get("/compute/vast/status")
def vast_status(st: StudioState = Depends(get_state)) -> dict[str, Any]:
    if st.cloud is not None:
        return st.cloud.vast_status()
    if not _vast_cli():
        return {"fake": False, "cli": False, "api_key": False, "credit_usd": None, "as_of": None,
                "error": f"The vastai CLI is not installed: {VAST_INSTALL}"}
    try:
        credit = _cloud(lambda: _vast_mod().credit(), what="vast.ai")
    except ApiError as e:
        return {"fake": False, "cli": True, "api_key": False, "credit_usd": None, "as_of": None, "error": e.message}
    except Exception as e:  # no key set, offline, ...
        return {"fake": False, "cli": True, "api_key": False, "credit_usd": None, "as_of": None,
                "error": st.redact(str(e))[:400]}
    return {"fake": False, "cli": True, "api_key": True, "credit_usd": round(float(credit), 2), "as_of": now_iso(),
            "error": None}


@router.get("/compute/vast/offers")
def vast_offers(gpu: str = Query("RTX_4090"), num_gpus: int = Query(1, ge=1, le=16),
                max_price: float = Query(0.8, gt=0, le=100), disk_gb: int = Query(80, ge=10, le=4000),
                min_gpu_ram_gb: float = Query(0, ge=0, le=1024),
                st: StudioState = Depends(get_state)) -> dict[str, Any]:
    if not GPU_RE.match(gpu):
        raise _bad("gpu", "use a vast.ai GPU name such as RTX_4090")
    if st.cloud is not None:
        return st.cloud.vast_offers(gpu=gpu, num_gpus=num_gpus, max_price=max_price, disk_gb=disk_gb,
                                    min_gpu_ram_gb=min_gpu_ram_gb)
    if not _vast_cli():
        raise _vast_unavailable()
    vast = _vast_mod()
    try:
        rows = _cloud(lambda: vast.offers(gpu, num_gpus=num_gpus, max_price=max_price, disk_gb=disk_gb,
                                          min_gpu_ram_gb=int(min_gpu_ram_gb)), what="vast.ai offer search")
    except ApiError:
        raise
    except Exception as e:
        raise _vast_error(st, e) from e
    items = sorted((_vast_offer_row(o) for o in rows if isinstance(o, dict) and "id" in o),
                   key=lambda o: (o["dph_total"], o["id"]))[:50]
    try:
        credit: float | None = round(float(_cloud(vast.credit, what="vast.ai", timeout=5.0)), 2)
    except Exception:
        credit = None
    return {"fake": False, "items": items, "credit_usd": credit, "gpu_names": GPU_NAMES}


def _vast_instances(st: StudioState) -> list[dict[str, Any]]:
    if st.cloud is not None:
        return st.cloud.vast_instances()["items"]
    if not _vast_cli():
        raise _vast_unavailable()
    try:
        rows = _cloud(lambda: _vast_mod().instances(VAST_LABEL_PREFIX), what="vast.ai")
    except ApiError:
        raise
    except Exception as e:
        raise _vast_error(st, e) from e
    now = time.time()
    return [_vast_instance_row(r, now) for r in rows if isinstance(r, dict) and "id" in r]


@router.get("/compute/vast/instances")
def vast_instances(st: StudioState = Depends(get_state)) -> dict[str, Any]:
    items = _overlay(st, _vast_instances(st), "vast")
    # vast.ai bills from creation: a machine still loading or scheduling costs money too
    burn = round(sum(float(i.get("dph_total") or 0) for i in items if i.get("status") not in VAST_NOT_BILLING), 4)
    return {"fake": st.fake_cloud, "items": items, "usd_per_hour": burn}


def _vast_destroy(st: StudioState, iid: int) -> dict[str, Any]:
    if st.cloud is not None:
        return st.cloud.vast_destroy(iid) or {"destroyed": False}
    try:
        gone = bool(_vast_mod().destroy(iid, verify=True))
    except Exception as e:
        raise _vast_error(st, e) from e
    return {"destroyed": gone}


@router.post("/compute/vast/instances/{instance_id}/destroy")
def vast_destroy(instance_id: str, body: dict[str, Any] = Body(default_factory=dict),
                 st: StudioState = Depends(get_state)) -> dict[str, Any]:
    if not instance_id.isdigit() or len(instance_id) > 12:
        raise ApiError(404, "not_found", "No such decider-lab instance on vast.ai.", detail={"what": "instance"})
    _confirm(body, instance_id, "type the instance id")
    iid = int(instance_id)
    if not any(int(i["id"]) == iid for i in _vast_instances(st)):
        raise ApiError(404, "not_found", "No such decider-lab instance on vast.ai.",
                       hint="Only machines labelled decider-lab are listed or destroyed.", detail={"what": "instance"})
    out = _vast_destroy(st, iid)
    _publish(st, "vast")
    if out.get("destroyed"):
        return {"destroyed": True}
    return {"destroyed": False,
            "message": f"Instance {iid} is still listed. Check `vastai show instances`."}


@router.post("/compute/vast/instances/destroy-all")
def vast_destroy_all(body: dict[str, Any] = Body(default_factory=dict),
                     st: StudioState = Depends(get_state)) -> dict[str, Any]:
    _confirm(body, "destroy all", "type `destroy all`")
    if st.cloud is not None:
        out = st.cloud.vast_destroy_all()
        _publish(st, "vast")
        return out
    results = []
    for it in _vast_instances(st):
        try:
            gone = _vast_destroy(st, int(it["id"]))["destroyed"]
        except ApiError:
            gone = False
        results.append({"id": int(it["id"]), "destroyed": gone})
    _publish(st, "vast")
    return {"results": results}


# ---- AWS ---------------------------------------------------------------------------------------


def aws_price(instance_type: str | None) -> float | None:
    """Static on-demand list price for an instance type, or None when it is not in the table."""
    return _PRICE.get(instance_type or "")


def _family_of(instance_type: str) -> str:
    letter = (instance_type or "x")[:1].lower()
    if letter in ("g", "p"):
        return letter
    return "standard" if letter in STANDARD_LETTERS else "other"


def _profile_region(profile: str | None, region: str | None) -> tuple[str | None, str]:
    profile = (profile or "").strip() or None
    region = (region or "").strip() or DEFAULT_REGION
    if profile is not None and not PROFILE_RE.match(profile):
        raise _bad("profile", "not a profile name")
    if not REGION_RE.match(region):
        raise _bad("region", "not an AWS region such as us-east-1")
    return profile, region


def _boto3_missing() -> ApiError:
    return ApiError(424, "backend_unavailable", "boto3 is not installed, so Studio cannot read AWS.",
                    hint=f"Install it with {AWS_INSTALL} and restart Studio.",
                    detail={"install": AWS_INSTALL, "provider": "aws"})


def _session(profile: str | None, region: str) -> Any:
    """A boto3 session for the profile (None: AWS_PROFILE or the default chain). Tests replace this."""
    if importlib.util.find_spec("boto3") is None:
        raise _boto3_missing()
    import boto3

    return boto3.Session(profile_name=profile, region_name=region)


def _client(sess: Any, name: str) -> Any:
    try:
        from botocore.config import Config

        return sess.client(name, config=Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 2}))
    except ImportError:
        return sess.client(name)


def _aws_code(e: Exception) -> str | None:
    resp = getattr(e, "response", None)
    if isinstance(resp, dict):
        return (resp.get("Error") or {}).get("Code")
    return None


def _aws_message(st: StudioState, e: Exception, profile: str | None, action: str | None = None) -> str:
    """One helpful sentence for an AWS failure; names the fix and, for denials, the IAM action."""
    name = type(e).__name__
    code = _aws_code(e) or name
    p = profile or os.environ.get("AWS_PROFILE") or "default"
    login = f"aws sso login --profile {p}"
    if name in ("ProfileNotFound",):
        return f"ProfileNotFound: the profile {p} is not in ~/.aws/config; pick another profile."
    if name in ("NoCredentialsError", "PartialCredentialsError", "CredentialRetrievalError"):
        return (f"NoCredentials: no AWS credentials for profile {p}; run aws configure --profile {p} "
                f"or {login} in a terminal.")
    if name in ("SSOTokenLoadError", "UnauthorizedSSOTokenError", "TokenRetrievalError", "SSOError") or code in (
            "ExpiredToken", "ExpiredTokenException", "UnrecognizedClientException", "InvalidClientTokenId"):
        return f"{code}: the credentials or SSO session for {p} expired or are not valid; run {login}"
    if code in ("AccessDenied", "AccessDeniedException", "UnauthorizedOperation"):
        return f"{code}: this identity is not allowed {action or 'this call'}."
    if name in ("EndpointConnectionError", "ConnectTimeoutError", "ReadTimeoutError", "ConnectionClosedError"):
        return f"{name}: could not reach AWS; check the network and the region."
    return f"{code}: {st.redact(str(e))[:300]}"


def _aws_call(st: StudioState, fn: Callable[[], T], *, profile: str | None, action: str, what: str) -> T:
    """Run a boto3 read under the deadline; failures become `cloud_error` naming the IAM action."""
    try:
        return _cloud(fn, what=what)
    except ApiError:
        raise
    except ImportError as e:
        raise _boto3_missing() from e
    except Exception as e:
        raise ApiError(502, "cloud_error", _aws_message(st, e, profile, action),
                       detail={"provider": "aws", "aws_code": _aws_code(e) or type(e).__name__,
                               "action": action}) from e


def _ini_sections(path: str) -> list[str]:
    """Section header names of an INI file; values are never read."""
    out: list[str] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = re.match(r"^\s*\[\s*([^\]]+?)\s*\]\s*$", line)
                if m:
                    out.append(m.group(1))
    except OSError:
        pass
    return out


def aws_profile_names() -> list[str]:
    home = os.path.expanduser("~")
    config = os.environ.get("AWS_CONFIG_FILE") or os.path.join(home, ".aws", "config")
    creds = os.environ.get("AWS_SHARED_CREDENTIALS_FILE") or os.path.join(home, ".aws", "credentials")
    names: list[str] = []
    for sec in _ini_sections(config):
        if sec == "default":
            names.append("default")
        elif sec.startswith("profile "):
            names.append(sec[len("profile "):].strip())
    names += _ini_sections(creds)
    return sorted({n for n in names if PROFILE_RE.match(n)}, key=lambda n: (n != "default", n.lower()))


@router.get("/compute/aws/profiles")
def aws_profiles(st: StudioState = Depends(get_state)) -> dict[str, Any]:
    env_profile = os.environ.get("AWS_PROFILE") or None
    if env_profile and not PROFILE_RE.match(env_profile):
        env_profile = None
    if st.cloud is not None:
        out = st.cloud.aws_profiles()
        out["env_profile"] = env_profile
        out["default_region"] = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or DEFAULT_REGION
        out["regions"] = REGIONS
        return out
    names = aws_profile_names()
    if env_profile and env_profile not in names:
        names.append(env_profile)
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or DEFAULT_REGION
    return {"fake": False, "items": names, "env_profile": env_profile,
            "boto3": importlib.util.find_spec("boto3") is not None,
            "default_region": region if REGION_RE.match(region) else DEFAULT_REGION, "regions": REGIONS}


REGIONS = ["us-east-1", "us-east-2", "us-west-1", "us-west-2", "ca-central-1", "eu-west-1", "eu-west-2",
           "eu-west-3", "eu-central-1", "eu-north-1", "ap-south-1", "ap-northeast-1", "ap-northeast-2",
           "ap-southeast-1", "ap-southeast-2", "sa-east-1"]


@router.get("/compute/aws/identity")
def aws_identity(profile: str | None = Query(None), region: str | None = Query(None),
                 st: StudioState = Depends(get_state)) -> dict[str, Any]:
    profile, region = _profile_region(profile, region)
    if st.cloud is not None:
        return st.cloud.aws_identity(profile=profile, region=region)
    shown = profile or os.environ.get("AWS_PROFILE") or "default"
    base = {"fake": False, "profile": shown, "region": region, "account": None, "arn": None}
    try:
        ident = _aws_call(st, lambda: _client(_session(profile, region), "sts").get_caller_identity(),
                          profile=profile, action="sts:GetCallerIdentity", what="AWS STS")
    except ApiError as e:
        if e.code == "backend_unavailable":
            raise
        return base | {"ok": False, "error": e.message,
                       "fix": f"aws sso login --profile {shown}" if e.code != "cloud_timeout" else None}
    return base | {"account": str(ident.get("Account")), "arn": str(ident.get("Arn")), "ok": True, "error": None}


def _used_vcpus(sess: Any) -> dict[str, int]:
    ec2 = _client(sess, "ec2")
    types: Counter[str] = Counter()
    pager = ec2.get_paginator("describe_instances")
    for page in pager.paginate(Filters=[{"Name": "instance-state-name", "Values": ["pending", "running"]}]):
        for r in page.get("Reservations", []):
            for inst in r.get("Instances", []):
                types[inst["InstanceType"]] += 1
    used = {"g": 0, "p": 0, "standard": 0}
    if not types:
        return used
    desc = ec2.describe_instance_types(InstanceTypes=sorted(types))["InstanceTypes"]
    vcpus = {d["InstanceType"]: int(d["VCpuInfo"]["DefaultVCpus"]) for d in desc}
    for t, n in types.items():
        fam = _family_of(t)
        if fam in used:
            used[fam] += vcpus.get(t, 0) * n
    return used


@router.get("/compute/aws/quotas")
def aws_quotas(profile: str | None = Query(None), region: str | None = Query(None),
               st: StudioState = Depends(get_state)) -> dict[str, Any]:
    profile, region = _profile_region(profile, region)
    if st.cloud is not None:
        return st.cloud.aws_quotas(profile=profile, region=region)

    def read() -> dict[str, Any]:
        sess = _session(profile, region)
        sq = _client(sess, "service-quotas")
        items = []
        denied = 0
        for fam, code, name in QUOTA_FAMILIES:
            item: dict[str, Any] = {"family": fam, "code": code, "name": name, "limit_vcpus": None,
                                    "used_vcpus": None}
            try:
                q = sq.get_service_quota(ServiceCode="ec2", QuotaCode=code)
                item["limit_vcpus"] = int(float(q["Quota"]["Value"]))
            except Exception as e:
                denied += 1
                item["error"] = _aws_message(st, e, profile, "servicequotas:GetServiceQuota")
            items.append(item)
        error = None
        try:
            used = _used_vcpus(sess)
            for it in items:
                it["used_vcpus"] = used.get(it["family"])
        except Exception as e:
            error = _aws_message(st, e, profile, "ec2:DescribeInstances") + " (vCPUs in use are unknown)"
        if denied == len(items) and error:
            error = items[0].get("error") or error
        return {"fake": False, "items": items, "error": error, "region": region}

    return _aws_call(st, read, profile=profile, action="servicequotas:GetServiceQuota", what="AWS Service Quotas")


@router.get("/compute/aws/instance-types")
def aws_instance_types() -> dict[str, Any]:
    return {"items": [dict(t) for t in AWS_INSTANCE_TYPES], "as_of": AWS_PRICES_AS_OF, "region_note": AWS_REGION_NOTE}


def _mask_ip(ip: str | None) -> str | None:
    if not ip:
        return None
    parts = ip.split(".")
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.x.x"
    return "••••"


def _aws_instance_row(inst: dict[str, Any], now: float) -> dict[str, Any]:
    tags = {t.get("Key"): t.get("Value") for t in inst.get("Tags") or []}
    launched = inst.get("LaunchTime")
    if isinstance(launched, datetime):
        lt = launched if launched.tzinfo else launched.replace(tzinfo=timezone.utc)
        launched_ts: float | None = lt.timestamp()
    else:
        try:
            launched_ts = datetime.fromisoformat(str(launched).replace("Z", "+00:00")).timestamp()
        except ValueError:
            launched_ts = None
    t = inst.get("InstanceType") or ""
    return {"id": inst["InstanceId"], "type": t, "state": (inst.get("State") or {}).get("Name", "unknown"),
            "lab": tags.get(AWS_TAG), "launched_at": iso(launched_ts),
            "uptime_s": int(max(0.0, now - launched_ts)) if launched_ts else 0, "usd_per_hour": aws_price(t),
            "public_ip_masked": _mask_ip(inst.get("PublicIpAddress"))}


def _aws_instances(st: StudioState, profile: str | None, region: str) -> list[dict[str, Any]]:
    if st.cloud is not None:
        return st.cloud.aws_instances(profile=profile, region=region)["items"]

    def read() -> list[dict[str, Any]]:
        ec2 = _client(_session(profile, region), "ec2")
        out: list[dict[str, Any]] = []
        pager = ec2.get_paginator("describe_instances")
        for page in pager.paginate(Filters=[{"Name": "tag-key", "Values": [AWS_TAG]},
                                            {"Name": "instance-state-name",
                                             "Values": ["pending", "running", "stopping", "stopped"]}]):
            for r in page.get("Reservations", []):
                out += r.get("Instances", [])
        now = time.time()
        return [_aws_instance_row(i, now) for i in out]

    return _aws_call(st, read, profile=profile, action="ec2:DescribeInstances", what="AWS EC2")


@router.get("/compute/aws/instances")
def aws_instances(profile: str | None = Query(None), region: str | None = Query(None),
                  st: StudioState = Depends(get_state)) -> dict[str, Any]:
    profile, region = _profile_region(profile, region)
    items = _overlay(st, _aws_instances(st, profile, region), "aws")
    rates = [i["usd_per_hour"] for i in items if i.get("state") in ("running", "pending")]
    burn = None if any(r is None for r in rates) else round(sum(rates), 4)
    return {"fake": st.fake_cloud, "items": items, "usd_per_hour": burn, "region": region}


def _aws_terminate(st: StudioState, ids: list[str], profile: str | None, region: str) -> list[str]:
    if not ids:
        return []

    def run() -> list[str]:
        ec2 = _client(_session(profile, region), "ec2")
        res = ec2.terminate_instances(InstanceIds=ids)
        return [x["InstanceId"] for x in res.get("TerminatingInstances", [])] or ids

    return _aws_call(st, run, profile=profile, action="ec2:TerminateInstances", what="AWS EC2")


@router.post("/compute/aws/instances/{instance_id}/terminate")
def aws_terminate(instance_id: str, body: dict[str, Any] = Body(default_factory=dict),
                  st: StudioState = Depends(get_state)) -> dict[str, Any]:
    if not AWS_ID_RE.match(instance_id):
        raise ApiError(404, "not_found", "No such decider-lab instance in this region.", detail={"what": "instance"})
    _confirm(body, instance_id, "type the instance id")
    profile, region = _profile_region(body.get("profile"), body.get("region"))
    if st.cloud is not None:
        out = st.cloud.aws_terminate(instance_id, profile=profile, region=region)
        if out is None:
            raise ApiError(404, "not_found", "No such decider-lab instance in this region.",
                           hint="Only instances tagged decider-lab are listed or terminated.", detail={"what": "instance"})
        _publish(st, "aws")
        return out
    if not any(i["id"] == instance_id for i in _aws_instances(st, profile, region)):
        raise ApiError(404, "not_found", "No such decider-lab instance in this region.",
                       hint="Only instances tagged decider-lab are listed or terminated.", detail={"what": "instance"})
    out = {"terminating": _aws_terminate(st, [instance_id], profile, region)}
    _publish(st, "aws")
    return out


@router.post("/compute/aws/instances/terminate-all")
def aws_terminate_all(body: dict[str, Any] = Body(default_factory=dict),
                      st: StudioState = Depends(get_state)) -> dict[str, Any]:
    _confirm(body, "terminate all", "type `terminate all`")
    profile, region = _profile_region(body.get("profile"), body.get("region"))
    if st.cloud is not None:
        out = st.cloud.aws_terminate_all(profile=profile, region=region)
    else:
        ids = [i["id"] for i in _aws_instances(st, profile, region) if i.get("state") != "terminated"]
        out = {"terminating": _aws_terminate(st, ids, profile, region)}
    _publish(st, "aws")
    return out


def _geo_prefix(region: str) -> str:
    if region.startswith("us-gov"):
        return "us-gov"
    return {"us": "us", "ca": "us", "eu": "eu", "ap": "apac", "sa": "us"}.get(region[:2], "us")


def _bedrock_models(sess: Any, region: str) -> list[dict[str, Any]]:
    br = _client(sess, "bedrock")
    models = br.list_foundation_models(byOutputModality="TEXT").get("modelSummaries", [])
    profiles: list[dict[str, Any]] = []
    try:
        kwargs: dict[str, Any] = {"typeEquals": "SYSTEM_DEFINED", "maxResults": 1000}
        while True:
            page = br.list_inference_profiles(**kwargs)
            profiles += page.get("inferenceProfileSummaries", [])
            if not page.get("nextToken"):
                break
            kwargs["nextToken"] = page["nextToken"]
    except Exception:  # older regions or a role without ListInferenceProfiles: on-demand ids only
        profiles = []
    geo = _geo_prefix(region)
    by_model: dict[str, list[str]] = {}
    for p in profiles:
        pid = p.get("inferenceProfileId") or ""
        for m in p.get("models") or []:
            arn = m.get("modelArn") or ""
            by_model.setdefault(arn.rsplit("/", 1)[-1], []).append(pid)
    out = []
    for m in models:
        mid = m.get("modelId") or ""
        if "TEXT" not in (m.get("outputModalities") or []):
            continue
        cands = by_model.get(mid, [])
        preferred = f"{geo}.{mid}"
        invoke = preferred if preferred in cands else (cands[0] if cands else None)
        if invoke is None and "ON_DEMAND" in (m.get("inferenceTypesSupported") or []):
            invoke = mid
        if invoke is None:  # provisioned-throughput only: not callable from a lab
            continue
        out.append({"model_id": mid, "invoke_id": invoke, "name": m.get("modelName") or mid,
                    "provider": m.get("providerName") or "", "input_modalities": m.get("inputModalities") or [],
                    "output_modalities": m.get("outputModalities") or [],
                    "status": (m.get("modelLifecycle") or {}).get("status", "ACTIVE")})
    out.sort(key=lambda x: (x["provider"].lower(), x["name"].lower()))
    return out


@router.get("/compute/aws/bedrock-models")
def aws_bedrock_models(profile: str | None = Query(None), region: str | None = Query(None),
                       q: str | None = Query(None, max_length=100),
                       st: StudioState = Depends(get_state)) -> dict[str, Any]:
    profile, region = _profile_region(profile, region)
    if st.cloud is not None:
        return st.cloud.bedrock_models(profile=profile, region=region, q=q) | {"region": region}
    try:
        items = _aws_call(st, lambda: _bedrock_models(_session(profile, region), region), profile=profile,
                          action="bedrock:ListFoundationModels", what="Amazon Bedrock")
    except ApiError as e:
        if e.code == "backend_unavailable":
            raise
        return {"fake": False, "items": [], "error": e.message, "region": region}
    if q:
        ql = q.lower()
        items = [m for m in items if ql in m["name"].lower() or ql in m["model_id"].lower()
                 or ql in m["provider"].lower()]
    for m in items:
        m["spec_yaml"] = f"{{bedrock: {m['invoke_id']}, region: {region}}}"
    return {"fake": False, "items": items, "error": None, "region": region}


# ---- fake-cloud test hook ----------------------------------------------------------------------


@router.post("/compute/fake/reset", include_in_schema=False)
def fake_reset(st: StudioState = Depends(get_state)) -> dict[str, Any]:
    """Restore the cloud fixtures (e2e specs that destroy or terminate put them back). Fake mode only."""
    if st.cloud is None:
        raise ApiError(404, "not_found", "No such API endpoint.", detail={"what": "route"})
    st.cloud.reset()
    _publish(st, "vast")
    _publish(st, "aws")
    return {"reset": True}


# ---- overview helper ---------------------------------------------------------------------------

_summary_lock = threading.Lock()


def cloud_summary(st: StudioState, *, timeout: float = 5.0, max_age_s: float = 60.0) -> dict[str, Any]:
    """Overview `kpis.cloud`: decider-lab machines, idle ones and the summed hourly rate (cached)."""
    with _summary_lock:
        cached = st.extra.get("compute.cloud_summary")
        if cached and st.cloud is None and time.time() - cached[0] < max_age_s:
            return dict(cached[1])
    items: list[dict[str, Any]] = []
    errors: list[str] = []
    known = False
    rate_ok = True
    providers: list[tuple[str, Callable[[], list[dict[str, Any]]]]] = []
    if st.cloud is not None or _vast_cli():
        providers.append(("vast", lambda: _vast_instances(st)))
    if st.cloud is not None or importlib.util.find_spec("boto3") is not None:
        providers.append(("aws", lambda: _aws_instances(st, None, DEFAULT_REGION)))
    for name, fn in providers:
        try:
            rows = _cloud(fn, what=name, timeout=timeout)
            known = True
        except ApiError as e:
            errors.append(f"{name}: {'timeout' if e.code == 'cloud_timeout' else e.code}")
            rate_ok = False
            continue
        for r in _overlay(st, rows, name):
            st_ = r.get("status", r.get("state"))
            running = st_ in ("running", "pending") if "state" in r and "status" not in r else st_ not in VAST_NOT_BILLING
            rate = r.get("dph_total") if name == "vast" else r.get("usd_per_hour")
            if running and rate is None:
                rate_ok = False
            items.append({"provider": name, "running": running, "idle": r["idle"], "rate": rate or 0.0})
    running = [i for i in items if i["running"]]
    value = {"instances": len(running), "idle_instances": sum(1 for i in running if i["idle"]),
             "usd_per_hour": round(sum(i["rate"] for i in running), 4) if known and rate_ok else None,
             "known": known, "errors": errors}
    with _summary_lock:
        st.extra["compute.cloud_summary"] = (time.time(), value)
    return dict(value)


# ---- SSH hosts ---------------------------------------------------------------------------------


class SshHostStore:
    """`<state_dir>/ssh_hosts.json`: bookmarks only (target and key path; never key material)."""

    def __init__(self, st: StudioState) -> None:
        self.st = st
        self.path = os.path.join(st.workspace.state_dir, "ssh_hosts.json")

    def _read(self) -> list[dict[str, Any]]:
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return []
        hosts = data.get("hosts") if isinstance(data, dict) else None
        return [h for h in hosts or [] if isinstance(h, dict) and HOST_ID_RE.match(str(h.get("host_id", "")))]

    def _write(self, hosts: list[dict[str, Any]]) -> None:
        self.st.workspace.ensure_state_dir()
        tmp = f"{self.path}.tmp{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"hosts": hosts}, fh, indent=2)
        os.replace(tmp, self.path)

    def list(self) -> list[dict[str, Any]]:
        return self._read()

    def get(self, host_id: str) -> dict[str, Any]:
        for h in self._read():
            if h["host_id"] == host_id:
                return h
        raise ApiError(404, "not_found", "No such SSH host.", detail={"what": "ssh_host"})

    def save(self, hosts: list[dict[str, Any]]) -> None:
        self._write(hosts)


_ssh_lock = threading.Lock()


def _store(st: StudioState) -> SshHostStore:
    return SshHostStore(st)


def _public_host(h: dict[str, Any]) -> dict[str, Any]:
    m = TARGET_RE.match(h["target"])
    user, address, port = (m.group("user"), m.group("host"), int(m.group("port") or 22)) if m else ("", "", 22)
    key = h.get("key_path") or ""
    exists = bool(key) and os.path.isfile(os.path.expanduser(key))
    return {"host_id": h["host_id"], "name": h["name"], "target": h["target"], "user": user,
            "address": address.strip("[]"), "port": port, "key_path": key, "key_exists": exists,
            "work_dir": h.get("work_dir"), "last_test": h.get("last_test")}


def _validate_host(body: dict[str, Any]) -> dict[str, Any]:
    fields = []
    name = body.get("name")
    target = body.get("target")
    key = body.get("key_path")
    work = body.get("work_dir")
    if not isinstance(name, str) or not HOST_NAME_RE.match(name.strip()):
        fields.append({"field": "name", "message": "use 1-64 letters, digits, dot, dash or underscore, "
                                                   "starting with a letter or digit"})
    m = TARGET_RE.match(target.strip()) if isinstance(target, str) else None
    if not m:
        fields.append({"field": "target", "message": "use user@host or user@host:port"})
    elif m.group("port") and not 1 <= int(m.group("port")) <= 65535:
        fields.append({"field": "target", "message": "the port must be 1-65535"})
    if key in (None, ""):
        key = ""
    elif not isinstance(key, str) or len(key) > 1024 or any(c in key for c in "\0\n\r"):
        fields.append({"field": "key_path", "message": "must be a file path"})
    if work in (None, ""):
        work = None
    elif not isinstance(work, str) or not WORK_DIR_RE.match(work):
        fields.append({"field": "work_dir", "message": "use a plain path (letters, digits, / . _ - ~)"})
    if fields:
        raise ApiError(422, "bad_request", "Some fields are not valid.", detail={"fields": fields})
    return {"name": name.strip(), "target": target.strip(), "key_path": key.strip(), "work_dir": work}


def _check_name(hosts: list[dict[str, Any]], name: str, skip: str | None = None) -> None:
    if any(h["name"].lower() == name.lower() and h["host_id"] != skip for h in hosts):
        raise ApiError(409, "name_taken", f"An SSH host named {name} already exists.", hint="Pick another name.",
                       detail={"fields": [{"field": "name", "message": "already used"}]})


@router.get("/compute/ssh/hosts")
def ssh_hosts(st: StudioState = Depends(get_state)) -> dict[str, Any]:
    return {"items": [_public_host(h) for h in _store(st).list()]}


@router.post("/compute/ssh/hosts", status_code=201)
def ssh_add(body: dict[str, Any] = Body(...), st: StudioState = Depends(get_state)) -> dict[str, Any]:
    clean = _validate_host(body)
    with _ssh_lock:
        store = _store(st)
        hosts = store.list()
        _check_name(hosts, clean["name"])
        host = {"host_id": "h_" + secrets.token_hex(4), **clean, "last_test": None}
        hosts.append(host)
        store.save(hosts)
    return _public_host(host)


def _host_id(host_id: str) -> str:
    if not HOST_ID_RE.match(host_id):
        raise ApiError(404, "not_found", "No such SSH host.", detail={"what": "ssh_host"})
    return host_id


@router.put("/compute/ssh/hosts/{host_id}")
def ssh_update(host_id: str, body: dict[str, Any] = Body(...), st: StudioState = Depends(get_state)) -> dict[str, Any]:
    _host_id(host_id)
    clean = _validate_host(body)
    with _ssh_lock:
        store = _store(st)
        hosts = store.list()
        cur = next((h for h in hosts if h["host_id"] == host_id), None)
        if cur is None:
            raise ApiError(404, "not_found", "No such SSH host.", detail={"what": "ssh_host"})
        _check_name(hosts, clean["name"], skip=host_id)
        changed = cur["target"] != clean["target"] or cur.get("key_path") != clean["key_path"]
        cur.update(clean)
        if changed:
            cur["last_test"] = None
        store.save(hosts)
    return _public_host(cur)


@router.delete("/compute/ssh/hosts/{host_id}")
def ssh_delete(host_id: str, st: StudioState = Depends(get_state)) -> dict[str, Any]:
    _host_id(host_id)
    with _ssh_lock:
        store = _store(st)
        hosts = store.list()
        left = [h for h in hosts if h["host_id"] != host_id]
        if len(left) == len(hosts):
            raise ApiError(404, "not_found", "No such SSH host.", detail={"what": "ssh_host"})
        store.save(left)
    return {"deleted": True}


def _gpu_line(out: str) -> str | None:
    rows = [x.strip() for x in out.splitlines() if x.strip() and x.strip() != "no-gpu"]
    rows = [x for x in rows if "," in x]
    if not rows:
        return None
    counts = Counter(rows)
    return "; ".join(f"{n}x {row}" for row, n in counts.items())


def _ssh_probe(h: dict[str, Any]) -> dict[str, Any]:
    from ..compute.base import Host

    pub = _public_host(h)
    host = Host(pub["address"], pub["port"], pub["user"], pub["key_path"] or None, work=h.get("work_dir"))
    t0 = time.monotonic()
    try:
        r = subprocess.run(["ssh", *host.opts, host.target, SSH_TEST_CMD], capture_output=True, text=True,
                           timeout=SSH_TIMEOUT_S, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return {"ok": False, "latency_ms": None, "gpu": None,
                "error": f"ssh: no answer from {pub['address']} within {int(SSH_TIMEOUT_S)} s (timed out)"}
    except FileNotFoundError:
        return {"ok": False, "latency_ms": None, "gpu": None, "error": "ssh is not installed on this machine."}
    ms = int((time.monotonic() - t0) * 1000)
    if r.returncode != 0:
        lines = [x.strip() for x in (r.stderr or "").splitlines() if x.strip()]
        return {"ok": False, "latency_ms": None, "gpu": None,
                "error": (lines[-1] if lines else f"ssh exited with code {r.returncode}")[:400]}
    return {"ok": True, "latency_ms": ms, "gpu": _gpu_line(r.stdout), "error": None}


@router.post("/compute/ssh/hosts/{host_id}/test")
def ssh_test(host_id: str, st: StudioState = Depends(get_state)) -> dict[str, Any]:
    _host_id(host_id)
    h = _store(st).get(host_id)
    if st.cloud is not None:
        addr = _public_host(h)["address"]
        if addr.lower().endswith(".invalid"):
            res = {"ok": False, "latency_ms": None, "gpu": None,
                   "error": f"ssh: connect to host {addr} port {_public_host(h)['port']}: Operation timed out"}
        else:
            res = {"ok": True, "latency_ms": 42, "gpu": "1x NVIDIA L40S, 46068 MiB", "error": None}
    else:
        res = _ssh_probe(h)
    res = st.redact.obj(res) | {"at": now_iso()}
    with _ssh_lock:
        store = _store(st)
        hosts = store.list()
        for x in hosts:
            if x["host_id"] == host_id:
                x["last_test"] = res
        store.save(hosts)
    return res
