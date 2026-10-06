"""Jobs API: Tracked CLI subprocesses (API.md section 6). Build on `st.jobs` (JobManager) and the SSE helpers
in `decider_lab.ui.jobs` (`job_event_stream`, `bus_event_stream`, `sse_response`).

Owned endpoints (all under the `/api` prefix this router registers):

    GET, POST /api/jobs
    GET, DELETE /api/jobs/{job_id}
    POST /api/jobs/{job_id}/cancel
    GET /api/jobs/{job_id}/log
    GET /api/jobs/{job_id}/log.txt
    GET /api/jobs/{job_id}/telemetry
    GET (SSE) /api/jobs/{job_id}/events
    GET (SSE) /api/events

Every job is a real `decider-lab` command built from validated fields (never a shell string). With
DECIDER_LAB_FAKE_CLOUD=1, remote runs (ssh, aws, vast) run `decider_lab.ui.simulate` instead, which
prints the remote stage lines around a real local run. Parsing, progress and telemetry: jobs_track.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, Header, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from . import labs_core
from .api_labs import (
    BACKEND_TITLE,
    RunOptions,
    estimate,
    lab_file,
    lab_name,
    parse_body,
    run_args,
    target_root,
    validate_file,
)
from .errors import ApiError
from .jobs import JobManager, bus_event_stream, default_stages, format_sse, job_event_stream, sse_response
from .jobs_track import count_predictions, get_tracker, read_telemetry, run_entry
from .redact import PRESIGNED, USERINFO
from .state import StudioState, get_state

router = APIRouter(prefix="/api", tags=["jobs"])

KINDS = ("run", "eval", "pull", "jevbench", "calibrate", "suite_build")
MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def install(st: StudioState) -> None:
    """Install the job tracker (log parsers, finish handler, sampler) for this server. Called at
    app creation when the server wires it; every route also calls it, so it is never missing."""
    get_tracker(st)


def tracked(st: StudioState = Depends(get_state)) -> StudioState:
    get_tracker(st)
    return st


# ---- request bodies ------------------------------------------------------------------------------


class EvalModel(BaseModel):
    type: Literal["url", "baseline", "serve", "python"]
    value: str = Field(..., min_length=1, max_length=2000)


class EvalBody(BaseModel):
    kind: Literal["eval"]
    model: EvalModel
    name: str = "model"
    suite: str = "smoke"
    split: Literal["dev", "test", "all"] | None = None
    limit: int | None = Field(None, ge=1, le=1_000_000)
    workers: int = Field(4, ge=1, le=64)
    vision: bool = False
    out: str | None = Field(None, max_length=1000)


class PullBody(BaseModel):
    kind: Literal["pull"]
    source: str = Field(..., max_length=2000)
    revision: str | None = Field(None, max_length=200)
    sha256: str | None = Field(None, max_length=64)
    require_pinned: bool = True
    profile: str | None = Field(None, max_length=200)
    region: str | None = Field(None, max_length=64)


class JevbenchBody(BaseModel):
    kind: Literal["jevbench"]
    url: str = Field(..., max_length=2000)
    label: str = "model"
    out: str | None = Field(None, max_length=1000)


class CalibrateBody(BaseModel):
    kind: Literal["calibrate"]
    root_id: str
    model: str
    suite: str
    out: str | None = Field(None, max_length=1000)


class SuiteBuildBody(BaseModel):
    kind: Literal["suite_build"]
    suite: str = Field(..., max_length=500)


# ---- helpers -------------------------------------------------------------------------------------


def _ws_path(st: StudioState, rel: str) -> str:
    if os.path.isabs(rel):
        raise ApiError(400, "path_outside_workspace", "Use a path inside the workspace.")
    return st.workspace.resolve(rel)


def _rel_or_abs(st: StudioState, path: str) -> str:
    try:
        return st.workspace.rel(path)
    except ApiError:
        return path


def _start(st: StudioState, kind: str, argv: list[str], *, title: str, cwd: str, ctx: dict[str, Any],
           backend: str = "local", lab_id: str | None = None, options: dict[str, Any] | None = None,
           env: dict[str, str] | None = None, env_names: list[str] | None = None,
           stages: list[dict[str, Any]] | None = None, runs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    tracker = get_tracker(st)
    with tracker.lock:  # no log line is parsed before the tracker knows the job
        job = st.jobs.start(kind, argv, title=title, cwd=cwd, env=env, env_names=env_names or [], backend=backend,
                            lab_id=lab_id, options=options or {}, stages=stages)
        prog = {"runs": runs} if runs is not None else None
        tracker.register(job["job_id"], ctx, progress=prog)
    job = st.jobs.get(job["job_id"])
    return {"job_id": job["job_id"], "status": job["status"], "title": job["title"],
            "command": st.redact(_display_command(st, job, ctx))}


def _display_command(st: StudioState, job: dict[str, Any], ctx: dict[str, Any]) -> str:
    return ctx.get("command") or job.get("command") or ""


def _suite_name(ref: str) -> str:
    base = ref.partition(":")[0]
    if base in ("smoke", "synthetic", "heldout"):
        return base
    return os.path.basename(ref).split(".")[0]


def _resolve_suite(st: StudioState, ref: str) -> tuple[str, str, int | None]:
    """(CLI suite argument, run-dir suite name, rows estimate) for a suite ref."""
    ref = ref.strip()
    if ref.startswith("file:"):
        path = st.workspace.decode_id(ref[len("file:"):])
        if not os.path.isfile(path):
            raise ApiError(404, "not_found", "No such suite file.", detail={"what": "suite"})
        n = sum(1 for line in open(path, encoding="utf-8", errors="replace") if line.strip())
        return path, os.path.basename(path).split(".")[0], n
    base = ref.partition(":")[0]
    if base in ("smoke", "synthetic", "heldout"):
        info = labs_core._suite_info(labs_core._Ctx({}, st.workspace.root, dict(os.environ), False), 0, ref)
        return ref, base, (info or {}).get("rows_estimate")
    path = _ws_path(st, ref)
    if not os.path.isfile(path):
        raise ApiError(404, "not_found", "No such suite file.", detail={"what": "suite"})
    n = sum(1 for line in open(path, encoding="utf-8", errors="replace") if line.strip())
    return path, os.path.basename(path).split(".")[0], n


def _plan_runs(v: labs_core.Validation, selected: list[str], limit: int | None, root: str,
               local: bool) -> tuple[list[dict[str, Any]], dict[str, int]]:
    s = v.summary or {}
    runs: list[dict[str, Any]] = []
    reused: dict[str, int] = {}
    for m in s.get("models", []):
        if m["name"] not in selected:
            continue
        for su in s.get("suites", []):
            total = su.get("rows_estimate")
            if total is not None and limit:
                total = min(total, limit * 3)
            r = run_entry(m["name"], su["name"], total)
            if local:
                done, errors = count_predictions(os.path.join(root, m["name"], su["name"], "predictions.jsonl"))
                reused[f"{m['name']}/{su['name']}"] = max(0, done - errors)
                r["reused"] = max(0, done - errors) or None
            runs.append(r)
            if s.get("calibrate") and su.get("has_splits") is not False:
                runs.append(run_entry(m["name"], su["name"] + "+cal", None))
        if m.get("jevbench"):
            runs.append(run_entry(m["name"], "jevbench", 231))
    return runs, reused


# ---- POST /api/jobs ------------------------------------------------------------------------------


@router.post("/jobs", status_code=202)
def start_job(body: dict[str, Any] = Body(...), st: StudioState = Depends(tracked)) -> dict[str, Any]:
    kind = body.get("kind")
    if kind not in KINDS:
        raise ApiError(422, "bad_request", "The request is not valid.",
                       detail={"fields": [{"field": "kind", "message": "one of " + ", ".join(KINDS)}]})
    return {"run": _start_run, "eval": _start_eval, "pull": _start_pull, "jevbench": _start_jevbench,
            "calibrate": _start_calibrate, "suite_build": _start_suite_build}[kind](st, body)


def _start_run(st: StudioState, body: dict[str, Any]) -> dict[str, Any]:
    lab_id = body.get("lab_id")
    if not isinstance(lab_id, str) or not lab_id:
        raise ApiError(422, "bad_request", "The request is not valid.",
                       detail={"fields": [{"field": "lab_id", "message": "required"}]})
    abspath, rel = lab_file(st, lab_id)
    confirm = body.get("confirm")
    opts: RunOptions = parse_body(RunOptions, {k: v for k, v in body.items()
                                               if k not in ("kind", "lab_id", "confirm")})
    v = validate_file(st, abspath)
    if not v.valid:
        raise ApiError(422, "lab_invalid", f"lab.yaml has {v.errors} problem{'s' if v.errors != 1 else ''}.",
                       hint="Open the editor to fix it.", detail={"problems": v.problems})
    est = estimate(st, abspath, opts)
    if est["blockers"]:
        raise ApiError(409, "run_blocked", "This run cannot start: " + "; ".join(est["blockers"]) + ".",
                       detail={"blockers": est["blockers"]})
    phrase = est["confirm_phrase"]
    if phrase and (not isinstance(confirm, str) or confirm.strip() != phrase):
        hint = ("type the spend phrase shown in the estimate" if est["backend"] in ("vast", "aws")
                else "type `call paid apis`")
        raise ApiError(409, "confirm_mismatch", "The confirmation does not match.", detail={"expected_hint": hint})
    for j in st.jobs.list(status="active", lab_id=lab_id):
        if j["kind"] == "run":
            raise ApiError(409, "job_active", "This lab is already running.", hint="Open its job, or cancel it first.",
                           detail={"job_id": j["job_id"]})
    backend = est["backend"]
    name = lab_name(v, abspath)
    root, out_abs = target_root(st, abspath, v, opts)
    max_hours = est["cost"]["max_hours"]
    args = run_args(os.path.basename(abspath), backend, opts, est["options"], out_abs, max_hours)
    env: dict[str, str] = {}
    if backend != "local" and st.fake_cloud:
        argv = JobManager.cli_argv(*args)
        argv[1:3] = ["-m", "decider_lab.ui.simulate"]
        rate = est["cost"].get("rate_usd_per_hour")
        env = {"DECIDER_LAB_SIM_RATE": str(rate or 0)}
        src = est["cost"].get("rate_source") or ""
        m = re.search(r"offer (\d+)", src)
        if m:
            env["DECIDER_LAB_SIM_OFFER"] = m.group(1)
        if backend == "vast":
            env["DECIDER_LAB_SIM_GPU"] = str(est["options"].get("gpu") or "RTX_4090")
    else:
        argv = JobManager.cli_argv(*args)
    selected = est["plan"]["models"]
    runs, reused = _plan_runs(v, selected, opts.limit, root, backend == "local")
    stages = default_stages("run", backend)
    if backend == "local" and (v.summary or {}).get("finetune"):
        stages.insert(1, {"name": "finetune", "label": "Fine-tune", "status": "pending", "started_at": None,
                          "ended_at": None, "detail": None})
    ctx = {"kind": "run", "root": root, "reused": reused, "rate": est["cost"].get("rate_usd_per_hour"),
           "lab": rel, "lab_name": name, "fake": bool(st.fake_cloud), "command": est["command"]}
    title = f"run {name}" + ("" if backend == "local" else f" on {BACKEND_TITLE[backend]}")
    options = {k: v for k, v in body.items() if k not in ("confirm", "kind", "lab_id")}
    options["backend"] = backend
    out = _start(st, "run", argv, title=title, cwd=os.path.dirname(abspath), ctx=ctx, backend=backend,
                 lab_id=lab_id, options=options, env=env, env_names=list(opts.env), stages=stages, runs=runs)
    extra: dict[str, Any] = {}
    if argv[1:3] == ["-m", "decider_lab.ui.simulate"]:
        extra["argv"] = ["python", *argv[1:]]
    if os.path.isdir(root):
        try:
            extra["root_id"] = st.workspace.encode_id(root)
        except ApiError:
            pass
    if extra:
        st.jobs.update(out["job_id"], **extra)
    return out


def _start_eval(st: StudioState, body: dict[str, Any]) -> dict[str, Any]:
    b: EvalBody = parse_body(EvalBody, body)
    if not MODEL_NAME.match(b.name):
        raise ApiError(422, "bad_request", "The request is not valid.",
                       detail={"fields": [{"field": "name", "message": "letters, digits, ., - and _"}]})
    value = b.model.value.strip()
    if b.model.type == "url":
        if not value.startswith(("http://", "https://")):
            raise ApiError(422, "bad_request", "The request is not valid.",
                           detail={"fields": [{"field": "model.value", "message": "a URL starting with http://"}]})
        if USERINFO.search(value) or PRESIGNED.search(value):
            raise ApiError(422, "credential_in_source", "This URL carries a credential; Studio does not store "
                           "credentials in commands or results.")
        model_args = ["--model", value]
    elif b.model.type == "baseline":
        if value not in labs_core.BASELINES:
            raise ApiError(422, "bad_request", "The request is not valid.",
                           detail={"fields": [{"field": "model.value", "message": "uniform, majority or random"}]})
        model_args = ["--model", value]
    elif b.model.type == "python":
        if ":" not in value:
            raise ApiError(422, "bad_request", "The request is not valid.",
                           detail={"fields": [{"field": "model.value", "message": "module:attr"}]})
        model_args = ["--model", f"python:{value}"]
    else:
        if USERINFO.search(value) or PRESIGNED.search(value):
            raise ApiError(422, "credential_in_source", "This source carries a credential.")
        model_args = ["--serve", value]
    suite_arg, suite_name, rows = _resolve_suite(st, b.suite)
    out = _ws_path(st, b.out) if b.out else os.path.join(st.workspace.root, "runs", b.name, suite_name)
    args = ["eval", *model_args, "--suite", suite_arg, "--name", b.name, "--workers", str(b.workers), "--out", out]
    if b.split:
        args += ["--split", b.split]
    if b.limit:
        args += ["--limit", str(b.limit)]
        rows = min(rows, b.limit * 3) if rows is not None else None
    if b.vision and b.model.type == "serve":
        args.append("--vision")
    pred = os.path.join(out, "predictions.jsonl")
    done, errors = count_predictions(pred)
    r = run_entry(b.name, suite_name, rows, path=pred)
    r["reused"] = max(0, done - errors) or None
    ctx = {"kind": "eval", "root": os.path.dirname(out), "out": out, "model_name": b.name,
           "reused": {f"{b.name}/{suite_name}": max(0, done - errors)}}
    return _start(st, "eval", JobManager.cli_argv(*args), title=f"eval {value if b.model.type != 'serve' else b.name}",
                  cwd=st.workspace.root, ctx=ctx, options=b.model_dump(), runs=[r])


def _start_pull(st: StudioState, body: dict[str, Any]) -> dict[str, Any]:
    b: PullBody = parse_body(PullBody, body)
    src = b.source.strip()
    if not src:
        raise ApiError(422, "source_invalid", "Give a model source: hf://org/repo@commit, s3://…, https://… or a "
                       "directory.", detail={"problems": [{"severity": "error", "code": "bad_source",
                                                           "message": "empty source", "line": None, "column": None,
                                                           "path": "source"}]})
    if USERINFO.search(src) or PRESIGNED.search(src):
        raise ApiError(422, "credential_in_source", "This source carries a credential (URL user info or a presigned "
                       "query); it would be stored in the cache metadata.")
    if b.sha256 and not re.match(r"^[0-9a-fA-F]{64}$", b.sha256):
        raise ApiError(422, "bad_request", "The request is not valid.",
                       detail={"fields": [{"field": "sha256", "message": "64 hexadecimal characters"}]})
    kind = labs_core._source_kind(src)
    if b.require_pinned and kind == "hf":
        rev = src.split("@", 1)[1] if "@" in src else (b.revision or "")
        if not FULL_SHA.match(rev):
            raise ApiError(422, "unpinned_source", "This hf:// source is not pinned to a full commit.",
                           hint="Add @<40-character commit>, or turn off Require pinned.")
    args = ["pull", src]
    for flag, val in (("--revision", b.revision), ("--sha256", b.sha256), ("--profile", b.profile),
                      ("--region", b.region)):
        if val:
            args += [flag, val]
    if b.require_pinned:
        args.append("--require-pinned")
    return _start(st, "pull", JobManager.cli_argv(*args), title=f"pull {src}", cwd=st.workspace.root,
                  ctx={"kind": "pull", "source": src}, options=b.model_dump())


def _start_jevbench(st: StudioState, body: dict[str, Any]) -> dict[str, Any]:
    b: JevbenchBody = parse_body(JevbenchBody, body)
    if not b.url.startswith(("http://", "https://")) or USERINFO.search(b.url):
        raise ApiError(422, "bad_request", "The request is not valid.",
                       detail={"fields": [{"field": "url", "message": "a System One URL starting with http://"}]})
    if not MODEL_NAME.match(b.label):
        raise ApiError(422, "bad_request", "The request is not valid.",
                       detail={"fields": [{"field": "label", "message": "letters, digits, ., - and _"}]})
    out = _ws_path(st, b.out) if b.out else os.path.join(st.workspace.root, "runs", "jevbench",
                                                          f"{b.label}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}")
    args = ["jevbench", "--url", b.url, "--out", out, "--label", b.label]
    return _start(st, "jevbench", JobManager.cli_argv(*args), title=f"jevbench {b.label}", cwd=st.workspace.root,
                  ctx={"kind": "jevbench", "out": out, "out_rel": _rel_or_abs(st, out)}, options=b.model_dump())


def _start_calibrate(st: StudioState, body: dict[str, Any]) -> dict[str, Any]:
    b: CalibrateBody = parse_body(CalibrateBody, body)
    root = st.workspace.decode_id(b.root_id)
    if "/" in b.model or "/" in b.suite or b.model in ("", ".", "..") or b.suite in ("", ".", ".."):
        raise ApiError(404, "not_found", "No such run.", detail={"what": "run"})
    run_dir = os.path.join(root, b.model, b.suite)
    pred = os.path.join(run_dir, "predictions.jsonl")
    if not os.path.isfile(pred):
        raise ApiError(404, "not_found", "No such run.", detail={"what": "run"})
    dev: dict[str, int] = {}
    with open(pred, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                p = json.loads(line)
            except ValueError:
                continue
            if p.get("split") == "dev" and not p.get("error"):
                dev[p.get("kind", "?")] = dev.get(p.get("kind", "?"), 0) + 1
    if max(dev.values(), default=0) < 30:
        raise ApiError(409, "too_few_dev_rows", "This run has too few dev rows to fit temperatures (at least 30 of "
                       "one kind are needed).", detail={"dev_rows_by_kind": dev})
    out = _ws_path(st, b.out) if b.out else run_dir + "+cal"
    args = ["calibrate", run_dir, "--out", out]
    return _start(st, "calibrate", JobManager.cli_argv(*args), title=f"calibrate {b.model} / {b.suite}",
                  cwd=st.workspace.root, ctx={"kind": "calibrate", "out": out, "model_name": b.model},
                  options=b.model_dump())


def _start_suite_build(st: StudioState, body: dict[str, Any]) -> dict[str, Any]:
    b: SuiteBuildBody = parse_body(SuiteBuildBody, body)
    base = b.suite.partition(":")[0]
    if base not in ("smoke", "synthetic", "heldout"):
        raise ApiError(422, "bad_request", "The request is not valid.",
                       detail={"fields": [{"field": "suite", "message": "smoke, synthetic or heldout (with options)"}]})
    return _start(st, "suite_build", JobManager.cli_argv("suites", "--build", b.suite), title=f"build suite {b.suite}",
                  cwd=st.workspace.root, ctx={"kind": "suite_build", "suite": b.suite}, options=b.model_dump())


# ---- reads, cancel, delete ---------------------------------------------------------------------


@router.get("/jobs")
def list_jobs(status: str | None = Query(None, max_length=20), kind: str | None = Query(None, max_length=20),
              lab_id: str | None = Query(None, max_length=1000), q: str | None = Query(None, max_length=200),
              limit: int = Query(100, ge=1, le=1000), st: StudioState = Depends(tracked)) -> dict[str, Any]:
    return {"items": st.jobs.list(status=status, kind=kind, lab_id=lab_id, q=q, limit=limit)}


@router.get("/jobs/{job_id}")
def get_job(job_id: str, st: StudioState = Depends(tracked)) -> dict[str, Any]:
    return st.jobs.get(job_id)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, st: StudioState = Depends(tracked)) -> JSONResponse:
    res = st.jobs.cancel(job_id)
    return JSONResponse({"status": res}, status_code=202 if res == "cancelling" else 200)


@router.delete("/jobs/{job_id}")
def delete_job(job_id: str, body: dict[str, Any] | None = Body(None), st: StudioState = Depends(tracked)) -> dict:
    job = st.jobs.get(job_id)
    if job["status"] in ("queued", "running", "cancelling"):
        raise ApiError(409, "job_active", "This job is still active.", detail={"job_id": job_id})
    if not isinstance(body, dict) or body.get("confirm") != "delete":
        raise ApiError(409, "confirm_mismatch", "Type `delete` to remove this job record.",
                       detail={"expected_hint": "type delete"})
    st.jobs.delete(job_id)
    get_tracker(st).jobs.pop(job_id, None)
    return {"deleted": True}


@router.get("/jobs/{job_id}/log")
def job_log(job_id: str, offset: int = Query(0, ge=0), limit: int = Query(5000, ge=1, le=20000),
            q: str | None = Query(None, max_length=500), level: Literal["all", "warn", "error"] = Query("all"),
            st: StudioState = Depends(tracked)) -> dict[str, Any]:
    return st.jobs.read_log(job_id, offset=offset, limit=limit, q=q, level=level)


@router.get("/jobs/{job_id}/log.txt")
def job_log_txt(job_id: str, st: StudioState = Depends(tracked)) -> PlainTextResponse:
    text = st.jobs.log_text(job_id)
    return PlainTextResponse(text, headers={"Content-Disposition": f'attachment; filename="{job_id}.log"'})


@router.get("/jobs/{job_id}/telemetry")
def job_telemetry(job_id: str, since: str | None = Query(None, max_length=64), limit: int = Query(900, ge=1, le=20000),
                  st: StudioState = Depends(tracked)) -> dict[str, Any]:
    job = st.jobs.get(job_id)
    tele = job.get("telemetry") or {"source": "none", "reason": None}
    raw = read_telemetry(os.path.join(st.jobs.job_dir(job_id), "telemetry.jsonl"), since, limit)
    meta: dict[int, dict[str, Any]] = {}
    samples = []
    for s in raw:
        gpus = []
        for g in s.get("gpus") or []:
            if not isinstance(g, dict):
                continue
            idx = int(g.get("index") or 0)
            meta[idx] = {"index": idx, "name": str(g.get("name") or f"GPU {idx}"),
                         "memory_total_gb": g.get("mem_total_gb"), "power_limit_w": g.get("power_limit_w")}
            gpus.append({"index": idx, "util_pct": g.get("util_pct"), "mem_used_gb": g.get("mem_used_gb"),
                         "temp_c": g.get("temp_c"), "power_w": g.get("power_w")})
        out = {"ts": s.get("ts"), "gpus": gpus, "rows_per_s": s.get("rows_per_s"),
               "errors_total": s.get("errors_total")}
        if "train_loss" in s:
            out["train_loss"] = s["train_loss"]
        samples.append(out)
    interval = 2 if job["backend"] == "local" else (1 if tele.get("source") == "simulated" else 10)
    return st.redact.obj({"source": tele.get("source", "none"), "reason": tele.get("reason"), "interval_s": interval,
                          "gpus": [meta[k] for k in sorted(meta)], "samples": samples})


# ---- server-sent events --------------------------------------------------------------------------


def _sample_public(s: dict[str, Any]) -> dict[str, Any]:
    return {"ts": s.get("ts"), "rows_per_s": s.get("rows_per_s"), "errors_total": s.get("errors_total"),
            "gpus": [{"index": int(g.get("index") or 0), "util_pct": g.get("util_pct"),
                      "mem_used_gb": g.get("mem_used_gb"), "temp_c": g.get("temp_c"), "power_w": g.get("power_w")}
                     for g in s.get("gpus") or [] if isinstance(g, dict)]}


async def _with_telemetry(st: StudioState, job_id: str, inner: AsyncIterator[str]) -> AsyncIterator[str]:
    """The foundation's job stream plus `telemetry` events for samples written while it runs."""
    path = os.path.join(st.jobs.job_dir(job_id), "telemetry.jsonl")
    try:
        offset = os.path.getsize(path)
    except OSError:
        offset = 0
    n = 0
    async for chunk in inner:
        try:
            size = os.path.getsize(path)
        except OSError:
            size = offset
        if size > offset:
            with open(path, encoding="utf-8", errors="replace") as fh:
                fh.seek(offset)
                data = fh.read(size - offset)
            cut = data.rfind("\n")
            if cut >= 0:
                offset += len(data[: cut + 1].encode("utf-8"))
                for line in data[: cut + 1].splitlines():
                    try:
                        sample = json.loads(line)
                    except ValueError:
                        continue
                    n += 1
                    yield format_sse("telemetry", {"sample": st.redact.obj(_sample_public(sample))}, f"t-{n}")
        yield chunk


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request, last_event_id: str | None = Query(None, max_length=40),
                     from_: str | None = Query(None, alias="from", max_length=8),
                     last_event_id_header: str | None = Header(None, alias="Last-Event-ID"),
                     st: StudioState = Depends(tracked)):  # noqa: ANN201
    st.jobs.get(job_id)  # 404 before the stream starts
    lei = last_event_id or last_event_id_header
    inner = job_event_stream(st.jobs, job_id, last_event_id=lei, from_start=from_ == "0",
                             is_disconnected=request.is_disconnected)
    return sse_response(_with_telemetry(st, job_id, inner))


@router.get("/events")
async def global_events(request: Request, st: StudioState = Depends(tracked)):  # noqa: ANN201
    return sse_response(bus_event_stream(st.bus, is_disconnected=request.is_disconnected))
