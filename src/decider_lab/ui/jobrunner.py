"""The per-job supervisor: `python -m decider_lab.ui.jobrunner <job_dir>`.

Studio never pipes a job's output into the server process: if it did, restarting the server would
break the pipe and kill the job. Instead the server starts this small supervisor in its own session.
It starts the real command (argv/cwd from <job_dir>/spawn.json) in another new session, reads its
merged stdout/stderr, redacts every line, appends `<ISO ts>\\t<text>` lines to <job_dir>/log.txt, and
writes <job_dir>/exit.json when the command ends. The server tails log.txt and signals the child's
process group (child.json) to cancel, so a restarted server can reattach to a running job.

Extra literals to redact (the Studio token) arrive in the environment variable named by
REDACT_ENV and are removed before the command starts.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys

from .redact import Redactor
from .workspace import now_iso

REDACT_ENV = "DECIDER_LAB_STUDIO_REDACT"


def _write_json(path: str, data: dict) -> None:
    tmp = f"{path}.tmp{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


def main(job_dir: str) -> int:
    with open(os.path.join(job_dir, "spawn.json"), encoding="utf-8") as fh:
        spawn = json.load(fh)
    extra = [v for v in (os.environ.pop(REDACT_ENV, "") or "").split("\n") if v]
    redact = Redactor(extra=extra)
    env = dict(os.environ)
    log_path = os.path.join(job_dir, "log.txt")
    log = open(log_path, "a", encoding="utf-8")  # noqa: SIM115 - closed at exit

    def emit(text: str) -> None:
        log.write(f"{now_iso()}\t{redact(text)}\n")
        log.flush()

    try:
        proc = subprocess.Popen(spawn["argv"], cwd=spawn["cwd"], env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    except OSError as e:
        emit(f"[studio] could not start the command: {type(e).__name__}: {e}")
        _write_json(os.path.join(job_dir, "exit.json"), {"exit_code": 127, "ended_at": now_iso(), "error": str(e)})
        return 127
    _write_json(os.path.join(job_dir, "child.json"), {"pid": proc.pid, "pgid": proc.pid, "started_at": now_iso()})

    def forward(signum: int, _frame: object) -> None:
        try:
            os.killpg(proc.pid, signum)
        except OSError:
            pass

    for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(s, forward)

    assert proc.stdout is not None
    pending = b""
    while True:
        chunk = proc.stdout.read1(65536) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)
        if not chunk:
            break
        pending += chunk
        # progress bars rewrite a line with \r; treat it as a line end so the log keeps moving
        *lines, pending = pending.replace(b"\r\n", b"\n").replace(b"\r", b"\n").split(b"\n")
        for raw in lines:
            emit(raw.decode("utf-8", errors="replace"))
    if pending:
        emit(pending.decode("utf-8", errors="replace"))
    code = proc.wait()
    log.close()
    _write_json(os.path.join(job_dir, "exit.json"), {"exit_code": code, "ended_at": now_iso()})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
