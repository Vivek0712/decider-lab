"""Baselines and your own Python code as adapters. General LLMs (Bedrock, Strands, OpenAI-compatible)
live in llm.py."""

from __future__ import annotations

import importlib
import random
import sys
from collections import Counter
from typing import Any

from ..rows import Row, row_id
from .base import Adapter, AdapterError, normalise


class UniformBaseline(Adapter):
    """Every option equally likely. Yes/no lands in the abstention band, so it scores 0."""

    name = "uniform"

    def predict_one(self, row: Row) -> list[float]:
        n = len(row["options"])
        return [1.0 / n] * n


class MajorityBaseline(Adapter):
    """Always the most common gold label of the row's kind (and family) in the suite: the
    constant-answer baseline a model must beat to show it reads the input at all."""

    name = "majority"

    def __init__(self, sharpness: float = 0.99) -> None:
        self.sharpness = sharpness
        self.top: dict[tuple[str, str], int] = {}

    def fit(self, rows: list[Row]) -> None:
        by: dict[tuple[str, str], Counter[int]] = {}
        for r in rows:
            by.setdefault((r["kind"], str(r.get("task"))), Counter())[r["label"]] += 1
        self.top = {k: c.most_common(1)[0][0] for k, c in by.items()}

    def predict_one(self, row: Row) -> list[float]:
        n = len(row["options"])
        top = min(self.top.get((row["kind"], str(row.get("task"))), 0), n - 1)
        rest = (1 - self.sharpness) / (n - 1)
        return [self.sharpness if i == top else rest for i in range(n)]


class RandomBaseline(Adapter):
    """A confident guess at random: the floor for a model that never abstains."""

    name = "random"

    def __init__(self, seed: int = 0, sharpness: float = 0.9) -> None:
        self.seed, self.sharpness = seed, sharpness

    def predict_one(self, row: Row) -> list[float]:
        n = len(row["options"])
        pick = random.Random(f"{self.seed}:{row_id(row)}").randrange(n)
        rest = (1 - self.sharpness) / (n - 1)
        return [self.sharpness if i == pick else rest for i in range(n)]


class PythonAdapter(Adapter):
    """`module:attr`, where attr is a function row -> probabilities, an object with
    `predict_one`/`predict`, or a class taking no arguments that has them."""

    name = "python"

    def __init__(self, target: str, path: str | None = None) -> None:
        if path and path not in sys.path:
            sys.path.insert(0, path)
        mod, _, attr = target.partition(":")
        if not attr:
            raise ValueError(f"python adapter target must be module:attr, got {target!r}")
        obj: Any = getattr(importlib.import_module(mod), attr)
        if isinstance(obj, type):
            obj = obj()
        self.target, self.obj = target, obj

    def describe(self) -> dict[str, Any]:
        return {"adapter": self.name, "target": self.target}

    def fit(self, rows: list[Row]) -> None:
        if hasattr(self.obj, "fit"):
            self.obj.fit(rows)

    def predict_one(self, row: Row) -> list[float]:
        fn = getattr(self.obj, "predict_one", None) or self.obj
        try:
            probs = fn(row)
        except AdapterError:
            raise
        except Exception as e:  # a user's model failing on one row must not end the run
            raise AdapterError(f"{type(e).__name__}: {e}") from e
        return normalise(list(probs), len(row["options"]))

    def predict(self, rows: list[Row]) -> list[list[float] | AdapterError]:
        if hasattr(self.obj, "predict") and not hasattr(self.obj, "predict_one"):
            out: list[list[float] | AdapterError] = []
            for r, p in zip(rows, self.obj.predict(rows), strict=True):
                try:
                    out.append(normalise(list(p), len(r["options"])))
                except AdapterError as e:
                    out.append(e)
            return out
        return super().predict(rows)
