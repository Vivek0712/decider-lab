"""The adapter contract: rows in, one probability per canonical option out.

Anything that can answer a decision row is an adapter: a Strands Decider server, another
System One compatible API, an OpenAI-compatible chat model, a Python function, a baseline.
Write one by subclassing `Adapter` and implementing `predict_one` (or `predict` for batching).
"""

from __future__ import annotations

import math
from typing import Any

from ..rows import Row


class AdapterError(RuntimeError):
    """One row could not be answered; the runner records it and goes on."""


class Adapter:
    name = "adapter"

    def describe(self) -> dict[str, Any]:
        """What is recorded in run.json to say which model answered."""
        return {"adapter": self.name}

    def predict_one(self, row: Row) -> list[float]:
        raise NotImplementedError

    def predict(self, rows: list[Row]) -> list[list[float] | AdapterError]:
        out: list[list[float] | AdapterError] = []
        for r in rows:
            try:
                out.append(self.predict_one(r))
            except AdapterError as e:
                out.append(e)
        return out

    def close(self) -> None:
        return None


def normalise(probs: list[float], n: int) -> list[float]:
    """A valid distribution over n options, or AdapterError."""
    if len(probs) != n:
        raise AdapterError(f"expected {n} probabilities, got {len(probs)}")
    if any((not isinstance(p, int | float)) or math.isnan(p) or p < 0 for p in probs):
        raise AdapterError(f"probabilities must be non-negative numbers: {probs}")
    total = float(sum(probs))
    if total <= 0:
        raise AdapterError("probabilities sum to zero")
    return [float(p) / total for p in probs]
