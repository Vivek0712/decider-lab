"""Labs API: Lab files, templates, validation, run estimates (API.md section 5).

Owned endpoints (all under the `/api` prefix this router registers):

    GET, POST /api/labs
    POST /api/labs/import
    GET /api/templates
    GET, PUT, DELETE /api/labs/{lab_id}
    POST /api/labs/validate
    POST /api/labs/{lab_id}/duplicate
    POST /api/labs/{lab_id}/fix-secret
    POST /api/labs/{lab_id}/estimate

Validation, the summary and secret masking live in `labs_core`. `estimate()` and `run_argv()` are
shared with the jobs API (POST /api/jobs re-checks the estimate before it starts a run).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import tempfile
from importlib import resources
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from . import labs_core
from .errors import ApiError
from .state import StudioState, get_state
from .workspace import LAB_MAX_BYTES, encode_id, iso, read_lab_head

router = APIRouter(prefix="/api", tags=["labs"])

NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
TEMPLATES = {"eval": ("Evaluate", "models on suites, compared with a baseline"),
             "finetune": ("Fine-tune", "train on your rows, then evaluate")}
BACKEND_TITLE = {"local": "this machine", "ssh": "SSH", "aws": "AWS", "vast": "vast.ai"}

# AWS on-demand list prices (us-east-1, Linux) used for spend caps. The Compute area may provide
# `ui/prices.py` with the same names; this table is the fallback.
_FALLBACK_PRICES = {
    "g4dn.xlarge": 0.526, "g4dn.2xlarge": 0.752, "g5.xlarge": 1.006, "g5.2xlarge": 1.212, "g5.4xlarge": 1.624,
    "g6.xlarge": 0.805, "g6.2xlarge": 0.978, "g6.4xlarge": 1.323, "g6e.xlarge": 1.861, "g6e.2xlarge": 2.242,
    "g6e.4xlarge": 3.004, "p4d.24xlarge": 32.773, "p5.48xlarge": 98.32, "c7i.xlarge": 0.1785,
    "c7i.2xlarge": 0.357, "c7i.4xlarge": 0.714, "m7i.xlarge": 0.2016, "m7i.2xlarge": 0.4032,
}
try:  # pragma: no cover - depends on the integrated tree
    from . import prices as _prices  # type: ignore[attr-defined]

    AWS_ON_DEMAND_USD_PER_HOUR: dict[str, float] = dict(_prices.AWS_ON_DEMAND_USD_PER_HOUR)
    AWS_PRICES_AS_OF: str = str(getattr(_prices, "AS_OF", "2026-09-01"))
    AWS_VCPUS: dict[str, int] = dict(getattr(_prices, "AWS_VCPUS", {}) or {})
except Exception:  # noqa: BLE001
    AWS_ON_DEMAND_USD_PER_HOUR = dict(_FALLBACK_PRICES)
    AWS_PRICES_AS_OF = "2026-09-01"
    AWS_VCPUS = {}
_VCPU_SIZE = {"xlarge": 4, "2xlarge": 8, "4xlarge": 16, "8xlarge": 32, "12xlarge": 48, "16xlarge": 64,
              "24xlarge": 96, "48xlarge": 192, "large": 2}


def aws_vcpus(instance_type: str) -> int | None:
    if instance_type in AWS_VCPUS:
        return AWS_VCPUS[instance_type]
    return _VCPU_SIZE.get(instance_type.partition(".")[2])


def aws_quota_family(instance_type: str) -> str:
    fam = instance_type[:1].lower()
    return fam if fam in ("g", "p") else "standard"


# ---- request bodies ------------------------------------------------------------------------------


class RunOptions(BaseModel):
    backend: Literal["local", "ssh", "aws", "vast"] | None = None
    only: list[str] | None = None
    limit: int | None = Field(None, ge=1, le=1_000_000)
    max_hours: float | None = Field(None, gt=0, le=72)
    env: list[str] = Field(default_factory=list)
    keep: bool = False
    fast_kernels: bool = False
    strands_decider: str | None = Field(None, max_length=1000)
    out: str | None = Field(None, max_length=1000)
    options: dict[str, Any] = Field(default_factory=dict)


def parse_body(model: type[BaseModel], data: Any) -> Any:
    try:
        return model.model_validate(data if data is not None else {})
    except ValidationError as e:
        fields = [{"field": ".".join(str(x) for x in err.get("loc", ())) or "body", "message": err.get("msg", "")}
                  for err in e.errors()]
        raise ApiError(422, "bad_request", "The request is not valid.", detail={"fields": fields}) from e


def bad(field: str, message: str, summary: str = "The request is not valid.") -> ApiError:
    return ApiError(422, "bad_request", summary, detail={"fields": [{"field": field, "message": message}]})


# ---- lab files ---------------------------------------------------------------------------------


def lab_file(st: StudioState, lab_id: str) -> tuple[str, str]:
    """(absolute path, workspace-relative path) of an existing lab file; 404 otherwise."""
    abspath = st.workspace.decode_id(lab_id)
    if not os.path.isfile(abspath):
        raise ApiError(404, "not_found", "No such lab file.", detail={"what": "lab"})
    return abspath, st.workspace.rel(abspath)


def read_bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def etag_of(data: bytes) -> str:
    return f'"sha256:{hashlib.sha256(data).hexdigest()}"'


def atomic_write(path: str, text: str) -> None:
    mode = None
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        pass
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".studio-", suffix=".yaml", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def validate_file(st: StudioState, abspath: str, text: str | None = None) -> labs_core.Validation:
    """Validation of a lab file, cached by (path, mtime, size) when read from disk."""
    if text is None:
        try:
            s = os.stat(abspath)
            key = (abspath, s.st_mtime_ns, s.st_size)
        except OSError:
            key = None
        cache = st.extra.setdefault("labs_validation_cache", {})
        if key is not None and key in cache:
            return cache[key]
        with open(abspath, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        v = labs_core.validate_text(text, lab_dir=os.path.dirname(abspath),
                                    require_sha256=bool(st.settings.get().get("require_sha256")))
        if key is not None:
            if len(cache) > 256:
                cache.clear()
            cache[key] = v
        return v
    return labs_core.validate_text(text, lab_dir=os.path.dirname(abspath),
                                   require_sha256=bool(st.settings.get().get("require_sha256")))


def lab_name(v: labs_core.Validation, abspath: str) -> str:
    if v.summary and v.summary.get("name"):
        return v.summary["name"]
    head = read_lab_head(abspath)
    return (head or {}).get("name") or os.path.splitext(os.path.basename(abspath))[0]


def default_root(st: StudioState, abspath: str, name: str, data: dict[str, Any] | None = None) -> str:
    """The run root `decider-lab run` writes to when no --out is given (Settings runs_dir applies to Studio runs)."""
    runs_dir = st.settings.get().get("runs_dir")
    if runs_dir:
        return os.path.join(st.workspace.resolve(runs_dir), name)
    out = (data or {}).get("out") if isinstance(data, dict) else None
    return os.path.join(os.path.dirname(abspath), str(out or "runs"), name)


def _root_candidates(st: StudioState, abspath: str, name: str, data: dict[str, Any] | None) -> list[str]:
    out = (data or {}).get("out") if isinstance(data, dict) else None
    c = [os.path.join(os.path.dirname(abspath), str(out or "runs"), name)]
    runs_dir = st.settings.get().get("runs_dir")
    if runs_dir:
        c.append(os.path.join(st.workspace.resolve(runs_dir), name))
    return c


def last_run_at(st: StudioState, abspath: str, name: str, data: dict[str, Any] | None) -> tuple[str | None, str | None]:
    """(ISO time of the newest lab.json, root_id) over the lab's possible run roots."""
    best: tuple[float, str] | None = None
    for root in _root_candidates(st, abspath, name, data):
        for f in ("lab.json", "report.json"):
            p = os.path.join(root, f)
            if os.path.isfile(p):
                m = os.path.getmtime(p)
                if best is None or m > best[0]:
                    best = (m, root)
    if best is None:
        return None, None
    try:
        return iso(best[0]), st.workspace.encode_id(best[1])
    except ApiError:
        return iso(best[0]), None


def list_item(st: StudioState, abspath: str) -> dict[str, Any]:
    v = validate_file(st, abspath)
    s = v.summary or {}
    name = lab_name(v, abspath)
    rel = st.workspace.rel(abspath)
    last, _root = last_run_at(st, abspath, name, v.data)
    return {"lab_id": encode_id(rel), "name": name, "path": rel,
            "models": sum(1 for m in s.get("models", []) if m["kind"] != "finetuned"),
            "finetune": len(s.get("finetune", [])), "suites": len(s.get("suites", [])),
            "backend": (s.get("compute") or {}).get("backend", "local"), "valid": v.valid, "errors": v.errors,
            "warnings": v.warnings, "last_run_at": last, "modified_at": iso(os.path.getmtime(abspath))}


def imported_paths(st: StudioState) -> list[str]:
    return [p for p in (st.settings.flag("imported_labs", []) or []) if isinstance(p, str)]


def all_lab_files(st: StudioState) -> list[str]:
    seen: dict[str, None] = {lab.abspath: None for lab in st.workspace.labs()}
    for rel in imported_paths(st):
        try:
            p = st.workspace.resolve(rel)
        except ApiError:
            continue
        if os.path.isfile(p) and read_lab_head(p) is not None:
            seen.setdefault(p, None)
    return list(seen)


def lab_detail(st: StudioState, abspath: str, *, warning: str | None = None) -> dict[str, Any]:
    data = read_bytes(abspath)
    if len(data) > LAB_MAX_BYTES:
        raise ApiError(413, "too_large", "This lab file is larger than 1 MB.", detail={"limit_bytes": LAB_MAX_BYTES})
    text = data.decode("utf-8", errors="replace")
    v = validate_file(st, abspath)
    masked, n = labs_core.mask(text)
    rel = st.workspace.rel(abspath)
    name = lab_name(v, abspath)
    _last, root_id = last_run_at(st, abspath, name, v.data)
    out = {"lab_id": encode_id(rel), "name": name, "path": rel, "dir": st.workspace.rel(os.path.dirname(abspath)),
           "yaml": masked, "secrets_masked": n, "etag": etag_of(data), "modified_at": iso(os.path.getmtime(abspath)),
           "valid": v.valid, "problems": v.problems, "summary": v.summary, "run_root_id": root_id,
           "command": shlex.join(["decider-lab", "run", rel])}
    if warning:
        out["warning"] = warning
    return st.redact.obj(out)


def _respond(body: dict[str, Any], status: int = 200) -> JSONResponse:
    return JSONResponse(body, status_code=status, headers={"ETag": body.get("etag", "")} if "etag" in body else None)


def _changed(st: StudioState, *ids: str) -> None:
    st.bus.publish("labs.changed", {"lab_ids": list(ids)})


def active_run_job(st: StudioState, lab_id: str) -> dict[str, Any] | None:
    for j in st.jobs.list(status="active", lab_id=lab_id):
        if j["kind"] == "run":
            return j
    return None


def _template_text(tid: str) -> str:
    return (resources.files("decider_lab") / "templates" / tid / "lab.yaml").read_text(encoding="utf-8")


def set_name(text: str, name: str) -> str:
    new, n = re.subn(r"(?m)^name:[^\n]*$", f"name: {name}", text, count=1)
    return new if n else f"name: {name}\n" + text


# ---- endpoints ---------------------------------------------------------------------------------


@router.get("/labs")
def list_labs(q: str | None = Query(None, max_length=200), invalid: str | None = Query(None),
              st: StudioState = Depends(get_state)) -> dict[str, Any]:
    items = []
    for p in all_lab_files(st):
        try:
            it = list_item(st, p)
        except (OSError, ApiError):
            continue
        if q and q.lower() not in it["name"].lower() and q.lower() not in it["path"].lower():
            continue
        if invalid in ("1", "true") and it["valid"]:
            continue
        items.append(it)
    items.sort(key=lambda x: x["path"])
    return {"items": st.redact.obj(items)}


@router.get("/templates")
def templates() -> dict[str, Any]:
    return {"items": [{"id": tid, "title": t, "description": d, "lab_yaml": _template_text(tid)}
                      for tid, (t, d) in TEMPLATES.items()]}


@router.post("/labs", status_code=201)
def create_lab(body: dict[str, Any] = Body(...), st: StudioState = Depends(get_state)) -> dict[str, Any]:
    template = body.get("template", "eval")
    name = body.get("name")
    d = body.get("dir")
    fields = []
    if template not in TEMPLATES:
        fields.append({"field": "template", "message": "must be eval or finetune"})
    if not isinstance(name, str) or not NAME.match(name):
        fields.append({"field": "name", "message": "lowercase letters, digits, - and _ (start with a letter or digit)"})
    if not isinstance(d, str) or not d.strip() or os.path.isabs(d):
        fields.append({"field": "dir", "message": "a directory inside the workspace, e.g. labs/first-lab"})
    if fields:
        raise ApiError(422, "bad_request", "Some fields are not valid.", detail={"fields": fields})
    target = st.workspace.resolve(d.strip())
    if target == st.workspace.root:
        raise bad("dir", "pick a new directory, not the workspace itself")
    if os.path.exists(target) and (not os.path.isdir(target) or os.listdir(target)):
        raise ApiError(409, "dir_not_empty", f"{st.workspace.rel(target)} exists and is not empty.",
                       hint="Pick a new directory.", detail={"dir": st.workspace.rel(target)})
    src = resources.files("decider_lab") / "templates" / template
    os.makedirs(target, exist_ok=True)
    files = []
    for item in sorted(src.iterdir(), key=lambda x: x.name):
        dest = os.path.join(target, item.name)
        if item.is_dir():
            shutil.copytree(str(item), dest)
        else:
            text = item.read_text(encoding="utf-8")
            if item.name == "lab.yaml":
                text = set_name(text, name)
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write(text)
        files.append(item.name)
    lab_path = os.path.join(target, "lab.yaml")
    rel = st.workspace.rel(lab_path)
    _changed(st, encode_id(rel))
    return {"lab_id": encode_id(rel), "name": name, "path": rel, "files": files}


@router.post("/labs/import")
def import_lab(body: dict[str, Any] = Body(...), st: StudioState = Depends(get_state)) -> dict[str, Any]:
    path = body.get("path")
    if not isinstance(path, str) or not path.strip():
        raise bad("path", "a workspace-relative path to a YAML file")
    p = st.workspace.resolve(path.strip())
    if not os.path.isfile(p):
        raise ApiError(404, "not_found", "No such file in the workspace.", detail={"what": "file"})
    if read_lab_head(p) is None:
        raise ApiError(422, "not_a_lab", "This file is not a lab: it has no `models:` key.")
    rel = st.workspace.rel(p)
    paths = imported_paths(st)
    if rel not in paths:
        st.settings.set_flag("imported_labs", [*paths, rel])
    _changed(st, encode_id(rel))
    return st.redact.obj(list_item(st, p))


@router.post("/labs/validate")
def validate(body: dict[str, Any] = Body(...), st: StudioState = Depends(get_state)) -> dict[str, Any]:
    text = body.get("yaml")
    if not isinstance(text, str):
        raise bad("yaml", "the lab YAML as a string")
    if len(text.encode("utf-8")) > LAB_MAX_BYTES:
        raise ApiError(413, "too_large", "The lab is larger than 1 MB.", detail={"limit_bytes": LAB_MAX_BYTES})
    base = st.workspace.root
    lab_id = body.get("lab_id")
    if isinstance(lab_id, str) and lab_id:
        base = os.path.dirname(st.workspace.decode_id(lab_id))
    v = labs_core.validate_text(text, lab_dir=base, require_sha256=bool(st.settings.get().get("require_sha256")))
    return st.redact.obj({"valid": v.valid, "problems": v.problems, "summary": v.summary})


@router.get("/labs/{lab_id}")
def get_lab(lab_id: str, st: StudioState = Depends(get_state)) -> JSONResponse:
    abspath, _rel = lab_file(st, lab_id)
    return _respond(lab_detail(st, abspath))


@router.put("/labs/{lab_id}")
async def put_lab(lab_id: str, request: Request, if_match: str | None = Header(None),
                  st: StudioState = Depends(get_state)) -> JSONResponse:
    abspath, _rel = lab_file(st, lab_id)
    raw = await request.body()
    if len(raw) > LAB_MAX_BYTES + 4096:
        raise ApiError(413, "too_large", "The lab is larger than 1 MB.", detail={"limit_bytes": LAB_MAX_BYTES})
    try:
        body = json.loads(raw or b"{}")
    except ValueError as e:
        raise bad("body", "JSON with a `yaml` string") from e
    text = body.get("yaml") if isinstance(body, dict) else None
    if not isinstance(text, str):
        raise bad("yaml", "the lab YAML as a string")
    if len(text.encode("utf-8")) > LAB_MAX_BYTES:
        raise ApiError(413, "too_large", "The lab is larger than 1 MB.", detail={"limit_bytes": LAB_MAX_BYTES})
    if not if_match:
        raise ApiError(428, "precondition_required", "Saving needs the version you edited (If-Match).",
                       hint="Reload the lab and save again.")
    disk = read_bytes(abspath)
    current = etag_of(disk)
    if if_match.strip() != "*" and if_match.strip() != current:
        raise ApiError(409, "etag_mismatch", "lab.yaml changed on disk since you opened it.",
                       hint="Load the disk version, or overwrite it.",
                       detail={"current_etag": current, "modified_at": iso(os.path.getmtime(abspath))})
    try:
        text = labs_core.restore_placeholders(text, disk.decode("utf-8", errors="replace"))
    except labs_core.PlaceholderError as e:
        raise ApiError(422, "secret_placeholder_unknown", "A hidden value (••••) is at a key that held no secret on "
                       "disk, so Studio cannot know what to save there.",
                       hint="Type the value as an environment variable reference instead.",
                       detail={"paths": e.paths}) from e
    atomic_write(abspath, text)
    j = active_run_job(st, lab_id)
    warning = "A run of this lab is in progress; it uses the version it started with." if j else None
    _changed(st, lab_id)
    return _respond(lab_detail(st, abspath, warning=warning))


@router.post("/labs/{lab_id}/fix-secret")
def fix_secret(lab_id: str, body: dict[str, Any] = Body(...), if_match: str | None = Header(None),
               st: StudioState = Depends(get_state)) -> JSONResponse:
    abspath, _rel = lab_file(st, lab_id)
    path, env_name = body.get("path"), body.get("env_name")
    if not isinstance(env_name, str) or not ENV_NAME.match(env_name):
        raise bad("env_name", "use A-Z, 0-9 and _ (for example NOVA_API_KEY)")
    if not isinstance(path, str) or not path:
        raise bad("path", "the key path of the secret, e.g. models.nova.api_key")
    disk = read_bytes(abspath)
    if if_match and if_match.strip() not in ("*", etag_of(disk)):
        raise ApiError(409, "etag_mismatch", "lab.yaml changed on disk since you opened it.",
                       detail={"current_etag": etag_of(disk), "modified_at": iso(os.path.getmtime(abspath))})
    try:
        text = labs_core.fix_secret(disk.decode("utf-8", errors="replace"), path, env_name)
    except (KeyError, ValueError, AssertionError) as e:
        raise ApiError(404, "not_found", "There is no secret value at this key to move.",
                       detail={"what": "secret"}) from e
    except Exception as e:  # noqa: BLE001 - e.g. the file no longer parses
        raise ApiError(404, "not_found", "There is no secret value at this key to move.",
                       detail={"what": "secret"}) from e
    atomic_write(abspath, text)
    _changed(st, lab_id)
    return _respond(lab_detail(st, abspath))


@router.delete("/labs/{lab_id}")
def delete_lab(lab_id: str, body: dict[str, Any] | None = Body(None), st: StudioState = Depends(get_state)) -> dict:
    abspath, rel = lab_file(st, lab_id)
    name = lab_name(validate_file(st, abspath), abspath)
    if not isinstance(body, dict) or body.get("confirm") != name:
        raise ApiError(409, "confirm_mismatch", "Type the lab name to delete its file.",
                       detail={"expected_hint": "type the lab name"})
    j = active_run_job(st, lab_id)
    if j:
        raise ApiError(409, "job_active", "This lab is running; cancel the job first.", detail={"job_id": j["job_id"]})
    os.remove(abspath)
    paths = imported_paths(st)
    if rel in paths:
        st.settings.set_flag("imported_labs", [p for p in paths if p != rel])
    _changed(st, lab_id)
    return {"deleted": True}


@router.post("/labs/{lab_id}/duplicate", status_code=201)
def duplicate_lab(lab_id: str, body: dict[str, Any] = Body(...), st: StudioState = Depends(get_state)) -> dict:
    abspath, _rel = lab_file(st, lab_id)
    name, path = body.get("name"), body.get("path")
    fields = []
    if not isinstance(name, str) or not NAME.match(name):
        fields.append({"field": "name", "message": "lowercase letters, digits, - and _"})
    if not isinstance(path, str) or not path.endswith((".yaml", ".yml")) or os.path.isabs(path):
        fields.append({"field": "path", "message": "a workspace-relative .yaml path"})
    if fields:
        raise ApiError(422, "bad_request", "Some fields are not valid.", detail={"fields": fields})
    target = st.workspace.resolve(path)
    if os.path.exists(target):
        raise ApiError(409, "file_exists", f"{st.workspace.rel(target)} already exists.",
                       detail={"path": st.workspace.rel(target)})
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(abspath, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(set_name(text, name))
    _changed(st, encode_id(st.workspace.rel(target)))
    return st.redact.obj(list_item(st, target))


@router.post("/labs/{lab_id}/estimate")
def estimate_endpoint(lab_id: str, body: dict[str, Any] = Body(...), st: StudioState = Depends(get_state)) -> dict:
    abspath, _rel = lab_file(st, lab_id)
    opts = parse_body(RunOptions, body)
    est = estimate(st, abspath, opts)
    est.pop("options", None)
    return st.redact.obj(est)


# ---- run plan, cost and the command ------------------------------------------------------------


def _ssh_hosts(st: StudioState) -> list[dict[str, Any]]:
    p = os.path.join(st.workspace.state_dir, "ssh_hosts.json")
    try:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    items = data.get("items", data) if isinstance(data, dict) else data
    return [h for h in items if isinstance(h, dict)] if isinstance(items, list) else []


def merged_options(st: StudioState, v: labs_core.Validation, backend: str, opts: RunOptions) -> dict[str, Any]:
    """The backend's options: the lab's compute.<backend> section, then the dialog's fields."""
    lab_compute = (v.data or {}).get("compute") if isinstance(v.data, dict) else None
    out: dict[str, Any] = {}
    if isinstance(lab_compute, dict) and isinstance(lab_compute.get(backend), dict):
        out.update(lab_compute[backend])
    for k, val in (opts.options or {}).items():
        if val is None or val == "":
            continue
        out[k] = val
    if backend == "ssh" and out.get("host_id"):
        host = next((h for h in _ssh_hosts(st) if h.get("host_id") == out["host_id"]), None)
        if host:
            out["host"] = host.get("target") or f"{host.get('user', 'root')}@{host.get('address')}:{host.get('port', 22)}"
            if host.get("key_path") and not out.get("key"):
                out["key"] = host["key_path"]
        out.pop("host_id", None)
    return out


FLAGS = {"vast": [("gpu", "--gpu"), ("num_gpus", "--num-gpus"), ("max_price", "--max-price"), ("disk_gb", "--disk"),
                  ("offer", "--offer"), ("ssh_key", "--ssh-key")],
         "aws": [("instance_type", "--instance-type"), ("region", "--region"), ("profile", "--profile"),
                 ("disk_gb", "--disk")],
         "ssh": [("host", "--host"), ("key", "--ssh-key")], "local": []}


def run_args(lab_arg: str, backend: str, opts: RunOptions, options: dict[str, Any], out_abs: str | None,
             max_hours: float) -> list[str]:
    """`decider-lab run` arguments (after `decider-lab`) for these options."""
    args = ["run", lab_arg, "--on", backend]
    if opts.only:
        args += ["--only", *opts.only]
    if opts.limit:
        args += ["--limit", str(opts.limit)]
    if out_abs:
        args += ["--out", out_abs]
    if backend != "local":
        args += ["--max-hours", f"{max_hours:g}"]
        for key, flag in FLAGS[backend]:
            val = options.get(key)
            if val is not None and val != "":
                args += [flag, str(val)]
        if opts.env:
            args += ["--env", *opts.env]
        if opts.keep:
            args.append("--keep")
        if opts.fast_kernels:
            args.append("--fast-kernels")
        if opts.strands_decider:
            args += ["--strands-decider", opts.strands_decider]
    return args


def target_root(st: StudioState, abspath: str, v: labs_core.Validation, opts: RunOptions) -> tuple[str, str | None]:
    """(absolute run root, absolute --out or None)."""
    if opts.out:
        out = st.workspace.resolve(opts.out)
        return out, out
    name = lab_name(v, abspath)
    if st.settings.get().get("runs_dir"):
        root = default_root(st, abspath, name, v.data)
        return root, root
    return default_root(st, abspath, name, v.data), None


def _rows_reused(root: str, models: list[str]) -> int:
    n = 0
    for m in models:
        md = os.path.join(root, m)
        if not os.path.isdir(md):
            continue
        for s in os.listdir(md):
            p = os.path.join(md, s, "predictions.jsonl")
            if not os.path.isfile(p):
                continue
            with open(p, encoding="utf-8", errors="replace") as fh:
                n += sum(1 for line in fh if line.strip() and ('"error": null' in line or '"error":null' in line))
    return n


def _num(v: Any, cast: type, default: Any) -> Any:
    try:
        return cast(v) if v is not None and v != "" else default
    except (TypeError, ValueError):
        return default


def estimate(st: StudioState, abspath: str, opts: RunOptions) -> dict[str, Any]:
    v = validate_file(st, abspath)
    s = v.summary or {"models": [], "suites": [], "compute": {"backend": "local", "max_hours": 2.0}, "calibrate": False}
    backend = opts.backend or s["compute"]["backend"]
    max_hours = float(opts.max_hours or s["compute"].get("max_hours") or 2.0)
    blockers: list[str] = []
    warnings: list[str] = []
    if v.errors:
        blockers.append(f"lab.yaml has {v.errors} error{'s' if v.errors != 1 else ''}")
    names = [m["name"] for m in s["models"]]
    if opts.only:
        unknown = [m for m in opts.only if m not in names]
        if unknown:
            blockers.append("not models of this lab: " + ", ".join(unknown))
    selected = [m for m in names if not opts.only or m in opts.only]
    if opts.only is not None and not opts.only:
        blockers.append("select at least one model")
    suites = s["suites"]
    rows = [x["rows_estimate"] for x in suites]
    per_model = None if any(r is None for r in rows) else sum(rows)  # type: ignore[arg-type]
    if per_model is not None and opts.limit:
        per_model = sum(min(r, opts.limit * 3) for r in rows)  # type: ignore[type-var]
    cal = sum(1 for x in suites if x["has_splits"] is not False) if s.get("calibrate") else 0
    plan = {"models": selected, "suites": [x["name"] for x in suites], "runs": len(selected) * len(suites),
            "calibrated_runs_max": len(selected) * cal,
            "requests_estimate": None if per_model is None else per_model * len(selected)}
    root, out_abs = target_root(st, abspath, v, opts)
    resume = {"root": st.workspace.rel(root) if root.startswith(st.workspace.root) else root,
              "exists": os.path.isdir(root), "rows_reused": None}
    if backend == "local" and os.path.isdir(root):
        resume["rows_reused"] = _rows_reused(root, selected)
    elif backend == "local":
        resume["rows_reused"] = 0
    paid = [{"model": m["name"], "kind": m["kind"], "provider": m.get("provider") or m["kind"],
             "requests_estimate": per_model} for m in s["models"] if m["name"] in selected and m.get("paid_api")]
    for name in opts.env:
        if not ENV_NAME.match(name):
            blockers.append(f"{name[:40]} is not an environment variable name")
        elif name not in os.environ:
            warnings.append(f"{name} is not set; it will not be passed")
    options = merged_options(st, v, backend, opts)
    cost: dict[str, Any] = {"billable": False, "rate_usd_per_hour": None, "rate_source": None, "cap_usd_per_hour": None,
                            "max_hours": max_hours, "cap_usd": None, "credit_usd": None, "note": None}
    needs_gpu = [m["name"] for m in s["models"] if m["name"] in selected and m.get("needs_gpu")]
    if backend == "vast":
        _vast_cost(st, options, max_hours, cost, blockers, warnings, needs_gpu)
    elif backend == "aws":
        _aws_cost(st, options, max_hours, cost, blockers, warnings, needs_gpu)
    elif backend == "ssh":
        if not options.get("host"):
            blockers.append("pick an SSH host or type user@host[:port]")
        if options.get("key") and not os.path.exists(os.path.expanduser(str(options["key"]))):
            warnings.append(f"ssh key {options['key']} does not exist on this machine")
    if opts.keep and backend in ("vast", "aws"):
        rate = cost.get("rate_usd_per_hour")
        warnings.append("Keep machine is on: the machine keeps billing after the run until you destroy it in Compute")
        cost["note"] = (f"No cap: the machine keeps billing {f'${rate:.3f}/h' if rate else 'its hourly rate'} after "
                        "the run until you destroy it in Compute.")
    phrase = None
    if backend in ("vast", "aws"):
        if cost["cap_usd"] is not None:
            phrase = f"spend {cost['cap_usd']:.2f} on {backend}" + (" and keep the machine" if opts.keep else "")
    elif paid:
        phrase = "call paid apis"
    rel_lab = st.workspace.rel(abspath)
    command = shlex.join(["decider-lab", *run_args(rel_lab, backend, opts, options, out_abs and
                                                   (st.workspace.rel(out_abs) if out_abs.startswith(st.workspace.root)
                                                    else out_abs), max_hours)])
    return {"backend": backend, "can_start": not blockers, "blockers": blockers, "warnings": warnings, "plan": plan,
            "resume": resume, "paid_models": paid, "cost": cost, "confirm_phrase": phrase, "command": command,
            "options": options}


REMOTE_NOTE = ("This rents a machine that bills until it is destroyed. decider-lab destroys it when the run ends, "
               "fails, is cancelled, or reaches max hours.")


def _vast_cost(st: StudioState, o: dict[str, Any], max_hours: float, cost: dict[str, Any], blockers: list[str],
               warnings: list[str], needs_gpu: list[str]) -> None:
    gpu = str(o.get("gpu") or "RTX_4090")
    num = _num(o.get("num_gpus"), int, 1)
    max_price = _num(o.get("max_price"), float, 0.8)
    disk = _num(o.get("disk_gb"), int, 80)
    offer_id = _num(o.get("offer"), int, None)
    cost.update(billable=True, cap_usd_per_hour=max_price, cap_usd=round(max_price * max_hours, 2), note=REMOTE_NOTE)
    offers: list[dict[str, Any]] = []
    credit = None
    try:
        if st.cloud is not None:
            res = st.cloud.vast_offers(gpu=gpu, num_gpus=num, max_price=max_price, disk_gb=disk)
            offers, credit = res["items"], res["credit_usd"]
        else:
            if not shutil.which("vastai"):
                blockers.append("the vastai CLI is not installed or has no API key (pip install vastai; vastai set "
                                "api-key ...)")
                return
            from ..compute import vast

            offers = [{**x, "gpu_ram_gb": round(float(x.get("gpu_ram", 0)) / 1024)} for x in
                      vast.offers(gpu, num_gpus=num, max_price=max_price, disk_gb=disk)]
            credit = float(vast.credit())
    except Exception as e:  # noqa: BLE001 - a failed cloud lookup is a blocker, not an HTTP error
        blockers.append(f"could not read vast.ai offers: {type(e).__name__}: {str(e)[:200]}")
        return
    if offer_id is not None:
        offers = [x for x in offers if int(x["id"]) == offer_id]
    if not offers:
        blockers.append(f"no vast.ai offer for {num}x {gpu} under ${max_price:.2f}/h"
                        + (f" with id {offer_id}" if offer_id else ""))
    else:
        o0 = offers[0]
        cost["rate_usd_per_hour"] = float(o0["dph_total"])
        cost["rate_source"] = (f"{'offer' if offer_id else 'cheapest matching vast.ai offer'} {o0['id']} "
                               f"({o0.get('num_gpus', num)}x {o0.get('gpu_name', gpu)} {o0.get('gpu_ram_gb', '?')} GB, "
                               f"{o0.get('geolocation', '?')})")
        if needs_gpu:
            warnings.append(f"{', '.join(needs_gpu)} {'runs' if len(needs_gpu) == 1 else 'run'} with `serve` and "
                            f"{'needs' if len(needs_gpu) == 1 else 'need'} a GPU: the {gpu} has "
                            f"{o0.get('gpu_ram_gb', '?')} GB")
    cost["credit_usd"] = credit
    if credit is not None and credit < cost["cap_usd"] + 0.5:
        blockers.append(f"credit ${credit:.2f} does not cover the ${cost['cap_usd']:.2f} cap (+$0.50 margin)")


def _aws_cost(st: StudioState, o: dict[str, Any], max_hours: float, cost: dict[str, Any], blockers: list[str],
              warnings: list[str], needs_gpu: list[str]) -> None:
    itype = str(o.get("instance_type") or "g6e.xlarge")
    region = str(o.get("region") or "us-east-1")
    profile = o.get("profile")
    rate = AWS_ON_DEMAND_USD_PER_HOUR.get(itype)
    cost.update(billable=True, note=REMOTE_NOTE)
    if rate is None:
        blockers.append(f"no price known for {itype}; pick a listed type")
    else:
        cost["rate_usd_per_hour"] = rate
        cost["cap_usd_per_hour"] = rate
        cost["rate_source"] = f"on-demand list price for {itype} (us-east-1, table dated {AWS_PRICES_AS_OF})"
        cost["cap_usd"] = round(rate * (max_hours + 0.25), 2)
    if region != "us-east-1":
        warnings.append("price table is for us-east-1")
    if needs_gpu and aws_quota_family(itype) == "standard":
        warnings.append(f"{', '.join(needs_gpu)} need{'s' if len(needs_gpu) == 1 else ''} a GPU; {itype} has none")
    vcpus = aws_vcpus(itype)
    try:
        if st.cloud is not None:
            quotas = st.cloud.aws_quotas(profile=profile, region=region)["items"]
            fam = aws_quota_family(itype)
            q = next((x for x in quotas if x["family"] == fam), None)
            if q and vcpus is not None and q.get("limit_vcpus") is not None:
                free = int(q["limit_vcpus"]) - int(q.get("used_vcpus") or 0)
                if free < vcpus:
                    label = fam.upper() + "-instance" if fam != "standard" else "Standard-instance"
                    blockers.append(f"{label} vCPU quota is {q['limit_vcpus']} in {region} ({free} free, {itype} needs "
                                    f"{vcpus}): request {q['code']}")
        else:
            import importlib.util

            if importlib.util.find_spec("boto3") is None:
                blockers.append("boto3 is not installed: pip install 'decider-lab[aws]'")
                return
            import boto3

            if boto3.Session(profile_name=profile or None).get_credentials() is None:
                blockers.append(f"no AWS credentials for profile {profile or 'default'}")
            warnings.append("vCPU quota is checked when the run starts")
    except Exception as e:  # noqa: BLE001
        blockers.append(f"could not check AWS: {type(e).__name__}: {str(e)[:200]}")
