"""Lab files for Studio: validation with line numbers, the visual summary, and secret masking.

    v = validate_text(yaml_text, lab_dir="/ws/labs/first")
    v.problems          [Problem dict]       (API.md 5 and 12.2), sorted by line
    v.summary           LabSummary dict or None when the YAML does not parse into a mapping
    v.secrets           [Secret]             literal secrets found (path, span, raw text)
    mask(text)          -> (masked_text, n)  every secret literal's value replaced by "••••"
    restore_placeholders(new_text, disk_text) -> text with "••••" put back from disk (or raises)
    fix_secret(text, path, env_name) -> text with `<key>: literal` rewritten to `<key>_env: NAME`

Validation never touches the network and never constructs an adapter: it applies the rules of
`lab.load_lab` and the spec checks `make_adapter`, `sources` and `suites` would make, statically.
"""

from __future__ import annotations

import difflib
import importlib.util
import inspect
import ipaddress
import os
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import yaml

from .redact import MASK, PATTERNS, PRESIGNED, USERINFO

SECRET_KEYS = {"api_key", "aws_access_key_id", "aws_secret_access_key", "aws_session_token", "token", "password",
               "secret"}
TOP_KEYS = {"name", "workers", "models", "finetune", "suites", "calibrate", "baseline", "jevbench", "compute", "out"}
BASELINES = ("uniform", "majority", "random")
MODEL_KINDS = ("baseline", "url", "bedrock", "strands", "chat", "python", "serve", "finetuned")
BACKENDS = ("local", "ssh", "aws", "vast")
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
ARCHIVES = (".tar", ".tar.gz", ".tgz", ".zip")
DEFAULT_FAMILIES = ("arithmetic", "calendar", "seating")
SERVE_KEYS = {"serve", "vision", "device", "model_name", "gpu", "health_timeout", "timeout", "sha256", "revision",
              "profile", "region", "require_pinned", "workers"}
COMPUTE_KEYS = {"backend", "on", "max_hours", "strands_decider", "env", "fast_kernels", "local", "ssh", "aws", "vast"}


# ---- known option names, from the code that reads them ---------------------------------------


def _params(fn: Any) -> set[str]:
    try:
        return {p.name for p in inspect.signature(fn).parameters.values()
                if p.kind not in (p.VAR_KEYWORD, p.VAR_POSITIONAL) and p.name != "self"}
    except (TypeError, ValueError):
        return set()


def _adapter_keys() -> dict[str, set[str]]:
    from ..adapters import llm, others, systemone

    llm_common = _params(llm.LLMAdapter.__init__)
    return {
        "baseline": {"baseline", "workers"} | _params(others.MajorityBaseline.__init__)
        | _params(others.RandomBaseline.__init__) | _params(others.UniformBaseline.__init__),
        "url": {"url", "workers"} | _params(systemone.SystemOneAdapter.__init__),
        "bedrock": {"bedrock", "workers"} | _params(llm.BedrockAdapter.__init__) | llm_common,
        "strands": {"strands", "workers"} | (_params(llm.StrandsAdapter.__init__) - {"provider", "model_id"})
        | llm_common,
        "chat": {"chat", "workers"} | _params(llm.ChatAdapter.__init__) | llm_common,
        "python": {"python", "path", "workers"},
        "serve": SERVE_KEYS,
        "finetuned": {"finetuned", "workers"} | SERVE_KEYS,
    }


def _compute_keys() -> dict[str, set[str]]:
    from ..compute import aws, ssh, vast

    return {"vast": _params(vast.VastProvider.__init__) - {"label"},
            "aws": _params(aws.AwsProvider.__init__) - {"label", "session"},
            "ssh": _params(ssh.SshProvider.__init__), "local": set()}


_KEYS_CACHE: dict[str, Any] = {}


def adapter_keys() -> dict[str, set[str]]:
    if "adapters" not in _KEYS_CACHE:
        _KEYS_CACHE["adapters"] = _adapter_keys()
    return _KEYS_CACHE["adapters"]


def compute_keys() -> dict[str, set[str]]:
    if "compute" not in _KEYS_CACHE:
        _KEYS_CACHE["compute"] = _compute_keys()
    return _KEYS_CACHE["compute"]


# ---- the YAML node tree, with positions ------------------------------------------------------


@dataclass
class Pos:
    line: int            # 1-based
    column: int          # 1-based
    start: int           # char index of the value
    end: int
    key_start: int | None = None
    key_end: int | None = None


@dataclass
class Secret:
    path: str
    key: str | None      # the mapping key when the secret is a value of a secret-named key
    start: int
    end: int
    raw: str             # the exact source text of the value
    code: str            # secret_literal | credential_in_source


def _index(node: Any, path: tuple[Any, ...], out: dict[tuple[Any, ...], Pos], scalars: list[tuple[tuple, Any, Any]],
           key_node: Any = None) -> None:
    pos = Pos(node.start_mark.line + 1, node.start_mark.column + 1, node.start_mark.index, node.end_mark.index,
              key_node.start_mark.index if key_node is not None else None,
              key_node.end_mark.index if key_node is not None else None)
    out[path] = pos
    if isinstance(node, yaml.MappingNode):
        for k, v in node.value:
            key = k.value if isinstance(k, yaml.ScalarNode) else str(k.value)
            if isinstance(k, yaml.ScalarNode) and k.tag == "tag:yaml.org,2002:bool":
                key = True if str(k.value).lower() in ("on", "yes", "true", "y") else key
            _index(v, (*path, key), out, scalars, k)
    elif isinstance(node, yaml.SequenceNode):
        for i, v in enumerate(node.value):
            _index(v, (*path, i), out, scalars)
    elif isinstance(node, yaml.ScalarNode):
        scalars.append((path, node, key_node))


def dotted(path: tuple[Any, ...]) -> str:
    return ".".join(str(p) for p in path)


def parse_dotted(path: str) -> tuple[Any, ...]:
    return tuple(int(p) if p.isdigit() else p for p in path.split(".") if p != "")


def scan_secrets(text: str, root: Any) -> list[Secret]:
    positions: dict[tuple[Any, ...], Pos] = {}
    scalars: list[tuple[tuple, Any, Any]] = []
    if root is not None:
        _index(root, (), positions, scalars)
    found: list[Secret] = []
    for path, node, _key_node in scalars:
        value = node.value if isinstance(node.value, str) else str(node.value)
        key = path[-1] if path and isinstance(path[-1], str) else None
        raw = text[node.start_mark.index:node.end_mark.index]
        code = None
        if key is not None and key.lower() in SECRET_KEYS and value.strip() and node.tag == "tag:yaml.org,2002:str":
            code = "secret_literal"
        elif value == MASK or any(p.search(value) for p in PATTERNS):
            code = "secret_literal"
        elif USERINFO.search(value) or PRESIGNED.search(value):
            code = "credential_in_source"
        if code:
            found.append(Secret(dotted(path), key if code == "secret_literal" and key and key.lower() in SECRET_KEYS
                                else None, node.start_mark.index, node.end_mark.index, raw, code))
    return found


def compose(text: str) -> Any:
    return yaml.compose(text, Loader=yaml.SafeLoader)


def mask(text: str) -> tuple[str, int]:
    """Replace each secret literal's value with "••••" (quoted); line and column stay the same."""
    try:
        root = compose(text)
    except yaml.YAMLError:
        return _mask_unparsed(text)
    secrets = [s for s in scan_secrets(text, root) if s.raw.strip("'\"") != MASK]
    out = text
    for s in sorted(secrets, key=lambda s: s.start, reverse=True):
        out = out[:s.start] + f'"{MASK}"' + out[s.end:]
    return out, len(secrets)


_LINE_SECRET = re.compile(r"(?im)^(\s*(?:-\s*)?(?:" + "|".join(sorted(SECRET_KEYS)) + r")\s*:\s*)(\S.*?)\s*$")


def _mask_unparsed(text: str) -> tuple[str, int]:
    """Best effort for YAML that does not parse: secret keys line by line, and the patterns."""
    n = 0

    def line_sub(m: re.Match) -> str:
        nonlocal n
        if m.group(2).strip("'\"") in ("", MASK):
            return m.group(0)
        n += 1
        return m.group(1) + f'"{MASK}"'

    out = _LINE_SECRET.sub(line_sub, text)
    for p in (*PATTERNS,):
        out, k = p.subn(MASK, out)
        n += k
    out, k = PRESIGNED.subn(lambda m: m.group(1) + MASK, out)
    n += k
    out, k = USERINFO.subn(lambda m: m.group(1) + MASK + "@", out)
    n += k
    return out, n


class PlaceholderError(ValueError):
    def __init__(self, paths: list[str]) -> None:
        super().__init__("placeholders at paths without a secret on disk: " + ", ".join(paths))
        self.paths = paths


def restore_placeholders(new_text: str, disk_text: str | None) -> str:
    """Put each "••••" back from the file on disk, where the same key path held a secret."""
    if MASK not in new_text:
        return new_text
    try:
        root = compose(new_text)
    except yaml.YAMLError as e:
        raise PlaceholderError(["(the YAML does not parse, so the hidden values cannot be placed)"]) from e
    positions: dict[tuple[Any, ...], Pos] = {}
    scalars: list[tuple[tuple, Any, Any]] = []
    if root is not None:
        _index(root, (), positions, scalars)
    disk: dict[str, Secret] = {}
    if disk_text:
        try:
            disk = {s.path: s for s in scan_secrets(disk_text, compose(disk_text)) if s.raw.strip("'\"") != MASK}
        except yaml.YAMLError:
            disk = {}
    holes = [(dotted(p), n) for p, n, _k in scalars if isinstance(n.value, str) and n.value == MASK]
    missing = [p for p, _n in holes if p not in disk]
    if missing:
        raise PlaceholderError(missing)
    out = new_text
    for p, n in sorted(holes, key=lambda x: x[1].start_mark.index, reverse=True):
        out = out[:n.start_mark.index] + disk[p].raw + out[n.end_mark.index:]
    if MASK in out:  # a placeholder inside a longer string (not a whole value)
        raise PlaceholderError(["(a hidden value was edited)"])
    return out


def fix_secret(text: str, path: str, env_name: str) -> str:
    """Rewrite `<key>: <literal>` at `path` to `<key>_env: <env_name>`. KeyError if no such secret."""
    root = compose(text)
    hit = next((s for s in scan_secrets(text, root) if s.path == path and s.key), None)
    if hit is None:
        raise KeyError(path)
    positions: dict[tuple[Any, ...], Pos] = {}
    _index(root, (), positions, [])
    pos = positions[parse_dotted(path)]
    assert pos.key_start is not None and pos.key_end is not None
    return text[:pos.key_start] + f"{hit.key}_env" + text[pos.key_end:pos.start] + env_name + text[pos.end:]


# ---- validation ------------------------------------------------------------------------------


@dataclass
class Validation:
    problems: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] | None = None
    secrets: list[Secret] = field(default_factory=list)
    data: dict[str, Any] | None = None

    @property
    def valid(self) -> bool:
        return not any(p["severity"] == "error" for p in self.problems)

    @property
    def errors(self) -> int:
        return sum(1 for p in self.problems if p["severity"] == "error")

    @property
    def warnings(self) -> int:
        return sum(1 for p in self.problems if p["severity"] == "warning")


class _Ctx:
    def __init__(self, positions: dict[tuple[Any, ...], Pos], lab_dir: str | None, environ: dict[str, str],
                 require_sha256: bool) -> None:
        self.positions = positions
        self.lab_dir = lab_dir
        self.environ = environ
        self.require_sha256 = require_sha256
        self.problems: list[dict[str, Any]] = []

    def add(self, severity: str, code: str, message: str, path: tuple[Any, ...] = (), *, key: bool = False) -> None:
        pos = None
        p = path
        while p and p not in self.positions:
            p = p[:-1]
        pos = self.positions.get(p) if p or () in self.positions else None
        line = col = None
        if pos is not None:
            line, col = pos.line, pos.column
        self.problems.append({"severity": severity, "code": code, "message": message, "line": line,
                              "column": col, "path": dotted(path) or None})

    def local_path(self, rel: str) -> str:
        rel = os.path.expanduser(rel)
        if os.path.isabs(rel) or not self.lab_dir:
            return rel
        return os.path.join(self.lab_dir, rel)


def _did_you_mean(word: str, choices: list[str]) -> str:
    m = difflib.get_close_matches(str(word), [str(c) for c in choices], n=1, cutoff=0.6)
    return f" Did you mean `{m[0]}`?" if m else ""


def _is_loopback_url(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return False
    if host in ("localhost",) or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def model_kind(spec: Any) -> str:
    if isinstance(spec, str):
        if spec in BASELINES:
            return "baseline"
        if spec.startswith(("http://", "https://")):
            return "url"
        return "unknown"
    if isinstance(spec, dict):
        for k in MODEL_KINDS:
            if k in spec:
                return k
    return "unknown"


def _source_kind(src: str) -> str:
    from ..sources import kind_of

    try:
        return kind_of(src)
    except Exception:  # noqa: BLE001
        return "local"


def _short(text: str, n: int = 80) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def _check_model(ctx: _Ctx, name: str, spec: Any, finetune_names: set[str]) -> dict[str, Any]:
    path = ("models", name)
    kind = model_kind(spec)
    info: dict[str, Any] = {"name": str(name), "kind": kind, "spec_text": "", "is_baseline": False, "jevbench": False,
                            "paid_api": False, "env_refs": [], "warnings": []}

    def warn(code: str, message: str, sub: tuple[Any, ...] = ()) -> None:
        ctx.add("warning", code, message, (*path, *sub))
        info["warnings"].append(message)

    if kind == "unknown":
        if isinstance(spec, str):
            ctx.add("error", "unknown_model_kind", f"model `{name}`: {spec!r} is not a baseline name or a URL. Use a "
                    "URL, uniform/majority/random, or a mapping such as {serve: ...}.", path)
            info["spec_text"] = _short(spec)
        else:
            ctx.add("error", "unknown_model_kind", f"model `{name}` has no known kind; give one of "
                    + ", ".join(k for k in MODEL_KINDS if k != "finetuned") + ".", path)
        return info
    d = {"baseline": spec} if isinstance(spec, str) and kind == "baseline" else (
        {"url": spec} if isinstance(spec, str) else dict(spec))
    main = d.get(kind)
    info["spec_text"] = _short(main if isinstance(main, str) else
                               (f"{main.get('provider', 'bedrock')}/{main.get('model_id', '')}"
                                if isinstance(main, dict) else str(main)))
    known = adapter_keys().get(kind, set())
    if isinstance(spec, dict):
        for k in spec:
            if k not in known and not str(k).endswith("_env") and str(k).lower() not in SECRET_KEYS:
                warn("unknown_key", f"model `{name}`: `{k}` is not an option of a {kind} model; it is ignored or "
                     "fails at run time.", (k,))
        workers = spec.get("workers")
        if workers is not None and not (isinstance(workers, int) and not isinstance(workers, bool) and workers >= 1):
            ctx.add("error", "workers_invalid", f"model `{name}`: workers must be a whole number of at least 1.",
                    (*path, "workers"))
    for k, v in (d.items() if isinstance(d, dict) else []):
        if str(k).endswith("_env") and isinstance(v, str) and v:
            info["env_refs"].append(v)
    if kind == "baseline":
        if main not in BASELINES:
            ctx.add("error", "unknown_baseline", f"model `{name}`: unknown baseline `{main}`; one of uniform, majority, "
                    f"random.{_did_you_mean(main, list(BASELINES))}", (*path, "baseline") if isinstance(spec, dict) else path)
    elif kind == "url":
        if not isinstance(main, str) or not main.startswith(("http://", "https://")):
            ctx.add("error", "bad_source", f"model `{name}`: url must start with http:// or https://.", (*path, "url"))
        elif USERINFO.search(main) or PRESIGNED.search(main):
            ctx.add("error", "credential_in_source", f"model `{name}`: the URL carries a credential. Put credentials in "
                    "an environment variable, not in lab.yaml.", (*path, "url") if isinstance(spec, dict) else path)
    elif kind in ("bedrock", "strands"):
        info["paid_api"] = True
        region = d.get("region") or (main.get("region") if isinstance(main, dict) else None) or "us-east-1"
        info["provider"] = f"AWS Bedrock {region}" if kind == "bedrock" else (
            f"Strands {main.get('provider', 'bedrock')}" if isinstance(main, dict) else "Strands bedrock")
        warn("paid_api_model", f"model `{name}` ({kind}) is billed per request by its provider.")
        if isinstance(main, dict):
            for k, v in main.items():
                if str(k).endswith("_env") and isinstance(v, str) and v:
                    info["env_refs"].append(v)
    elif kind == "chat":
        base = d.get("base_url") or "https://api.openai.com/v1"
        info["paid_api"] = not _is_loopback_url(str(base))
        info["provider"] = urlparse(str(base)).hostname or str(base)
        if USERINFO.search(str(base)) or PRESIGNED.search(str(base)):
            ctx.add("error", "credential_in_source", f"model `{name}`: base_url carries a credential.",
                    (*path, "base_url"))
        key_env = d.get("api_key_env", "OPENAI_API_KEY")
        if key_env:
            info["env_refs"].append(str(key_env))
        if info["paid_api"]:
            warn("paid_api_model", f"model `{name}` (chat at {info['provider']}) is billed per request.")
    elif kind == "python":
        if not isinstance(main, str) or ":" not in main:
            ctx.add("error", "bad_source", f"model `{name}`: python must be module:attr, e.g. my_model:Heuristic.",
                    (*path, "python"))
    elif kind == "serve":
        info["needs_gpu"] = True
        info["vision"] = bool(d.get("vision"))
        if not isinstance(main, str) or not main.strip():
            ctx.add("error", "bad_source", f"model `{name}`: serve needs a source (hf://, s3://, https:// or a "
                    "directory).", (*path, "serve"))
        else:
            sk = _source_kind(main)
            info["source_kind"] = sk
            if USERINFO.search(main) or PRESIGNED.search(main):
                ctx.add("error", "credential_in_source", f"model `{name}`: the source carries a credential (URL user "
                        "info or a presigned query); it would be stored in source.json.", (*path, "serve"))
            elif sk == "hf":
                rev = main.split("@", 1)[1] if "@" in main else str(d.get("revision") or "")
                info["pinned"] = bool(FULL_SHA.match(rev))
                if not info["pinned"]:
                    warn("unpinned_source", f"model `{name}`: hf:// source not pinned to a commit: results may not be "
                         "reproducible. Add @<40-character commit>.", ("serve",))
            elif sk in ("s3", "url"):
                is_archive = main.split("?")[0].endswith(ARCHIVES) or sk == "s3"
                info["pinned"] = bool(d.get("sha256"))
                if is_archive and not d.get("sha256"):
                    msg = (f"model `{name}`: {sk} source without sha256: the download cannot be verified.")
                    if ctx.require_sha256:
                        ctx.add("error", "unverified_source", msg, (*path, "serve"))
                    else:
                        warn("unverified_source", msg, ("serve",))
            elif sk == "local":
                if not os.path.exists(ctx.local_path(main)):
                    ctx.add("error", "local_path_missing", f"model `{name}`: directory {main} does not exist "
                            "(relative paths are relative to lab.yaml).", (*path, "serve"))
    elif kind == "finetuned":
        info["needs_gpu"] = True
        if main not in finetune_names:
            ctx.add("error", "unknown_model_ref", f"model `{name}`: finetuned `{main}` is not in finetune.",
                    (*path, "finetuned"))
    for env in info["env_refs"]:
        if env not in ctx.environ:
            warn("env_not_set", f"model `{name}` reads {env}, which is not set in the shell Studio runs in.")
    return info


def _suite_info(ctx: _Ctx, i: int, spec: Any) -> dict[str, Any] | None:
    path = ("suites", i)
    if isinstance(spec, str):
        base, _, args = spec.partition(":")
        if base in ("smoke", "synthetic", "heldout"):
            params: dict[str, Any] = {}
            for kv in filter(None, args.split(",")):
                k, _, v = kv.partition("=")
                params[k] = int(v) if v.lstrip("-").isdigit() else (v.split("+") if k == "families" else v)
            spec_d: dict[str, Any] = {base: params}
            ref = spec
        else:
            spec_d = {"file": spec}
            ref = spec
    elif isinstance(spec, dict):
        spec_d = dict(spec)
        kinds = [k for k in spec_d if k != "name"]
        if "file" in spec_d:
            ref = str(spec_d["file"])
        elif len(kinds) == 1:
            params = spec_d[kinds[0]] or {}
            ref = kinds[0] + (":" + ",".join(f"{k}={'+'.join(v) if isinstance(v, list) else v}"
                                             for k, v in params.items()) if isinstance(params, dict) and params else "")
        else:
            ctx.add("error", "unknown_suite", "a suite mapping needs exactly one of smoke, synthetic, heldout or file.",
                    path)
            return None
    else:
        ctx.add("error", "unknown_suite", "a suite is a name (smoke, synthetic, heldout), a path, or a mapping.", path)
        return None
    if "file" in spec_d:
        f = str(spec_d["file"])
        name = spec_d.get("name") or os.path.basename(f).split(".")[0]
        full = ctx.local_path(f)
        out = {"ref": ref, "name": name, "label": f, "rows_estimate": None, "has_splits": None}
        if not os.path.isfile(full):
            ctx.add("error", "suite_file_missing", f"suite file {f} does not exist (relative paths are relative to "
                    "lab.yaml).", path)
            return out
        try:
            n = 0
            splits = False
            with open(full, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if line.strip():
                        n += 1
                        if n <= 500 and '"split"' in line and '"split": null' not in line:
                            splits = True
            out["rows_estimate"] = n
            out["has_splits"] = splits
        except OSError:
            pass
        return out
    (kind, params), = ((k, v or {}) for k, v in spec_d.items() if k != "name")
    name = spec_d.get("name") or kind
    if kind not in ("smoke", "synthetic", "heldout"):
        ctx.add("error", "unknown_suite", f"unknown suite `{kind}`; one of smoke, synthetic, heldout, or a file."
                f"{_did_you_mean(kind, ['smoke', 'synthetic', 'heldout'])}", path)
        return None
    if not isinstance(params, dict):
        ctx.add("error", "unknown_suite", f"suite `{kind}` options must be a mapping.", path)
        return None
    if kind == "smoke":
        return {"ref": ref, "name": name, "label": "smoke", "rows_estimate": 90, "has_splits": True}
    if kind == "synthetic":
        from ..suites import generators

        per_kind = params.get("per_kind", 150)
        fams = params.get("families", list(DEFAULT_FAMILIES))
        if isinstance(fams, str):
            fams = [fams]
        unknown = [f for f in fams if f not in generators.FAMILIES]
        if unknown:
            ctx.add("error", "unknown_suite", f"synthetic: unknown families {unknown}; available: "
                    f"{sorted(generators.FAMILIES)}.", path)
        if "chess" in fams and importlib.util.find_spec("chess") is None:
            ctx.add("warning", "suite_unavailable", "synthetic with chess needs python-chess: pip install "
                    "'decider-lab[chess]'.", path)
        n = per_kind * len(fams) * 3 if isinstance(per_kind, int) else None
        label = f"synthetic · per_kind {per_kind}" + (f" · {'+'.join(fams)}" if list(fams) != list(DEFAULT_FAMILIES)
                                                       else "")
        return {"ref": ref, "name": name, "label": label, "rows_estimate": n, "has_splits": True}
    if importlib.util.find_spec("datasets") is None:
        ctx.add("warning", "suite_unavailable", "heldout needs the heldout extra: pip install 'decider-lab[heldout]'.",
                path)
    return {"ref": ref, "name": name, "label": "heldout", "rows_estimate": None, "has_splits": True}


def validate_text(text: str, *, lab_dir: str | None = None, environ: dict[str, str] | None = None,
                  require_sha256: bool = False) -> Validation:
    v = Validation()
    env = dict(os.environ if environ is None else environ)
    try:
        root = compose(text)
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None) or getattr(e, "context_mark", None)
        problem = getattr(e, "problem", None) or str(e).splitlines()[0]
        v.problems.append({"severity": "error", "code": "yaml_syntax", "message": f"YAML syntax: {problem}.",
                           "line": mark.line + 1 if mark else None, "column": mark.column + 1 if mark else None,
                           "path": None})
        _, n = _mask_unparsed(text)
        if n:
            v.problems.append({"severity": "error", "code": "secret_literal", "message": "This file holds a secret "
                               "value. Put the key in an environment variable and reference it with "
                               "`api_key_env: NAME`.", "line": None, "column": None, "path": None})
        return v
    positions: dict[tuple[Any, ...], Pos] = {}
    if root is not None:
        _index(root, (), positions, [])
    ctx = _Ctx(positions, lab_dir, env, require_sha256)
    if not isinstance(data, dict):
        ctx.add("error", "not_a_mapping", "lab.yaml must be a mapping with at least a `models:` key.")
        v.problems = ctx.problems
        return v
    v.data = data
    v.secrets = scan_secrets(text, root)
    for s in v.secrets:
        if s.code == "secret_literal":
            ctx.add("error", "secret_literal", f"`{s.path}` holds a secret value. Put the key in an environment "
                    "variable and reference it with `api_key_env: NAME`.", parse_dotted(s.path))
        elif not any(p["code"] == "credential_in_source" and p["path"] == s.path for p in ctx.problems):
            ctx.add("error", "credential_in_source", f"`{s.path}` carries a credential (URL user info or a presigned "
                    "query). Keep credentials in environment variables.", parse_dotted(s.path))
    for k in data:
        if k not in TOP_KEYS:
            ctx.add("warning", "unknown_key", f"`{k}` is not a lab key; it is ignored.", (k,))
    workers = data.get("workers", 4)
    if not (isinstance(workers, int) and not isinstance(workers, bool) and workers >= 1):
        ctx.add("error", "workers_invalid", "workers must be a whole number of at least 1.", ("workers",))
        workers = 4
    finetune = data.get("finetune") or {}
    ft_rows: list[dict[str, Any]] = []
    if not isinstance(finetune, dict):
        ctx.add("error", "finetune_no_train", "finetune must be a mapping of name: {from, train, ...}.", ("finetune",))
        finetune = {}
    for fname, fspec in finetune.items():
        fspec = fspec if isinstance(fspec, dict) else {}
        train = fspec.get("train")
        trains = [train] if isinstance(train, str) else ([str(t) for t in train] if isinstance(train, list) else [])
        if not trains:
            ctx.add("error", "finetune_no_train", f"finetune `{fname}` needs `train:` (a JSONL of rows).",
                    ("finetune", fname))
        for t in trains:
            if not os.path.isfile(ctx.local_path(t)):
                ctx.add("error", "finetune_train_missing", f"finetune `{fname}`: training file {t} does not exist.",
                        ("finetune", fname, "train"))
        ft_rows.append({"name": str(fname), "from": fspec.get("from"), "base_model": fspec.get("base_model"),
                        "train": trains, "steps": fspec.get("steps") if isinstance(fspec.get("steps"), int) else None})
    models = data.get("models")
    models_d: dict[str, Any] = {}
    if models is None or models == {}:
        if not finetune:
            ctx.add("error", "no_models", "lab.yaml has no models. Add at least one under `models:`.",
                    ("models",) if "models" in data else ())
    elif not isinstance(models, dict):
        ctx.add("error", "no_models", "`models:` must be a mapping of name: spec.", ("models",))
    else:
        models_d = dict(models)
    for fname in finetune:
        models_d.setdefault(fname, {"finetuned": fname})
    infos = [_check_model(ctx, str(n), s, set(map(str, finetune))) for n, s in models_d.items()]
    names = [i["name"] for i in infos]
    baseline = data.get("baseline")
    if baseline is not None and str(baseline) not in names:
        ctx.add("error", "unknown_model_ref", f"baseline `{baseline}` is not in models.{_did_you_mean(baseline, names)}",
                ("baseline",))
    jev = data.get("jevbench") or []
    if isinstance(jev, str):
        jev = [jev]
    if not isinstance(jev, list):
        ctx.add("error", "unknown_model_ref", "jevbench must be a list of model names.", ("jevbench",))
        jev = []
    for i, m in enumerate(jev):
        if str(m) not in names:
            ctx.add("error", "unknown_model_ref", f"jevbench names `{m}`, which is not in models."
                    f"{_did_you_mean(m, names)}", ("jevbench", i))
            continue
        info = next(x for x in infos if x["name"] == str(m))
        info["jevbench"] = True
        if info["kind"] not in ("serve", "url", "finetuned"):
            ctx.add("error", "jevbench_needs_systemone", f"jevbench needs a System One model (serve, url or "
                    f"finetuned); `{m}` is {info['kind']}.", ("jevbench", i))
    for info in infos:
        info["is_baseline"] = baseline is not None and info["name"] == str(baseline)
    suites = data.get("suites", ["smoke"])
    if suites is None:
        suites = ["smoke"]
    if not isinstance(suites, list):
        suites = [suites]
    suite_rows = [x for x in (_suite_info(ctx, i, s) for i, s in enumerate(suites)) if x]
    calibrate = bool(data.get("calibrate"))
    if calibrate and suite_rows and not any(s["has_splits"] for s in suite_rows if s["has_splits"] is not None) \
            and all(s["has_splits"] is not None for s in suite_rows):
        ctx.add("warning", "calibrate_no_splits", "calibrate is on, but no suite has dev/test splits, so there is "
                "nothing to fit temperatures on.", ("calibrate",))
    compute = data.get("compute") or {}
    backend = "local"
    max_hours = 2.0
    options: dict[str, Any] = {}
    if not isinstance(compute, dict):
        ctx.add("error", "bad_compute_backend", "compute must be a mapping.", ("compute",))
        compute = {}
    b = compute.get("backend") or compute.get("on") or compute.get(True) or "local"
    if b not in BACKENDS:
        bpath = ("compute", "backend" if "backend" in compute else ("on" if "on" in compute else True))
        ctx.add("error", "bad_compute_backend", f"compute backend `{b}` is not one of local, ssh, aws, vast."
                f"{_did_you_mean(b, list(BACKENDS))}", bpath)
    else:
        backend = b
    for k in compute:
        if k not in COMPUTE_KEYS and k is not True:
            ctx.add("warning", "unknown_compute_option", f"compute: `{k}` is not a compute option.", ("compute", k))
    ck = compute_keys()
    for bk in ("ssh", "aws", "vast"):
        sect = compute.get(bk)
        if sect is None:
            continue
        if not isinstance(sect, dict):
            ctx.add("error", "bad_compute_backend", f"compute.{bk} must be a mapping.", ("compute", bk))
            continue
        for k in sect:
            if k not in ck[bk]:
                ctx.add("warning", "unknown_compute_option", f"compute.{bk}: `{k}` is not a {bk} option."
                        f"{_did_you_mean(k, sorted(ck[bk]))}", ("compute", bk, k))
    mh = compute.get("max_hours", 2)
    if isinstance(mh, (int, float)) and not isinstance(mh, bool) and mh > 0:
        max_hours = float(mh)
    else:
        ctx.add("error", "bad_compute_backend", "compute.max_hours must be a number above 0.", ("compute", "max_hours"))
    if isinstance(compute.get(backend), dict):
        options = dict(compute[backend])
    for env in compute.get("env") or []:
        if isinstance(env, str) and env not in ctx.environ:
            ctx.add("warning", "env_not_set", f"compute.env passes {env}, which is not set in the shell Studio runs "
                    "in.", ("compute", "env"))
    n_models = len(infos)
    splits = sum(1 for s in suite_rows if s["has_splits"] is not False)
    rows_known = [s["rows_estimate"] for s in suite_rows]
    req = None if any(r is None for r in rows_known) else n_models * sum(rows_known)  # type: ignore[arg-type]
    for info in infos:
        info["spec_text"] = _short(_mask_text(info["spec_text"]))
    v.summary = {
        "name": str(data.get("name") or ""),
        "workers": workers,
        "models": infos,
        "suites": suite_rows,
        "finetune": ft_rows,
        "calibrate": calibrate,
        "baseline": str(baseline) if baseline is not None else None,
        "jevbench": [str(m) for m in jev],
        "compute": {"backend": backend, "max_hours": max_hours, "options": _mask_obj(options)},
        "plan": {"runs": n_models * len(suite_rows), "calibrated_runs_max": n_models * splits if calibrate else 0,
                 "requests_estimate": req},
    }
    order = {"error": 0, "warning": 1}
    seen: set[tuple[str, str | None]] = set()
    unique = []
    for p in ctx.problems:  # one problem per code and key (a credential is found by two checks)
        if p["code"] in ("credential_in_source", "secret_literal"):
            if (p["code"], p["path"]) in seen:
                continue
            seen.add((p["code"], p["path"]))
        unique.append(p)
    v.problems = sorted(unique, key=lambda p: (p["line"] or 0, order[p["severity"]]))
    return v


def _mask_text(s: str) -> str:
    for p in PATTERNS:
        s = p.sub(MASK, s)
    s = PRESIGNED.sub(lambda m: m.group(1) + MASK, s)
    return USERINFO.sub(lambda m: m.group(1) + MASK + "@", s)


def _mask_obj(o: Any, key: str | None = None) -> Any:
    if isinstance(o, dict):
        return {k: _mask_obj(v, str(k)) for k, v in o.items()}
    if isinstance(o, list):
        return [_mask_obj(v, key) for v in o]
    if isinstance(o, str):
        if key and key.lower() in SECRET_KEYS and o:
            return MASK
        return _mask_text(o)
    return o
