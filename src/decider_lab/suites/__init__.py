"""Suites: named, reproducible sets of decision rows.

    smoke       90 generated rows (arithmetic, calendar, seating x 3 kinds x 10). Seconds to
                run; proves the plumbing, not the model. No downloads.
    synthetic   generated families, every gold label computed by a program, balanced labels,
                dev/test splits. Options: per_kind (150), families, seed. `chess` needs
                python-chess (the `chess` extra).
    heldout     public datasets no Strands Decider trains on (StrategyQA, CommonsenseQA 2.0,
                CommonsenseQA, ARC-Challenge, HellaSwag, STS-B), downloaded at pinned
                revisions, plus the synthetic families. Needs the `heldout` extra.
    <path>      any JSONL of decision rows (see rows.py).

JevBench's public tasks run through JevBench's own harness instead (jevbench.py).

Every built suite is cached under ~/.cache/decider-lab/suites by its parameters, and its
sha256 fingerprint is recorded in each run, so two runs can be checked to have scored the
same rows.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
from typing import Any

from ..rows import Row, check, read_jsonl, write_jsonl
from . import generators

DEFAULT_FAMILIES = ("arithmetic", "calendar", "seating")
DEV_FRACTION = 0.4
CACHE = os.environ.get("DECIDER_LAB_CACHE", os.path.join(os.path.expanduser("~"), ".cache", "decider-lab"))


def split(rows: list[Row], rng: random.Random, dev_fraction: float = DEV_FRACTION) -> list[Row]:
    """Within each task family and kind, `dev_fraction` of rows to dev and the rest to test."""
    out: list[Row] = []
    groups: dict[tuple[str, str], list[Row]] = {}
    for r in rows:
        groups.setdefault((str(r["task"]), r["kind"]), []).append(r)
    for mine in groups.values():
        dev = set(rng.sample(range(len(mine)), round(dev_fraction * len(mine))))
        out += [{**r, "split": "dev" if i in dev else "test"} for i, r in enumerate(mine)]
    return out


def synthetic(per_kind: int = 150, families: list[str] | tuple[str, ...] = DEFAULT_FAMILIES,
              seed: int = 0, kinds: tuple[str, ...] = ("noul", "choice", "score")) -> list[Row]:
    """Generated rows, `per_kind` per family and kind, with dev/test splits."""
    unknown = sorted(set(families) - set(generators.FAMILIES))
    if unknown:
        raise ValueError(f"unknown families {unknown}; available: {sorted(generators.FAMILIES)}")
    if "chess" in families:
        try:
            import chess  # noqa: F401
        except ImportError as e:
            raise ImportError("the chess family needs python-chess: pip install 'decider-lab[chess]'") from e
    rng = random.Random(seed)
    rows: list[Row] = []
    for kind in kinds:
        for fam in families:
            rows += generators.draw(fam, kind, per_kind, rng)
    return split(rows, rng)


def _cached(name: str, params: dict[str, Any], build: Any) -> list[Row]:
    key = hashlib.sha256(json.dumps([name, params], sort_keys=True).encode()).hexdigest()[:12]
    path = os.path.join(CACHE, "suites", f"{name}-{key}.jsonl")
    if os.path.exists(path):
        return list(read_jsonl(path))
    rows = build()
    tmp = path + ".tmp"
    write_jsonl(tmp, rows)
    os.replace(tmp, path)
    return rows


def load_suite(spec: str | dict[str, Any], *, base_dir: str = "") -> tuple[str, list[Row], dict[str, Any]]:
    """(name, checked rows, params) for a suite spec: a built-in name, a path, or a mapping
    {"synthetic": {...}} / {"file": path, "name": ...} / {"heldout": {...}}."""
    if isinstance(spec, str):
        base, _, args = spec.partition(":")
        if base in ("smoke", "synthetic", "heldout"):
            # "synthetic:per_kind=100,seed=3" on the command line = {"synthetic": {...}} in a lab
            params: dict[str, Any] = {}
            for kv in filter(None, args.split(",")):
                k, _, v = kv.partition("=")
                params[k] = int(v) if v.lstrip("-").isdigit() else (v.split("+") if k == "families" else v)
            spec = {base: params}
        else:
            spec = {"file": spec}
    spec = dict(spec)
    if "file" in spec:
        path = spec["file"] if os.path.isabs(spec["file"]) or not base_dir else os.path.join(base_dir, spec["file"])
        name = spec.get("name") or os.path.basename(path).split(".")[0]
        return name, check(read_jsonl(path), where=f"{path}: "), {"file": path}
    (kind, params), = ((k, v or {}) for k, v in spec.items() if k != "name")
    name = spec.get("name") or kind
    if kind == "smoke":
        params = {"per_kind": 10, "families": list(DEFAULT_FAMILIES), "seed": 1234}
        rows = _cached("smoke", params, lambda: synthetic(10, DEFAULT_FAMILIES, 1234))
    elif kind == "synthetic":
        params = {"per_kind": 150, "families": list(DEFAULT_FAMILIES), "seed": 0, **params}
        rows = _cached("synthetic", params, lambda: synthetic(params["per_kind"], tuple(params["families"]),
                                                            params["seed"]))
    elif kind == "heldout":
        from .heldout import build

        params = {"seed": 0, **params}
        rows = _cached("heldout", params, lambda: build(**params))
    else:
        raise ValueError(f"unknown suite {kind!r}: smoke, synthetic, heldout, or a file")
    return name, check(rows, where=f"{name}: "), params
