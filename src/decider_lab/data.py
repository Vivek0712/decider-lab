"""Data tools: make decision rows from a CSV, generate training rows, split, and check for leaks.

The leak check is the one to run before every fine-tune: a training row whose state also
appears in an evaluation suite makes that suite's score meaningless for the model.
"""

from __future__ import annotations

import csv
import json
import random
import re
from collections import Counter
from typing import Any

from .rows import YES_NO, Row, check, read_jsonl, row_id
from .suites import generators
from .suites import split as split_rows

YES = {"yes", "true", "y", "1"}
NO = {"no", "false", "n", "0"}


def from_csv(path: str, *, task: str = "custom", delimiter: str = ",") -> list[Row]:
    """Rows from a CSV with columns: state, question, answer, and optionally options and kind.

    - yes/no: answer is yes/no (or true/false), options empty -> noul
    - choice: options "A|B|C", answer one of them -> choice
    - score:  kind "score", options are the levels low to high "bad|ok|good", answer one of them
    """
    out: list[Row] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for n, r in enumerate(csv.DictReader(fh, delimiter=delimiter), 2):
            missing = [c for c in ("state", "question", "answer") if not (r.get(c) or "").strip()]
            if missing:
                raise ValueError(f"{path}:{n}: missing {missing}")
            answer = r["answer"].strip()
            opts = [o.strip() for o in (r.get("options") or "").split("|") if o.strip()]
            kind = (r.get("kind") or "").strip().lower() or ("choice" if opts else "noul")
            if kind == "noul":
                if answer.lower() not in YES | NO:
                    raise ValueError(f"{path}:{n}: a yes/no answer must be yes or no, got {answer!r}")
                row: Row = {"kind": "noul", "state": r["state"], "instructions": r["question"], "options": YES_NO,
                            "label": int(answer.lower() in YES)}
            else:
                if answer not in opts:
                    raise ValueError(f"{path}:{n}: answer {answer!r} is not one of the options {opts}")
                options = [[o, o] for o in opts] if kind == "choice" else [[str(i), o] for i, o in enumerate(opts)]
                row = {"kind": kind, "state": r["state"], "instructions": r["question"], "options": options,
                       "label": opts.index(answer)}
            row["task"] = (r.get("task") or "").strip() or task
            out.append(row)
    return check(out, where=f"{path}: ")


def generate(per_kind: int, families: list[str], seed: int, exclude: set[str] | None = None) -> list[Row]:
    """Generated training rows; any row whose id is in `exclude` (an eval suite) is dropped."""
    rng = random.Random(seed)
    rows: list[Row] = []
    for kind in ("noul", "choice", "score"):
        for fam in families:
            rows += [{**r, "task": f"train/{fam}"} for r in generators.draw(fam, kind, per_kind, rng)]
    if exclude:
        rows = [r for r in rows if row_id(r) not in exclude]
    return check(rows)


def assign_splits(rows: list[Row], dev_fraction: float, seed: int) -> list[Row]:
    return split_rows(rows, random.Random(seed), dev_fraction)


def _norm(x: Any) -> str:
    s = x if isinstance(x, str) else json.dumps(x, sort_keys=True, ensure_ascii=False)
    return re.sub(r"\s+", " ", s.strip().lower())


def leakcheck(train: list[Row], evals: list[Row]) -> dict[str, Any]:
    """Training rows that share a normalised state with an evaluation row (the same question
    about the same input, or a different question about it: both leak)."""
    ev = {_norm(r["state"]): r for r in evals}
    hits = [(i, r) for i, r in enumerate(train) if _norm(r["state"]) in ev]
    return {"train_rows": len(train), "eval_rows": len(evals), "overlapping": len(hits),
            "examples": [{"train_row": i, "task": r.get("task"), "state": str(r["state"])[:120]} for i, r in hits[:5]],
            "overlapping_indices": [i for i, _ in hits]}


def stats(rows: list[Row]) -> dict[str, Any]:
    labels: dict[str, Counter[int]] = {}
    for r in rows:
        labels.setdefault(r["kind"], Counter())[r["label"]] += 1
    return {"rows": len(rows), "by_kind": dict(Counter(r["kind"] for r in rows)),
            "by_task": dict(Counter(str(r.get("task")) for r in rows).most_common(30)),
            "by_split": dict(Counter(str(r.get("split")) for r in rows)),
            "label_balance": {k: dict(sorted(v.items())) for k, v in labels.items()},
            "with_images": sum(1 for r in rows if r.get("images"))}


def load(path: str) -> list[Row]:
    return check(read_jsonl(path), where=f"{path}: ")
