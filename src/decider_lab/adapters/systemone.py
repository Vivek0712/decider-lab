"""Any server that speaks the System One API: `strands-decider serve`, or another compatible one.

    POST {url}/v1/systemone  {"state": ..., "questions": {"q": {...}}, "images": [b64, ...]}

The adapter asks one question per request and reads the answer back as one probability per
canonical option: P(false), P(true) for noul; the per-option probabilities for choice; the
per-level probabilities for score.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import time
import urllib.error
import urllib.request
from typing import Any

from ..rows import Row
from .base import Adapter, AdapterError, normalise


def question(row: Row) -> dict[str, Any]:
    """The System One question for a row (the same rendering strands-decider trains with)."""
    kind, opts = row["kind"], row["options"]
    if kind == "noul":
        return {"type": "noul", "instructions": row["instructions"], "criteria": {n: d for n, d in opts}}
    if kind == "choice":
        return {"type": "choice", "instructions": row["instructions"], "criteria": {n: d for n, d in opts}}
    return {"type": "score", "instructions": row["instructions"], "criteria": [d for _, d in opts]}


def encode_image(ref: str, base_dir: str = "") -> str:
    """A data: URI stays as it is; a path is read and base64-encoded."""
    if ref.startswith("data:"):
        return ref
    path = ref if os.path.isabs(ref) or not base_dir else os.path.join(base_dir, ref)
    with open(path, "rb") as fh:
        data = base64.b64encode(fh.read()).decode("ascii")
    mime = mimetypes.guess_type(path)[0] or "image/png"
    return f"data:{mime};base64,{data}"


def read_answer(row: Row, answer: dict[str, Any]) -> list[float]:
    """The answer as one probability per canonical option."""
    kind, names = row["kind"], [n for n, _ in row["options"]]
    if answer.get("type") != kind:
        raise AdapterError(f"asked a {kind} question, got a {answer.get('type')!r} answer")
    if kind == "noul":
        p = float(answer["noul"])
        return [1.0 - p, p]
    probs = answer.get("probabilities") or {}
    keys = names if kind == "choice" else [str(i) for i in range(len(names))]
    missing = [k for k in keys if k not in probs]
    if missing:
        raise AdapterError(f"answer has no probability for {missing}")
    return normalise([float(probs[k]) for k in keys], len(keys))


class SystemOneAdapter(Adapter):
    name = "systemone"

    def __init__(self, url: str, *, model: str | None = None, timeout: float = 120.0, retries: int = 2,
                 headers: dict[str, str] | None = None, base_dir: str = "") -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.retries = retries
        self.headers = {"Content-Type": "application/json", **(headers or {})}
        self.base_dir = base_dir
        self._health: dict[str, Any] | None = None

    def health(self) -> dict[str, Any]:
        """GET /health, cached; {} when the server has none."""
        if self._health is None:
            try:
                with urllib.request.urlopen(self.url + "/health", timeout=10) as r:
                    self._health = json.loads(r.read().decode("utf-8"))
            except (urllib.error.URLError, ValueError, OSError):
                self._health = {}
        return self._health

    def describe(self) -> dict[str, Any]:
        h = self.health()
        return {"adapter": self.name, "url": self.url, "model": self.model or h.get("model"),
                "health": h}

    def predict_one(self, row: Row) -> list[float]:
        body: dict[str, Any] = {"state": row["state"], "questions": {"q": question(row)}}
        if self.model:
            body["model"] = self.model
        if row.get("images"):
            body["images"] = [encode_image(i, self.base_dir) for i in row["images"]]
        data = json.dumps(body).encode("utf-8")
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(self.url + "/v1/systemone", data=data, headers=self.headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    payload = json.loads(r.read().decode("utf-8"))
                return read_answer(row, payload["answers"]["q"])
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:300]
                if e.code < 500:  # the request is wrong; asking again will not help
                    raise AdapterError(f"HTTP {e.code}: {detail}") from e
                last = AdapterError(f"HTTP {e.code}: {detail}")
            except (urllib.error.URLError, TimeoutError, OSError, KeyError, ValueError) as e:
                last = e
            time.sleep(min(2 ** attempt, 8))
        raise AdapterError(f"no answer after {self.retries + 1} attempts: {last}")
