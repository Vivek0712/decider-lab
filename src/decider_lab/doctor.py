"""`decider-lab doctor`: what this machine can do, and what to install for the rest."""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys

from . import __version__
from .suites import CACHE


def checks() -> list[tuple[str, str, str]]:
    """(status, item, detail) rows; status is ok, warn or info."""
    out: list[tuple[str, str, str]] = [("ok", "decider-lab", f"{__version__} on Python {platform.python_version()}")]
    if importlib.util.find_spec("torch"):
        import torch

        cuda = torch.cuda.is_available()
        mps = getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available()
        dev = torch.cuda.get_device_name(0) if cuda else ("Apple MPS" if mps else "CPU only")
        out.append(("ok" if cuda or mps else "warn", "torch", f"{torch.__version__} (cuda {torch.version.cuda}) -> {dev}"))
    else:
        out.append(("info", "torch", "not installed (only needed to serve or train a model here)"))
    smi = shutil.which("nvidia-smi")
    if smi:
        r = subprocess.run([smi, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
                           capture_output=True, text=True)
        out.append(("ok", "gpu", r.stdout.strip().replace("\n", "; ") or "nvidia-smi gave nothing"))
    if importlib.util.find_spec("strands_decider"):
        code = ("import dataclasses,json,strands_decider as s;from strands_decider.train import TrainConfig;"
                "from strands_decider.schema import SystemOneRequest as R;"
                "f={x.name for x in dataclasses.fields(TrainConfig)};"
                "print(json.dumps({'file':s.__file__,'vision':'images' in R.model_fields,"
                "'continue_from':'continue_from' in f}))")
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        if r.returncode == 0:
            info = json.loads(r.stdout.strip().splitlines()[-1])
            out.append(("ok", "strands-decider", os.path.dirname(info["file"])))
            out.append(("ok" if info["vision"] else "warn", "  image input", "yes" if info["vision"] else
                        "no: install from GitHub main for --vision (PyPI 0.1.0 predates it)"))
            out.append(("ok" if info["continue_from"] else "warn", "  full fine-tune (continue_from)",
                        "yes" if info["continue_from"] else
                        "no: fine-tunes fall back to init_from (frozen torso, new head); see docs/finetune.md"))
        else:
            out.append(("warn", "strands-decider", "installed but not importable: " + r.stderr.strip()[-200:]))
    else:
        out.append(("info", "strands-decider", "not installed: pip install 'decider-lab[strands]' to serve/train here"))
    for mod, extra, what in (("datasets", "heldout", "the heldout suite"), ("chess", "chess", "the chess family")):
        out.append(("ok" if importlib.util.find_spec(mod) else "info", mod,
                    "installed" if importlib.util.find_spec(mod) else f"not installed ({what}: [{extra}] extra)"))
    out.append(("ok" if os.path.isdir(os.path.join(CACHE, "jevbench", ".git")) else "info", "jevbench harness",
                "cached" if os.path.isdir(os.path.join(CACHE, "jevbench", ".git")) else "fetched on first use"))
    if shutil.which("vastai"):
        try:
            from .compute.vast import credit

            out.append(("ok", "vast.ai", f"credit ${credit():.2f}"))
        except Exception as e:  # no key set, offline, ...
            out.append(("warn", "vast.ai", f"CLI present, but: {e}"[:200]))
    else:
        out.append(("info", "vast.ai", "CLI not installed (only for `--on vast`): [vast] extra"))
    if importlib.util.find_spec("boto3"):
        try:
            import boto3

            ident = boto3.Session().client("sts").get_caller_identity()
            out.append(("ok", "aws", f"credentials for account {ident['Account']} (default profile)"))
        except Exception as e:  # no credentials, expired SSO, ...
            out.append(("info", "aws", f"boto3 present; default credentials unusable ({type(e).__name__})"))
    else:
        out.append(("info", "aws", "boto3 not installed (only for `--on aws`): [aws] extra"))
    out.append(("ok" if os.environ.get("HF_TOKEN") else "info", "HF_TOKEN",
                "set" if os.environ.get("HF_TOKEN") else "not set (public models do not need it)"))
    return out


def main() -> int:
    marks = {"ok": "OK  ", "warn": "WARN", "info": "--  "}
    rows = checks()
    for status, item, detail in rows:
        print(f"{marks[status]}  {item:<32} {detail}")
    return 0
