"""SDK telemetry: the sampler thread that `decider-lab run` keeps during a lab (telemetry.py)."""

from __future__ import annotations

import json
import os
import stat
import threading
import time

import yaml

from decider_lab import telemetry
from decider_lab.cli import main

FAKE_SMI = """#!/bin/sh
# a fake nvidia-smi: two GPUs, the second without power readings
echo "0, NVIDIA L40S, 87, 31948, 46068, 64, 241.50, 350.00"
echo "1, NVIDIA L40S, [N/A], 1024, 46068, 40, [N/A], [N/A]"
"""


def fake_smi(tmp_path, body: str = FAKE_SMI) -> str:
    p = tmp_path / "nvidia-smi"
    p.write_text(body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def write_preds(root, model, suite, n, errors=0):
    d = root / model / suite
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "predictions.jsonl", "w") as fh:
        for i in range(n):
            fh.write(json.dumps({"id": str(i), "error": "boom" if i < errors else None}) + "\n")


def test_gpu_sample_parses_fake_nvidia_smi(tmp_path):
    gpus = telemetry.gpu_sample(fake_smi(tmp_path))
    assert gpus == [
        {"index": 0, "name": "NVIDIA L40S", "util_pct": 87.0, "mem_used_gb": 31.2, "mem_total_gb": 44.99,
         "temp_c": 64.0, "power_w": 241.5, "power_limit_w": 350.0},
        {"index": 1, "name": "NVIDIA L40S", "util_pct": None, "mem_used_gb": 1.0, "mem_total_gb": 44.99,
         "temp_c": 40.0, "power_w": None, "power_limit_w": None},
    ]


def test_gpu_sample_without_or_with_broken_nvidia_smi(tmp_path, monkeypatch):
    monkeypatch.setenv("DECIDER_LAB_NVIDIA_SMI", str(tmp_path / "missing"))
    monkeypatch.setenv("PATH", str(tmp_path))
    assert telemetry.gpu_sample() == []
    assert telemetry.gpu_sample(fake_smi(tmp_path, "#!/bin/sh\necho driver gone >&2\nexit 9\n")) == []
    assert telemetry.gpu_sample(fake_smi(tmp_path, "#!/bin/sh\necho garbage\n")) == []


def test_rows_done_counts_lines_and_errors(tmp_path):
    write_preds(tmp_path, "v19", "smoke", 90, errors=2)
    write_preds(tmp_path, "v19", "synthetic+cal", 5)
    (tmp_path / "_finetune").mkdir()
    (tmp_path / "REPORT.md").write_text("x")
    assert telemetry.rows_done(str(tmp_path)) == {"v19/smoke": {"done": 90, "errors": 2},
                                                  "v19/synthetic+cal": {"done": 5, "errors": 0}}
    assert telemetry.rows_done(str(tmp_path / "nope")) == {}


def test_sampler_appends_prints_and_stops_cleanly(tmp_path):
    root = tmp_path / "runs" / "lab"
    write_preds(root, "m", "smoke", 7, errors=1)
    lines: list[str] = []
    s = telemetry.Sampler(str(root), 0.05, nvidia_smi=fake_smi(tmp_path), emit=lines.append).start()
    deadline = time.time() + 10
    while s.samples < 3 and time.time() < deadline:
        time.sleep(0.02)
    s.stop()
    n = s.samples
    assert n >= 3
    time.sleep(0.2)
    assert s.samples == n, "no samples after stop()"
    assert not any(t.name == "decider-lab-telemetry" and t.is_alive() for t in threading.enumerate())
    rows = [json.loads(x) for x in (root / "telemetry.jsonl").read_text().splitlines()]
    assert len(rows) == n
    first = rows[0]
    assert set(first) == {"ts", "interval_s", "gpus", "load", "rows"}
    assert first["ts"].endswith("Z")
    assert first["gpus"][0]["util_pct"] == 87.0 and first["gpus"][0]["power_w"] == 241.5
    assert first["rows"] == {"m/smoke": {"done": 7, "errors": 1}}
    assert lines[0].startswith("[telemetry] {") and json.loads(lines[0][len("[telemetry] "):]) == first


def test_sampler_never_raises(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(telemetry, "sample", boom)
    s = telemetry.Sampler(str(tmp_path), 0.05, emit=boom)
    assert s.tick() is None
    s.start()
    time.sleep(0.2)
    s.stop()
    assert s.samples == 0


def test_sampling_context_disabled_and_exception_safe(tmp_path, monkeypatch):
    monkeypatch.setenv("DECIDER_LAB_TELEMETRY", "0")
    with telemetry.sampling(str(tmp_path)) as s:
        assert s is None
    monkeypatch.setenv("DECIDER_LAB_TELEMETRY", "1")
    monkeypatch.setenv("DECIDER_LAB_TELEMETRY_INTERVAL", "0.05")
    try:
        with telemetry.sampling(str(tmp_path), emit=lambda _l: None) as s:
            assert s is not None and s.interval_s == 0.05
            raise KeyError("the run failed")
    except KeyError:
        pass
    assert s._stop.is_set()


def test_run_writes_telemetry_and_prints_lines(fake_server, tmp_path, monkeypatch, capsys):
    url, _ = fake_server
    monkeypatch.setenv("DECIDER_LAB_TELEMETRY_INTERVAL", "0.05")
    monkeypatch.setenv("DECIDER_LAB_NVIDIA_SMI", fake_smi(tmp_path))
    lab = {"name": "t", "workers": 2, "models": {"fake": {"url": url}, "majority": {"baseline": "majority"}},
           "suites": ["smoke"], "baseline": "majority"}
    p = tmp_path / "lab.yaml"
    p.write_text(yaml.safe_dump(lab))
    assert main(["run", str(p)]) == 0
    out = capsys.readouterr().out
    tele = [line for line in out.splitlines() if line.startswith("[telemetry] ")]
    assert tele, "run printed [telemetry] lines"
    path = tmp_path / "runs" / "t" / "telemetry.jsonl"
    samples = [json.loads(x) for x in path.read_text().splitlines()]
    assert samples and samples[0]["gpus"][0]["name"] == "NVIDIA L40S"
    assert (tmp_path / "runs" / "t" / "REPORT.md").exists()
    assert not any(t.name == "decider-lab-telemetry" for t in threading.enumerate())


def test_run_is_fine_when_the_sampler_cannot_start(fake_server, tmp_path, monkeypatch):
    url, _ = fake_server

    def no_threads(self):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(telemetry.Sampler, "start", no_threads)
    lab = {"name": "t2", "models": {"fake": {"url": url}}, "suites": ["smoke"]}
    p = tmp_path / "lab.yaml"
    p.write_text(yaml.safe_dump(lab))
    assert main(["run", str(p)]) == 0
    assert os.path.exists(tmp_path / "runs" / "t2" / "fake" / "smoke" / "scores.json")
