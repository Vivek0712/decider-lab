"""Fine-tune a Strands Decider on your own decision rows, with `strands-decider train`.

decider-lab writes the training config, checks the data, runs training and calibration, and
hands the checkpoint to the evaluation. The model code is Strands Decider's own; nothing here
changes how it trains.

Two ways to start from a released checkpoint, picked by what the installed strands-decider
supports:

  continue_from   the checkpoint's LoRA adapter and its trained head keep training
                  (strands-decider with `continue_from`, strands-labs/strands-decider#29).
                  This is a real fine-tune, and the default when available.
  init_from       upstream main today: the checkpoint's torso and adapter are FROZEN and a
                  fresh slot head is trained on top. Cheaper and much weaker; decider-lab
                  says so when it falls back to it.

Or from a base model (`base_model`, no `from`): a new Strands Decider trained from scratch.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import Any

import yaml

from .rows import check, read_jsonl, write_jsonl

# The released 2B recipe (configs/train.yaml in strands-decider), minus its data, for one GPU.
RECIPE: dict[str, Any] = {
    "base_model": "Qwen/Qwen3.5-2B-Base",
    "num_slots": 24, "head_type": "pointer", "pointer_dim": 256, "head_init": "random",
    "kl_frozen_weight": 0.3, "head_dropout": 0.05, "max_length": 4096,
    "use_lora": True, "lora_r": 16, "lora_alpha": 32,
    "lora_targets": ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj",
                     "in_proj_qkv", "in_proj_z", "in_proj_a", "in_proj_b", "out_proj"],
    "freeze_torso": False, "epochs": 1, "micro_batch_size": 8, "grad_accum": 4,
    "lr": 5e-5, "head_lr": 5e-4, "weight_decay": 0.01, "warmup_ratio": 0.03, "max_grad_norm": 1.0,
    "gradient_checkpointing": True, "shuffle_options": True, "group_by_length": True,
    "ordinal_smoothing": 0.1, "reverse_score_prob": 0.5, "val_fraction": 0.03,
    "seed": 0, "log_every": 20, "eval_every": 0,
}
# A continued checkpoint already has a trained head: smaller steps than from scratch.
FROM_SCRATCH = {"lr": 1e-4, "head_lr": 1e-3}


def strands_python() -> str:
    """The Python that has strands_decider installed (this one, by default)."""
    return os.environ.get("STRANDS_DECIDER_PYTHON", sys.executable)


def train_fields(python: str | None = None) -> set[str]:
    """The TrainConfig fields of the installed strands-decider."""
    code = "import dataclasses,json;from strands_decider.train import TrainConfig;" \
           "print(json.dumps([f.name for f in dataclasses.fields(TrainConfig)]))"
    out = subprocess.run([python or strands_python(), "-c", code], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError("strands-decider is not importable here (pip install 'decider-lab[strands]'):\n"
                           + out.stderr[-800:])
    return set(json.loads(out.stdout.strip().splitlines()[-1]))


def make_config(name: str, spec: dict[str, Any], workdir: str, *, fields: set[str] | None = None,
                resolve_sources: bool = True) -> tuple[str, str, list[str]]:
    """Write `<workdir>/<name>/train.yaml`; returns (config path, checkpoint dir, notes)."""
    notes: list[str] = []
    out_dir = os.path.join(workdir, name)
    os.makedirs(out_dir, exist_ok=True)
    train = spec.get("train")
    if not train:
        raise ValueError(f"finetune.{name}: `train` (a JSONL of decision rows) is required")
    train_files = [train] if isinstance(train, str) else list(train)
    # validate every training row now, not 20 minutes into a GPU job
    clean = []
    for i, f in enumerate(train_files):
        rows = check(read_jsonl(f), where=f"{f}: ")
        path = os.path.join(out_dir, f"train_{i}.jsonl")
        write_jsonl(path, ({k: v for k, v in r.items() if k in ("kind", "state", "instructions", "options", "label",
                                                                   "task", "weight", "instruction_variants")}
                           for r in rows))
        clean.append(os.path.abspath(path))
        notes.append(f"{f}: {len(rows)} rows")
    cfg: dict[str, Any] = dict(RECIPE)
    start = spec.get("from")
    if start and resolve_sources:
        from .sources import resolve

        # hf://...@commit, s3://, https:// or a directory -> a local checkpoint, recorded
        start, source = resolve(start, spec, log=lambda m: print(m, flush=True))
        notes.append(f"from {source.get('source')} ({source.get('resolved_commit') or source.get('sha256') or 'local'})")
    fields = fields if fields is not None else train_fields()
    if start:
        if "continue_from" in fields and spec.get("mode", "continue") == "continue":
            cfg["continue_from"] = start
            notes.append(f"continue_from {start}: adapter and head keep training")
        else:
            cfg["init_from"] = start
            cfg.update(FROM_SCRATCH)
            notes.append(f"init_from {start}: the installed strands-decider has no continue_from, so the "
                         "torso and adapter are FROZEN and a new slot head is trained (much weaker). "
                         "Install a strands-decider with continue_from (strands-labs/strands-decider#29) "
                         "for a full fine-tune.")
            cfg["head_type"] = "slot"
    else:
        cfg.update(FROM_SCRATCH)
        notes.append(f"from scratch on {spec.get('base_model', cfg['base_model'])}")
    for k in ("base_model", "steps", "lr", "head_lr", "micro_batch_size", "grad_accum", "seed", "max_length",
              "epochs", "lora_r", "lora_alpha", "kl_frozen_weight"):
        if k in spec:
            cfg["max_steps" if k == "steps" else k] = spec[k]
    cfg.update(spec.get("config") or {})
    cfg["train_files"] = clean
    ckpt = os.path.abspath(os.path.join(out_dir, "checkpoint"))
    cfg["output_dir"] = ckpt
    unknown = sorted(set(cfg) - fields)
    if unknown:
        raise ValueError(f"finetune.{name}: the installed strands-decider has no training option(s) {unknown}")
    path = os.path.join(out_dir, "train.yaml")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"# written by decider-lab for finetune.{name}\n")
        yaml.safe_dump(cfg, fh, sort_keys=False)
    return path, ckpt, notes


def run_finetune(name: str, spec: dict[str, Any], workdir: str, *, log=print) -> str:
    """Train (and calibrate, when `calibrate_on` is given); returns the checkpoint directory.
    A finished checkpoint is reused, so a re-run of the lab does not train twice."""
    python = strands_python()
    cfg_path, ckpt, notes = make_config(name, spec, workdir, fields=train_fields(python))
    for n in notes:
        log(f"[decider-lab] finetune.{name}: {n}")
    exe = shutil.which("strands-decider") or os.path.join(os.path.dirname(python), "strands-decider")
    done = os.path.join(os.path.dirname(cfg_path), "TRAINED")
    if not os.path.exists(done):
        log(f"[decider-lab] finetune.{name}: training -> {ckpt}")
        with open(os.path.join(os.path.dirname(cfg_path), "train.log"), "w", encoding="utf-8") as fh:
            rc = subprocess.run([exe, "train", "-c", cfg_path], stdout=fh, stderr=subprocess.STDOUT).returncode
        if rc != 0:
            raise RuntimeError(f"finetune.{name}: training failed (exit {rc}); see "
                               f"{os.path.join(os.path.dirname(cfg_path), 'train.log')}")
        open(done, "w").close()
    cal = spec.get("calibrate_on")
    if cal and not os.path.exists(os.path.join(os.path.dirname(cfg_path), "CALIBRATED")):
        rows = check(read_jsonl(cal), where=f"{cal}: ")
        cal_file = os.path.join(os.path.dirname(cfg_path), "calibrate.jsonl")
        write_jsonl(cal_file, ({k: v for k, v in r.items() if k in ("kind", "state", "instructions", "options",
                                                                       "label", "task")} for r in rows))
        log(f"[decider-lab] finetune.{name}: calibrating on {len(rows)} rows of {cal}")
        with open(os.path.join(os.path.dirname(cfg_path), "calibrate.log"), "w", encoding="utf-8") as fh:
            rc = subprocess.run([exe, "calibrate", ckpt, "--data", cal_file, "--split", "all"],
                                stdout=fh, stderr=subprocess.STDOUT).returncode
        if rc != 0:
            raise RuntimeError(f"finetune.{name}: calibration failed (exit {rc})")
        open(os.path.join(os.path.dirname(cfg_path), "CALIBRATED"), "w").close()
    return ckpt
