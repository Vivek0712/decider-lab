"""Data API: suites and the data tools (API.md section 9).

    GET  /api/suites                     built-ins (smoke, synthetic, heldout) and workspace JSONL files
    GET  /api/suites/{ref}/stats         data.stats + sha256 (202 + suite_build job for an unbuilt heldout)
    GET  /api/suites/{ref}/rows          paged rows with kind/split/task filters and search
    POST /api/data/export-suite          CLI `suites --build --out`
    POST /api/data/csv/preview           multipart upload, per-row errors, first 20 converted rows
    POST /api/data/csv/convert           CLI `data from-csv` (data.from_csv)
    POST /api/data/generate              CLI `data generate`
    POST /api/data/split                 CLI `data split`
    POST /api/data/check                 CLI `data check` / `data stats`
    POST /api/data/leakcheck             CLI `data leakcheck [--drop-to]`
    GET  /api/files/raw                  images referenced by rows (png, jpg, gif, webp; sniffed)

Suite refs: `smoke`, `synthetic`, `synthetic:per_kind=100,seed=3,families=arithmetic+calendar`,
`heldout`, or `file:<file_id>` (file ids are base64url workspace-relative paths). Every write
refuses to overwrite an existing file unless `overwrite` is true (`409 file_exists`).
"""

from __future__ import annotations

import csv
import importlib.util
import json
import os
import re
import secrets
import threading
import time
from collections import Counter
from typing import Any

import yaml
from fastapi import APIRouter, Body, Depends, File, Form, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from .. import data as sdk_data
from .. import rows as sdk_rows
from .. import suites
from ..suites import generators
from .errors import ApiError
from .jobs import JobManager
from .state import StudioState, get_state
from .workspace import IGNORED_DIRS, iso

router = APIRouter(prefix="/api", tags=["data"])

BUILTINS = ("smoke", "synthetic", "heldout")
FILE_MAX_DEPTH = 5
FILE_VALIDATE_MAX = 200 * 1024 * 1024
CSV_MAX = 50 * 1024 * 1024
UPLOAD_TTL_S = 24 * 3600
STATE_MAX = 2000
SYNC_GENERATE_MAX = 30_000
UPLOAD_ID = re.compile(r"^u_[0-9a-f]{8}$")
DELIMITERS = {",": ",", ";": ";", "\t": "\t", "tab": "\t", "|": "|"}
CSV_COLUMNS = ("state", "question", "answer", "options", "kind", "task")
CSV_REQUIRED = ("state", "question", "answer")
DESCRIPTIONS = {
    "smoke": "90 generated rows (arithmetic, calendar, seating × 3 kinds × 10). Seconds to run; proves the "
             "plumbing, not the model. No downloads.",
    "synthetic": "Generated families, every gold label computed by a program, balanced labels, dev/test splits.",
    "heldout": "Public datasets no Strands Decider trains on (StrategyQA, CommonsenseQA 2.0, CommonsenseQA, "
               "ARC-Challenge, HellaSwag, STS-B) at pinned revisions, plus the synthetic families.",
}
_lock = threading.Lock()


def _bad(field: str, message: str, text: str | None = None) -> ApiError:
    return ApiError(422, "bad_request", text or "The request is not valid.",
                    detail={"fields": [{"field": field, "message": message}]})


# ---- suite refs ----------------------------------------------------------------------------------


def _chess_available() -> bool:
    return importlib.util.find_spec("chess") is not None


def _heldout_available() -> bool:
    return importlib.util.find_spec("datasets") is not None


def _suite_cache_path(name: str, params: dict[str, Any]) -> str:
    import hashlib

    key = hashlib.sha256(json.dumps([name, params], sort_keys=True).encode()).hexdigest()[:12]
    return os.path.join(suites.CACHE, "suites", f"{name}-{key}.jsonl")


def parse_builtin(ref: str) -> tuple[str, dict[str, Any]]:
    """('synthetic', {'per_kind': 100, ...}) for a built-in ref; params as the SDK fills them."""
    base, _, args = ref.partition(":")
    if base not in BUILTINS:
        raise ApiError(404, "not_found", f"No suite named {base}.", detail={"what": "suite"})
    params: dict[str, Any] = {}
    for kv in filter(None, args.split(",")):
        k, _, v = kv.partition("=")
        k = k.strip()
        if k == "families":
            params[k] = [x for x in v.split("+") if x]
        elif v.lstrip("-").isdigit():
            params[k] = int(v)
        else:
            raise _bad("ref", f"{k}={v} is not a number", f"The suite parameter {k} is not valid.")
    if base == "smoke":
        return base, {"per_kind": 10, "families": list(suites.DEFAULT_FAMILIES), "seed": 1234}
    if base == "synthetic":
        unknown = set(params) - {"per_kind", "seed", "families"}
        if unknown:
            raise _bad("ref", f"unknown parameters {sorted(unknown)}", "This suite parameter is not known.")
        merged = {"per_kind": 150, "families": list(suites.DEFAULT_FAMILIES), "seed": 0, **params}
        if not 1 <= int(merged["per_kind"]) <= 5000:
            raise _bad("per_kind", "must be 1 to 5000", "per_kind must be from 1 to 5,000.")
        bad = sorted(set(merged["families"]) - set(generators.FAMILIES))
        if bad or not merged["families"]:
            raise _bad("families", f"unknown families {bad}", "Pick families from the list.")
        return base, merged
    return base, {"seed": 0, **params}


def _file_path(st: StudioState, file_id: str) -> str:
    path = st.workspace.decode_id(file_id)
    if not path.endswith((".jsonl", ".jsonl.gz")):
        raise ApiError(404, "not_found", "No such data file.", detail={"what": "file"})
    if not os.path.isfile(path):
        raise ApiError(404, "not_found", "No such data file.", detail={"what": "file"})
    return path


def load_ref(st: StudioState, ref: str) -> tuple[str, list[dict[str, Any]], dict[str, Any], str | None]:
    """(name, checked rows, params, abspath for files) for a suite ref. Raises ApiError."""
    if ref.startswith("file:"):
        path = _file_path(st, ref[5:])
        if os.path.getsize(path) > FILE_VALIDATE_MAX:
            raise ApiError(413, "too_large", "This file is too large to inspect here (over 200 MB).",
                           detail={"limit_bytes": FILE_VALIDATE_MAX})
        try:
            name, rows, params = suites.load_suite({"file": path})
        except (ValueError, OSError) as e:
            raise ApiError(422, "rows_invalid", "This file has rows that are not valid.",
                           detail={"problem": _first_problem(str(e))}) from e
        return name, rows, {"file": st.workspace.rel(path)}, path
    base, params = parse_builtin(ref)
    if base == "heldout" and not _heldout_available():
        raise ApiError(422, "suite_unavailable", "The heldout suite needs the heldout extra.",
                       hint="pip install 'decider-lab[heldout]'", detail={"extra": "heldout"})
    if base == "synthetic" and "chess" in params["families"] and not _chess_available():
        raise ApiError(422, "suite_unavailable", "The chess family needs the chess extra.",
                       hint="pip install 'decider-lab[chess]'", detail={"extra": "chess"})
    spec = {base: {k: v for k, v in params.items() if base != "smoke"}}
    try:
        name, rows, params = suites.load_suite(spec)
    except ImportError as e:
        raise ApiError(422, "suite_unavailable", str(e).split(":")[0] + ".", hint=str(e)) from e
    except ValueError as e:
        raise ApiError(422, "rows_invalid", "This suite could not be built.", detail={"problem": str(e)[:500]}) from e
    return name, rows, params, None


def _first_problem(msg: str) -> str:
    lines = [x.strip() for x in msg.splitlines() if x.strip()]
    if len(lines) >= 2 and lines[0].startswith("invalid rows"):
        text = lines[1]
    else:
        text = lines[0] if lines else msg
    # "<abs path>: row 17: ..." -> "row 17: ..." (paths stay out of messages)
    m = re.search(r"(row \d+: .*)", text)
    if m:
        return m.group(1)[:300]
    m = re.search(r":(\d+): (.*)", text)
    if m:
        return f"line {m.group(1)}: {m.group(2)}"[:300]
    return text[:300]


def _stats_cache(st: StudioState) -> dict[str, Any]:
    return st.extra.setdefault("data.stats", {})


def _cache_key(st: StudioState, ref: str) -> tuple[Any, ...]:
    if ref.startswith("file:"):
        path = _file_path(st, ref[5:])
        s = os.stat(path)
        return (ref, s.st_mtime_ns, s.st_size)
    return (ref, suites.CACHE)


def suite_stats(st: StudioState, ref: str) -> dict[str, Any]:
    key = _cache_key(st, ref)
    memo = _stats_cache(st).get(key)
    if memo:
        return memo
    name, rows, params, _path = load_ref(st, ref)
    out = {"ref": ref, "name": name, "params": params, "sha256": sdk_rows.rows_sha256(rows), **sdk_data.stats(rows)}
    out["by_task"] = dict(Counter(str(r.get("task")) for r in rows).most_common(30))
    with _lock:
        _stats_cache(st)[key] = out
    return out


# ---- workspace files -----------------------------------------------------------------------------


def _walk_files(st: StudioState) -> list[str]:
    root = st.workspace.root
    base = root.rstrip(os.sep).count(os.sep)
    skip = IGNORED_DIRS | {"runs"}
    out = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        depth = dirpath.rstrip(os.sep).count(os.sep) - base
        dirnames[:] = sorted(d for d in dirnames if d not in skip and not d.startswith("."))
        if depth >= FILE_MAX_DEPTH:
            dirnames[:] = []
        for f in sorted(filenames):
            if f.endswith((".jsonl", ".jsonl.gz")):
                out.append(os.path.join(dirpath, f))
    return out


def _check_file(path: str) -> tuple[int | None, bool | None, str | None]:
    size = os.path.getsize(path)
    if size > FILE_VALIDATE_MAX:
        return None, None, "over 200 MB: not checked here; use decider-lab data check"
    n = 0
    try:
        bad: str | None = None
        for i, row in enumerate(sdk_rows.read_jsonl(path)):
            n += 1
            if bad is None:
                p = sdk_rows.problems(row) if isinstance(row, dict) else ["not a JSON object"]
                if p:
                    bad = f"row {i}: {p[0]}"
        return n, bad is None, bad
    except (ValueError, OSError, EOFError) as e:
        return (n or None), False, _first_problem(str(e))


def workspace_files(st: StudioState) -> list[dict[str, Any]]:
    memo = st.extra.setdefault("data.files", {})
    out = []
    for path in _walk_files(st):
        try:
            s = os.stat(path)
        except OSError:
            continue
        key = (path, s.st_mtime_ns, s.st_size)
        if key not in memo:
            memo[key] = _check_file(path)
        rows, valid, problem = memo[key]
        rel = st.workspace.rel(path)
        fid = st.workspace.encode_id(rel)
        out.append({"file_id": fid, "ref": f"file:{fid}", "path": rel, "rows": rows, "valid": valid,
                    "problem": problem, "modified_at": iso(s.st_mtime), "size_bytes": s.st_size})
    return out


def lab_suite_refs(st: StudioState) -> list[str]:
    """Suite refs used by the workspace's labs (for the Generate tab's default exclusions)."""
    refs: list[str] = []
    for lab in st.workspace.labs():
        try:
            with open(lab.abspath, encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(data, dict):
            continue
        for spec in data.get("suites") or ["smoke"]:
            ref = _spec_ref(st, spec, os.path.dirname(lab.abspath))
            if ref and ref not in refs:
                refs.append(ref)
    return refs


def _spec_ref(st: StudioState, spec: Any, base_dir: str) -> str | None:
    if isinstance(spec, str):
        base = spec.partition(":")[0]
        if base in BUILTINS:
            return spec
        path = spec if os.path.isabs(spec) else os.path.join(base_dir, spec)
        try:
            return "file:" + st.workspace.encode_id(st.workspace.rel(path)) if os.path.isfile(path) else None
        except ApiError:
            return None
    if isinstance(spec, dict):
        if "file" in spec:
            return _spec_ref(st, str(spec["file"]), base_dir)
        for k, v in spec.items():
            if k in BUILTINS:
                params = v or {}
                if k == "smoke" or not params:
                    return k
                parts = []
                for pk, pv in params.items():
                    parts.append(f"{pk}={'+'.join(pv) if isinstance(pv, list) else pv}")
                return f"{k}:{','.join(parts)}"
    return None


@router.get("/suites")
def list_suites(st: StudioState = Depends(get_state)) -> dict[str, Any]:
    chess = _chess_available()
    held = _heldout_available()
    smoke_params = {"per_kind": 10, "families": list(suites.DEFAULT_FAMILIES), "seed": 1234}
    synth_params = {"per_kind": 150, "families": list(suites.DEFAULT_FAMILIES), "seed": 0}
    held_params = {"seed": 0}
    builtins = [
        {"ref": "smoke", "name": "smoke", "description": DESCRIPTIONS["smoke"], "params": {}, "param_schema": {},
         "available": True, "reason": None, "rows_estimate": 90,
         "cached": os.path.exists(_suite_cache_path("smoke", smoke_params))},
        {"ref": "synthetic", "name": "synthetic", "description": DESCRIPTIONS["synthetic"],
         "params": {"per_kind": 150, "seed": 0, "families": list(suites.DEFAULT_FAMILIES)},
         "param_schema": {"per_kind": {"type": "int", "min": 1, "max": 5000}, "seed": {"type": "int"},
                          "families": {"type": "multi", "options": sorted(generators.FAMILIES),
                                       "unavailable": {} if chess else {"chess": "needs the chess extra"}}},
         "available": True, "reason": None, "rows_estimate": 150 * 3 * 3,
         "cached": os.path.exists(_suite_cache_path("synthetic", synth_params))},
        {"ref": "heldout", "name": "heldout", "description": DESCRIPTIONS["heldout"], "params": {},
         "param_schema": {}, "available": held,
         "reason": None if held else "needs the heldout extra: pip install 'decider-lab[heldout]'",
         "rows_estimate": None, "cached": os.path.exists(_suite_cache_path("heldout", held_params))},
    ]
    return {"builtins": builtins, "files": workspace_files(st), "used_by_labs": lab_suite_refs(st)}


@router.get("/suites/{ref}/stats")
def get_suite_stats(ref: str, st: StudioState = Depends(get_state)) -> Any:
    if ref.partition(":")[0] == "heldout":
        _base, params = parse_builtin(ref)
        if not _heldout_available():
            raise ApiError(422, "suite_unavailable", "The heldout suite needs the heldout extra.",
                           hint="pip install 'decider-lab[heldout]'", detail={"extra": "heldout"})
        if not os.path.exists(_suite_cache_path("heldout", params)):
            job = st.jobs.start("suite_build", JobManager.cli_argv("suites", "--build", ref),
                                title=f"build suite {ref}", cwd=st.workspace.root, options={"suite": ref})
            return JSONResponse({"job_id": job["job_id"]}, status_code=202)
    return suite_stats(st, ref)


def _row_matches(r: dict[str, Any], kind: str | None, split: str | None, task: str | None, q: str | None) -> bool:
    if kind and r.get("kind") != kind:
        return False
    if split and str(r.get("split")) != split:
        return False
    if task and str(r.get("task")) != task:
        return False
    if q:
        state = r.get("state")
        hay = " ".join([state if isinstance(state, str) else json.dumps(state, ensure_ascii=False),
                        str(r.get("instructions") or ""), str(r.get("id") or ""), str(r.get("task") or ""),
                        " ".join(o[1] for o in r.get("options") or [] if isinstance(o, list) and len(o) == 2)])
        if q.lower() not in hay.lower():
            return False
    return True


def _public_row(st: StudioState, r: dict[str, Any], index: int, base_dir: str | None) -> dict[str, Any]:
    out = dict(r)
    out["index"] = index
    state = r.get("state")
    text = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)
    if len(text) > STATE_MAX:
        out["state"] = text[:STATE_MAX]
        out["state_truncated"] = True
    if r.get("images"):
        refs = []
        for img in r["images"]:
            if isinstance(img, str) and not img.startswith("data:"):
                p = img if os.path.isabs(img) or not base_dir else os.path.join(base_dir, img)
                try:
                    refs.append({"path": img, "file_id": st.workspace.encode_id(st.workspace.rel(p))})
                except ApiError:
                    refs.append({"path": img, "file_id": None})
            else:
                refs.append({"path": "inline image", "file_id": None})
        out["images"] = refs
    return out


@router.get("/suites/{ref}/rows")
def suite_rows(ref: str, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
               kind: str | None = None, split: str | None = None, task: str | None = None, q: str | None = None,
               st: StudioState = Depends(get_state)) -> dict[str, Any]:
    _name, rows, _params, path = load_ref(st, ref)
    q = (q or "").strip() or None
    picked = [(i, r) for i, r in enumerate(rows) if _row_matches(r, kind, split, task, q)]
    base_dir = os.path.dirname(path) if path else None
    items = [_public_row(st, r, i, base_dir) for i, r in picked[offset: offset + limit]]
    facets = {"kinds": sorted({str(r.get("kind")) for r in rows}),
              "splits": sorted({str(r.get("split")) for r in rows}),
              "tasks": [t for t, _ in Counter(str(r.get("task")) for r in rows).most_common(200)]}
    return {"items": st.redact.obj(items), "total": len(picked), "offset": offset, "limit": limit, "facets": facets}


# ---- writes --------------------------------------------------------------------------------------


def _out_path(st: StudioState, out: Any, overwrite: bool, field: str = "out") -> str:
    if not isinstance(out, str) or not out.strip():
        raise _bad(field, "required", "Give an output path inside the workspace.")
    out = out.strip()
    if os.path.isabs(out) or out.startswith("~"):
        raise ApiError(400, "path_outside_workspace", "The output path must be relative to the workspace.")
    if not out.endswith((".jsonl", ".jsonl.gz")):
        raise _bad(field, "must end in .jsonl or .jsonl.gz", "The output file must end in .jsonl.")
    path = st.workspace.resolve(out)
    if os.path.realpath(path).startswith(os.path.realpath(st.workspace.state_dir) + os.sep):
        raise _bad(field, "inside the Studio state directory", "Pick a path outside .decider-lab-studio.")
    if os.path.exists(path) and not overwrite:
        raise ApiError(409, "file_exists", f"{out} exists.", hint="Check Overwrite to replace it.",
                       detail={"path": out})
    return path


def _written(st: StudioState, path: str, n: int, **extra: Any) -> dict[str, Any]:
    rel = st.workspace.rel(path)
    return {"path": rel, "rows": n, "file_id": st.workspace.encode_id(rel), **extra}


def _atomic_write(path: str, rows: list[dict[str, Any]]) -> int:
    tmp = path + f".tmp{os.getpid()}"
    if path.endswith(".gz"):
        tmp = path[:-3] + f".tmp{os.getpid()}.gz"
    n = sdk_rows.write_jsonl(tmp, rows)
    os.replace(tmp, path)
    return n


@router.post("/data/export-suite")
def export_suite(body: dict[str, Any] = Body(...), st: StudioState = Depends(get_state)) -> dict[str, Any]:
    ref = body.get("ref")
    if not isinstance(ref, str) or not ref:
        raise _bad("ref", "required")
    path = _out_path(st, body.get("out"), bool(body.get("overwrite")))
    _name, rows, _params, _src = load_ref(st, ref)
    n = _atomic_write(path, rows)
    return _written(st, path, n)


def _refs_rows(st: StudioState, refs: Any, field: str) -> list[dict[str, Any]]:
    if not isinstance(refs, list) or not all(isinstance(x, str) and x for x in refs):
        raise _bad(field, "a list of suite refs")
    out: list[dict[str, Any]] = []
    for ref in refs:
        out += load_ref(st, ref)[1]
    return out


@router.post("/data/generate")
def generate(body: dict[str, Any] = Body(...), st: StudioState = Depends(get_state)) -> Any:
    fams = body.get("families")
    if fams is None:
        fams = list(suites.DEFAULT_FAMILIES)
    if not isinstance(fams, list) or not fams or not all(isinstance(f, str) for f in fams):
        raise _bad("families", "pick at least one family", "Pick at least one family.")
    unknown = sorted(set(fams) - set(generators.FAMILIES))
    if unknown:
        raise _bad("families", f"unknown families {unknown}", "Pick families from the list.")
    if "chess" in fams and not _chess_available():
        raise ApiError(422, "suite_unavailable", "The chess family needs the chess extra.",
                       hint="pip install 'decider-lab[chess]'", detail={"extra": "chess"})
    per_kind = body.get("per_kind", 500)
    seed = body.get("seed", 1)
    if not isinstance(per_kind, int) or isinstance(per_kind, bool) or not 1 <= per_kind <= 5000:
        raise _bad("per_kind", "must be an integer from 1 to 5000", "Rows per kind must be from 1 to 5,000.")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise _bad("seed", "must be an integer", "The seed must be a whole number.")
    exclude_refs = body.get("exclude_suites") or []
    overwrite = bool(body.get("overwrite"))
    path = _out_path(st, body.get("out"), overwrite)
    total = per_kind * len(fams) * 3
    if total > SYNC_GENERATE_MAX:
        args = ["data", "generate", "--out", st.workspace.rel(path), "--families", ",".join(fams),
                "--per-kind", str(per_kind), "--seed", str(seed)]
        if exclude_refs:
            for ref in exclude_refs:
                if ref.startswith("file:"):
                    args += ["--exclude-suite", st.workspace.rel(_file_path(st, ref[5:]))]
                else:
                    parse_builtin(ref)
                    args += ["--exclude-suite", ref]
        job = st.jobs.start("suite_build", JobManager.cli_argv(*args), title=f"generate {st.workspace.rel(path)}",
                            cwd=st.workspace.root, options={k: body.get(k) for k in body if k != "overwrite"})
        return JSONResponse({"job_id": job["job_id"]}, status_code=202)
    exclude = {r["id"] for r in _refs_rows(st, exclude_refs, "exclude_suites")} if exclude_refs else set()
    try:
        rows = sdk_data.generate(per_kind, fams, seed, None)
    except (ImportError, ValueError) as e:
        raise ApiError(422, "rows_invalid", "Could not generate rows.", detail={"problem": str(e)[:300]}) from e
    kept = [r for r in rows if r["id"] not in exclude]
    n = _atomic_write(path, kept)
    return _written(st, path, n, dropped_overlapping=len(rows) - len(kept), stats=sdk_data.stats(kept),
                    command=_cmd(["decider-lab", "data", "generate", "--out", st.workspace.rel(path), "--families",
                                  ",".join(fams), "--per-kind", str(per_kind), "--seed", str(seed),
                                  *(["--exclude-suite", *exclude_refs] if exclude_refs else [])]))


def _cmd(argv: list[str]) -> str:
    import shlex

    return shlex.join(argv)


@router.post("/data/split")
def split(body: dict[str, Any] = Body(...), st: StudioState = Depends(get_state)) -> dict[str, Any]:
    fid = body.get("file_id")
    if not isinstance(fid, str):
        raise _bad("file_id", "required", "Pick a file to split.")
    src = _file_path(st, fid)
    frac = body.get("dev_fraction", 0.4)
    if not isinstance(frac, int | float) or isinstance(frac, bool) or not 0 < float(frac) < 1:
        raise _bad("dev_fraction", "must be between 0 and 1", "The dev fraction must be between 0 and 1.")
    seed = body.get("seed", 0)
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise _bad("seed", "must be an integer", "The seed must be a whole number.")
    path = _out_path(st, body.get("out"), bool(body.get("overwrite")))
    try:
        rows = sdk_data.assign_splits(sdk_data.load(src), float(frac), seed)
    except (ValueError, OSError) as e:
        raise ApiError(422, "rows_invalid", "This file has rows that are not valid.",
                       detail={"problem": _first_problem(str(e))}) from e
    n = _atomic_write(path, rows)
    stats = sdk_data.stats(rows)
    dev_by_kind = dict(Counter(r["kind"] for r in rows if r.get("split") == "dev"))
    return _written(st, path, n, by_split=stats["by_split"], dev_by_kind=dev_by_kind,
                    snippet=f"suites:\n  - {os.path.relpath(path, st.workspace.root)}")


@router.post("/data/check")
def check(body: dict[str, Any] = Body(...), st: StudioState = Depends(get_state)) -> dict[str, Any]:
    fid = body.get("file_id")
    if not isinstance(fid, str):
        raise _bad("file_id", "required")
    src = _file_path(st, fid)
    try:
        rows = sdk_data.load(src)
    except (ValueError, OSError, EOFError) as e:
        return {"valid": False, "problem": _first_problem(str(e)), "stats": None}
    return {"valid": True, "problem": None, "stats": sdk_data.stats(rows)}


@router.post("/data/leakcheck")
def leakcheck(body: dict[str, Any] = Body(...), st: StudioState = Depends(get_state)) -> dict[str, Any]:
    fid = body.get("train_file_id")
    if not isinstance(fid, str):
        raise _bad("train_file_id", "required", "Pick the training file.")
    against = body.get("against")
    if not isinstance(against, list) or not against:
        raise _bad("against", "pick at least one suite or file", "Pick at least one suite or file to check against.")
    drop_to = body.get("drop_to")
    out_path = _out_path(st, drop_to, bool(body.get("overwrite")), "drop_to") if drop_to else None
    try:
        train = sdk_data.load(_file_path(st, fid))
    except (ValueError, OSError) as e:
        raise ApiError(422, "rows_invalid", "The training file has rows that are not valid.",
                       detail={"problem": _first_problem(str(e))}) from e
    evals = _refs_rows(st, against, "against")
    res = sdk_data.leakcheck(train, evals)
    bad = set(res.pop("overlapping_indices"))
    clean = None
    if out_path:
        n = _atomic_write(out_path, [r for i, r in enumerate(train) if i not in bad])
        clean = _written(st, out_path, n)
    res["examples"] = st.redact.obj(res["examples"])
    return {**res, "clean": clean, "passed": res["overlapping"] == 0, "against": against}


# ---- CSV upload ----------------------------------------------------------------------------------


def _uploads_dir(st: StudioState) -> str:
    d = os.path.join(st.workspace.ensure_state_dir(), "uploads")
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def _sweep_uploads(st: StudioState) -> None:
    d = _uploads_dir(st)
    now = time.time()
    for f in os.listdir(d):
        p = os.path.join(d, f)
        try:
            if now - os.path.getmtime(p) > UPLOAD_TTL_S:
                os.remove(p)
        except OSError:
            pass


def _delimiter(value: str | None) -> str:
    d = DELIMITERS.get(value if value is not None else ",")
    if d is None:
        raise _bad("delimiter", "one of , ; | or tab", "The delimiter must be a comma, semicolon, pipe or tab.")
    return d


def _csv_row(r: dict[str, Any], task: str) -> dict[str, Any]:
    """One CSV record -> a decision row, as data.from_csv converts it (raises ValueError)."""
    missing = [c for c in CSV_REQUIRED if not (r.get(c) or "").strip()]
    if missing:
        raise ValueError(f"missing {', '.join(missing)}")
    answer = r["answer"].strip()
    opts = [o.strip() for o in (r.get("options") or "").split("|") if o.strip()]
    kind = (r.get("kind") or "").strip().lower() or ("choice" if opts else "noul")
    if kind == "noul":
        if answer.lower() not in sdk_data.YES | sdk_data.NO:
            raise ValueError(f"a yes/no answer must be yes or no, got `{answer}`")
        row = {"kind": "noul", "state": r["state"], "instructions": r["question"], "options": sdk_rows.YES_NO,
               "label": int(answer.lower() in sdk_data.YES)}
    elif kind in ("choice", "score"):
        if answer not in opts:
            raise ValueError(f"answer `{answer}` is not one of the options")
        options = [[o, o] for o in opts] if kind == "choice" else [[str(i), o] for i, o in enumerate(opts)]
        row = {"kind": kind, "state": r["state"], "instructions": r["question"], "options": options,
               "label": opts.index(answer)}
    else:
        raise ValueError(f"kind must be noul, choice or score, got `{kind}`")
    row["task"] = (r.get("task") or "").strip() or task
    problems = sdk_rows.problems(row)
    if problems:
        raise ValueError(problems[0])
    return {**row, "id": sdk_rows.row_id(row)}


def _preview(path: str, task: str, delimiter: str) -> dict[str, Any]:
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter=delimiter)
        header = [h for h in (reader.fieldnames or []) if h is not None]
        found = [h for h in header if h in CSV_COLUMNS]
        cols = {"found": found, "missing_required": [c for c in CSV_REQUIRED if c not in header],
                "ignored": [h for h in header if h not in CSV_COLUMNS]}
        rows: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        total = 0
        if not cols["missing_required"]:
            for n, r in enumerate(reader, 2):
                total += 1
                try:
                    rows.append(_csv_row(r, task))
                except ValueError as e:
                    if len(errors) < 100:
                        errors.append({"row": n, "message": str(e)})
        else:
            total = sum(1 for _ in reader)
    stats = sdk_data.stats(rows) if rows and not errors and not cols["missing_required"] else None
    return {"rows_total": total, "columns": cols, "preview": rows[:20], "errors": errors, "stats": stats,
            "error_count": len(errors)}


@router.post("/data/csv/preview")
async def csv_preview(file: UploadFile = File(...), task: str = Form("custom"), delimiter: str = Form(","),
                      st: StudioState = Depends(get_state)) -> dict[str, Any]:
    delim = _delimiter(delimiter)
    task = (task or "custom").strip() or "custom"
    if len(task) > 128:
        raise _bad("task", "at most 128 characters")
    _sweep_uploads(st)
    upload_id = "u_" + secrets.token_hex(4)
    dest = os.path.join(_uploads_dir(st), f"{upload_id}.csv")
    size = 0
    first = True
    with open(dest, "wb") as out:
        while True:
            chunk = await file.read(1 << 20)
            if not chunk:
                break
            if first:
                chunk = chunk.removeprefix(b"\xef\xbb\xbf")  # a BOM would hide the first column's name
                first = False
            size += len(chunk)
            if size > CSV_MAX:
                out.close()
                os.remove(dest)
                raise ApiError(413, "too_large", "This CSV is larger than 50 MB.", detail={"limit_bytes": CSV_MAX})
            out.write(chunk)
    try:
        result = _preview(dest, task, delim)
    except UnicodeDecodeError as e:
        os.remove(dest)
        raise ApiError(422, "rows_invalid", "This file is not UTF-8 text.", hint="Save the CSV as UTF-8 and try again.",
                       detail={"errors": [{"row": None, "message": "not UTF-8"}]}) from e
    except csv.Error as e:
        os.remove(dest)
        raise ApiError(422, "rows_invalid", "This file is not a CSV this tool can read.",
                       detail={"errors": [{"row": None, "message": str(e)[:200]}]}) from e
    name = os.path.basename(file.filename or "upload.csv")[:200]
    base = re.sub(r"[^A-Za-z0-9_.-]+", "-", os.path.splitext(name)[0]).strip("-.") or "upload"
    return st.redact.obj({"upload_id": upload_id, "filename": name, "suggested_out": f"data/{base}.jsonl",
                          "task": task, "delimiter": delimiter, **result})


@router.post("/data/csv/convert")
def csv_convert(body: dict[str, Any] = Body(...), st: StudioState = Depends(get_state)) -> dict[str, Any]:
    upload_id = body.get("upload_id")
    if not isinstance(upload_id, str) or not UPLOAD_ID.match(upload_id):
        raise ApiError(404, "upload_not_found", "This upload is gone. Upload the CSV again.")
    src = os.path.join(_uploads_dir(st), f"{upload_id}.csv")
    if not os.path.isfile(src):
        raise ApiError(404, "upload_not_found", "This upload is gone. Upload the CSV again.")
    delim = _delimiter(body.get("delimiter", ","))
    task = (body.get("task") or "custom").strip() or "custom"
    path = _out_path(st, body.get("out"), bool(body.get("overwrite")))
    try:
        rows = sdk_data.from_csv(src, task=task, delimiter=delim)
    except (ValueError, KeyError, csv.Error, UnicodeDecodeError) as e:
        msg = str(e).replace(src, "upload")
        raise ApiError(422, "rows_invalid", "Some CSV rows are not valid; nothing was written.",
                       detail={"problem": _first_problem(msg)}) from e
    n = _atomic_write(path, rows)
    try:
        os.remove(src)
    except OSError:
        pass
    return _written(st, path, n, stats=sdk_data.stats(rows))


# ---- raw files (row images) ----------------------------------------------------------------------

_MAGIC = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"), (b"GIF87a", "image/gif"),
          (b"GIF89a", "image/gif"))


def _sniff(path: str) -> str | None:
    with open(path, "rb") as fh:
        head = fh.read(16)
    for magic, mime in _MAGIC:
        if head.startswith(magic):
            return mime
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


@router.get("/files/raw")
def raw_file(file_id: str = Query(...), st: StudioState = Depends(get_state)) -> FileResponse:
    path = st.workspace.decode_id(file_id)
    if not os.path.isfile(path):
        raise ApiError(404, "not_found", "No such file.", detail={"what": "file"})
    mime = _sniff(path)
    if mime is None:
        raise ApiError(415, "unsupported_media", "Only png, jpg, gif and webp images are served.")
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "no-store",
                                                        "Content-Security-Policy": "default-src 'none'"})

