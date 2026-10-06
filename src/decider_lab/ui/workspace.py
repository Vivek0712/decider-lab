"""The workspace: the directory Studio was started in, its lab files and its run roots.

    ws = Workspace("/Users/me/labs")
    ws.labs()        -> [LabFile]   *.yaml / *.yml with a top-level `models:` key (depth <= 4)
    ws.run_roots()   -> [RunRoot]   dirs holding report.json or lab.json, or <model>/<suite>/scores.json
    ws.encode_id("labs/first/lab.yaml") / ws.decode_id(lab_id) / ws.resolve(rel)

Ids are base64url (no padding) of the workspace-relative POSIX path (API.md 2.1). Anything that
resolves outside the workspace raises `ApiError(400, "path_outside_workspace")`.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import yaml

from .errors import ApiError

STATE_DIRNAME = ".decider-lab-studio"
IGNORED_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache",
                ".ruff_cache", ".tox", ".idea", ".vscode", STATE_DIRNAME, ".decider-lab"}
LAB_MAX_DEPTH = 4
ROOT_MAX_DEPTH = 6
LAB_MAX_BYTES = 1_000_000


def iso(ts: float | None) -> str | None:
    """Epoch seconds -> ISO 8601 UTC with milliseconds and Z (API.md 1.3)."""
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(ts * 1000) % 1000:03d}Z"


def now_iso() -> str:
    return iso(datetime.now(tz=timezone.utc).timestamp()) or ""


def encode_id(rel: str) -> str:
    return base64.urlsafe_b64encode(rel.replace(os.sep, "/").encode()).decode().rstrip("=")


def decode_id(value: str) -> str:
    try:
        pad = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode((value + pad).encode()).decode()
    except (binascii.Error, UnicodeDecodeError, ValueError) as e:
        raise ApiError(400, "bad_request", "This id is not valid.", detail={"id": value[:200]}) from e


@dataclass
class LabFile:
    lab_id: str
    path: str            # workspace-relative, POSIX
    abspath: str
    name: str
    modified_at: str | None
    parsed: bool         # the YAML parsed into a mapping
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("abspath")
        return d


@dataclass
class RunRoot:
    root_id: str
    path: str            # workspace-relative, POSIX
    abspath: str
    kind: str            # "lab" (lab.json/report.json) or "eval" (only <model>/<suite>/scores.json)
    title: str
    has_report: bool
    has_lab_json: bool
    finished_at: str | None
    models: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("abspath")
        return d


class Workspace:
    def __init__(self, root: str, state_dirname: str = STATE_DIRNAME) -> None:
        self.root = os.path.realpath(os.path.abspath(os.path.expanduser(root)))
        if not os.path.isdir(self.root):
            raise FileNotFoundError(f"workspace {root} is not a directory")
        self.state_dir = os.path.join(self.root, state_dirname)

    # ---- paths and ids -------------------------------------------------------------------------

    def ensure_state_dir(self) -> str:
        """Create the state dir (mode 700) and add it to an existing .gitignore (never create one)."""
        os.makedirs(self.state_dir, mode=0o700, exist_ok=True)
        try:
            os.chmod(self.state_dir, 0o700)
        except OSError:
            pass
        gi = os.path.join(self.root, ".gitignore")
        name = os.path.basename(self.state_dir) + "/"
        if os.path.isfile(gi):
            try:
                with open(gi, encoding="utf-8") as fh:
                    lines = [x.strip() for x in fh.read().splitlines()]
                if name not in lines and name.rstrip("/") not in lines and "/" + name not in lines:
                    with open(gi, "a", encoding="utf-8") as fh:
                        fh.write(("" if not lines or lines[-1] == "" else "\n") + name + "\n")
            except OSError:
                pass
        return self.state_dir

    def rel(self, path: str) -> str:
        """Workspace-relative POSIX path of an absolute path inside the workspace."""
        real = os.path.realpath(path)
        if real != self.root and not real.startswith(self.root + os.sep):
            raise ApiError(400, "path_outside_workspace", "This path is outside the workspace.")
        rel = os.path.relpath(real, self.root)
        return "." if rel == "." else rel.replace(os.sep, "/")

    def resolve(self, rel: str) -> str:
        """Absolute path of a workspace-relative path; refuses anything that escapes the workspace."""
        if rel is None or "\x00" in rel:
            raise ApiError(400, "path_outside_workspace", "This path is outside the workspace.")
        candidate = os.path.realpath(os.path.join(self.root, os.path.expanduser(rel)))
        if candidate != self.root and not candidate.startswith(self.root + os.sep):
            raise ApiError(400, "path_outside_workspace", "This path is outside the workspace.")
        return candidate

    def encode_id(self, rel_or_abs: str) -> str:
        rel = self.rel(rel_or_abs) if os.path.isabs(rel_or_abs) else rel_or_abs
        return encode_id(rel)

    def decode_id(self, value: str) -> str:
        """Absolute path for an id (the file need not exist)."""
        return self.resolve(decode_id(value))

    # ---- scanning ------------------------------------------------------------------------------

    def _walk(self, max_depth: int, skip: set[str]):
        base_depth = self.root.rstrip(os.sep).count(os.sep)
        for dirpath, dirnames, filenames in os.walk(self.root, followlinks=False):
            depth = dirpath.rstrip(os.sep).count(os.sep) - base_depth
            dirnames[:] = sorted(d for d in dirnames if d not in skip and not d.startswith("."))
            if depth >= max_depth:
                dirnames[:] = []
            yield dirpath, dirnames, sorted(filenames), depth

    def labs(self, max_depth: int = LAB_MAX_DEPTH) -> list[LabFile]:
        """Lab files: *.yaml/*.yml (depth <= max_depth, skipping ignored dirs and `runs`) with `models:`."""
        out: list[LabFile] = []
        for dirpath, _dirs, files, _depth in self._walk(max_depth, IGNORED_DIRS | {"runs"}):
            for f in files:
                if not f.endswith((".yaml", ".yml")):
                    continue
                p = os.path.join(dirpath, f)
                lab = read_lab_head(p)
                if lab is None:
                    continue
                rel = self.rel(p)
                out.append(LabFile(lab_id=encode_id(rel), path=rel, abspath=p, name=lab["name"],
                                   modified_at=iso(os.path.getmtime(p)), parsed=lab["parsed"], error=lab["error"]))
        return out

    def run_roots(self, max_depth: int = ROOT_MAX_DEPTH) -> list[RunRoot]:
        """Run roots: directories with report.json or lab.json, or eval outputs (<model>/<suite>/scores.json)."""
        out: list[RunRoot] = []
        for dirpath, dirnames, files, _depth in self._walk(max_depth, IGNORED_DIRS):
            has_report, has_lab = "report.json" in files, "lab.json" in files
            models = _scored_models(dirpath)
            if not (has_report or has_lab or models):
                continue
            if not (has_report or has_lab):
                # an eval output; but a <model> or <suite> dir of another root is not a root itself
                parent = os.path.dirname(dirpath)
                if any(os.path.exists(os.path.join(p, n)) for p in (parent, os.path.dirname(parent))
                       for n in ("lab.json", "report.json")):
                    continue
            dirnames[:] = []  # a run root's model and suite dirs are not roots themselves
            rel = self.rel(dirpath)
            title = os.path.basename(dirpath)
            if has_lab:
                try:
                    with open(os.path.join(dirpath, "lab.json"), encoding="utf-8") as fh:
                        title = str((json.load(fh) or {}).get("name") or title)
                except (OSError, ValueError):
                    pass
            stamp = max((os.path.getmtime(os.path.join(dirpath, n)) for n in ("lab.json", "report.json")
                         if os.path.exists(os.path.join(dirpath, n))), default=None)
            out.append(RunRoot(root_id=encode_id(rel), path=rel, abspath=dirpath,
                               kind="lab" if (has_lab or has_report) else "eval", title=title,
                               has_report=has_report, has_lab_json=has_lab, finished_at=iso(stamp),
                               models=models))
        return out


def _scored_models(d: str) -> list[str]:
    """Model dirs under d that hold <suite>/scores.json."""
    models = []
    try:
        entries = sorted(os.listdir(d))
    except OSError:
        return []
    for m in entries:
        md = os.path.join(d, m)
        if m.startswith((".", "_")) or not os.path.isdir(md):
            continue
        try:
            if any(os.path.exists(os.path.join(md, s, "scores.json")) for s in os.listdir(md)):
                models.append(m)
        except OSError:
            continue
    return models


def read_lab_head(path: str) -> dict[str, Any] | None:
    """{name, parsed, error} if `path` looks like a lab (top-level `models:`), else None.

    A file that fails to parse but has a `models:` line at column 0 still counts as a lab (an
    invalid one), so a typo never makes a lab disappear from the list.
    """
    try:
        if os.path.getsize(path) > LAB_MAX_BYTES:
            return None
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError):
        return None
    default_name = os.path.splitext(os.path.basename(path))[0]
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        if any(line.startswith("models:") for line in text.splitlines()):
            name = next((line.split(":", 1)[1].strip().strip("'\"") for line in text.splitlines()
                         if line.startswith("name:")), "") or default_name
            return {"name": name, "parsed": False, "error": str(e).splitlines()[0][:200]}
        return None
    if not isinstance(data, dict) or "models" not in data:
        return None
    return {"name": str(data.get("name") or default_name), "parsed": True, "error": None}
