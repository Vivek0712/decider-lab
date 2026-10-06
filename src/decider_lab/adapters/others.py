"""Adapters beyond System One: baselines, a Python callable, and OpenAI-compatible chat models."""

from __future__ import annotations

import importlib
import json
import os
import random
import re
import string
import sys
import time
import urllib.error
import urllib.request
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


CHAT_PROMPT = """You are a decision model. Read the input and answer the question by giving a
probability for every option. Use calibrated probabilities: say 0.5 when unsure.

Input:
{state}

Question: {instructions}

Options:
{options}

Reply with JSON only, one probability per option letter, summing to 1, e.g. {{{example}}}."""


class ChatAdapter(Adapter):
    """An OpenAI-compatible /chat/completions model asked for probabilities as JSON.

    Text only; the model states its probabilities, which are usually less calibrated than
    a decision head's, and that is part of what the comparison measures."""

    name = "chat"

    def __init__(self, model: str, base_url: str = "https://api.openai.com/v1", api_key_env: str = "OPENAI_API_KEY",
                 timeout: float = 120.0, retries: int = 2, temperature: float = 0.0) -> None:
        self.model, self.base_url = model, base_url.rstrip("/")
        self.api_key_env, self.timeout, self.retries, self.temperature = api_key_env, timeout, retries, temperature

    def describe(self) -> dict[str, Any]:
        return {"adapter": self.name, "model": self.model, "base_url": self.base_url}

    def prompt(self, row: Row) -> tuple[str, list[str]]:
        letters = list(string.ascii_uppercase[: len(row["options"])])
        if len(row["options"]) > 26:
            raise AdapterError("the chat adapter supports at most 26 options")
        opts = "\n".join(f"{L}. {d if row['kind'] != 'noul' else ('No' if n == 'false' else 'Yes')}"
                         f"{'' if row['kind'] != 'noul' else ' - ' + d}"
                         for L, (n, d) in zip(letters, row["options"], strict=True))
        state = row["state"] if isinstance(row["state"], str) else json.dumps(row["state"], ensure_ascii=False)
        example = ", ".join(f'"{L}": 0.{i}' for i, L in enumerate(letters[:2], 3))
        return CHAT_PROMPT.format(state=state, instructions=row["instructions"], options=opts,
                                  example=example), letters

    def predict_one(self, row: Row) -> list[float]:
        text, letters = self.prompt(row)
        key = os.environ.get(self.api_key_env, "") if self.api_key_env else ""
        body = {"model": self.model, "temperature": self.temperature,
                "messages": [{"role": "user", "content": text}]}
        headers = {"Content-Type": "application/json", **({"Authorization": f"Bearer {key}"} if key else {})}
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(self.base_url + "/chat/completions", data=json.dumps(body).encode(),
                                         headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    content = json.loads(r.read())["choices"][0]["message"]["content"]
                m = re.search(r"\{.*\}", content, re.S)
                if not m:
                    raise AdapterError(f"no JSON in reply: {content[:120]!r}")
                probs = json.loads(m.group(0))
                return normalise([float(probs.get(L, 0.0)) for L in letters], len(letters))
            except urllib.error.HTTPError as e:
                if e.code < 500 and e.code != 429:
                    raise AdapterError(f"HTTP {e.code}: {e.read()[:300]!r}") from e
                last = e
            except (urllib.error.URLError, TimeoutError, OSError, KeyError, ValueError) as e:
                last = e
            time.sleep(min(2 ** attempt, 8))
        raise AdapterError(f"no answer after {self.retries + 1} attempts: {last}")
