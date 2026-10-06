"""The fake-cloud stand-in for a remote run (API.md 2.6), used only when DECIDER_LAB_FAKE_CLOUD=1.

    python -m decider_lab.ui.simulate run lab.yaml --on vast --gpu A100_SXM4 --max-price 0.8 ...

Takes exactly the arguments of `decider-lab run`. It prints the stage lines a real remote run
prints (check, renting/launching, ssh ok, bootstrap, results copied, destroyed/terminated) with
short sleeps, and in between runs the real `decider-lab run <lab> --on local` here, its output
prefixed `  | ` like a remote log, plus synthetic `[telemetry]` lines. Nothing is rented, launched
or billed; the results are real local results. SIGINT/SIGTERM "release" the machine and exit.

Env: DECIDER_LAB_SIM_RATE (the $/h to print), DECIDER_LAB_SIM_OFFER (vast offer id),
DECIDER_LAB_SIM_GPU (GPU label), DECIDER_LAB_SIM_SPEED (multiplies the sleeps; default 1).
"""

from __future__ import annotations

import json
import math
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
from typing import Any

from .. import telemetry
from ..cli import build_parser


def _speed() -> float:
    try:
        return max(0.0, float(os.environ.get("DECIDER_LAB_SIM_SPEED", "1")))
    except ValueError:
        return 1.0


class Sim:
    def __init__(self, a: Any) -> None:
        self.a = a
        self.on = a.on
        self.child: subprocess.Popen | None = None
        self.machine: str | None = None
        self.released = False
        self.stopping = False
        self.lock = threading.Lock()
        self.rate = float(os.environ.get("DECIDER_LAB_SIM_RATE") or 0.0)
        self.gpu = os.environ.get("DECIDER_LAB_SIM_GPU") or (a.gpu or "A100_SXM4")

    def say(self, text: str) -> None:
        with self.lock:
            print(text, flush=True)

    def sleep(self, s: float) -> None:
        end = time.time() + s * _speed()
        while time.time() < end and not self.stopping:
            time.sleep(0.05)
        if self.stopping:
            raise KeyboardInterrupt

    def release(self) -> None:
        if self.released:
            return
        self.released = True
        p = self.on
        if self.a.keep and p in ("vast", "aws"):
            self.say(f"[{p}] --keep: the machine is left running; release it yourself")
        elif p == "vast" and self.machine:
            self.say(f"[vast] instance {self.machine} destroyed")
        elif p == "aws" and self.machine:
            self.say(f"[aws] instance {self.machine} terminated")
        elif p == "ssh":
            self.say(f"[ssh] done; {self.a.host or 'root@host'} is your machine and is left running "
                     "(work dir /root/decider-lab-work)")

    def stop(self, signum: int, _frame: Any) -> None:
        self.stopping = True
        c = self.child
        if c is not None and c.poll() is None:
            try:
                c.send_signal(signal.SIGINT)
            except OSError:
                pass

    def telemetry_loop(self, root: str, done: threading.Event) -> None:
        t0 = time.time()
        interval = float(os.environ.get("DECIDER_LAB_SIM_TELEMETRY_INTERVAL", "1"))
        while not done.wait(interval):
            x = time.time() - t0
            mem_total = 80.0 if "A100" in self.gpu or "H100" in self.gpu else 48.0
            sample = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "interval_s": interval,
                      "simulated": True,
                      "gpus": [{"index": 0, "name": f"NVIDIA {self.gpu.replace('_', ' ')} (simulated)",
                                "util_pct": round(72 + 20 * math.sin(x / 3), 1),
                                "mem_used_gb": round(mem_total * (0.35 + 0.1 * math.sin(x / 5)), 2),
                                "mem_total_gb": mem_total, "temp_c": round(58 + 6 * math.sin(x / 7), 1),
                                "power_w": round(220 + 60 * math.sin(x / 4), 1), "power_limit_w": 400.0}],
                      "load": [1.2, 1.0, 0.8], "rows": telemetry.rows_done(root)}
            self.say("  | " + telemetry.PREFIX + json.dumps(sample, separators=(",", ":")))

    def run(self) -> int:
        a = self.a
        p = self.on
        lab_dir = os.path.dirname(os.path.abspath(a.lab))
        self.say(f"[{p}] check: {'credit and offers' if p == 'vast' else 'credentials and quota'} "
                 "(fake cloud fixtures)" if p != "ssh" else f"[ssh] check: {a.host} answers (fake cloud fixtures)")
        self.sleep(0.2)
        if p == "vast":
            offer = os.environ.get("DECIDER_LAB_SIM_OFFER") or "1234567"
            self.say(f"[vast] renting {a.num_gpus or 1}x {self.gpu} at ${self.rate:.3f}/h (offer {offer})")
            self.machine = str(9800000 + secrets.randbelow(99999))
            self.say(f"[vast] instance {self.machine}; waiting for it to boot (image pull)")
            target, port = "root@ssh4.vast.ai", 22000 + secrets.randbelow(999)
        elif p == "aws":
            self.machine = "i-0" + secrets.token_hex(8)
            itype = a.instance_type or "g6e.xlarge"
            region = a.region or "us-east-1"
            minutes = int((a.max_hours or 2.0) * 60) + 15
            self.say(f"[aws] {itype} {self.machine} from ami-0fake0000000000000 in {region} (self-terminates after "
                     f"{minutes} min); ssh allowed from 127.0.0.1 only")
            target, port = "ubuntu@3.91.0.10", 22
        else:
            target, _, port_s = (a.host or "root@host").partition(":")
            port = int(port_s or 22)
        self.sleep(1.0)
        work = "/root/decider-lab-work" if target.startswith("root@") else f"/home/{target.split('@')[0]}/decider-lab-work"
        self.say(f"[{p}] ssh ok: {target}:{port}, work dir {work}")
        self.sleep(0.3)
        self.say(f"[{p}] bootstrap: torch for this machine, strands-decider[vision,cuda], decider-lab")
        self.sleep(1.0)
        self.say(f"[{p}] bootstrap ok")
        run_args = ["run", os.path.basename(a.lab), "--on", "local"]
        if a.only:
            run_args += ["--only", *a.only]
        if a.limit:
            run_args += ["--limit", str(a.limit)]
        if a.out:
            run_args += ["--out", a.out]
        env = dict(os.environ)
        env["DECIDER_LAB_TELEMETRY"] = "0"  # the simulator prints its own (simulated) samples
        env.pop("DECIDER_LAB_FAKE_CLOUD", None)
        self.child = subprocess.Popen([sys.executable, "-m", "decider_lab", *run_args], cwd=lab_dir, env=env,
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                      text=True, bufsize=1)
        root = a.out or os.path.join(lab_dir, "runs", _lab_name(a.lab))
        done = threading.Event()
        t = threading.Thread(target=self.telemetry_loop, args=(root, done), daemon=True)
        t.start()
        assert self.child.stdout is not None
        for line in self.child.stdout:
            self.say("  | " + line.rstrip("\n"))
        code = self.child.wait()
        done.set()
        t.join(2)
        if self.stopping:
            raise KeyboardInterrupt
        self.sleep(0.2)
        if code != 0:
            self.say(f"decider-lab: the remote run exited with {code}; results and logs copied to "
                     f"{os.path.join(lab_dir, 'runs')}")
            return 2
        self.say(f"[{p}] results copied to {os.path.join(lab_dir, 'runs')}")
        return 0


def _lab_name(path: str) -> str:
    import yaml

    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return str(data.get("name") or os.path.splitext(os.path.basename(path))[0])
    except (OSError, yaml.YAMLError):
        return os.path.splitext(os.path.basename(path))[0]


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    if getattr(a, "cmd", None) != "run" or a.on not in ("ssh", "aws", "vast"):
        print("simulate: only `run ... --on ssh|aws|vast`", file=sys.stderr)
        return 2
    sim = Sim(a)
    for s in (signal.SIGINT, signal.SIGTERM):
        signal.signal(s, sim.stop)
    try:
        code = sim.run()
    except KeyboardInterrupt:
        sim.say(f"[{a.on}] interrupted; releasing the machine")
        code = 130
    finally:
        sim.release()
    return code


if __name__ == "__main__":
    sys.exit(main())
