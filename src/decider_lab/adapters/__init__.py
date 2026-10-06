"""Adapters: how a model is asked. `make_adapter` builds one from a config spec.

Spec forms (a string, or a mapping from lab.yaml):

    "http://host:port"               a System One server (strands-decider serve)
    {"url": ..., "model": ...}       the same, with options
    {"bedrock": "us.amazon.nova-pro-v1:0", "region": ..., "profile": ...}   an Amazon Bedrock model
    {"strands": {"provider": "bedrock", "model_id": ...}}                  a Strands Agents model
    {"chat": "model", "base_url": ..., "api_key_env": ...}                 an OpenAI-compatible endpoint
    {"python": "module:attr", "path": "."}                    your own code
    "uniform" | "majority" | "random"                          baselines
    {"serve": "<checkpoint or hub id>", "vision": false}      started by the runner (serve.py)
"""

from __future__ import annotations

from typing import Any

from .base import Adapter, AdapterError, normalise
from .llm import BedrockAdapter, ChatAdapter, LLMAdapter, StrandsAdapter
from .others import MajorityBaseline, PythonAdapter, RandomBaseline, UniformBaseline
from .systemone import SystemOneAdapter

BASELINES = {"uniform": UniformBaseline, "majority": MajorityBaseline, "random": RandomBaseline}
# keys in a lab's model entry that the lab reads itself, not the adapter
LAB_KEYS = ("workers",)

__all__ = ["Adapter", "AdapterError", "BedrockAdapter", "ChatAdapter", "LLMAdapter", "StrandsAdapter", "MajorityBaseline", "PythonAdapter", "RandomBaseline",
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
    spec = {k: v for k, v in spec.items() if k not in LAB_KEYS}
    if "baseline" in spec:
        name = spec.pop("baseline")
        if name not in BASELINES:
            raise ValueError(f"unknown baseline {name!r}; one of {sorted(BASELINES)}")
        return BASELINES[name](**spec)
    if "url" in spec:
        return SystemOneAdapter(spec.pop("url"), base_dir=base_dir, **spec)
    if "bedrock" in spec:
        return BedrockAdapter(spec.pop("bedrock"), base_dir=base_dir, **spec)
    if "strands" in spec:
        cfg = spec.pop("strands")
        cfg = {"model_id": cfg} if isinstance(cfg, str) else dict(cfg)
        return StrandsAdapter(base_dir=base_dir, **cfg, **spec)
    if "chat" in spec:
        return ChatAdapter(spec.pop("chat"), base_dir=base_dir, **spec)
    if "python" in spec:
        return PythonAdapter(spec["python"], path=spec.get("path") or base_dir or None)
    if "serve" in spec:
        raise ValueError("a `serve` model must be started first (decider_lab.serve.served)")
    raise ValueError(f"cannot build an adapter from {spec!r}")
