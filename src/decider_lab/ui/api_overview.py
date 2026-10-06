"""Overview API: KPIs, active jobs, recent results, onboarding (API.md section 3).

    GET /api/overview

Beyond the contract's fields, the response carries what the home page also shows:
`kpis.run_roots`, `kpis.cached_models`, `kpis.cached_models_gb`, `kpis.jobs_queued`,
`kpis.cloud.vast` / `kpis.cloud.aws` (per-provider instances, rate, vast credit, AWS account) and
`recent_roots` (the newest run roots with the top non-baseline Intelligence per suite) and `labs`
(id, name, path, valid: the quick actions' lab picker).

`start_eval_job(st, body)` builds the Quick eval dialog's `decider-lab eval` job (API.md section 6,
`kind: "eval"`); the jobs router owns `POST /api/jobs` and can delegate to it.

Cloud reads: the fixtures with DECIDER_LAB_FAKE_CLOUD=1; otherwise the vastai CLI and boto3 when
present, cached 60 s and bounded at 5 s so the page never waits on a cloud. A provider whose read
failed is named in `errors` and makes `usd_per_hour` null (never a partial sum shown as complete).
"""

from __future__ import annotations

import concurrent.futures
import importlib.util
import json
import os
import re
import shutil
import time
from typing import Any

from fastapi import APIRouter, Depends

from .. import lab as sdk_lab
from .errors import ApiError
from .state import StudioState, get_state
from .workspace import iso

router = APIRouter(prefix="/api", tags=["overview"])

CLOUD_TTL_S = 60.0
CLOUD_TIMEOUT_S = 5.0
RECENT_RESULTS = 10
RECENT_ROOTS = 6
_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="studio-overview")


def _read_json(path: str) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _ci(scores: dict[str, Any]) -> list[float | None] | None:
    ci = scores.get("intelligence_ci95")
    if isinstance(ci, list | tuple) and len(ci) == 2:
        return [ci[0], ci[1]]
    return None


def _overlaps(a: list[float | None] | None, b: list[float | None] | None) -> bool:
    if not a or not b or None in a or None in b:
        return False
    return not (a[1] < b[0] or b[1] < a[0])  # type: ignore[operator]


# ---- runs ----------------------------------------------------------------------------------------


def collect_runs(st: StudioState) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """(runs, failures, roots): every <model>/<suite>/scores.json under every run root."""
    runs: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    roots: list[dict[str, Any]] = []
    for root in st.workspace.run_roots():
        lab_json = _read_json(os.path.join(root.abspath, "lab.json")) or {}
        models_spec = lab_json.get("models") if isinstance(lab_json.get("models"), dict) else {}
        baseline_name = lab_json.get("baseline")
        root_runs = []
        for model in root.models:
            mdir = os.path.join(root.abspath, model)
            try:
                suites_here = sorted(os.listdir(mdir))
            except OSError:
                continue
            spec = models_spec.get(model)
            is_baseline = model == baseline_name or (isinstance(spec, dict) and "baseline" in spec)
            for suite in suites_here:
                scores = _read_json(os.path.join(mdir, suite, "scores.json"))
                if scores is None:
                    continue
                run = _read_json(os.path.join(mdir, suite, "run.json")) or {}
                finished = run.get("finished_utc")
                if not finished:
                    try:
                        finished = iso(os.path.getmtime(os.path.join(mdir, suite, "scores.json")))
                    except OSError:
                        finished = None
                item = {"root_id": root.root_id, "lab": root.title, "model": model, "suite": suite,
                        "calibrated": suite.endswith("+cal"), "is_baseline": is_baseline, "status": "ok",
                        "message": None, "intelligence": scores.get("intelligence"), "ci95": _ci(scores),
                        "accuracy": scores.get("accuracy"), "errors": scores.get("errors"), "n": scores.get("n"),
                        "finished_at": finished, "suite_sha256": run.get("suite_sha256")}
                runs.append(item)
                root_runs.append(item)
        lab_mtime = None
        try:
            lab_mtime = iso(os.path.getmtime(os.path.join(root.abspath, "lab.json")))
        except OSError:
            pass
        for f in lab_json.get("failures") or []:
            text = str(f)
            model, sep, msg = text.partition(":")
            failures.append({"root_id": root.root_id, "lab": root.title, "model": model.strip() if sep else "?",
                             "suite": "", "calibrated": False, "is_baseline": False, "status": "failed",
                             "message": st.redact((msg if sep else text).strip())[:300], "intelligence": None,
                             "ci95": None, "accuracy": None, "errors": None, "n": None,
                             "finished_at": lab_mtime or root.finished_at})
        roots.append({"root_id": root.root_id, "title": root.title, "path": root.path, "kind": root.kind,
                      "finished_at": root.finished_at or max((r["finished_at"] or "" for r in root_runs), default=None),
                      "runs": root_runs, "failures": len(lab_json.get("failures") or [])})
    return runs, failures, roots


def _base(suite: str) -> str:
    return suite[: -len("+cal")] if suite.endswith("+cal") else suite


def top_on_suite(runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Highest Intelligence (local proxy) among raw non-baseline runs on the most recently scored non-smoke
    suite (same rows: same suite name and fingerprint), with the count of runs whose CI overlaps it."""
    raw = [r for r in runs if not r["calibrated"] and r["intelligence"] is not None]
    recent = sorted((r for r in raw if r["suite"] != "smoke"), key=lambda r: r["finished_at"] or "", reverse=True)
    if not recent:
        return None
    suite, sha = recent[0]["suite"], recent[0]["suite_sha256"]
    cands = [r for r in raw if r["suite"] == suite and not r["is_baseline"]
             and (sha is None or r["suite_sha256"] in (None, sha))]
    if not cands:
        return None
    top = max(cands, key=lambda r: (r["intelligence"], r["finished_at"] or ""))
    tied = sum(1 for r in cands if r is not top and _overlaps(r["ci95"], top["ci95"]))
    return {"root_id": top["root_id"], "lab": top["lab"], "model": top["model"], "suite": suite,
            "intelligence": top["intelligence"], "ci95": top["ci95"], "tied_with": tied, "suite_rows": top["n"],
            "accuracy": top["accuracy"]}


def _root_summary(root: dict[str, Any]) -> dict[str, Any]:
    by_suite: dict[str, list[dict[str, Any]]] = {}
    for r in root["runs"]:
        if not r["calibrated"]:
            by_suite.setdefault(r["suite"], []).append(r)
    suites_out = []
    for suite, rs in sorted(by_suite.items()):
        cands = [r for r in rs if not r["is_baseline"] and r["intelligence"] is not None]
        top = max(cands, key=lambda r: r["intelligence"]) if cands else None
        baseline = next((r for r in rs if r["is_baseline"] and r["intelligence"] is not None), None)
        suites_out.append({
            "suite": suite, "models": len(rs), "n": max((r["n"] or 0 for r in rs), default=0),
            "errors": sum(r["errors"] or 0 for r in rs),
            "top": None if top is None else {"model": top["model"], "intelligence": top["intelligence"],
                                             "ci95": top["ci95"],
                                             "tied_with": sum(1 for r in cands if r is not top
                                                              and _overlaps(r["ci95"], top["ci95"]))},
            "baseline": None if baseline is None else {"model": baseline["model"],
                                                       "intelligence": baseline["intelligence"]},
        })
    return {"root_id": root["root_id"], "title": root["title"], "path": root["path"], "kind": root["kind"],
            "finished_at": root["finished_at"], "failures": root["failures"], "suites": suites_out}


# ---- labs ----------------------------------------------------------------------------------------


def _labs(st: StudioState) -> list[dict[str, Any]]:
    out = []
    for lab in st.workspace.labs():
        valid = lab.parsed
        if valid:
            try:
                sdk_lab.load_lab(lab.abspath)
            except Exception:
                valid = False
        out.append({"lab_id": lab.lab_id, "name": lab.name, "path": lab.path, "valid": valid,
                    "modified_at": lab.modified_at})
    return out


# ---- cloud ---------------------------------------------------------------------------------------


def _owners(st: StudioState) -> set[str]:
    out = set()
    for j in st.jobs.list(status="active"):
        if j.get("machine_id"):
            out.add(str(j["machine_id"]))
    return out


def _fake_cloud(st: StudioState) -> dict[str, Any]:
    owners = _owners(st)
    cloud = st.cloud
    assert cloud is not None
    vast = cloud.vast_instances()["items"]
    aws = [i for i in cloud.aws_instances()["items"] if i.get("state") in ("running", "pending")]
    ident = cloud.aws_identity()
    return {
        "vast": {"configured": True, "instances": len(vast), "idle": sum(1 for i in vast if str(i["id"]) not in owners),
                 "usd_per_hour": round(sum(i["dph_total"] for i in vast), 3),
                 "credit_usd": cloud.vast_status()["credit_usd"], "error": None},
        "aws": {"configured": True, "instances": len(aws), "idle": sum(1 for i in aws if i["id"] not in owners),
                "usd_per_hour": None if any(i.get("usd_per_hour") is None for i in aws)
                else round(sum(i["usd_per_hour"] for i in aws), 3),
                "account": ident["account"], "profile": ident["profile"], "region": ident["region"],
                "error": None if all(i.get("usd_per_hour") is not None for i in aws) else "aws: unknown price"},
    }


def _aws_price(instance_type: str) -> float | None:
    try:
        from . import prices  # type: ignore[attr-defined]

        return prices.AWS_ON_DEMAND_USD_PER_HOUR.get(instance_type)  # type: ignore[no-any-return]
    except Exception:
        return None


def _real_vast(owners: set[str]) -> dict[str, Any]:
    from ..compute import vast

    rows = vast.instances()
    return {"configured": True, "instances": len(rows), "idle": sum(1 for r in rows if str(r.get("id")) not in owners),
            "usd_per_hour": round(sum(float(r.get("dph_total") or 0) for r in rows), 3),
            "credit_usd": vast.credit(), "error": None}


def _aws_configured() -> bool:
    if importlib.util.find_spec("boto3") is None:
        return False
    home = os.path.expanduser("~")
    return bool(os.environ.get("AWS_PROFILE") or os.environ.get("AWS_ACCESS_KEY_ID")
                or os.path.exists(os.path.join(home, ".aws", "config"))
                or os.path.exists(os.path.join(home, ".aws", "credentials")))


def _real_aws(owners: set[str]) -> dict[str, Any]:
    from ..compute import aws

    profile = os.environ.get("AWS_PROFILE") or None
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    session = aws._boto(profile, region)
    account = session.client("sts").get_caller_identity().get("Account")
    rows = [r for r in aws.tagged_instances(region, profile) if (r.get("State") or {}).get("Name") in
            ("running", "pending")]
    prices = [_aws_price(r.get("InstanceType", "")) for r in rows]
    unknown = [r.get("InstanceType") for r, p in zip(rows, prices, strict=False) if p is None]
    return {"configured": True, "instances": len(rows),
            "idle": sum(1 for r in rows if r.get("InstanceId") not in owners),
            "usd_per_hour": None if unknown else round(sum(p or 0 for p in prices), 3),
            "account": account, "profile": profile, "region": region,
            "error": f"aws: no price for {', '.join(sorted(set(unknown)))}" if unknown else None}


def _real_cloud(st: StudioState) -> dict[str, Any]:
    memo = st.extra.get("overview.cloud")
    if memo and time.time() - memo[0] < CLOUD_TTL_S:
        return memo[1]
    owners = _owners(st)
    out: dict[str, Any] = {"vast": {"configured": False}, "aws": {"configured": False}}
    futures = {}
    if shutil.which("vastai"):
        futures["vast"] = _pool.submit(_real_vast, owners)
    if _aws_configured():
        futures["aws"] = _pool.submit(_real_aws, owners)
    deadline = time.time() + CLOUD_TIMEOUT_S
    for name, fut in futures.items():
        try:
            out[name] = fut.result(timeout=max(0.0, deadline - time.time()))
        except concurrent.futures.TimeoutError:
            out[name] = {"configured": True, "error": f"{name}: timeout"}
        except Exception as e:  # a failed read is shown, never a partial sum
            msg = st.redact(str(e).splitlines()[0] if str(e) else type(e).__name__)[:160]
            out[name] = {"configured": True, "error": f"{name}: {msg}"}
    st.extra["overview.cloud"] = (time.time(), out)
    return out


def cloud_kpi(st: StudioState) -> dict[str, Any]:
    providers = _fake_cloud(st) if st.cloud is not None else _real_cloud(st)
    configured = [p for p in providers.values() if p.get("configured")]
    errors = [p["error"] for p in configured if p.get("error")]
    ok = [p for p in configured if not p.get("error")]
    known = bool(configured)
    rate = None
    if known and not errors:
        rate = round(sum(p.get("usd_per_hour") or 0 for p in ok), 3)
    return {"instances": sum(p.get("instances") or 0 for p in ok), "idle_instances": sum(p.get("idle") or 0 for p in ok),
            "usd_per_hour": rate, "known": known, "errors": errors, "fake": st.cloud is not None, **providers}


# ---- the endpoint --------------------------------------------------------------------------------


@router.get("/overview")
def overview(st: StudioState = Depends(get_state)) -> dict[str, Any]:
    from . import api_models

    lab_list = _labs(st)
    labs, labs_invalid = len(lab_list), sum(1 for x in lab_list if not x["valid"])
    runs, failures, roots = collect_runs(st)
    active = st.jobs.list(status="active")
    models = api_models.cached_models(st)
    recent = sorted((r for r in runs if not r["calibrated"]), key=lambda r: r["finished_at"] or "", reverse=True)
    recent = sorted(recent[:RECENT_RESULTS] + failures, key=lambda r: r["finished_at"] or "", reverse=True)
    recent = [{k: v for k, v in r.items() if k != "suite_sha256"} for r in recent]
    roots_sorted = sorted(roots, key=lambda r: r["finished_at"] or "", reverse=True)
    settings = st.settings.get()
    any_run_job = bool(st.jobs.list(kind="run", limit=1)) or bool(st.jobs.list(kind="eval", limit=1))
    return {
        "kpis": {
            "labs": labs, "labs_invalid": labs_invalid,
            "run_roots": len(roots),
            "runs_scored": len(runs),
            "runs_with_errors": sum(1 for r in runs if (r["errors"] or 0) > 0),
            "best": top_on_suite(runs),
            "jobs_active": len(active),
            "jobs_queued": sum(1 for j in active if j["status"] == "queued"),
            "jobs_active_remote": sum(1 for j in active if j.get("backend") not in (None, "local")),
            "cached_models": len(models),
            "cached_models_gb": round(sum(m.get("size_gb") or 0 for m in models), 2),
            "cloud": cloud_kpi(st),
        },
        "active_jobs": active[:5],
        "recent_results": recent[: RECENT_RESULTS + len(failures)],
        "recent_roots": [_root_summary(r) for r in roots_sorted[:RECENT_ROOTS]],
        "labs": lab_list,
        "onboarding": {
            "doctor_seen": bool(st.settings.flag("doctor_seen", False)),
            "has_lab": labs > 0,
            "has_run": bool(roots) or any_run_job,
            "has_results": bool(runs),
            "dismissed": bool(settings.get("onboarding_dismissed")),
        },
    }


# ---- quick eval ----------------------------------------------------------------------------------

BASELINES = ("uniform", "majority", "random")
EVAL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
PY_ATTR = re.compile(r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$")


def _int_field(body: dict[str, Any], key: str, default: int | None, lo: int, hi: int) -> int | None:
    v = body.get(key, default)
    if v is None:
        return None
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        raise ApiError(422, "bad_request", "Some fields are not valid.",
                       detail={"fields": [{"field": key, "message": f"a whole number from {lo} to {hi}"}]})
    return v


def start_eval_job(st: StudioState, body: dict[str, Any]) -> dict[str, Any]:
    """`POST /api/jobs {"kind": "eval", ...}`: one model on one suite with `decider-lab eval`."""
    from . import api_data, api_models
    from .jobs import JobManager

    model = body.get("model") if isinstance(body.get("model"), dict) else {}
    mtype, value = model.get("type"), str(model.get("value") or "").strip()
    problem = None
    if mtype not in ("url", "baseline", "serve", "python"):
        problem = ("model.type", "url, baseline, serve or python")
    elif not value:
        problem = ("model.value", "required")
    elif mtype == "url" and not re.match(r"^https?://\S+$", value):
        problem = ("model.value", "the http(s) URL of a System One server")
    elif mtype == "baseline" and value not in BASELINES:
        problem = ("model.value", "uniform, majority or random")
    elif mtype == "python" and not PY_ATTR.match(value):
        problem = ("model.value", "module:attr, e.g. my_model:Heuristic")
    name = str(body.get("name") or "model").strip()
    if problem is None and not EVAL_NAME.match(name):
        problem = ("name", "letters, digits, . _ - (up to 64)")
    split = body.get("split") or None
    if problem is None and split not in (None, "dev", "test", "all"):
        problem = ("split", "dev, test or all")
    if problem:
        raise ApiError(422, "bad_request", "Some fields are not valid.",
                       detail={"fields": [{"field": problem[0], "message": problem[1]}]})
    workers = _int_field(body, "workers", 4, 1, 64)
    limit = _int_field(body, "limit", None, 1, 1_000_000)
    if mtype == "url" and api_models._has_credential(value):
        raise ApiError(422, "credential_in_source", "This URL carries a credential.",
                       hint="Use a URL without user info or tokens.")
    suite = str(body.get("suite") or "smoke")
    if suite.startswith("file:"):
        suite_arg = st.workspace.rel(api_data._file_path(st, suite[5:]))
        suite_name = os.path.basename(suite_arg).split(".")[0]
    else:
        api_data.parse_builtin(suite)
        suite_arg, suite_name = suite, suite.partition(":")[0]
    if mtype == "serve":
        res = api_models.inspect_source(st, {"source": value, "require_pinned": False})
        errors = [p for p in res["problems"] if p["severity"] == "error"]
        if errors:
            raise ApiError(422, "source_invalid", errors[0]["message"], detail={"problems": res["problems"]})
    out = str(body.get("out") or "").strip() or f"runs/{name}/{suite_name}"
    if os.path.isabs(out):
        raise ApiError(400, "path_outside_workspace", "The output directory must be relative to the workspace.")
    st.workspace.resolve(out)
    args = ["eval"]
    if mtype == "serve":
        args += ["--serve", value, *(["--vision"] if body.get("vision") else [])]
    else:
        args += ["--model", f"python:{value}" if mtype == "python" else value]
    args += ["--suite", suite_arg, "--name", name, "--workers", str(workers), "--out", out]
    if limit:
        args += ["--limit", str(limit)]
    if split:
        args += ["--split", split]
    parent = os.path.dirname(out.rstrip("/")) or "."
    job = st.jobs.start("eval", JobManager.cli_argv(*args), title=f"eval {name} on {suite_name}",
                        cwd=st.workspace.root, root_id=st.workspace.encode_id(parent),
                        options={k: v for k, v in body.items() if k != "kind"})
    return {"job_id": job["job_id"], "status": job["status"], "title": job["title"], "command": job["command"]}
