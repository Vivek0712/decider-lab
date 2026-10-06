"""Decision rows: the one record every suite, adapter, trainer and scorer shares.

A row is the on-disk training example of Strands Decider (``strands_decider.data.format.Example``),
so a suite is also valid training data and a training file is also a valid suite:

    {"kind": "noul" | "choice" | "score",
     "state": "the content to judge (str, or JSON-able data)",
     "instructions": "the question",
     "options": [[name, description], ...],   # canonical order
     "label": 0,                               # index into options
     "task": "family/name",                    # provenance; reports break down by it
     "split": "dev" | "test" | "train",        # optional
     "images": ["path.png" | "data:..." ],     # optional, needs a vision model
     "id": "..."}                              # optional; derived from content when absent

Conventions, the same as Strands Decider's:
  - noul (yes/no): options are [["false", ...], ["true", ...]], label 1 means yes.
  - choice: label is the position of the right option.
  - score: options are ordered levels, low to high; label is the gold level index.

A prediction is one probability per canonical option, so every metric is defined on rows and
probability vectors and never on a particular model's wire format.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from collections.abc import Iterable, Iterator
from typing import Any

Row = dict[str, Any]
KINDS = ("noul", "choice", "score")
YES_NO = [["false", "The answer is no."], ["true", "The answer is yes."]]
MAX_SCORE_LEVELS = 10


def row_id(row: Row) -> str:
    """A stable id from what the model sees (state, question, options, images), not the label."""
    if row.get("id"):
        return str(row["id"])
    key = json.dumps([row.get("kind"), row.get("state"), row.get("instructions"), row.get("options"),
                      row.get("images") or []], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def problems(row: Row) -> list[str]:
    """Everything wrong with one row, as messages; an empty list means the row is valid."""
    out: list[str] = []
    kind = row.get("kind")
    if kind not in KINDS:
        return [f"kind must be one of {KINDS}, got {kind!r}"]
    state = row.get("state")
    if state is None or (isinstance(state, str) and not state.strip()):
        out.append("state is empty")
    if not isinstance(row.get("instructions"), str) or not row["instructions"].strip():
        out.append("instructions must be a non-empty string")
    opts = row.get("options")
    if not isinstance(opts, list) or len(opts) < 2:
        return [*out, "options must be a list of at least two [name, description] pairs"]
    if not all(isinstance(o, list | tuple) and len(o) == 2 and all(isinstance(x, str) for x in o) for o in opts):
        out.append("every option must be a [name, description] pair of strings")
        return out
    names = [o[0] for o in opts]
    if len(set(names)) != len(names):
        out.append("option names must be unique")
    if kind == "noul" and names != ["false", "true"]:
        out.append('noul options must be named ["false", "true"] in that order')
    if kind == "score":
        if not 2 <= len(opts) <= MAX_SCORE_LEVELS:
            out.append(f"score needs 2 to {MAX_SCORE_LEVELS} levels")
        if names != [str(i) for i in range(len(opts))]:
            out.append('score option names must be the level indices "0", "1", ...')
    label = row.get("label")
    if not isinstance(label, int) or isinstance(label, bool) or not 0 <= label < len(opts):
        out.append(f"label must be an integer index into options, got {label!r}")
    images = row.get("images")
    if images is not None and (not isinstance(images, list) or not all(isinstance(i, str) for i in images)):
        out.append("images must be a list of file paths or data: URIs")
    return out


def check(rows: Iterable[Row], *, where: str = "") -> list[Row]:
    """The rows, each given its id; raises ValueError naming the first bad rows."""
    out, bad = [], []
    for i, r in enumerate(rows):
        p = problems(r)
        if p:
            bad.append(f"{where}row {i}: {'; '.join(p)}")
        else:
            out.append({**r, "id": row_id(r)})
    if bad:
        more = f" (and {len(bad) - 5} more)" if len(bad) > 5 else ""
        raise ValueError("invalid rows:\n  " + "\n  ".join(bad[:5]) + more)
    return out


def _open(path: str, mode: str = "rt") -> Any:
    return gzip.open(path, mode, encoding="utf-8") if path.endswith(".gz") else open(path, mode, encoding="utf-8")


def read_jsonl(path: str) -> Iterator[Row]:
    with _open(path) as fh:
        for n, line in enumerate(fh, 1):
            if line.strip():
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as e:
                    raise ValueError(f"{path}:{n}: not JSON: {e}") from e


def write_jsonl(path: str, rows: Iterable[Row]) -> int:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    n = 0
    with _open(path, "wt") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    return n


def rows_sha256(rows: Iterable[Row]) -> str:
    """A fingerprint of a suite: its row ids and labels, in order."""
    h = hashlib.sha256()
    for r in rows:
        h.update(f"{row_id(r)}:{r.get('label')}\n".encode())
    return h.hexdigest()


def family(row: Row) -> str:
    return str(row.get("task") or "unknown")
