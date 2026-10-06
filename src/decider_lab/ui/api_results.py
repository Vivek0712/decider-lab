"""Results API: Run roots, leaderboards with CIs, comparisons, exports (API.md section 7).

Run roots come from `st.workspace.run_roots()`; every number is read from the files the SDK wrote
(scores.json, report.json, calibration.json) or recomputed with `decider_lab.metrics` from
predictions.jsonl, never invented. Intelligence is always a local JevBench v1.5-rule proxy and every
response that carries it says so (`proxy_note`).

Owned endpoints (all under the `/api` prefix this router registers):

    GET /api/runs
    GET /api/runs/refs (registered before /api/runs/{root_id})
    GET, DELETE /api/runs/{root_id}
    GET /api/runs/{root_id}/leaderboard[.csv|.md]
    GET /api/runs/{root_id}/vs-baseline
    GET /api/runs/{root_id}/families
    GET /api/runs/{root_id}/latency
    GET /api/runs/{root_id}/jevbench
    GET /api/runs/{root_id}/calibration
    GET /api/runs/{root_id}/rows[/{row_id}]
    GET /api/runs/{root_id}/provenance
    POST /api/runs/{root_id}/report
    GET /api/runs/{root_id}/report.md, /report.json
    GET /api/runs/{root_id}/runs/{model}/{suite}[/predictions[.csv]|/reliability|/latency]
    GET /api/compare

Caches (paired bootstraps, rebuilt suite rows, parsed predictions) live in `st.extra["results"]`,
keyed by file paths and mtimes, so a re-run or a rebuilt report is picked up without a restart.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import shutil
import threading
from collections import Counter
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import FileResponse, Response

from .. import metrics
from ..rows import check as check_rows
from ..rows import read_jsonl
from .errors import ApiError
from .state import StudioState, get_state
from .workspace import RunRoot, decode_id, encode_id, iso

router = APIRouter(prefix="/api", tags=["results"])

PROXY_NOTE = ("Intelligence is a local proxy computed with the JevBench v1.5 rules on this suite; "
              "it is not a JevBench board score.")
JEVBENCH_LABEL = "JevBench public tasks · local v1.5-rule proxy"
JEVBENCH_NOTE = "local proxy of the JevBench v1.5 rules on the 231 public v1 tasks; not a board score"
JEVBENCH_DIR = "jevbench"
CAL = "+cal"
BOOTSTRAP_COMPARE = 2000
EXCERPT = 160
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp")
MIN_DEV_ROWS = 30
_LOCK = threading.RLock()


# ---- small helpers ---------------------------------------------------------------------------------


def _read_json(path: str) -> Any:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _num(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def _ci(x: Any) -> list[float | None] | None:
    if not isinstance(x, (list, tuple)) or len(x) != 2:
        return None
    return [_num(x[0]), _num(x[1])]


def _cache(st: StudioState) -> dict[str, Any]:
    with _LOCK:
        return st.extra.setdefault("results", {})


def _cached(st: StudioState, key: tuple, build: Any) -> Any:
    c = _cache(st)
    if key in c:
        return c[key]
    value = build()
    with _LOCK:
        if len(c) > 512:
            c.clear()
        c[key] = value
    return value


def verdict(diff: dict[str, Any] | None, lower_is_better: bool = False) -> str:
    """better / worse / unclear from a paired difference: only a CI that excludes 0 decides."""
    ci = _ci((diff or {}).get("ci95"))
    if not ci or ci[0] is None or ci[1] is None:
        return "unclear"
    lo, hi = ci
    if lower_is_better:
        return "better" if hi < 0 else "worse" if lo > 0 else "unclear"
    return "better" if lo > 0 else "worse" if hi < 0 else "unclear"


def _with_verdicts(c: dict[str, Any]) -> dict[str, Any]:
    out = {k: c.get(k) for k in ("n_paired", "only_a", "only_b")}
    for m in ("intelligence", "accuracy", "nll"):
        d = dict(c.get(m) or {"diff": None, "ci95": [None, None]})
        d["ci95"] = _ci(d.get("ci95")) or [None, None]
        d["verdict"] = verdict(d, lower_is_better=m == "nll")
        out[m] = d
    return out


def _scored(preds: list[dict[str, Any]], split: str | None) -> list[dict[str, Any]]:
    """Rows of `split` (rows without a split are kept), as report.py and cmd_compare do."""
    return [p for p in preds if not split or split == "all" or p.get("split") in (None, split)]


def _base(suite: str) -> str:
    return suite[: -len(CAL)] if suite.endswith(CAL) else suite


def _safe_name(name: str, what: str) -> str:
    if not name or name in (".", "..") or "/" in name or "\\" in name or "\x00" in name:
        raise ApiError(404, "not_found", f"No such {what}.", detail={"what": what})
    return name


# ---- run roots -------------------------------------------------------------------------------------


@dataclass
class Root:
    root_id: str
    abspath: str
    path: str
    kind: str
    title: str
    lab: dict[str, Any] | None          # lab.json (raw; redact before returning)
    finished_at: str | None

    @property
    def baseline(self) -> str | None:
        b = (self.lab or {}).get("baseline")
        return b if isinstance(b, str) and b else None

    @property
    def failures(self) -> list[str]:
        f = (self.lab or {}).get("failures") or []
        return [str(x) for x in f] if isinstance(f, list) else []


def _runs(root: str) -> dict[str, dict[str, str]]:
    """{model: {suite: run dir}} for every scored run under root (like report._runs)."""
    out: dict[str, dict[str, str]] = {}
    try:
        models = sorted(os.listdir(root))
    except OSError:
        return out
    for model in models:
        mdir = os.path.join(root, model)
        if model.startswith((".", "_")) or not os.path.isdir(mdir):
            continue
        try:
            suites = sorted(os.listdir(mdir))
        except OSError:
            continue
        for suite in suites:
            if os.path.exists(os.path.join(mdir, suite, "scores.json")):
                out.setdefault(model, {})[suite] = os.path.join(mdir, suite)
    return out


def _model_order(root: Root, runs: dict[str, dict[str, str]]) -> list[str]:
    lab_models = list(((root.lab or {}).get("models") or {}).keys())
    return [m for m in lab_models] + [m for m in runs if m not in lab_models]


def _base_suites(root: Root, runs: dict[str, dict[str, str]]) -> list[str]:
    """Base suite names (no +cal, no jevbench), in lab order where known."""
    found = {_base(s) for m in runs.values() for s in m if _base(s) != JEVBENCH_DIR}
    order: list[str] = []
    for spec in (root.lab or {}).get("suites") or []:
        n = _spec_name(spec)
        if n and n in found and n not in order:
            order.append(n)
    return order + sorted(found - set(order))


def _spec_name(spec: Any) -> str | None:
    """The name load_suite gives a lab suite spec."""
    if isinstance(spec, str):
        base = spec.partition(":")[0]
        if base in ("smoke", "synthetic", "heldout"):
            return base
        return os.path.basename(spec).split(".")[0]
    if isinstance(spec, dict):
        if spec.get("name"):
            return str(spec["name"])
        if "file" in spec:
            return os.path.basename(str(spec["file"])).split(".")[0]
        keys = [k for k in spec if k != "name"]
        return str(keys[0]) if keys else None
    return None


def _model_kind(spec: Any) -> str:
    if not isinstance(spec, dict):
        return "unknown"
    for k in ("serve", "finetuned", "url", "bedrock", "strands", "chat", "python", "baseline"):
        if k in spec:
            return k
    return "unknown"


def _load_root(st: StudioState, root_id: str) -> Root:
    path = decode_id(root_id)
    abspath = st.workspace.resolve(path)
    if not os.path.isdir(abspath):
        raise ApiError(404, "not_found", "This run root does not exist.", detail={"what": "run_root"})
    has_lab = os.path.isfile(os.path.join(abspath, "lab.json"))
    has_report = os.path.isfile(os.path.join(abspath, "report.json"))
    if not (has_lab or has_report or _runs(abspath)):
        raise ApiError(404, "not_found", "This directory holds no results.", detail={"what": "run_root"})
    lab = _read_json(os.path.join(abspath, "lab.json")) if has_lab else None
    lab = lab if isinstance(lab, dict) else None
    title = str((lab or {}).get("name") or os.path.basename(abspath))
    stamp = max((_mtime(os.path.join(abspath, n)) for n in ("lab.json", "report.json")
                 if os.path.exists(os.path.join(abspath, n))), default=None)
    if stamp is None:
        stamp = max((_mtime(os.path.join(d, "scores.json")) for m in _runs(abspath).values() for d in m.values()),
                    default=None)
    return Root(root_id=encode_id(st.workspace.rel(abspath)), abspath=abspath, path=st.workspace.rel(abspath),
                kind="lab" if (has_lab or has_report) else "eval", title=title, lab=lab, finished_at=iso(stamp))


def _root_from_scan(rr: RunRoot) -> Root:
    lab = _read_json(os.path.join(rr.abspath, "lab.json")) if rr.has_lab_json else None
    return Root(root_id=rr.root_id, abspath=rr.abspath, path=rr.path, kind=rr.kind, title=rr.title,
                lab=lab if isinstance(lab, dict) else None, finished_at=rr.finished_at)


def _lab_id_for(st: StudioState, root: Root, labs: list[Any] | None = None) -> str | None:
    """The lab file that produced this run root: same name, and the root sits under the lab's dir."""
    if root.kind != "lab":
        return None
    labs = st.workspace.labs() if labs is None else labs
    best: tuple[int, str] | None = None
    for lf in labs:
        if lf.name != root.title:
            continue
        lab_dir = os.path.dirname(lf.path)
        if lab_dir in ("", ".") or root.path == lab_dir or root.path.startswith(lab_dir + "/"):
            score = len(lab_dir)
            if best is None or score > best[0]:
                best = (score, lf.lab_id)
    return best[1] if best else None


def _scores(path: str) -> dict[str, Any] | None:
    s = _read_json(os.path.join(path, "scores.json"))
    return s if isinstance(s, dict) else None


def _run_meta(path: str) -> dict[str, Any]:
    m = _read_json(os.path.join(path, "run.json"))
    return m if isinstance(m, dict) else {}


def _preds(st: StudioState, run_dir: str) -> list[dict[str, Any]]:
    path = os.path.join(run_dir, "predictions.jsonl")
    if not os.path.isfile(path):
        return []

    def load() -> list[dict[str, Any]]:
        try:
            return list(read_jsonl(path))
        except (OSError, ValueError):
            return []

    return _cached(st, ("preds", path, _mtime(path)), load)


def _run_dir(root: Root, model: str, suite: str) -> str:
    _safe_name(model, "model")
    _safe_name(suite, "suite")
    d = os.path.join(root.abspath, model, suite)
    if not os.path.isfile(os.path.join(d, "scores.json")):
        raise ApiError(404, "not_found", f"There is no run of {model} on {suite} in this run root.",
                       detail={"what": "run"})
    return d


def _failure_for(root: Root, model: str) -> str | None:
    for f in root.failures:
        if f.split(":", 1)[0].split(" / ")[0].strip() == model:
            return f
    return None


# ---- list and detail -------------------------------------------------------------------------------


def _row_from_scores(s: dict[str, Any]) -> tuple[float | None, list[float | None] | None]:
    return _num(s.get("intelligence")), _ci(s.get("intelligence_ci95"))


def _summary(st: StudioState, root: Root, labs: list[Any]) -> dict[str, Any]:
    runs = _runs(root.abspath)
    order = _model_order(root, runs)
    suites = _base_suites(root, runs)
    has_cal = any(s.endswith(CAL) for m in runs.values() for s in m)
    has_jb = any(JEVBENCH_DIR in m for m in runs.values())
    best_by_suite = []
    for suite in suites:
        best = None
        for model in order:
            if model == root.baseline or suite not in runs.get(model, {}):
                continue
            s = _scores(runs[model][suite]) or {}
            val, ci = _row_from_scores(s)
            if val is None:
                continue
            if best is None or val > best["intelligence"]:
                best = {"suite": suite, "model": model, "intelligence": val, "ci95": ci or [None, None],
                        "n": s.get("n")}
        if best:
            best_by_suite.append(best)
    # the headline "best" is on the suite with the most scored rows (the most informative one)
    top = max(best_by_suite, key=lambda b: (b.get("n") or 0), default=None)
    return {
        "root_id": root.root_id, "lab": root.title, "lab_id": _lab_id_for(st, root, labs), "path": root.path,
        "kind": root.kind, "models": [m for m in order if m in runs or m in (root.lab or {}).get("models", {})],
        "suites": suites, "has_calibrated": has_cal, "has_jevbench": has_jb, "baseline": root.baseline,
        "failures": st.redact.obj(root.failures), "wall_s": _num((root.lab or {}).get("wall_s")),
        "finished_at": root.finished_at,
        "best": {k: top[k] for k in ("suite", "model", "intelligence", "ci95")} if top else None,
        "best_by_suite": [{k: b[k] for k in ("suite", "model", "intelligence", "ci95")} for b in best_by_suite],
        "proxy_note": PROXY_NOTE,
    }


@router.get("/runs")
def list_runs(lab_id: str | None = None, q: str | None = None, st: StudioState = Depends(get_state)) -> dict[str, Any]:
    labs = st.workspace.labs()
    items = []
    for rr in st.workspace.run_roots():
        root = _root_from_scan(rr)
        s = _summary(st, root, labs)
        if lab_id and s["lab_id"] != lab_id:
            continue
        if q:
            ql = q.lower()
            hay = " ".join([s["lab"], s["path"], *s["models"], *s["suites"]]).lower()
            if ql not in hay:
                continue
        items.append(s)
    items.sort(key=lambda s: s["finished_at"] or "", reverse=True)
    return {"items": items}


@router.get("/runs/refs")
def run_refs(st: StudioState = Depends(get_state)) -> dict[str, Any]:
    items = []
    for rr in st.workspace.run_roots():
        root = _root_from_scan(rr)
        runs = _runs(root.abspath)
        for model in _model_order(root, runs):
            for suite, d in sorted(runs.get(model, {}).items()):
                if suite == JEVBENCH_DIR:
                    continue
                s = _scores(d) or {}
                meta = _run_meta(d)
                items.append({"root_id": root.root_id, "lab": root.title, "model": model, "suite": suite,
                              "calibrated": suite.endswith(CAL), "n": s.get("n"),
                              "intelligence": _num(s.get("intelligence")), "ci95": _ci(s.get("intelligence_ci95")),
                              "limit": meta.get("limit") or (root.lab or {}).get("limit"),
                              "has_splits": (s.get("scored_split") or "all") != "all",
                              "finished_at": meta.get("finished_utc") or root.finished_at})
    return {"items": items}


@router.get("/runs/{root_id}")
def run_root(root_id: str, st: StudioState = Depends(get_state)) -> dict[str, Any]:
    root = _load_root(st, root_id)
    runs = _runs(root.abspath)
    order = _model_order(root, runs)
    specs = (root.lab or {}).get("models") or {}
    models = []
    ci = 0
    for m in order:
        is_base = m == root.baseline or _model_kind(specs.get(m)) == "baseline"
        models.append({"name": m, "color_index": None if is_base else ci, "is_baseline": m == root.baseline,
                       "kind": _model_kind(specs.get(m)), "has_runs": m in runs})
        if not is_base:
            ci += 1
    suites = []
    for suite in _base_suites(root, runs):
        with_suite = [m for m in order if suite in runs.get(m, {})]
        split = "all"
        for m in with_suite:
            split = (_scores(runs[m][suite]) or {}).get("scored_split") or split
            break
        suites.append({"name": suite, "has_calibrated": any(suite + CAL in runs.get(m, {}) for m in order),
                       "scored_split": split, "models": with_suite,
                       "calibrated_models": [m for m in order if suite + CAL in runs.get(m, {})]})
    lab_json = st.redact.obj({k: v for k, v in (root.lab or {}).items()}) if root.lab else None
    return {
        "root_id": root.root_id, "lab": root.title, "lab_id": _lab_id_for(st, root), "path": root.path,
        "kind": root.kind, "title": root.title, "baseline": root.baseline, "lab_json": lab_json,
        "models": models, "suites": suites,
        "jevbench_models": [m for m in order if JEVBENCH_DIR in runs.get(m, {})],
        "failures": st.redact.obj(root.failures),
        "report_md_available": os.path.isfile(os.path.join(root.abspath, "REPORT.md")),
        "report_json_available": os.path.isfile(os.path.join(root.abspath, "report.json")),
        "finished_at": root.finished_at, "wall_s": _num((root.lab or {}).get("wall_s")),
        "proxy_note": PROXY_NOTE,
    }


# ---- leaderboard -----------------------------------------------------------------------------------


def _lb_row(root: Root, runs: dict[str, dict[str, str]], model: str, suite: str, variant: str) -> dict[str, Any]:
    key = suite + (CAL if variant == "cal" else "")
    row: dict[str, Any] = {"model": model, "variant": variant, "status": "ok", "intelligence": None, "ci95": None,
                           "accuracy": None, "nll": None, "ece": None, "noul_in_band": None, "errors": None,
                           "n": None, "latency_s": None, "tie_group": None, "temperatures": None, "message": None,
                           "is_baseline": model == root.baseline}
    d = runs.get(model, {}).get(key)
    s = _scores(d) if d else None
    if not s:
        fail = _failure_for(root, model)
        if fail and suite not in runs.get(model, {}):
            row.update(status="failed", message=fail)
        else:
            row.update(status="missing", message=(
                "no +cal run: too few dev rows, no dev/test split, or calibration was not run"
                if variant == "cal" and suite in runs.get(model, {}) else f"{model} did not run {key}"))
        return row
    lat = s.get("latency_s") if isinstance(s.get("latency_s"), dict) else None
    row.update(intelligence=_num(s.get("intelligence")), ci95=_ci(s.get("intelligence_ci95")) or [None, None],
               accuracy=_num(s.get("accuracy")), nll=_num(s.get("nll")), ece=_num(s.get("ece")),
               noul_in_band=_num(((s.get("by_kind") or {}).get("noul") or {}).get("in_band")),
               errors=s.get("errors"), n=s.get("n"), latency_s=lat)
    if variant == "cal":
        cal = _read_json(os.path.join(d, "calibration.json")) or {}
        row["temperatures"] = cal.get("temperatures")
    return row


def _sort_and_tie(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ok = sorted([r for r in rows if r["status"] == "ok"],
                key=lambda r: (r["intelligence"] is None, -(r["intelligence"] or 0.0), r["model"]))
    rest = [r for r in rows if r["status"] != "ok"]
    rest.sort(key=lambda r: (r["status"] != "failed", r["model"]))
    group, prev = -1, None
    for r in ok:
        ci = r["ci95"] or [None, None]
        if prev is not None and None not in ci and None not in prev and ci[1] >= prev[0] and ci[0] <= prev[1]:
            pass
        else:
            group += 1
        r["tie_group"] = group
        prev = ci if None not in ci else None
    return ok + rest


def leaderboard_data(st: StudioState, root: Root, suite: str, scores: str) -> dict[str, Any]:
    if suite == JEVBENCH_DIR:
        raise ApiError(400, "use_jevbench_endpoint", "JevBench results have their own endpoint.",
                       hint="Use GET /api/runs/:root_id/jevbench.")
    suite = _base(suite)
    if scores not in ("raw", "cal", "both"):
        raise ApiError(422, "bad_request", "scores must be raw, cal or both.",
                       detail={"fields": [{"field": "scores", "message": "must be raw, cal or both"}]})
    runs = _runs(root.abspath)
    if suite not in _base_suites(root, runs):
        raise ApiError(404, "not_found", f"No model ran the suite {suite} in this run root.", detail={"what": "suite"})
    order = _model_order(root, runs)
    variants = ["raw", "cal"] if scores == "both" else [scores]
    by_variant = {}
    for v in variants:
        by_variant[v] = _sort_and_tie([_lb_row(root, runs, m, suite, v) for m in order])
    if scores == "both":
        # raw order decides; each model's +cal row sits right under its raw row
        cal_by_model = {r["model"]: r for r in by_variant["cal"]}
        rows = []
        for r in by_variant["raw"]:
            rows.append(r)
            c = cal_by_model.get(r["model"])
            if c and c["status"] == "ok":
                rows.append(c)
    else:
        rows = by_variant[variants[0]]
    for r in rows:
        r["message"] = st.redact(r["message"]) if r["message"] else None
    vals = [x for r in rows if r["status"] == "ok"
            for x in [r["intelligence"], *(r["ci95"] or [])] if x is not None]
    domain = [max(-100.0, min(vals) - 5), min(100.0, max(vals) + 5)] if vals else [0.0, 100.0]
    domain = [round(domain[0], 1), round(domain[1], 1)]
    ok_raw = [r for r in by_variant.get("raw", by_variant[variants[0]]) if r["status"] == "ok"]
    n_by_model = {r["model"]: r["n"] for r in ok_raw}
    sha_by_model, limited = {}, []
    for r in ok_raw:
        key = suite + (CAL if r["variant"] == "cal" else "")
        meta = _run_meta(runs[r["model"]][key])
        if meta.get("suite_sha256"):
            sha_by_model[r["model"]] = meta["suite_sha256"]
        if meta.get("limit") or (root.lab or {}).get("limit"):
            limited.append(r["model"])
    consistent = len(set(n_by_model.values())) <= 1 and len(set(sha_by_model.values())) <= 1 and not limited
    scored_split = "all"
    for r in ok_raw:
        scored_split = (_scores(runs[r["model"]][suite + (CAL if r["variant"] == "cal" else "")]) or {}).get(
            "scored_split") or "all"
        break
    return {"suite": suite, "scores": scores, "scored_split": scored_split, "baseline": root.baseline,
            "proxy_note": PROXY_NOTE, "rows": rows, "domain": domain,
            "same_rows": {"consistent": consistent, "n_by_model": n_by_model, "sha256_by_model": sha_by_model,
                          "limited": limited}}


@router.get("/runs/{root_id}/leaderboard")
def leaderboard(root_id: str, suite: str, scores: str = "raw", st: StudioState = Depends(get_state)) -> dict[str, Any]:
    return leaderboard_data(st, _load_root(st, root_id), suite, scores)


LB_COLUMNS = ["model", "variant", "intelligence_local_proxy", "ci95_lo", "ci95_hi", "accuracy", "nll", "ece",
              "noul_in_band", "errors", "n", "latency_p50", "latency_p95", "status", "message"]


def _lb_values(r: dict[str, Any]) -> list[Any]:
    ci = r["ci95"] or [None, None]
    lat = r["latency_s"] or {}
    return [r["model"], r["variant"], r["intelligence"], ci[0], ci[1], r["accuracy"], r["nll"], r["ece"],
            r["noul_in_band"], r["errors"], r["n"], lat.get("median"), lat.get("p95"), r["status"], r["message"] or ""]


def _attachment(name: str) -> dict[str, str]:
    return {"Content-Disposition": f'attachment; filename="{name}"'}


def _fname(*parts: str) -> str:
    return "-".join("".join(c if c.isalnum() or c in "._-" else "_" for c in p) for p in parts if p)


@router.get("/runs/{root_id}/leaderboard.csv")
def leaderboard_csv(root_id: str, suite: str, scores: str = "raw", st: StudioState = Depends(get_state)) -> Response:
    root = _load_root(st, root_id)
    lb = leaderboard_data(st, root, suite, scores)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(LB_COLUMNS)
    for r in lb["rows"]:
        w.writerow(["" if v is None else v for v in _lb_values(r)])
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers=_attachment(_fname(root.title, lb["suite"], scores, "leaderboard.csv")))


def leaderboard_markdown(root: Root, lb: dict[str, Any]) -> str:
    def f(x: Any, d: int = 1) -> str:
        return "—" if x is None else (f"{x:.{d}f}" if isinstance(x, float) else str(x))

    lines = [f"### {root.title} · {lb['suite']} ({lb['scores']})", "", lb["proxy_note"], "",
             "| # | model | variant | Intelligence (local proxy) | 95% CI | accuracy % | NLL | ECE | yes/no in band % "
             "| errors | n | latency p50 s | status |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(lb["rows"], 1):
        ci = r["ci95"] or [None, None]
        lat = (r["latency_s"] or {}).get("median")
        status = r["status"] if r["status"] == "ok" else f"{r['status']}: {r['message'] or ''}".replace("|", "/")
        lines.append(f"| {i if r['status'] == 'ok' else '—'} | {r['model']} | {r['variant']} | {f(r['intelligence'])} "
                     f"| {f(ci[0])} to {f(ci[1])} | {f(r['accuracy'])} | {f(r['nll'], 3)} | {f(r['ece'], 3)} "
                     f"| {f(r['noul_in_band'])} | {f(r['errors'])} | {f(r['n'])} | {f(lat, 3)} | {status} |")
    lines += ["", f"Scored on: {lb['scored_split']}. 95% CI: bootstrap over rows (1,000 resamples)."]
    return "\n".join(lines) + "\n"


@router.get("/runs/{root_id}/leaderboard.md")
def leaderboard_md(root_id: str, suite: str, scores: str = "raw", st: StudioState = Depends(get_state)) -> Response:
    root = _load_root(st, root_id)
    lb = leaderboard_data(st, root, suite, scores)
    return Response(leaderboard_markdown(root, lb), media_type="text/markdown; charset=utf-8",
                    headers=_attachment(_fname(root.title, lb["suite"], scores, "leaderboard.md")))


# ---- one run ---------------------------------------------------------------------------------------


def derive(p: dict[str, Any]) -> dict[str, Any]:
    """A prediction plus what the scorer derives from it (top, correct, proxy_right, in_band, expected_level)."""
    s = metrics.row_stats(p)
    probs = metrics.probs_of(p)
    top = max(range(len(probs)), key=lambda i: probs[i])
    kind = p.get("kind")
    proxy_right = None if kind == "score" else bool((s["credit"] or 0) > 0)
    return {**p, "top": top, "correct": bool(s["correct"]), "proxy_right": proxy_right,
            "in_band": bool(s["in_band"]) if kind == "noul" else None,
            "expected_level": round(sum(i * q for i, q in enumerate(probs)), 4) if kind == "score" else None,
            "p_top": round(probs[top], 6), "p_gold": round(probs[int(p["label"])], 6)}


@router.get("/runs/{root_id}/runs/{model}/{suite}")
def run_detail(root_id: str, model: str, suite: str, st: StudioState = Depends(get_state)) -> dict[str, Any]:
    root = _load_root(st, root_id)
    d = _run_dir(root, model, suite)
    preds = _preds(st, d)
    src = _read_json(os.path.join(root.abspath, model, "source.json"))
    cal = _read_json(os.path.join(d, "calibration.json")) if suite.endswith(CAL) else None
    errors = [{"id": p.get("id"), "error": st.redact(str(p["error"]))} for p in preds if p.get("error")][:5]
    rel = lambda name: st.workspace.rel(os.path.join(d, name))  # noqa: E731
    base = _base(suite)
    return {
        "ref": {"root_id": root.root_id, "model": model, "suite": suite}, "lab": root.title,
        "scores": _scores(d), "run": st.redact.obj(_run_meta(d)),
        "source": st.redact.obj(src) if isinstance(src, dict) else None,
        "calibration": st.redact.obj(cal) if isinstance(cal, dict) else None,
        "has_calibrated": os.path.isfile(os.path.join(root.abspath, model, base + CAL, "scores.json")),
        "is_baseline": model == root.baseline, "errors_sample": errors,
        "files": {"predictions": rel("predictions.jsonl"), "scores": rel("scores.json"), "run": rel("run.json")},
        "proxy_note": PROXY_NOTE,
    }


def _filter_preds(preds: list[dict[str, Any]], *, kind: str | None, family: str | None, split: str | None,
                  correct: str | None, error: str | None) -> list[dict[str, Any]]:
    out = []
    for p in preds:
        if kind and p.get("kind") != kind:
            continue
        if family and str(p.get("task")) != family:
            continue
        if split and split != "all" and p.get("split") != split:
            continue
        if error == "1" and not p.get("error"):
            continue
        out.append(p)
    rows = [derive(p) for p in out]
    if correct in ("0", "1"):
        rows = [r for r in rows if r["correct"] == (correct == "1")]
    return rows


@router.get("/runs/{root_id}/runs/{model}/{suite}/predictions")
def predictions(root_id: str, model: str, suite: str, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=500),
                kind: str | None = None, family: str | None = None, split: str | None = None,
                correct: str | None = None, error: str | None = None,
                st: StudioState = Depends(get_state)) -> dict[str, Any]:
    root = _load_root(st, root_id)
    rows = _filter_preds(_preds(st, _run_dir(root, model, suite)), kind=kind, family=family, split=split,
                         correct=correct, error=error)
    page = rows[offset: offset + limit]
    for r in page:
        if r.get("error"):
            r["error"] = st.redact(str(r["error"]))
    return {"items": page, "total": len(rows), "offset": offset, "limit": limit}


PRED_COLUMNS = ["id", "task", "kind", "split", "label", "n", "probs", "latency_s", "error", "top", "correct",
                "proxy_right", "in_band", "expected_level"]


@router.get("/runs/{root_id}/runs/{model}/{suite}/predictions.csv")
def predictions_csv(root_id: str, model: str, suite: str, kind: str | None = None, family: str | None = None,
                    split: str | None = None, correct: str | None = None, error: str | None = None,
                    st: StudioState = Depends(get_state)) -> Response:
    root = _load_root(st, root_id)
    rows = _filter_preds(_preds(st, _run_dir(root, model, suite)), kind=kind, family=family, split=split,
                         correct=correct, error=error)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(PRED_COLUMNS)
    for r in rows:
        vals = []
        for c in PRED_COLUMNS:
            v = r.get(c)
            if c == "probs":
                v = ";".join(str(x) for x in v) if v else ""
            elif c == "error":
                v = st.redact(str(v)) if v else ""
            vals.append("" if v is None else v)
        w.writerow(vals)
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers=_attachment(_fname(root.title, model, suite, "predictions.csv")))


def reliability_of(preds: list[dict[str, Any]], bins: int) -> dict[str, Any]:
    stats = [metrics.row_stats(p) for p in preds]
    cells: list[list[dict[str, Any]]] = [[] for _ in range(bins)]
    for s in stats:
        cells[min(bins - 1, int(s["conf"] * bins))].append(s)
    out = []
    for i, c in enumerate(cells):
        if not c:
            continue
        out.append({"lo": round(i / bins, 4), "hi": round((i + 1) / bins, 4), "n": len(c),
                    "mean_conf": round(sum(s["conf"] for s in c) / len(c), 4),
                    "accuracy": round(sum(s["correct"] for s in c) / len(c), 4)})
    return {"bins": out, "ece": round(metrics.ece(stats, bins), 4) if stats else None, "n": len(stats)}


@router.get("/runs/{root_id}/runs/{model}/{suite}/reliability")
def reliability(root_id: str, model: str, suite: str, bins: int = Query(10, ge=5, le=20), kind: str = "all",
                split: str | None = None, st: StudioState = Depends(get_state)) -> dict[str, Any]:
    if kind not in ("all", *metrics.KINDS):
        raise ApiError(422, "bad_request", "kind must be all, noul, choice or score.",
                       detail={"fields": [{"field": "kind", "message": "must be all, noul, choice or score"}]})
    root = _load_root(st, root_id)
    d = _run_dir(root, model, suite)
    split = split or (_scores(d) or {}).get("scored_split") or "all"

    def sel(ps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [p for p in _scored(ps, split) if kind == "all" or p.get("kind") == kind]

    out = {"kind": kind, "split": split, "bins_count": bins, **reliability_of(sel(_preds(st, d)), bins),
           "calibrated": None}
    if not suite.endswith(CAL):
        cal_dir = os.path.join(root.abspath, model, suite + CAL)
        if os.path.isfile(os.path.join(cal_dir, "scores.json")):
            out["calibrated"] = reliability_of(sel(_preds(st, cal_dir)), bins)
    return out


def latency_of(preds: list[dict[str, Any]]) -> dict[str, Any]:
    lat = sorted(float(p["latency_s"]) for p in preds if _num(p.get("latency_s")) is not None)
    errors = sum(1 for p in preds if p.get("error"))
    if not lat:
        return {"p50": None, "p95": None, "n": len(preds), "errors": errors, "histogram": []}
    lo, hi = lat[0], lat[-1]
    nb = 30
    if hi <= lo:
        edges = [lo, lo + 1e-6]
        nb = 1
    elif lo > 0 and hi / lo >= 3:
        edges = [lo * (hi / lo) ** (i / nb) for i in range(nb + 1)]
    else:
        edges = [lo + (hi - lo) * i / nb for i in range(nb + 1)]
    hist = [{"lo": round(edges[i], 6), "hi": round(edges[i + 1], 6), "ok": 0, "failed": 0} for i in range(nb)]
    for p in preds:
        v = _num(p.get("latency_s"))
        if v is None:
            continue
        i = nb - 1
        for j in range(nb):
            if v < edges[j + 1]:
                i = j
                break
        hist[i]["failed" if p.get("error") else "ok"] += 1
    return {"p50": round(lat[len(lat) // 2], 4), "p95": round(lat[int(0.95 * (len(lat) - 1))], 4),
            "n": len(preds), "errors": errors, "histogram": hist}


@router.get("/runs/{root_id}/runs/{model}/{suite}/latency")
def run_latency(root_id: str, model: str, suite: str, st: StudioState = Depends(get_state)) -> dict[str, Any]:
    root = _load_root(st, root_id)
    d = _run_dir(root, model, suite)
    split = (_scores(d) or {}).get("scored_split") or "all"
    return latency_of(_scored(_preds(st, d), split))


def _workers(root: Root, model: str) -> int | None:
    spec = ((root.lab or {}).get("models") or {}).get(model)
    w = spec.get("workers") if isinstance(spec, dict) else None
    w = w or (root.lab or {}).get("workers")
    return int(w) if isinstance(w, (int, float)) and not isinstance(w, bool) else None


@router.get("/runs/{root_id}/latency")
def root_latency(root_id: str, suite: str, st: StudioState = Depends(get_state)) -> dict[str, Any]:
    root = _load_root(st, root_id)
    runs = _runs(root.abspath)
    items = []
    for m in _model_order(root, runs):
        d = runs.get(m, {}).get(suite)
        if not d:
            continue
        split = (_scores(d) or {}).get("scored_split") or "all"
        lat = latency_of(_scored(_preds(st, d), split))
        meta = _run_meta(d)
        items.append({"model": m, "p50": lat["p50"], "p95": lat["p95"], "n": lat["n"], "errors": lat["errors"],
                      "host": st.redact(meta.get("host")), "workers": _workers(root, m),
                      "is_baseline": m == root.baseline})
    hosts = {(i["host"], i["workers"]) for i in items}
    return {"suite": suite, "items": items, "same_machine": len(hosts) <= 1}


# ---- vs baseline, families, calibration, jevbench --------------------------------------------------


def _compare_cached(st: StudioState, a_dir: str, b_dir: str, split: str | None) -> dict[str, Any]:
    pa_path, pb_path = os.path.join(a_dir, "predictions.jsonl"), os.path.join(b_dir, "predictions.jsonl")
    key = ("compare", pa_path, _mtime(pa_path), pb_path, _mtime(pb_path), split or "all")

    def build() -> dict[str, Any]:
        return metrics.compare(_scored(_preds(st, a_dir), split), _scored(_preds(st, b_dir), split),
                               bootstrap=BOOTSTRAP_COMPARE)

    return _cached(st, key, build)


@router.get("/runs/{root_id}/vs-baseline")
def vs_baseline(root_id: str, suite: str, variant: str = "raw", st: StudioState = Depends(get_state)) -> dict[str, Any]:
    if variant not in ("raw", "cal"):
        raise ApiError(422, "bad_request", "variant must be raw or cal.",
                       detail={"fields": [{"field": "variant", "message": "must be raw or cal"}]})
    root = _load_root(st, root_id)
    suite = _base(suite)
    base = root.baseline
    if not base:
        return {"suite": suite, "variant": variant, "baseline": None, "split": None, "items": []}
    key = suite + (CAL if variant == "cal" else "")
    runs = _runs(root.abspath)
    b_dir = runs.get(base, {}).get(key)
    if not b_dir:
        return {"suite": suite, "variant": variant, "baseline": base, "split": None, "items": [],
                "message": f"The baseline {base} has no {key} run."}
    split = (_scores(b_dir) or {}).get("scored_split")
    report = _read_json(os.path.join(root.abspath, "report.json")) or {}
    from_report = (report.get("vs_baseline") or {}) if isinstance(report, dict) else {}
    items = []
    for m in _model_order(root, runs):
        if m == base or key not in runs.get(m, {}):
            continue
        c = (from_report.get(m) or {}).get(key)
        if not isinstance(c, dict) or "intelligence" not in c:
            try:
                c = _compare_cached(st, runs[m][key], b_dir, split)
            except ValueError:
                continue
        row = _with_verdicts(c)
        items.append({"model": m, **row,
                      "verdict": {k: row[k]["verdict"] for k in ("intelligence", "accuracy", "nll")}})
    return {"suite": suite, "variant": variant, "baseline": base, "split": split or "all", "items": items,
            "bootstrap": BOOTSTRAP_COMPARE}


@router.get("/runs/{root_id}/families")
def families(root_id: str, suite: str, variant: str = "raw", st: StudioState = Depends(get_state)) -> dict[str, Any]:
    root = _load_root(st, root_id)
    suite = _base(suite)
    key = suite + (CAL if variant == "cal" else "")
    runs = _runs(root.abspath)
    models, cells, fams = [], [], set()
    for m in _model_order(root, runs):
        d = runs.get(m, {}).get(key)
        s = _scores(d) if d else None
        if not s:
            continue
        models.append(m)
        for fam, v in sorted((s.get("by_family") or {}).items()):
            fams.add(fam)
            cells.append({"family": fam, "model": m, "n": v.get("n"), "intelligence": _num(v.get("intelligence")),
                          "accuracy": _num(v.get("accuracy"))})
    return {"suite": suite, "variant": variant, "families": sorted(fams), "models": models, "cells": cells,
            "proxy_note": PROXY_NOTE}


def _dev_by_kind(preds: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for p in preds:
        if p.get("split") == "dev" and p.get("probs"):
            out[p["kind"]] = out.get(p["kind"], 0) + 1
    return out


@router.get("/runs/{root_id}/calibration")
def calibration(root_id: str, suite: str, st: StudioState = Depends(get_state)) -> dict[str, Any]:
    root = _load_root(st, root_id)
    suite = _base(suite)
    runs = _runs(root.abspath)
    items, eligible, ineligible = [], [], []
    for m in _model_order(root, runs):
        raw = runs.get(m, {}).get(suite)
        cal = runs.get(m, {}).get(suite + CAL)
        if cal and raw:
            info = _read_json(os.path.join(cal, "calibration.json")) or {}
            split = info.get("score_split") or "test"
            try:
                delta = _with_verdicts(_compare_cached(st, cal, raw, split))
            except ValueError:
                delta = None
            items.append({"model": m, "temperatures": info.get("temperatures"), "fit_split": info.get("fit_split"),
                          "fit_rows": info.get("fit_rows"), "score_split": split,
                          "before": info.get("before"), "after": info.get("after"), "delta": delta,
                          "is_baseline": m == root.baseline})
        elif raw:
            preds = _preds(st, raw)
            dev = _dev_by_kind(preds)
            if not any(p.get("split") for p in preds):
                ineligible.append({"model": m, "reason": "no dev/test split in this suite"})
            elif max(dev.values(), default=0) >= MIN_DEV_ROWS:
                eligible.append({"model": m, "dev_rows_by_kind": dev})
            else:
                worst = min(dev.items(), key=lambda kv: kv[1]) if dev else ("noul", 0)
                ineligible.append({"model": m, "dev_rows_by_kind": dev,
                                   "reason": f"too few dev rows ({worst[1]} of {worst[0]}; needs {MIN_DEV_ROWS})"})
    return {"suite": suite, "items": items, "eligible": eligible, "ineligible": ineligible,
            "bootstrap": BOOTSTRAP_COMPARE, "proxy_note": PROXY_NOTE}


@router.get("/runs/{root_id}/jevbench")
def jevbench(root_id: str, st: StudioState = Depends(get_state)) -> dict[str, Any]:
    root = _load_root(st, root_id)
    runs = _runs(root.abspath)
    items = []
    for m in _model_order(root, runs):
        d = runs.get(m, {}).get(JEVBENCH_DIR)
        s = _scores(d) if d else None
        if not s or "intelligence_proxy" not in s:
            continue
        items.append({"model": m, "intelligence_proxy": _num(s.get("intelligence_proxy")),
                      "n_correct": s.get("n_correct"), "tasks": s.get("tasks"),
                      "competence_by_type": s.get("competence_by_type") or {},
                      "yes_no_in_band": s.get("yes_no_in_band"), "yes_no": s.get("yes_no"),
                      "jevbench_commit": s.get("jevbench_commit"), "note": s.get("note") or JEVBENCH_NOTE,
                      "label": JEVBENCH_LABEL})
    return {"items": items, "label": JEVBENCH_LABEL, "note": JEVBENCH_NOTE}


# ---- rows explorer ---------------------------------------------------------------------------------


def _suite_rows(st: StudioState, root: Root, suite: str, runs: dict[str, dict[str, str]]) -> dict[str, Any]:
    """{"rows": {id: row} | None, "reason": str | None, "file": abs path | None} for a base suite,
    rebuilt from run.json suite params (or the lab's suite spec) without writing any cache."""
    meta: dict[str, Any] = {}
    for m in runs.values():
        if suite in m:
            meta = _run_meta(m[suite])
            if meta:
                break
    params = meta.get("suite_params") if isinstance(meta.get("suite_params"), dict) else {}
    key = ("suite_rows", root.abspath, suite, json.dumps(params, sort_keys=True, default=str),
           json.dumps((root.lab or {}).get("suites"), sort_keys=True, default=str))

    def build() -> dict[str, Any]:
        from .. import suites as S

        try:
            if "file" in params:
                path = str(params["file"])
                if not os.path.isabs(path):
                    path = os.path.join(root.abspath, path)
                if not os.path.isfile(path):
                    return {"rows": None, "file": None,
                            "reason": "The suite file this run used is no longer at its path."}
                rows = check_rows(read_jsonl(path))
                return {"rows": {r["id"]: r for r in rows}, "file": path, "reason": None}
            kind = suite if suite in ("smoke", "synthetic", "heldout") else None
            if kind is None:
                for spec in (root.lab or {}).get("suites") or []:
                    if _spec_name(spec) == suite and isinstance(spec, dict):
                        kind = next((k for k in spec if k != "name"), None)
            if kind in ("smoke", "synthetic"):
                p = dict(params)
                if kind == "smoke":
                    rows = S.synthetic(10, S.DEFAULT_FAMILIES, 1234)
                else:
                    rows = S.synthetic(int(p.get("per_kind", 150)), tuple(p.get("families") or S.DEFAULT_FAMILIES),
                                       int(p.get("seed", 0)))
                rows = check_rows(rows)
                return {"rows": {r["id"]: r for r in rows}, "file": None, "reason": None}
            if kind == "heldout":
                h = hashlib.sha256(json.dumps(["heldout", params], sort_keys=True).encode()).hexdigest()[:12]
                path = os.path.join(S.CACHE, "suites", f"heldout-{h}.jsonl")
                if not os.path.isfile(path):
                    return {"rows": None, "file": None,
                            "reason": "The heldout suite is not in the local cache; Studio does not download it "
                                      "to show rows."}
                rows = check_rows(read_jsonl(path))
                return {"rows": {r["id"]: r for r in rows}, "file": None, "reason": None}
            return {"rows": None, "file": None, "reason": f"Studio cannot rebuild the suite {suite} from this run."}
        except Exception as e:  # missing extra, invalid file: show predictions without content
            return {"rows": None, "file": None, "reason": f"Could not rebuild the suite rows: {type(e).__name__}."}

    return _cached(st, key, build)


def _text(x: Any) -> str:
    if isinstance(x, str):
        return x
    try:
        return json.dumps(x, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(x)


def _excerpt(x: Any) -> str | None:
    if x is None:
        return None
    t = " ".join(_text(x).split())
    return t if len(t) <= EXCERPT else t[: EXCERPT - 1] + "…"


def _answer(p: dict[str, Any] | None, options: list[Any] | None) -> dict[str, Any] | None:
    if p is None:
        return None
    d = derive(p)
    name = None
    if options and 0 <= d["top"] < len(options):
        name = str(options[d["top"]][0])
    elif p.get("kind") == "noul":
        name = ["false", "true"][d["top"]] if d["top"] in (0, 1) else None
    else:
        name = str(d["top"])
    return {"top": d["top"], "top_name": name, "p_top": d["p_top"], "p_gold": d["p_gold"],
            "proxy_right": d["proxy_right"], "correct": d["correct"], "in_band": d["in_band"],
            "expected_level": d["expected_level"], "error": p.get("error")}


def _wrong(a: dict[str, Any] | None) -> bool:
    if a is None:
        return False
    return (not a["proxy_right"]) if a["proxy_right"] is not None else (not a["correct"])


def _rows_context(st: StudioState, root: Root, suite: str, models_q: str | None) -> dict[str, Any]:
    runs = _runs(root.abspath)
    base = _base(suite)
    if base == JEVBENCH_DIR or base not in _base_suites(root, runs):
        raise ApiError(404, "not_found", f"No model ran the suite {suite} in this run root.", detail={"what": "suite"})
    order = [m for m in _model_order(root, runs) if suite in runs.get(m, {})]
    if not order:
        raise ApiError(404, "not_found", f"No model has a {suite} run.", detail={"what": "suite"})
    if models_q:
        wanted = [m.strip() for m in models_q.split(",") if m.strip()]
        order = [m for m in order if m in wanted] or order
    by_model = {m: {p["id"]: p for p in _preds(st, runs[m][suite]) if "id" in p} for m in order}
    content = _suite_rows(st, root, base, runs)
    ids: list[str] = []
    seen: set[str] = set()
    if content["rows"]:
        for rid in content["rows"]:
            if any(rid in by_model[m] for m in order):
                ids.append(rid)
                seen.add(rid)
    for m in order:
        for rid in by_model[m]:
            if rid not in seen:
                ids.append(rid)
                seen.add(rid)
    split = None
    for m in order:
        split = (_scores(runs[m][suite]) or {}).get("scored_split")
        break
    return {"order": order, "by_model": by_model, "content": content, "ids": ids, "scored_split": split or "all"}


@router.get("/runs/{root_id}/rows")
def rows(root_id: str, suite: str, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
         kind: str | None = None, family: str | None = None, split: str | None = None, q: str | None = None,
         show: str = "all", wrong_for: str | None = None, models: str | None = None,
         st: StudioState = Depends(get_state)) -> dict[str, Any]:
    if show not in ("all", "wrong", "disagree", "errors"):
        raise ApiError(422, "bad_request", "show must be all, wrong, disagree or errors.",
                       detail={"fields": [{"field": "show", "message": "must be all, wrong, disagree or errors"}]})
    root = _load_root(st, root_id)
    ctx = _rows_context(st, root, suite, models)
    order, by_model, content = ctx["order"], ctx["by_model"], ctx["content"]
    split = split or ctx["scored_split"]
    rows_by_id = content["rows"] or {}
    ql = q.lower().strip() if q else None
    items, families_seen, kinds_seen, splits_seen = [], set(), set(), set()
    for rid in ctx["ids"]:
        first = next((by_model[m][rid] for m in order if rid in by_model[m]), None)
        row = rows_by_id.get(rid)
        meta = row or first or {}
        r_kind, r_task, r_split = meta.get("kind"), str(meta.get("task") or "unknown"), meta.get("split")
        families_seen.add(r_task)
        kinds_seen.add(r_kind)
        if r_split:
            splits_seen.add(r_split)
        if kind and r_kind != kind:
            continue
        if family and r_task != family:
            continue
        if split and split != "all" and r_split not in (None, split):
            continue
        if ql:
            hay = (rid + " " + r_task + " " + (_text(row.get("state")) + " " + _text(row.get("instructions"))
                                               if row else "")).lower()
            if ql not in st.redact(hay):
                continue
        options = row.get("options") if row else None
        answers = {m: _answer(by_model[m].get(rid), options) for m in order}
        present = [a for a in answers.values() if a is not None]
        if show == "errors" and not any(a["error"] for a in present):
            continue
        if show == "disagree" and len({a["top"] for a in present}) < 2:
            continue
        if show == "wrong":
            if wrong_for and wrong_for in answers:
                if not _wrong(answers[wrong_for]):
                    continue
            elif not any(_wrong(a) for a in present):
                continue
        items.append((rid, meta, row, answers))
    total = len(items)
    page = []
    for rid, meta, row, answers in items[offset: offset + limit]:
        label = int(meta.get("label", 0))
        options = row.get("options") if row else None
        gold = (str(options[label][0]) if options and 0 <= label < len(options)
                else (["false", "true"][label] if meta.get("kind") == "noul" and label in (0, 1) else None))
        for a in answers.values():
            if a and a["error"]:
                a["error"] = st.redact(str(a["error"]))
        page.append({"id": rid, "task": str(meta.get("task") or "unknown"), "kind": meta.get("kind"),
                     "split": meta.get("split"), "label": label, "gold_name": gold,
                     "n_options": len(options) if options else (int(meta.get("n") or 0) or None),
                     "state_excerpt": st.redact(_excerpt(row.get("state"))) if row else None,
                     "instructions_excerpt": st.redact(_excerpt(row.get("instructions"))) if row else None,
                     "answers": answers})
    out: dict[str, Any] = {"suite": suite, "split": split, "models": order, "items": page, "total": total,
                           "offset": offset, "limit": limit, "content_available": content["rows"] is not None,
                           "content_reason": content["reason"], "families": sorted(families_seen),
                           "kinds": [k for k in metrics.KINDS if k in kinds_seen], "splits": sorted(splits_seen)}
    return out


def _image(st: StudioState, src: str, base_dir: str | None, rid: str, i: int, total: int) -> dict[str, Any]:
    alt = f"image {i + 1} of {total} for row {rid[:8]}"
    if src.startswith("data:image/"):
        return {"src": src, "alt": alt}
    if src.startswith("data:"):
        return {"src": None, "alt": "embedded data that is not an image: not shown"}
    candidates = [src] if os.path.isabs(src) else [os.path.join(d, src) for d in (base_dir, st.workspace.root) if d]
    for c in candidates:
        real = os.path.realpath(c)
        if real.startswith(st.workspace.root + os.sep) and os.path.isfile(real) and real.lower().endswith(IMAGE_EXT):
            return {"src": f"/api/files/raw?file_id={encode_id(st.workspace.rel(real))}", "alt": alt}
    return {"src": None, "alt": "image outside the workspace: not shown"}


@router.get("/runs/{root_id}/rows/{row_id}")
def row_detail(root_id: str, row_id: str, suite: str, st: StudioState = Depends(get_state)) -> dict[str, Any]:
    root = _load_root(st, root_id)
    ctx = _rows_context(st, root, suite, None)
    order, by_model, content = ctx["order"], ctx["by_model"], ctx["content"]
    row = (content["rows"] or {}).get(row_id)
    first = next((by_model[m][row_id] for m in order if row_id in by_model[m]), None)
    if row is None and first is None:
        raise ApiError(404, "not_found", "No such row in this suite.", detail={"what": "row"})
    meta = row or first or {}
    options = row.get("options") if row else None
    answers = {}
    for m in order:
        p = by_model[m].get(row_id)
        if p is None:
            answers[m] = None
            continue
        a = _answer(p, options) or {}
        answers[m] = {**a, "probs": p.get("probs"), "latency_s": p.get("latency_s"),
                      "error": st.redact(str(p["error"])) if p.get("error") else None}
    state = row.get("state") if row else None
    images = []
    if row and isinstance(row.get("images"), list):
        base_dir = os.path.dirname(content["file"]) if content.get("file") else None
        imgs = [x for x in row["images"] if isinstance(x, str)]
        images = [_image(st, x, base_dir, row_id, i, len(imgs)) for i, x in enumerate(imgs)]
    pos = ctx["ids"].index(row_id) if row_id in ctx["ids"] else None
    return {"id": row_id, "suite": suite, "task": str(meta.get("task") or "unknown"), "kind": meta.get("kind"),
            "split": meta.get("split"), "label": int(meta.get("label", 0)),
            "state": st.redact.obj(state) if row else None, "state_is_json": row is not None and not isinstance(state, str),
            "instructions": st.redact(row.get("instructions")) if row else None,
            "options": options, "images": images, "models": order, "answers": answers,
            "content_available": row is not None, "content_reason": None if row else content["reason"],
            "position": pos}


# ---- provenance ------------------------------------------------------------------------------------


def _job_for(st: StudioState, root: Root) -> str | None:
    try:
        jobs = st.jobs.list(limit=1000)
    except Exception:  # the job store is optional for provenance
        return None
    for j in jobs:
        if j.get("root_id") == root.root_id:
            return j.get("id") or j.get("job_id")
    return None


@router.get("/runs/{root_id}/provenance")
def provenance(root_id: str, st: StudioState = Depends(get_state)) -> dict[str, Any]:
    root = _load_root(st, root_id)
    runs = _runs(root.abspath)
    job_id = _job_for(st, root)
    items = []
    for m in _model_order(root, runs):
        src = _read_json(os.path.join(root.abspath, m, "source.json"))
        for suite, d in sorted(runs.get(m, {}).items()):
            meta = _run_meta(d)
            cal = meta.get("calibration") if isinstance(meta.get("calibration"), dict) else None
            cal_from = meta.get("calibrated_from")
            if isinstance(cal_from, str):
                try:
                    cal_from = st.workspace.rel(cal_from)
                except ApiError:
                    # written on another machine or before the run root moved: keep <model>/<suite>
                    cal_from = "/".join(cal_from.rstrip("/").split("/")[-2:])
            if suite == JEVBENCH_DIR:
                s = _scores(d) or {}
                meta = {"suite": JEVBENCH_DIR, "jevbench_commit": s.get("jevbench_commit"), **meta}
            items.append(st.redact.obj({
                "model": m, "suite": suite, "suite_sha256": meta.get("suite_sha256"),
                "suite_params": meta.get("suite_params"), "n_rows": meta.get("n_rows"),
                "answerer": meta.get("answerer"), "source": src if isinstance(src, dict) else None,
                "host": meta.get("host"), "platform": meta.get("platform"), "python": meta.get("python"),
                "gpu": meta.get("gpu"), "decider_lab": meta.get("decider_lab"),
                "finished_utc": meta.get("finished_utc"), "wall_s": meta.get("wall_s"),
                "workers": _workers(root, m), "limit": meta.get("limit") or (root.lab or {}).get("limit"),
                "calibration": cal, "calibrated_from": cal_from, "jevbench_commit": meta.get("jevbench_commit"),
                "job_id": job_id}))
    same_rows: dict[str, Any] = {}
    for suite in _base_suites(root, runs):
        shas = {i["model"]: i["suite_sha256"] for i in items if i["suite"] == suite and i["suite_sha256"]}
        common = Counter(shas.values()).most_common(1)
        sha = common[0][0] if common else None
        mism = sorted(m for m, s in shas.items() if s != sha)
        same_rows[suite] = {"consistent": not mism, "sha256": sha, "mismatched_models": mism}
    return {"items": items, "same_rows": same_rows, "job_id": job_id}


# ---- compare ---------------------------------------------------------------------------------------


def _parse_ref(st: StudioState, value: str, which: str) -> tuple[Root, str, str, str]:
    parts = value.split(":")
    if len(parts) != 3:
        raise ApiError(422, "bad_request", f"{which} must be <root_id>:<model>:<suite>.",
                       detail={"fields": [{"field": which, "message": "must be <root_id>:<model>:<suite>"}]})
    root_id, model, suite = (unquote(p) for p in parts)
    root = _load_root(st, root_id)
    return root, model, suite, _run_dir(root, model, suite)


@router.get("/compare")
def compare(a: str, b: str, split: str = "test", st: StudioState = Depends(get_state)) -> dict[str, Any]:
    if split not in ("test", "dev", "all"):
        raise ApiError(422, "bad_request", "split must be test, dev or all.",
                       detail={"fields": [{"field": "split", "message": "must be test, dev or all"}]})
    ra, ma, sa, da = _parse_ref(st, a, "a")
    rb, mb, sb, db = _parse_ref(st, b, "b")
    pa, pb = _preds(st, da), _preds(st, db)
    has_splits = any(p.get("split") for p in pa) or any(p.get("split") for p in pb)
    try:
        c = _compare_cached(st, da, db, None if split == "all" else split)
    except ValueError as e:
        raise ApiError(422, "no_shared_rows", "These two runs share no rows: they scored different suites.",
                       hint="Pick two runs of the same suite.") from e
    sha_a, sha_b = _run_meta(da).get("suite_sha256"), _run_meta(db).get("suite_sha256")
    notes = []
    if not has_splits:
        notes.append("These runs have no dev/test split: all rows are compared.")
    if sha_a and sha_b and sha_a != sha_b:
        notes.append(f"Only the {c['n_paired']} shared rows are compared.")
    for root, model, d in ((ra, ma, da), (rb, mb, db)):
        lim = _run_meta(d).get("limit") or (root.lab or {}).get("limit")
        if lim:
            notes.append(f"{root.title} / {model} was run with --limit {lim}.")
    row = _with_verdicts(c)
    return {"a": {"root_id": ra.root_id, "model": ma, "suite": sa, "label": f"{ra.title} / {ma} / {sa}"},
            "b": {"root_id": rb.root_id, "model": mb, "suite": sb, "label": f"{rb.title} / {mb} / {sb}"},
            "split": split, "same_suite_sha256": bool(sha_a and sha_a == sha_b), **row,
            "bootstrap": BOOTSTRAP_COMPARE, "has_splits": has_splits, "notes": notes, "proxy_note": PROXY_NOTE}


# ---- report, exports, delete -----------------------------------------------------------------------


@router.post("/runs/{root_id}/report")
def rebuild_report(root_id: str, body: dict[str, Any] | None = Body(default=None),
                   st: StudioState = Depends(get_state)) -> dict[str, Any]:
    from .. import report

    root = _load_root(st, root_id)
    baseline = (body or {}).get("baseline", root.baseline)
    if baseline is not None and not isinstance(baseline, str):
        raise ApiError(422, "bad_request", "baseline must be a model name.",
                       detail={"fields": [{"field": "baseline", "message": "must be a string"}]})
    if baseline and baseline not in _runs(root.abspath):
        raise ApiError(422, "bad_request", f"The baseline {baseline} has no runs in this run root.",
                       detail={"fields": [{"field": "baseline", "message": "not a model with runs here"}]})
    try:
        path = report.write(root.abspath, baseline=baseline or None, title=root.title)
    except (OSError, ValueError, KeyError) as e:
        raise ApiError(500, "internal", "Could not rebuild the report.", hint=st.redact(str(e))[:200]) from e
    st.bus.publish("runs.changed", {"root_ids": [root.root_id]})
    return {"report_md": st.workspace.rel(path), "report_json": st.workspace.rel(os.path.join(root.abspath, "report.json"))}


@router.get("/runs/{root_id}/report.md")
def report_md(root_id: str, st: StudioState = Depends(get_state)) -> Response:
    root = _load_root(st, root_id)
    p = os.path.join(root.abspath, "REPORT.md")
    if not os.path.isfile(p):
        raise ApiError(404, "not_found", "This run root has no REPORT.md yet.", hint="Rebuild the report.",
                       detail={"what": "file"})
    return FileResponse(p, media_type="text/markdown; charset=utf-8",
                        headers={**_attachment(_fname(root.title, "REPORT.md")), "Cache-Control": "no-store"})


@router.get("/runs/{root_id}/report.json")
def report_json(root_id: str, st: StudioState = Depends(get_state)) -> Response:
    root = _load_root(st, root_id)
    p = os.path.join(root.abspath, "report.json")
    if not os.path.isfile(p):
        raise ApiError(404, "not_found", "This run root has no report.json yet.", hint="Rebuild the report.",
                       detail={"what": "file"})
    return FileResponse(p, media_type="application/json",
                        headers={**_attachment(_fname(root.title, "report.json")), "Cache-Control": "no-store"})


def _dir_size(path: str) -> int:
    total = 0
    for dp, _dn, fns in os.walk(path):
        for f in fns:
            try:
                total += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return total


@router.delete("/runs/{root_id}")
def delete_root(root_id: str, body: dict[str, Any] | None = Body(default=None),
                st: StudioState = Depends(get_state)) -> dict[str, Any]:
    root = _load_root(st, root_id)
    phrase = f"delete {root.title}"
    if (body or {}).get("confirm") != phrase:
        raise ApiError(409, "confirm_mismatch", "The confirmation text does not match.",
                       detail={"expected_hint": f"type delete followed by the lab name ({root.title})"})
    if root.abspath == st.workspace.root or root.abspath == st.workspace.state_dir:
        raise ApiError(400, "bad_request", "Studio does not delete the workspace itself.")
    for j in st.jobs.list(status="active", limit=1000):
        if j.get("root_id") == root.root_id:
            raise ApiError(409, "job_active", "A job is writing to this run root.",
                           hint="Cancel the job or wait for it to finish.",
                           detail={"job_id": j.get("id") or j.get("job_id")})
    size = _dir_size(root.abspath)
    shutil.rmtree(root.abspath)
    with _LOCK:
        _cache(st).clear()
    st.bus.publish("runs.changed", {"root_ids": [root.root_id]})
    return {"deleted": True, "freed_mb": round(size / 1e6, 1)}
