"""Adapters: how a model is asked. `make_adapter` builds one from a config spec.

Spec forms (a string, or a mapping from lab.yaml):

    "http://host:port"               a System One server (strands-decider serve)
    {"url": ..., "model": ...}       the same, with options
    {"chat": "gpt-x", "base_url": ..., "api_key_env": ...}   an OpenAI-compatible chat model
    {"python": "module:attr", "path": "."}                    your own code
    "uniform" | "majority" | "random"                          baselines
    {"serve": "<checkpoint or hub id>", "vision": false}      started by the runner (serve.py)
"""

from __future__ import annotations

from typing import Any

from .base import Adapter, AdapterError, normalise
from .others import ChatAdapter, MajorityBaseline, PythonAdapter, RandomBaseline, UniformBaseline
from .systemone import SystemOneAdapter

BASELINES = {"uniform": UniformBaseline, "majority": MajorityBaseline, "random": RandomBaseline}

__all__ = ["Adapter", "AdapterError", "ChatAdapter", "MajorityBaseline", "PythonAdapter", "RandomBaseline",
           "SystemOneAdapter", "UniformBaseline", "make_adapter", "normalise"]


def make_adapter(spec: str | dict[str, Any], *, base_dir: str = "") -> Adapter:
    """An adapter from a spec (see the module docstring). `serve` specs are resolved by the
    runner first, which starts the server and passes its URL here."""
    if isinstance(spec, str):
        if spec in BASELINES:
            return BASELINES[spec]()
        if spec.startswith(("http://", "https://")):
            return SystemOneAdapter(spec, base_dir=base_dir)
        raise ValueError(f"unknown model spec {spec!r}: use a URL, a baseline name, or a mapping")
    spec = dict(spec)
    if "baseline" in spec:
        name = spec.pop("baseline")
        if name not in BASELINES:
            raise ValueError(f"unknown baseline {name!r}; one of {sorted(BASELINES)}")
        return BASELINES[name](**spec)
    if "url" in spec:
        return SystemOneAdapter(spec.pop("url"), base_dir=base_dir, **spec)
    if "chat" in spec:
        return ChatAdapter(spec.pop("chat"), **spec)
    if "python" in spec:
        return PythonAdapter(spec["python"], path=spec.get("path") or base_dir or None)
    if "serve" in spec:
        raise ValueError("a `serve` model must be started first (decider_lab.serve.served)")
    raise ValueError(f"cannot build an adapter from {spec!r}")
