"""decider-lab: evaluate and fine-tune decision models, locally or on a rented GPU.

    decider-lab init my-lab [--template eval|finetune]   a ready-to-run lab directory
    decider-lab doctor                                  what this machine can do
    decider-lab run lab.yaml                            every model on every suite -> REPORT.md
    decider-lab gpu run lab.yaml --gpu RTX_4090         the same, on a rented vast.ai GPU
    decider-lab eval --model URL --suite smoke          one model, one suite, no lab file
    decider-lab compare RUN_A RUN_B                     paired difference with a 95% CI
    decider-lab calibrate RUN                           per-kind temperature (dev) -> RUN+cal (test)
    decider-lab data ...                                from-csv, generate, check, split, leakcheck, stats
    decider-lab suites                                  the built-in suites
    decider-lab jevbench --url URL --out DIR            JevBench public tasks, v1.5-rule proxy
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


def cmd_run(a: argparse.Namespace) -> int:
    from .lab import run_lab

    run_lab(a.lab, only=a.only, limit=a.limit, out=a.out)
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


def cmd_jevbench(a: argparse.Namespace) -> int:
    from . import jevbench

    _print(jevbench.run(a.url, a.out, label=a.label))
    return 0


def cmd_doctor(a: argparse.Namespace) -> int:
    from .doctor import main

    return main()


def cmd_gpu(a: argparse.Namespace) -> int:
    from .gpu import vast

    if a.gpu_cmd == "offers":
        rows = vast.offers(a.gpu, num_gpus=a.num_gpus, max_price=a.max_price, disk_gb=a.disk)
        for r in rows[:15]:
            print(f"{r['id']:>10}  {r['num_gpus']}x {r['gpu_name']:<14} ${r['dph_total']:.3f}/h  "
                  f"{r.get('gpu_ram', 0) / 1024:.0f} GB  cuda {r.get('cuda_max_good')}  {r.get('geolocation', '')}")
        print(f"{len(rows)} offers; credit ${vast.credit():.2f}")
    elif a.gpu_cmd == "ls":
        for r in vast.instances():
            print(f"{r['id']:>10}  {r.get('label')}  {r.get('actual_status')}  ${r.get('dph_total', 0):.3f}/h")
    elif a.gpu_cmd == "down":
        ids = [r["id"] for r in vast.instances()] if a.all else a.ids
        for i in ids:
            print(f"{i}: {'destroyed' if vast.destroy(int(i)) else 'STILL LISTED, check vastai show instances'}")
    elif a.gpu_cmd == "run":
        vast.run_remote(a.lab, gpu=a.gpu, num_gpus=a.num_gpus, max_price=a.max_price, max_hours=a.max_hours,
                        disk_gb=a.disk, strands_spec=a.strands_decider, ssh_key=a.ssh_key, env=a.env, keep=a.keep,
                        run_args=a.run_args, min_gpu_ram_gb=a.min_gpu_ram, fast_kernels=a.fast_kernels, boot_timeout=a.boot_timeout, offer_id=a.offer,
                        log=lambda m: print(m, flush=True))
    return 0


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

    s = sub.add_parser("run", help="run a lab file")
    s.add_argument("lab")
    s.add_argument("--only", nargs="*", help="only these models")
    s.add_argument("--limit", type=int, help="first N rows per kind of every suite (a quick look)")
    s.add_argument("--out", help="run root (default runs/<lab name>)")
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

    s = sub.add_parser("jevbench", help="JevBench public tasks against a System One URL (v1.5-rule proxy)")
    s.add_argument("--url", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--label", default="model")
    s.set_defaults(fn=cmd_jevbench)

    s = sub.add_parser("gpu", help="rented GPUs (vast.ai)")
    gsub = s.add_subparsers(dest="gpu_cmd", required=True)
    for name in ("offers", "run"):
        g2 = gsub.add_parser(name)
        if name == "run":
            g2.add_argument("lab")
            g2.add_argument("--max-hours", type=float, default=2.0)
            g2.add_argument("--strands-decider", help="pip spec for strands-decider on the host")
            g2.add_argument("--ssh-key", default="~/.ssh/id_ed25519")
            g2.add_argument("--env", nargs="*", default=[], help="environment variables to pass (e.g. HF_TOKEN)")
            g2.add_argument("--keep", action="store_true", help="do not destroy the instance (debugging)")
            g2.add_argument("--run-args", default="", help="extra arguments for the remote `decider-lab run`")
            g2.add_argument("--min-gpu-ram", type=int, default=0, help="GB")
            g2.add_argument("--fast-kernels", action="store_true", help="also build causal-conv1d on the host")
            g2.add_argument("--boot-timeout", type=float, default=1800,
                            help="seconds to wait for the instance to boot and accept ssh (image pull)")
            g2.add_argument("--offer", type=int, help="rent this offer id (from `gpu offers`) instead of the cheapest")
        g2.add_argument("--gpu", default="RTX_4090")
        g2.add_argument("--num-gpus", type=int, default=1)
        g2.add_argument("--max-price", type=float, default=0.8, help="$/hour")
        g2.add_argument("--disk", type=int, default=80, help="GB")
    gsub.add_parser("ls")
    g2 = gsub.add_parser("down")
    g2.add_argument("ids", nargs="*")
    g2.add_argument("--all", action="store_true", help="every instance labelled decider-lab:*")
    s.set_defaults(fn=cmd_gpu)
    return p


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        return int(a.fn(a) or 0)
    except (ValueError, RuntimeError, FileNotFoundError, FileExistsError, ImportError, TimeoutError) as e:
        print(f"decider-lab: {e}", file=sys.stderr)
        return 2
