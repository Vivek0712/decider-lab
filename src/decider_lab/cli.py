"""decider-lab: evaluate and fine-tune decision models, locally or on a rented GPU.

    decider-lab init my-lab [--template eval|finetune]   a ready-to-run lab directory
    decider-lab doctor                                  what this machine can do
    decider-lab run lab.yaml                            every model on every suite -> REPORT.md
    decider-lab run lab.yaml --on aws|vast|ssh          the same on another machine (or compute: in lab.yaml)
    decider-lab compute ls|down|offers --on vast|aws    machines decider-lab started
    decider-lab eval --model URL --suite smoke          one model, one suite, no lab file
    decider-lab compare RUN_A RUN_B                     paired difference with a 95% CI
    decider-lab calibrate RUN                           per-kind temperature (dev) -> RUN+cal (test)
    decider-lab pull hf://org/repo@commit | s3://... | https://...   fetch, verify and cache a model
    decider-lab data ...                                from-csv, generate, check, split, leakcheck, stats
    decider-lab suites                                  the built-in suites
    decider-lab jevbench --url URL --out DIR            JevBench public tasks, v1.5-rule proxy
    decider-lab ui [--workspace DIR]                    Studio: the local web portal ([ui] extra)
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from importlib import resources

from . import __version__


def _print(obj: object) -> None:
    print(json.dumps(obj, indent=2))


def cmd_init(a: argparse.Namespace) -> int:
    if os.path.exists(a.dir) and os.listdir(a.dir):
        print(f"{a.dir} exists and is not empty", file=sys.stderr)
        return 1
    src = resources.files("decider_lab") / "templates" / a.template
    os.makedirs(a.dir, exist_ok=True)
    for item in src.iterdir():
        target = os.path.join(a.dir, item.name)
        if item.is_dir():
            shutil.copytree(str(item), target)
        else:
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(item.read_text(encoding="utf-8"))
    print(f"created {a.dir} from the {a.template} template. Next:\n  cd {a.dir}\n  cat README.md")
    return 0


# CLI flag -> (backend, its option name), so `--on vast --gpu A100_SXM4` needs no lab edit
FLAG_OPTIONS = {"gpu": ("vast", "gpu"), "max_price": ("vast", "max_price"), "offer": ("vast", "offer"),
                "num_gpus": ("vast", "num_gpus"), "instance_type": ("aws", "instance_type"),
                "region": ("aws", "region"), "profile": ("aws", "profile"), "host": ("ssh", "host"),
                "disk": (None, "disk_gb"), "ssh_key": (None, "ssh_key")}


def compute_config(a: argparse.Namespace) -> tuple[str, dict, dict]:
    """(backend, the backend's options, the compute section) from lab.yaml, then CLI flags."""
    import yaml

    with open(a.lab, encoding="utf-8") as fh:
        compute = dict((yaml.safe_load(fh) or {}).get("compute") or {})
    # `backend:`; `on:` also works, though YAML 1.1 reads a bare `on` key as the boolean True
    on = a.on or compute.get("backend") or compute.get("on") or compute.get(True) or "local"
    opts = dict(compute.get(on) or {})
    for flag, (backend, key) in FLAG_OPTIONS.items():
        val = getattr(a, flag, None)
        if val is None or (backend and backend != on):
            continue
        if key == "ssh_key" and on == "ssh":
            key = "key"
        if key == "ssh_key" and on == "aws":
            continue  # aws makes its own key pair per run
        opts[key] = val
    return on, opts, compute


def cmd_run(a: argparse.Namespace) -> int:
    on, opts, compute = compute_config(a)
    if on == "local":
        from .lab import run_lab

        run_lab(a.lab, only=a.only, limit=a.limit, out=a.out)
        return 0
    from .compute import make_provider, run_on

    label = os.path.splitext(os.path.basename(a.lab))[0]
    provider = make_provider(on, opts, label=label)
    extra = " ".join([*(["--only", *a.only] if a.only else []), *(["--limit", str(a.limit)] if a.limit else [])])
    run_on(provider, a.lab, max_hours=a.max_hours or float(compute.get("max_hours", 2.0)),
           strands_spec=a.strands_decider or compute.get("strands_decider"), env=a.env or compute.get("env"),
           keep=a.keep, run_args=extra, fast_kernels=a.fast_kernels or bool(compute.get("fast_kernels")),
           log=lambda m: print(m, flush=True))
    return 0


def cmd_eval(a: argparse.Namespace) -> int:
    from . import runner
    from .adapters import make_adapter
    from .serve import served
    from .suites import load_suite

    name, rows, params = load_suite(a.suite)
    rows = runner.select(rows, split=a.split, limit=a.limit)
    has_splits = any(r.get("split") for r in rows)
    out = a.out or os.path.join("runs", a.name, name)

    def go(adapter) -> None:  # noqa: ANN001
        s = runner.run(adapter, rows, out, suite=name, suite_params=params, workers=a.workers, model=a.name,
                       score_split="test" if has_splits and not a.split else a.split)
        _print({k: s[k] for k in ("n", "errors", "intelligence", "intelligence_ci95", "accuracy", "nll", "ece")
                if k in s})
        print(f"run written to {out}")

    if a.serve:
        with served(a.serve, vision=a.vision, log_path=os.path.join(out, "server.log")) as (url, _):
            go(make_adapter(url))
    else:
        spec: object = a.model
        if a.model.startswith("python:"):
            spec = {"python": a.model[len("python:"):], "path": os.getcwd()}
        go(make_adapter(spec))
    return 0


def cmd_compare(a: argparse.Namespace) -> int:
    from .metrics import compare
    from .runner import load_run

    pa, _ = load_run(a.a)
    pb, _ = load_run(a.b)
    if a.split != "all":
        pa = [p for p in pa if p.get("split") in (None, a.split)]
        pb = [p for p in pb if p.get("split") in (None, a.split)]
    _print(compare(pa, pb))
    return 0


def cmd_calibrate(a: argparse.Namespace) -> int:
    from .calibrate import calibrate_run

    _print(calibrate_run(a.run, a.out))
    return 0


def cmd_report(a: argparse.Namespace) -> int:
    from .report import write

    print(write(a.root, baseline=a.baseline, title=os.path.basename(os.path.abspath(a.root))))
    return 0


def cmd_suites(a: argparse.Namespace) -> int:
    from . import suites

    print(suites.__doc__)
    if a.build:
        name, rows, params = suites.load_suite(a.build)
        from .data import stats

        _print({"suite": name, "params": params, **stats(rows)})
        if a.out:
            from .rows import write_jsonl

            write_jsonl(a.out, rows)
            print(f"written to {a.out}")
    return 0


def cmd_data(a: argparse.Namespace) -> int:
    from . import data
    from .rows import write_jsonl

    if a.data_cmd == "from-csv":
        rows = data.from_csv(a.csv, task=a.task, delimiter=a.delimiter)
        n = write_jsonl(a.out, rows)
        print(f"{n} rows -> {a.out}")
    elif a.data_cmd == "generate":
        exclude: set[str] = set()
        for s in a.exclude_suite or []:
            from .suites import load_suite

            exclude |= {r["id"] for r in load_suite(s)[1]}
        rows = data.generate(a.per_kind, a.families.split(","), a.seed, exclude)
        n = write_jsonl(a.out, rows)
        print(f"{n} rows -> {a.out}")
    elif a.data_cmd == "check":
        rows = data.load(a.file)
        _print(data.stats(rows))
        print("valid")
    elif a.data_cmd == "stats":
        _print(data.stats(data.load(a.file)))
    elif a.data_cmd == "split":
        rows = data.assign_splits(data.load(a.file), a.dev_fraction, a.seed)
        write_jsonl(a.out, rows)
        _print(data.stats(rows)["by_split"])
    elif a.data_cmd == "leakcheck":
        from .suites import load_suite

        train = data.load(a.train)
        evals = [r for s in a.against for r in load_suite(s)[1]]
        res = data.leakcheck(train, evals)
        _print({k: v for k, v in res.items() if k != "overlapping_indices"})
        if a.drop_to:
            bad = set(res["overlapping_indices"])
            n = write_jsonl(a.drop_to, (r for i, r in enumerate(train) if i not in bad))
            print(f"{n} clean rows -> {a.drop_to}")
        return 1 if res["overlapping"] and not a.drop_to else 0
    return 0


def cmd_pull(a: argparse.Namespace) -> int:
    from . import sources

    opts = {k: v for k, v in (("sha256", a.sha256), ("revision", a.revision), ("profile", a.profile),
                              ("region", a.region), ("require_pinned", a.require_pinned or None)) if v}
    path, info = sources.resolve(a.source, opts, log=lambda m: print(m, flush=True))
    _print({"path": path, **info})
    return 0


def cmd_models(a: argparse.Namespace) -> int:
    from . import sources

    rows = sources.cached()
    for r in rows:
        ref = r.get("resolved_commit") or r.get("sha256") or r.get("etags_sha256") or ""
        print(f"{r['size_gb']:>7.2f} GB  {r['kind']:<4} {r['source']}  {ref[:16]}")
    print(f"{len(rows)} cached model(s) in {sources._models_dir()} (Hugging Face snapshots live in the HF cache)")
    return 0


def cmd_jevbench(a: argparse.Namespace) -> int:
    from . import jevbench

    _print(jevbench.run(a.url, a.out, label=a.label))
    return 0


def cmd_doctor(a: argparse.Namespace) -> int:
    from .doctor import main

    return main()


def cmd_compute(a: argparse.Namespace) -> int:
    if a.on == "vast":
        from .compute import vast

        if a.compute_cmd == "offers":
            rows = vast.offers(a.gpu, num_gpus=a.num_gpus, max_price=a.max_price, disk_gb=a.disk)
            for r in rows[:15]:
                print(f"{r['id']:>10}  {r['num_gpus']}x {r['gpu_name']:<14} ${r['dph_total']:.3f}/h  "
                      f"{r.get('gpu_ram', 0) / 1024:.0f} GB  cuda {r.get('cuda_max_good')}  {r.get('geolocation', '')}")
            print(f"{len(rows)} offers; credit ${vast.credit():.2f}")
        elif a.compute_cmd == "ls":
            for r in vast.instances():
                print(f"{r['id']:>10}  {r.get('label')}  {r.get('actual_status')}  ${r.get('dph_total', 0):.3f}/h")
        else:
            ids = [r["id"] for r in vast.instances()] if a.all else a.ids
            for i in ids:
                print(f"{i}: {'destroyed' if vast.destroy(int(i)) else 'STILL LISTED, check vastai show instances'}")
        return 0
    if a.on == "aws":
        from .compute import aws

        if a.compute_cmd == "offers":
            print("aws: pick any instance type, e.g. g6e.xlarge (L40S 48 GB), g6.xlarge (L4 24 GB), "
                  "g5.xlarge (A10G 24 GB), p4d/p5 for multi-GPU; c7i/m7i for CPU-only evaluation.")
            return 0
        rows = aws.tagged_instances(a.region, a.profile)
        if a.compute_cmd == "ls":
            for r in rows:
                tags = {t["Key"]: t["Value"] for t in r.get("Tags", [])}
                print(f"{r['InstanceId']}  {r['InstanceType']}  {r['State']['Name']}  {tags.get(aws.TAG)}")
        else:
            ids = [r["InstanceId"] for r in rows] if a.all else a.ids
            if ids:
                aws._boto(a.profile, a.region).client("ec2").terminate_instances(InstanceIds=ids)
            print(f"terminating: {ids or 'nothing'}")
        return 0
    print(f"`compute {a.compute_cmd}` is for the vast and aws backends; {a.on} has nothing to list", file=sys.stderr)
    return 1


def cmd_ui(a: argparse.Namespace) -> int:
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError:
        print("decider-lab ui needs the ui extra: pip install 'decider-lab[ui]'", file=sys.stderr)
        return 2
    from .ui.server import cmd_ui as run_ui

    return run_ui(a)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="decider-lab", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create a ready-to-run lab directory")
    s.add_argument("dir")
    s.add_argument("--template", choices=["eval", "finetune"], default="eval")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("doctor", help="check this machine")
    s.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("run", help="run a lab file, here or on another machine (--on / compute:)")
    s.add_argument("lab")
    s.add_argument("--only", nargs="*", help="only these models")
    s.add_argument("--limit", type=int, help="first N rows per kind of every suite (a quick look)")
    s.add_argument("--out", help="run root (default runs/<lab name>)")
    s.add_argument("--on", choices=["local", "ssh", "aws", "vast"], help="where to run (default: lab's compute.on, "
                   "else local)")
    s.add_argument("--max-hours", type=float, help="hard deadline for a remote run (default 2)")
    s.add_argument("--strands-decider", help="pip spec for strands-decider on the remote machine")
    s.add_argument("--env", nargs="*", help="environment variables to pass to the remote run (e.g. HF_TOKEN)")
    s.add_argument("--keep", action="store_true", help="leave the remote machine running (debugging)")
    s.add_argument("--fast-kernels", action="store_true", help="also build causal-conv1d on the remote GPU")
    s.add_argument("--gpu", help="vast: GPU name, e.g. A100_SXM4")
    s.add_argument("--max-price", type=float, help="vast: $/hour cap")
    s.add_argument("--offer", type=int, help="vast: this offer id")
    s.add_argument("--num-gpus", type=int, help="vast: GPUs per machine")
    s.add_argument("--instance-type", help="aws: e.g. g6e.xlarge")
    s.add_argument("--region", help="aws: e.g. us-east-1")
    s.add_argument("--profile", help="aws: credentials profile")
    s.add_argument("--host", help="ssh: user@address[:port]")
    s.add_argument("--disk", type=int, help="vast/aws: disk GB")
    s.add_argument("--ssh-key", help="vast/ssh: private key")
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("eval", help="one model on one suite")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--model", help="URL of a System One server, a baseline name, or python:module:attr")
    g.add_argument("--serve", help="a checkpoint or hub id to start with strands-decider serve")
    s.add_argument("--vision", action="store_true")
    s.add_argument("--suite", default="smoke")
    s.add_argument("--name", default="model")
    s.add_argument("--split", help="dev, test or all (default: score test when the suite has splits)")
    s.add_argument("--limit", type=int)
    s.add_argument("--workers", type=int, default=4)
    s.add_argument("--out")
    s.set_defaults(fn=cmd_eval)

    s = sub.add_parser("compare", help="paired difference between two runs (a minus b)")
    s.add_argument("a")
    s.add_argument("b")
    s.add_argument("--split", default="test", help="test (default), dev or all")
    s.set_defaults(fn=cmd_compare)

    s = sub.add_parser("calibrate", help="per-kind temperature fitted on dev, scored on test")
    s.add_argument("run")
    s.add_argument("--out")
    s.set_defaults(fn=cmd_calibrate)

    s = sub.add_parser("report", help="rebuild REPORT.md for a run root")
    s.add_argument("root")
    s.add_argument("--baseline")
    s.set_defaults(fn=cmd_report)

    s = sub.add_parser("suites", help="list built-in suites; --build one to inspect or export it")
    s.add_argument("--build")
    s.add_argument("--out")
    s.set_defaults(fn=cmd_suites)

    s = sub.add_parser("data", help="data tools")
    dsub = s.add_subparsers(dest="data_cmd", required=True)
    d = dsub.add_parser("from-csv", help="decision rows from a CSV (state, question, answer[, options, kind, task])")
    d.add_argument("csv")
    d.add_argument("--out", required=True)
    d.add_argument("--task", default="custom")
    d.add_argument("--delimiter", default=",")
    d = dsub.add_parser("generate", help="generated training rows with program-computed labels")
    d.add_argument("--out", required=True)
    d.add_argument("--families", default="arithmetic,calendar,seating")
    d.add_argument("--per-kind", type=int, default=500)
    d.add_argument("--seed", type=int, default=1)
    d.add_argument("--exclude-suite", nargs="*", help="drop any row that is in these suites")
    for name, hlp in (("check", "validate a JSONL of rows"), ("stats", "counts and label balance")):
        d = dsub.add_parser(name, help=hlp)
        d.add_argument("file")
    d = dsub.add_parser("split", help="assign dev/test splits within each family and kind")
    d.add_argument("file")
    d.add_argument("--out", required=True)
    d.add_argument("--dev-fraction", type=float, default=0.4)
    d.add_argument("--seed", type=int, default=0)
    d = dsub.add_parser("leakcheck", help="training rows whose input also appears in evaluation suites")
    d.add_argument("train")
    d.add_argument("--against", nargs="+", required=True, help="suites or files")
    d.add_argument("--drop-to", help="write the training rows without the overlapping ones here")
    s.set_defaults(fn=cmd_data)

    s = sub.add_parser("pull", help="fetch a model now: hf://org/repo@commit, s3://..., https://..., or a directory")
    s.add_argument("source")
    s.add_argument("--sha256", help="required checksum of an archive or file")
    s.add_argument("--revision", help="hf:// commit, branch or tag (or use @ in the source)")
    s.add_argument("--require-pinned", action="store_true", help="refuse an hf:// source without a full commit")
    s.add_argument("--profile", help="s3:// AWS profile")
    s.add_argument("--region", help="s3:// AWS region")
    s.set_defaults(fn=cmd_pull)

    s = sub.add_parser("models", help="models pulled into the decider-lab cache")
    s.set_defaults(fn=cmd_models)

    s = sub.add_parser("jevbench", help="JevBench public tasks against a System One URL (v1.5-rule proxy)")
    s.add_argument("--url", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--label", default="model")
    s.set_defaults(fn=cmd_jevbench)

    s = sub.add_parser("compute", help="list, inspect or clean up remote machines (vast, aws)")
    csub = s.add_subparsers(dest="compute_cmd", required=True)
    for name in ("offers", "ls", "down"):
        c = csub.add_parser(name)
        c.add_argument("--on", choices=["vast", "aws"], default="vast")
        c.add_argument("--region", default="us-east-1")
        c.add_argument("--profile")
        if name == "offers":
            c.add_argument("--gpu", default="RTX_4090")
            c.add_argument("--num-gpus", type=int, default=1)
            c.add_argument("--max-price", type=float, default=0.8)
            c.add_argument("--disk", type=int, default=80)
        if name == "down":
            c.add_argument("ids", nargs="*")
            c.add_argument("--all", action="store_true", help="every machine decider-lab started")
    s.set_defaults(fn=cmd_compute)

    s = sub.add_parser("ui", help="Studio: the local web portal (needs the [ui] extra)")
    s.add_argument("--workspace", default=".", help="directory with your labs and runs (default: here)")
    s.add_argument("--host", default="127.0.0.1", help="bind address (loopback; any other needs --token)")
    s.add_argument("--port", type=int, default=7861, help="port (default 7861; 0 picks a free one)")
    s.add_argument("--token", help="access token (default: $DECIDER_LAB_UI_TOKEN, else a new random one)")
    s.add_argument("--no-browser", action="store_true", help="do not open a browser")
    s.set_defaults(fn=cmd_ui)
    return p


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        return int(a.fn(a) or 0)
    except (ValueError, RuntimeError, FileNotFoundError, FileExistsError, ImportError, TimeoutError) as e:
        print(f"decider-lab: {e}", file=sys.stderr)
        return 2
