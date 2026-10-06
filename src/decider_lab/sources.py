"""Model sources: where a checkpoint comes from, pulled once, verified, cached, recorded.

    serve: hf://StrandsAgents/strands-decider-2B-hobson-v19@bb282d786bc251fd4e3068de3ada9ddbb38127cd
    serve: StrandsAgents/strands-decider-2B-hobson-v19        # a bare hub id: hf://, latest revision
    serve: s3://my-bucket/weights/my-decider.tar              # an archive (.tar, .tar.gz, .tgz, .zip) ...
    serve: s3://my-bucket/checkpoints/run-7/                  # ... or a prefix holding the checkpoint files
    serve: https://example.com/my-decider.tar                 # any URL, including presigned S3 links
    serve: checkpoints/run-7                                  # a local directory, used as it is

Options beside the source, in the same mapping:

    sha256: <hex>        archives and single files must match it, or the pull fails
    revision: <commit>   for hf:// (same as @commit in the spec)
    require_pinned: true refuse an hf:// source without a full commit hash (reproducible labs)
    profile / region:    for s3:// (your credentials, on your machine)

Hugging Face policy: the resolved commit hash is always recorded, a branch name or no revision
is allowed but warned about, private and gated repos use HF_TOKEN, and HF_HUB_OFFLINE=1 works
from the cache. Archives are extracted safely (no absolute paths, no `..`, no links out of the
target). Everything lands in ~/.cache/decider-lab/models/<key>/, reused on the next pull.

On a remote backend, an s3:// source is turned into a short-lived presigned https URL on your
machine before the lab is copied, so no AWS credentials ever reach the remote host.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tarfile
import time
import urllib.parse
import urllib.request
import zipfile
from typing import Any

from . import suites

ARCHIVES = (".tar", ".tar.gz", ".tgz", ".zip")
CONFIG_FILES = ("strands_decider_config.json", "hobson_config.json")
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
HUB_ID = re.compile(r"^[A-Za-z0-9][\w.-]*/[\w.-]+$")


def kind_of(spec: str) -> str:
    """hf, s3, url or local."""
    if spec.startswith("hf://"):
        return "hf"
    if spec.startswith("s3://"):
        return "s3"
    if spec.startswith(("http://", "https://")):
        return "url"
    if os.path.exists(spec) or spec.startswith(("/", ".", "~")):
        return "local"
    if HUB_ID.match(spec.split("@")[0]):
        return "hf"
    return "local"


def _models_dir() -> str:
    return os.path.join(suites.CACHE, "models")  # read at call time, so tests and env overrides apply


def _key(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:16]


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def find_checkpoint(root: str) -> str:
    """The directory holding a Strands Decider config (the root, or one level or two below it);
    the root itself when there is none (another kind of model)."""
    for depth in range(3):
        for dirpath, _dirnames, filenames in os.walk(root):
            if dirpath[len(root):].count(os.sep) != depth:
                continue
            if any(c in filenames for c in CONFIG_FILES):
                return dirpath
    return root


def safe_extract(archive: str, dest: str) -> None:
    """Extract, refusing any member that would land outside `dest`."""
    dest_real = os.path.realpath(dest)

    def check(name: str) -> None:
        target = os.path.realpath(os.path.join(dest, name))
        if os.path.isabs(name) or not (target == dest_real or target.startswith(dest_real + os.sep)):
            raise ValueError(f"unsafe path in archive {os.path.basename(archive)}: {name!r}")

    if archive.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            for n in z.namelist():
                check(n)
            z.extractall(dest)
        return
    with tarfile.open(archive) as t:
        for m in t.getmembers():
            check(m.name)
            if m.issym() or m.islnk():
                check(os.path.join(os.path.dirname(m.name), m.linkname))
            if m.isdev():
                raise ValueError(f"device file in archive: {m.name!r}")
        if hasattr(tarfile, "data_filter"):  # Python 3.12+: the standard library's own safety filter too
            t.extractall(dest, filter="data")
        else:
            t.extractall(dest)


def _finish(tmp: str, final: str, info: dict[str, Any]) -> str:
    with open(os.path.join(tmp, ".decider-lab-source.json"), "w", encoding="utf-8") as fh:
        json.dump(info, fh, indent=2)
    if os.path.exists(final):
        shutil.rmtree(final)
    os.replace(tmp, final)
    return final


def _cached(final: str) -> dict[str, Any] | None:
    marker = os.path.join(final, ".decider-lab-source.json")
    if os.path.exists(marker):
        with open(marker, encoding="utf-8") as fh:
            return json.load(fh)
    return None


def _download(url: str, path: str, log: Any) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "decider-lab"})
    t0, done = time.time(), 0
    with urllib.request.urlopen(req, timeout=60) as r, open(path, "wb") as fh:
        total = int(r.headers.get("Content-Length") or 0)
        last = 0.0
        while True:
            chunk = r.read(1 << 22)
            if not chunk:
                break
            fh.write(chunk)
            done += len(chunk)
            if time.time() - last > 15:
                last = time.time()
                pct = f" ({100 * done / total:.0f}%)" if total else ""
                log(f"[pull] {done / 1e9:.2f} GB{pct}, {done / 1e6 / max(time.time() - t0, 1e-6):.0f} MB/s")


def _from_file(src_name: str, fetch: Any, spec: str, opts: dict[str, Any], log: Any) -> tuple[str, dict[str, Any]]:
    """Pull one file (an archive or a single file) through `fetch(dst)`, verify, extract, cache.
    `spec` is the label used for the cache key and provenance (a URL without its query string)."""
    want = (opts.get("sha256") or "").lower() or None
    final = os.path.join(_models_dir(), _key(spec, want))
    hit = _cached(final)
    if hit:
        return find_checkpoint(final), hit
    os.makedirs(_models_dir(), exist_ok=True)
    tmp = final + ".partial"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    blob = os.path.join(_models_dir(), os.path.basename(final) + "-" + os.path.basename(src_name))
    try:
        log(f"[pull] {spec}")
        fetch(blob)
        got = sha256_file(blob)
        if want and got != want:
            raise ValueError(f"sha256 mismatch for {spec}: expected {want}, got {got}")
        if not want:
            log(f"[pull] WARNING: no sha256 given for {spec}; recorded {got} (pin it with sha256: {got})")
        if src_name.endswith(ARCHIVES):
            safe_extract(blob, tmp)
        else:
            shutil.move(blob, os.path.join(tmp, os.path.basename(src_name)))
        info = {"source": spec, "kind": kind_of(spec), "sha256": got, "pulled_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ")}
        _finish(tmp, final, info)
    finally:
        if os.path.exists(blob):
            os.remove(blob)
        shutil.rmtree(tmp, ignore_errors=True)
    return find_checkpoint(final), info


def _hf(spec: str, opts: dict[str, Any], log: Any) -> tuple[str, dict[str, Any]]:
    try:
        from huggingface_hub import HfApi, snapshot_download
    except ImportError as e:
        raise ImportError("hf:// sources need huggingface_hub: pip install 'decider-lab[hub]'") from e
    body = spec[len("hf://"):] if spec.startswith("hf://") else spec
    repo, _, rev = body.partition("@")
    rev = rev or opts.get("revision")
    pinned = bool(rev and FULL_SHA.match(rev))
    if opts.get("require_pinned") and not pinned:
        raise ValueError(f"{spec}: require_pinned is set, so give a full 40-character commit (hf://{repo}@<commit>)")
    if not pinned:
        log(f"[pull] WARNING: {repo} is not pinned to a commit ({rev or 'latest'}); the resolved commit is recorded")
    token = os.environ.get("HF_TOKEN") or None
    path = snapshot_download(repo, revision=rev, token=token, allow_patterns=opts.get("allow_patterns"))
    commit = os.path.basename(os.path.normpath(path))
    if not FULL_SHA.match(commit):
        try:
            commit = HfApi().model_info(repo, revision=rev, token=token).sha or commit
        except Exception:  # offline: keep what the cache path says
            pass
    info = {"source": spec, "kind": "hf", "repo": repo, "requested_revision": rev, "resolved_commit": commit,
            "pinned": pinned}
    return find_checkpoint(path), info


def _s3_client(opts: dict[str, Any]) -> Any:
    try:
        import boto3
    except ImportError as e:
        raise ImportError("s3:// sources need boto3: pip install 'decider-lab[aws]'") from e
    return boto3.Session(profile_name=opts.get("profile"), region_name=opts.get("region")).client("s3")


def split_s3(spec: str) -> tuple[str, str]:
    p = urllib.parse.urlparse(spec)
    return p.netloc, p.path.lstrip("/")


def _s3(spec: str, opts: dict[str, Any], log: Any) -> tuple[str, dict[str, Any]]:
    bucket, key = split_s3(spec)
    s3 = _s3_client(opts)
    if not key.endswith("/"):  # one object: an archive or a file
        return _from_file(key, lambda dst: s3.download_file(bucket, key, dst), spec, opts, log)
    final = os.path.join(_models_dir(), _key(spec))
    hit = _cached(final)
    if hit:
        return find_checkpoint(final), hit
    tmp = final + ".partial"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    try:
        objects = []
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=key):
            objects += [o for o in page.get("Contents", []) if not o["Key"].endswith("/")]
        if not objects:
            raise FileNotFoundError(f"{spec}: no objects under this prefix")
        log(f"[pull] {spec}: {len(objects)} objects, {sum(o['Size'] for o in objects) / 1e9:.2f} GB")
        etags = []
        for o in objects:
            rel = o["Key"][len(key):]
            dst = os.path.join(tmp, rel)
            if not os.path.realpath(dst).startswith(os.path.realpath(tmp) + os.sep):
                raise ValueError(f"unsafe object key {o['Key']!r}")
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            s3.download_file(bucket, o["Key"], dst)
            etags.append(f"{rel}:{o.get('ETag', '')}")
        info = {"source": spec, "kind": "s3", "objects": len(objects),
                "etags_sha256": hashlib.sha256("\n".join(sorted(etags)).encode()).hexdigest(),
                "pulled_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ")}
        _finish(tmp, final, info)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return find_checkpoint(final), info


def resolve(spec: str, opts: dict[str, Any] | None = None, *, log: Any = print) -> tuple[str, dict[str, Any]]:
    """(local checkpoint directory, provenance) for a source spec."""
    opts = opts or {}
    k = kind_of(spec)
    if k == "local":
        path = os.path.abspath(os.path.expanduser(spec))
        if not os.path.exists(path):
            raise FileNotFoundError(f"{spec}: no such directory (and not hf://, s3:// or a URL)")
        return find_checkpoint(path) if os.path.isdir(path) else path, {"source": spec, "kind": "local", "path": path}
    if k == "hf":
        return _hf(spec, opts, log)
    if k == "s3":
        return _s3(spec, opts, log)
    name = urllib.parse.urlparse(spec).path.rsplit("/", 1)[-1] or "model"
    # a presigned URL changes on every request and carries a signature: neither may become the
    # cache key or be recorded, so both use the URL without its query string
    label = spec.split("?", 1)[0]
    return _from_file(name, lambda dst: _download(spec, dst, log), label, opts, log)


def presign(spec: str, opts: dict[str, Any] | None = None, expires_s: int = 6 * 3600) -> str:
    """An https URL for an s3:// object, made with your credentials, for a remote host."""
    opts = opts or {}
    bucket, key = split_s3(spec)
    if key.endswith("/"):
        raise ValueError(f"{spec}: a remote run needs an archive (tar/zip) or a single object, not a prefix; "
                         "tar the checkpoint first, or run --on local")
    return _s3_client(opts).generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": key},
                                                   ExpiresIn=expires_s)


def cached() -> list[dict[str, Any]]:
    out = []
    root = _models_dir()
    if os.path.isdir(root):
        for d in sorted(os.listdir(root)):
            info = _cached(os.path.join(root, d))
            if info:
                size = sum(os.path.getsize(os.path.join(p, f)) for p, _, fs in os.walk(os.path.join(root, d)) for f in fs)
                out.append({"dir": os.path.join(root, d), "size_gb": round(size / 1e9, 2), **info})
    return out
