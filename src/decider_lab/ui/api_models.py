"""Models API: the model cache, source inspection and pulls (API.md section 8).

    GET    /api/models                 cached models: the decider-lab cache (`sources.cached()`) plus the
                                       Hugging Face cache entries of strands-decider repos (read only)
    POST   /api/models/inspect         classify a source as you type (no network)
    DELETE /api/models/{model_key}     remove one decider-lab cache directory (typed: first 8 of its ref)

Pulls are jobs (`POST /api/jobs {"kind": "pull", ...}`, API.md section 6). The jobs router owns
`POST /api/jobs`; it delegates `kind: "pull"` to `start_pull_job(st, body)` here. Until that router
exists, this module serves a fallback for `POST /api/jobs` (kinds `pull` and `eval`, the latter via
`api_overview.start_eval_job`) and `GET /api/jobs/{job_id}`; the jobs router is included first, so
its routes win once it implements them (then delete the two fallbacks).

`install_job_hooks(st)` registers the pull log parser (stages, bytes) and the finish handler (result,
`models.changed`); it is idempotent and called by every route here.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import time
import urllib.parse
from typing import Any

import yaml
from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from .. import sources, suites
from .errors import ApiError
from .jobs import JobManager, default_stages
from .state import StudioState, get_state
from .workspace import iso, now_iso

router = APIRouter(prefix="/api", tags=["models"])

MODEL_KEY = re.compile(r"^[0-9a-f]{16}$")
SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
HF_REPO = re.compile(r"^[A-Za-z0-9][\w.-]*/[\w.-]+$")
CREDENTIAL_QUERY = re.compile(r"(?i)(^|&)(x-amz-(signature|credential|security-token)|signature|sig|token|"
                              r"access_token|api_key|apikey|key|awsaccesskeyid)=")
HF_NOTE = "Hugging Face snapshots live in the HF cache; Studio lists strands-decider repos from it but never deletes them."
HF_SCAN_TTL_S = 30.0


def _p(severity: str, code: str, message: str, path: str | None = "source") -> dict[str, Any]:
    return {"severity": severity, "code": code, "message": message, "line": None, "column": None, "path": path}


# ---- the caches ----------------------------------------------------------------------------------


def models_dir() -> str:
    return os.path.join(suites.CACHE, "models")


def _ref(info: dict[str, Any]) -> tuple[str | None, str]:
    for field, kind in (("resolved_commit", "commit"), ("sha256", "sha256"), ("etags_sha256", "etags")):
        if info.get(field):
            return str(info[field]), kind
    return None, "none"


def _requested_sha256(model_key: str, info: dict[str, Any]) -> bool:
    """True when the pull was asked to verify a sha256 (the SDK keys the cache on it)."""
    got = info.get("sha256")
    if not got:
        return False
    key = hashlib.sha256(json.dumps([info.get("source"), got], sort_keys=True).encode()).hexdigest()[:16]
    return key == model_key


def _dir_size(path: str) -> int:
    total = 0
    for p, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(p, f))
            except OSError:
                pass
    return total


def _lab_serve_specs(st: StudioState) -> dict[str, set[str]]:
    """{serve source: {lab names}} for every `serve:` model in the workspace's labs."""
    out: dict[str, set[str]] = {}
    for lab in st.workspace.labs():
        try:
            with open(lab.abspath, encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except (OSError, yaml.YAMLError):
            continue
        models = data.get("models") if isinstance(data, dict) else None
        if not isinstance(models, dict):
            continue
        for spec in models.values():
            src = spec.get("serve") if isinstance(spec, dict) else None
            if isinstance(src, str) and src.strip():
                out.setdefault(_norm_source(src.strip()), set()).add(lab.name)
    return out


def _norm_source(src: str) -> str:
    s = src.split("?", 1)[0]
    if not s.startswith(("hf://", "s3://", "http://", "https://")) and HF_REPO.match(s.split("@")[0]) \
            and not os.path.exists(s):
        s = "hf://" + s
    return s


def _labs_using(source: str, specs: dict[str, set[str]]) -> list[str]:
    want = _norm_source(source)
    base = want.split("@", 1)[0] if want.startswith("hf://") else want
    names: set[str] = set()
    for spec, labs in specs.items():
        if spec == want or (want.startswith("hf://") and spec.split("@", 1)[0] == base):
            names |= labs
    return sorted(names)


def _active_job_for(st: StudioState, source: str, labs: list[str]) -> str | None:
    want = _norm_source(source)
    lab_names = set(labs)
    for j in st.jobs.list(status="active"):
        rec = st.jobs.get(j["job_id"])
        opts = rec.get("options") or {}
        if rec["kind"] == "pull" and _norm_source(str(opts.get("source") or "")) == want:
            return rec["job_id"]
        if rec["kind"] == "run" and lab_names and any(name in rec.get("title", "").split() for name in lab_names):
            return rec["job_id"]
    return None


def _hf_cache_dir() -> str:
    env = os.environ.get("HF_HUB_CACHE") or os.environ.get("HUGGINGFACE_HUB_CACHE")
    if env:
        return env
    home = os.environ.get("HF_HOME") or os.path.join(os.path.expanduser("~"), ".cache", "huggingface")
    return os.path.join(home, "hub")


def hf_cache_entries(st: StudioState) -> list[dict[str, Any]]:
    """Snapshots of strands-decider repos in the Hugging Face cache (cached for 30 s)."""
    cache_dir = _hf_cache_dir()
    memo = st.extra.get("models.hf_scan")
    if memo and memo[0] == cache_dir and time.time() - memo[1] < HF_SCAN_TTL_S:
        return memo[2]
    items: list[dict[str, Any]] = []
    if os.path.isdir(cache_dir):
        try:
            from huggingface_hub import scan_cache_dir

            info = scan_cache_dir(cache_dir)
            for repo in sorted(info.repos, key=lambda r: r.repo_id):
                if repo.repo_type != "model" or "decider" not in repo.repo_id.lower():
                    continue
                for rev in sorted(repo.revisions, key=lambda r: r.last_modified, reverse=True):
                    refs = sorted(rev.refs)
                    items.append({
                        "model_key": f"hf-{rev.commit_hash[:16]}",
                        "source": f"hf://{repo.repo_id}@{rev.commit_hash}",
                        "kind": "hf", "ref": rev.commit_hash, "ref_kind": "commit",
                        "pinned": not refs, "hf_refs": refs,
                        "size_gb": round(rev.size_on_disk / 1e9, 2), "dir": str(rev.snapshot_path),
                        "pulled_at": iso(rev.last_modified), "location": "hf", "deletable": False,
                    })
        except Exception:  # an unreadable HF cache is not an error for this page
            items = []
    st.extra["models.hf_scan"] = (cache_dir, time.time(), items)
    return items


def cached_models(st: StudioState, *, include_hf: bool = True) -> list[dict[str, Any]]:
    specs = _lab_serve_specs(st)
    items: list[dict[str, Any]] = []
    for row in sources.cached():
        key = os.path.basename(row["dir"])
        ref, ref_kind = _ref(row)
        pinned = bool(row.get("pinned")) if ref_kind == "commit" else _requested_sha256(key, row)
        try:
            pulled = iso(os.path.getmtime(row["dir"]))
        except OSError:
            pulled = None
        source = st.redact(str(row.get("source") or ""))
        labs = _labs_using(source, specs)
        items.append({"model_key": key, "source": source, "kind": row.get("kind") or sources.kind_of(source),
                      "ref": ref, "ref_kind": ref_kind, "pinned": pinned, "size_gb": row.get("size_gb", 0.0),
                      "dir": row["dir"], "pulled_at": pulled or row.get("pulled_utc"), "used_by_labs": labs,
                      "in_use_by_job": _active_job_for(st, source, labs), "location": "decider-lab",
                      "deletable": True})
    if include_hf:
        for row in hf_cache_entries(st):
            labs = _labs_using(row["source"], specs)
            items.append({**row, "used_by_labs": labs, "in_use_by_job": _active_job_for(st, row["source"], labs)})
    return items


@router.get("/models")
def list_models(st: StudioState = Depends(get_state)) -> dict[str, Any]:
    install_job_hooks(st)
    items = cached_models(st)
    total = sum(i["size_gb"] or 0 for i in items if i.get("location") == "decider-lab")
    return {"cache_dir": models_dir(), "hf_cache_dir": _hf_cache_dir(), "total_gb": round(total, 2),
            "items": items, "hf_cache_note": HF_NOTE}


# ---- inspect -------------------------------------------------------------------------------------


def _has_credential(source: str) -> bool:
    try:
        p = urllib.parse.urlsplit(source)
    except ValueError:
        return False
    if p.scheme in ("http", "https", "s3") and (p.username or p.password or "@" in p.netloc):
        return True
    return bool(p.query and CREDENTIAL_QUERY.search(p.query))


def _is_loopback_url(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1") or host.startswith("127.")


def _classify(source: str, st: StudioState) -> str:
    if source.startswith("hf://"):
        return "hf"
    if source.startswith("s3://"):
        return "s3"
    if source.startswith(("http://", "https://")):
        return "url"
    if source.startswith(("/", ".", "~")) or os.path.exists(os.path.join(st.workspace.root, source)):
        return "local"
    if HF_REPO.match(source.split("@")[0]):
        return "hf"
    return "local"


def _local_path(source: str, st: StudioState) -> str:
    p = os.path.expanduser(source)
    if not os.path.isabs(p):
        p = os.path.join(st.workspace.root, p)
    return os.path.realpath(p)


def _inside(path: str, root: str) -> bool:
    root = os.path.realpath(root)
    return path == root or path.startswith(root + os.sep)


def inspect_source(st: StudioState, body: dict[str, Any]) -> dict[str, Any]:
    """API.md `POST /api/models/inspect` (no network)."""
    raw = body.get("source")
    if not isinstance(raw, str):
        raise ApiError(422, "bad_request", "The source must be a string.",
                       detail={"fields": [{"field": "source", "message": "required"}]})
    source = raw.strip()
    revision = (body.get("revision") or "").strip() or None
    want_sha = (body.get("sha256") or "").strip().lower() or None
    require_pinned = bool(body.get("require_pinned"))
    profile = (body.get("profile") or "").strip() or None
    region = (body.get("region") or "").strip() or None
    settings = st.settings.get()
    problems: list[dict[str, Any]] = []
    out: dict[str, Any] = {"kind": "local", "normalized": source, "pinned": False, "cached": False, "model_key": None,
                           "problems": problems, "needs": {"env": [], "profile": False}, "command": ""}
    if not source:
        problems.append(_p("error", "bad_source", "Enter a source: hf://org/repo@commit, s3://bucket/key, "
                                                  "https://… or a directory."))
        return out
    if len(source) > 2048:
        problems.append(_p("error", "bad_source", "This source is too long."))
        out["normalized"] = source[:80] + "…"
        return out
    if _has_credential(source):
        out["kind"] = _classify(source, st)
        out["normalized"] = st.redact(source.split("?", 1)[0]) + ("?••••" if "?" in source else "")
        problems.append(_p("error", "credential_in_source",
                           "This URL carries a credential. Use s3:// with an AWS profile instead, or a URL "
                           "without credentials."))
        return out
    if want_sha and not SHA256.match(want_sha):
        problems.append(_p("error", "bad_source", "sha256 must be 64 hexadecimal characters.", "sha256"))
    kind = _classify(source, st)
    out["kind"] = kind
    flags: list[str] = []
    if kind == "hf":
        body_ = source[len("hf://"):] if source.startswith("hf://") else source
        repo, _, at_rev = body_.partition("@")
        if at_rev and revision and at_rev != revision:
            problems.append(_p("warning", "bad_source", f"The source names revision {at_rev} and the revision "
                                                        f"field {revision}; the one in the source is used.",
                               "revision"))
        rev = at_rev or revision
        if not HF_REPO.match(repo):
            problems.append(_p("error", "bad_source", "A Hugging Face source is hf://org/repo, optionally "
                                                      "@<commit>."))
        pinned = bool(rev and sources.FULL_SHA.match(rev))
        out["pinned"] = pinned
        out["normalized"] = f"hf://{repo}" + (f"@{rev}" if rev else "")
        if not pinned:
            if require_pinned:
                problems.append(_p("error", "unpinned_source",
                                   "Pinned revisions are required: use a full 40-character commit."))
            else:
                problems.append(_p("warning", "unpinned_source",
                                   f"Not pinned to a commit ({rev or 'latest'}): the resolved commit is recorded, "
                                   "but the branch may move."))
        if want_sha:
            problems.append(_p("warning", "unknown_key", "sha256 applies to archives and files, not to Hugging Face "
                                                         "repos; it is ignored.", "sha256"))
        out["needs"]["env"] = ["HF_TOKEN?"]
        if pinned:
            for entry in hf_cache_entries(st):
                if entry["ref"] == rev and entry["source"].split("@")[0] == f"hf://{repo}":
                    out.update(cached=True, model_key=entry["model_key"],
                               cached_info={"model_key": entry["model_key"], "pulled_at": entry["pulled_at"],
                                            "ref": entry["ref"]})
                    break
    elif kind in ("s3", "url"):
        parsed = urllib.parse.urlsplit(source)
        if kind == "s3":
            bucket, key = sources.split_s3(source)
            if not bucket:
                problems.append(_p("error", "bad_source", "An S3 source is s3://bucket/key (an archive or file) or "
                                                          "s3://bucket/prefix/."))
            out["needs"]["profile"] = True
            is_prefix = not key or key.endswith("/")
            label = source
        else:
            if not parsed.netloc:
                problems.append(_p("error", "bad_source", "This URL has no host."))
            if parsed.scheme == "http" and not _is_loopback_url(source):
                problems.append(_p("warning", "unverified_source", "Plain http: the download is not encrypted."))
            is_prefix = False
            label = source.split("?", 1)[0]
        if is_prefix:
            if want_sha:
                problems.append(_p("warning", "unknown_key", "sha256 applies to one archive or file, not to a "
                                                             "prefix; object ETags are recorded instead.", "sha256"))
            want_sha_key: Any = None
            final = os.path.join(models_dir(), hashlib.sha256(json.dumps([label], sort_keys=True).encode())
                                 .hexdigest()[:16])
        else:
            want_sha_key = want_sha
            final = os.path.join(models_dir(), hashlib.sha256(json.dumps([label, want_sha], sort_keys=True).encode())
                                 .hexdigest()[:16])
            if not want_sha:
                sev = "error" if settings.get("require_sha256") else "warning"
                problems.append(_p(sev, "unverified_source",
                                   "No sha256: this download cannot be verified" +
                                   (" and the require-sha256 setting is on." if sev == "error" else
                                    "; the sha256 it has is recorded.")))
        out["pinned"] = bool(want_sha_key) and SHA256.match(want_sha_key or "") is not None
        out["normalized"] = label
        marker = os.path.join(final, ".decider-lab-source.json")
        if os.path.exists(marker):
            try:
                with open(marker, encoding="utf-8") as fh:
                    info = json.load(fh)
            except (OSError, ValueError):
                info = {}
            ref, _ = _ref(info)
            key = os.path.basename(final)
            out.update(cached=True, model_key=key,
                       cached_info={"model_key": key, "pulled_at": iso(os.path.getmtime(final)), "ref": ref})
        if want_sha_key:
            flags += ["--sha256", want_sha_key]
        if kind == "s3":
            flags += (["--profile", profile] if profile else []) + (["--region", region] if region else [])
    else:  # local
        path = _local_path(source, st)
        home = os.path.realpath(os.path.expanduser("~"))
        if not (_inside(path, st.workspace.root) or _inside(path, home)):
            problems.append(_p("error", "path_outside_workspace",
                               "A local model must be inside the workspace or your home directory."))
        elif not os.path.exists(path):
            problems.append(_p("error", "local_path_missing",
                               "No such directory here, and it is not hf://, s3:// or a URL."))
        out["pinned"] = False
        out["normalized"] = source
        problems.append(_p("warning", "unverified_source", "A local directory is used as it is: nothing is copied "
                                                           "into the cache or verified."))
    if require_pinned and kind == "hf":
        flags.append("--require-pinned")
    out["command"] = shlex.join(["decider-lab", "pull", out["normalized"], *flags])
    return st.redact.obj(out)


@router.post("/models/inspect")
def inspect(body: dict[str, Any] = Body(...), st: StudioState = Depends(get_state)) -> dict[str, Any]:
    return inspect_source(st, body)


# ---- delete --------------------------------------------------------------------------------------


@router.delete("/models/{model_key}")
def delete_model(model_key: str, body: dict[str, Any] | None = Body(None),
                 st: StudioState = Depends(get_state)) -> dict[str, Any]:
    if model_key.startswith("hf-"):
        raise ApiError(400, "bad_request", "Studio does not delete from the Hugging Face cache.",
                       hint="Use `huggingface-cli delete-cache` for Hugging Face snapshots.")
    if not MODEL_KEY.match(model_key):
        raise ApiError(404, "not_found", "No such cached model.", detail={"what": "model"})
    item = next((i for i in cached_models(st, include_hf=False) if i["model_key"] == model_key), None)
    if item is None:
        raise ApiError(404, "not_found", "No such cached model.", detail={"what": "model"})
    expected = (item["ref"] or model_key)[:8]
    if (body or {}).get("confirm") != expected:
        raise ApiError(409, "confirm_mismatch", "The confirmation does not match.",
                       detail={"expected_hint": "type the first 8 characters of the model's ref"})
    if item["in_use_by_job"]:
        raise ApiError(409, "job_active", "A running job uses this model.", detail={"job_id": item["in_use_by_job"]})
    path = os.path.join(models_dir(), model_key)
    if not _inside(os.path.realpath(path), models_dir()):
        raise ApiError(400, "path_outside_workspace", "This path is outside the model cache.")
    freed = _dir_size(path)
    shutil.rmtree(path)
    st.bus.publish("models.changed", {})
    return {"deleted": True, "freed_gb": round(freed / 1e9, 2)}


# ---- pull jobs -----------------------------------------------------------------------------------

_PULL_SIZE = re.compile(r"\[pull\] (\d+(?:\.\d+)?) GB(?: \((\d+)%\))?")
_PULL_OBJECTS = re.compile(r"\[pull\] .*: (\d+) objects, (\d+(?:\.\d+)?) GB")


def _stage(stages: list[dict[str, Any]], name: str, status: str, detail: str | None = None) -> list[dict[str, Any]]:
    out = []
    now = now_iso()
    for s in stages:
        s = dict(s)
        if s["name"] == name:
            if status == "active" and s["status"] == "pending":
                s["started_at"] = now
            if status in ("done", "failed", "warning", "skipped"):
                s["started_at"] = s["started_at"] or now
                s["ended_at"] = now
            s["status"] = status
            if detail is not None:
                s["detail"] = detail
        out.append(s)
    return out


def _activate(stages: list[dict[str, Any]], name: str, detail: str | None = None) -> list[dict[str, Any]]:
    """Mark every stage before `name` done (unless warned/failed) and `name` active."""
    names = [s["name"] for s in stages]
    if name not in names:
        return stages
    idx = names.index(name)
    for s in stages[:idx]:
        if s["status"] in ("pending", "active"):
            stages = _stage(stages, s["name"], "done")
    cur = next(s for s in stages if s["name"] == name)
    if cur["status"] in ("pending",):
        stages = _stage(stages, name, "active", detail)
    elif detail is not None:
        stages = _stage(stages, name, cur["status"], detail)
    return stages


def pull_line_handler(job: dict[str, Any], line: dict[str, Any]) -> dict[str, Any] | None:
    if job.get("kind") != "pull":
        return None
    text = line["text"].strip()
    stages = job.get("stages") or default_stages("pull")
    progress = dict(job.get("progress") or {})
    if not text.startswith("[pull]") and not text.startswith("{"):
        if any(s["status"] == "pending" for s in stages[:1]):
            return {"stages": _activate(stages, "resolve")}
        return None
    m_obj = _PULL_OBJECTS.search(text)
    m_size = _PULL_SIZE.search(text)
    if "WARNING" in text and "not pinned" in text:
        return {"stages": _stage(_activate(stages, "resolve"), "resolve", "warning", "not pinned to a commit"),
                "progress": progress | {"label": "resolving"}}
    if "WARNING: no sha256" in text:
        got = re.search(r"recorded ([0-9a-f]{64})", text)
        return {"stages": _stage(_activate(stages, "verify"), "verify", "warning",
                                 f"not verified; sha256 {got.group(1)[:12]}…" if got else "not verified"),
                "progress": progress | {"label": "verifying"}}
    if m_obj:
        total = float(m_obj.group(2)) * 1e9
        progress["bytes"] = {"done": 0, "total": int(total)}
        return {"stages": _activate(stages, "download", f"{m_obj.group(1)} objects"),
                "progress": progress | {"label": f"{m_obj.group(2)} GB", "fraction": None}}
    if m_size:
        done = float(m_size.group(1)) * 1e9
        pct = int(m_size.group(2)) if m_size.group(2) else None
        total = int(done * 100 / pct) if pct else None
        progress["bytes"] = {"done": int(done), "total": total}
        frac = pct / 100 if pct is not None else None
        return {"stages": _activate(stages, "download"),
                "progress": progress | {"fraction": frac, "label": f"{m_size.group(1)} GB" + (f" ({pct}%)" if pct else "")}}
    if text.startswith("[pull] "):
        return {"stages": _activate(stages, "download", None), "progress": progress | {"label": "downloading"}}
    return None


def _result_json(st: StudioState, job_id: str) -> dict[str, Any] | None:
    try:
        text = st.jobs.log_text(job_id)
    except ApiError:
        return None
    lines = text.splitlines()
    end = max((i for i, x in enumerate(lines) if x == "}"), default=None)
    if end is None:
        return None
    start = max((i for i, x in enumerate(lines[: end + 1]) if x == "{"), default=None)
    if start is None:
        return None
    try:
        return json.loads("\n".join(lines[start: end + 1]))
    except ValueError:
        return None


def _pull_finish(st: StudioState, job: dict[str, Any]) -> dict[str, Any] | None:
    if job.get("kind") != "pull":
        return None
    stages = job.get("stages") or default_stages("pull")
    progress = dict(job.get("progress") or {})
    st.bus.publish("models.changed", {})
    if job["status"] != "succeeded":
        failures = list(job.get("failures") or [])
        human = []
        for f in failures:
            if "sha256 mismatch" in f:
                human.append("Could not pull the model. The file's sha256 does not match the one given; the download "
                             "was discarded. " + f[f.find("sha256 mismatch"):])
            elif "require_pinned" in f:
                human.append("Could not pull the model: a pinned revision is required. " + f)
            else:
                human.append(f)
        active = next((s["name"] for s in stages if s["status"] == "active"), None) or stages[0]["name"]
        status = "failed" if job["status"] in ("failed", "lost") else "skipped"
        return {"stages": _stage(stages, active, status), "failures": human or failures,
                "progress": progress | {"label": job["status"]}}
    out = _result_json(st, job["job_id"]) or {}
    path = out.pop("path", None)
    key = None
    if path and _inside(os.path.realpath(path), models_dir()):
        rel = os.path.relpath(os.path.realpath(path), os.path.realpath(models_dir()))
        key = rel.split(os.sep)[0]
    kind = out.get("kind")
    for s in list(stages):
        if s["status"] in ("pending", "active"):
            skip = s["name"] == "extract" and kind in ("hf", "local")
            stages = _stage(stages, s["name"], "skipped" if skip else "done")
    if progress.get("bytes"):
        b = progress["bytes"]
        progress["bytes"] = {"done": b.get("total") or b.get("done"), "total": b.get("total") or b.get("done")}
    return {"stages": stages, "result": {"path": path, "model_key": key, "info": out},
            "progress": progress | {"fraction": 1.0, "label": "pulled"}}


def install_job_hooks(st: StudioState) -> None:
    """Register the pull parser once per JobManager."""
    if st.extra.get("models.hooks"):
        return
    st.extra["models.hooks"] = True
    st.jobs.add_line_handler(pull_line_handler)
    st.jobs.add_finish_handler(lambda job: _pull_finish(st, job))


def start_pull_job(st: StudioState, body: dict[str, Any]) -> dict[str, Any]:
    """`POST /api/jobs {"kind": "pull", ...}`: validate like inspect, then start `decider-lab pull`."""
    install_job_hooks(st)
    res = inspect_source(st, body)
    errors = [p for p in res["problems"] if p["severity"] == "error"]
    if errors:
        codes = {p["code"] for p in errors}
        if "credential_in_source" in codes:
            raise ApiError(422, "credential_in_source", "This URL carries a credential.",
                           hint="Use s3:// with an AWS profile instead, or a URL without credentials.")
        if codes == {"unpinned_source"}:
            raise ApiError(422, "unpinned_source", "Pinned revisions are required: use a full 40-character commit.")
        raise ApiError(422, "source_invalid", errors[0]["message"], detail={"problems": res["problems"]})
    source = res["normalized"] if res["kind"] == "hf" else body["source"].strip()
    args = ["pull", source]
    sha = (body.get("sha256") or "").strip().lower() or None
    if sha and res["kind"] in ("s3", "url"):
        args += ["--sha256", sha]
    if body.get("require_pinned") and res["kind"] == "hf":
        args.append("--require-pinned")
    if res["kind"] == "s3":
        for flag, field in (("--profile", "profile"), ("--region", "region")):
            v = (body.get(field) or "").strip()
            if v:
                if not re.match(r"^[\w.+-]{1,128}$", v):
                    raise ApiError(422, "bad_request", f"The {field} is not valid.",
                                   detail={"fields": [{"field": field, "message": "letters, digits, . _ + -"}]})
                args += [flag, v]
    title = f"pull {res['normalized']}"
    if len(title) > 120:
        title = title[:119] + "…"
    options = {k: body.get(k) for k in ("source", "revision", "sha256", "require_pinned", "profile", "region")}
    job = st.jobs.start("pull", JobManager.cli_argv(*args), title=title, cwd=st.workspace.root,
                        env_names=["HF_TOKEN"] if res["kind"] == "hf" else [], options=options)
    return {"job_id": job["job_id"], "status": job["status"], "title": job["title"], "command": job["command"]}


# Fallbacks until the jobs router serves these (it is included first, so it wins when it does).


@router.post("/jobs", include_in_schema=False)
def _jobs_post_fallback(body: dict[str, Any] = Body(...), st: StudioState = Depends(get_state)) -> JSONResponse:
    if body.get("kind") == "pull":
        return JSONResponse(start_pull_job(st, body), status_code=202)
    if body.get("kind") == "eval":
        from .api_overview import start_eval_job

        return JSONResponse(start_eval_job(st, body), status_code=202)
    raise ApiError(422, "bad_request", "This fallback starts pull and eval jobs only.",
                   detail={"fields": [{"field": "kind", "message": "unsupported kind"}]})


@router.get("/jobs/{job_id}", include_in_schema=False)
def _jobs_get_fallback(job_id: str, st: StudioState = Depends(get_state)) -> dict[str, Any]:
    install_job_hooks(st)
    return st.jobs.get(job_id)

