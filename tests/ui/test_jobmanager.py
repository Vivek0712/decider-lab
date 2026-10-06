"""JobManager: start, log, stream, cancel, queue, reattach, lost."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

import pytest
from fastapi import Request

from decider_lab.ui.errors import ApiError
from decider_lab.ui.jobs import JobManager, format_sse, job_event_stream, sse_response
from decider_lab.ui.redact import Redactor

PY = sys.executable


@pytest.fixture
def jm(tmp_path):
    m = JobManager(str(tmp_path / "state"), Redactor(extra=["studio-token-xyz"]), max_concurrent=2,
                   cancel_grace_s=3, kill_after_s=6, poll_s=0.05, redact_extra=["studio-token-xyz"])
    yield m
    m.shutdown(cancel_active=True, wait_s=10)


def test_short_job_succeeds_with_log(jm, tmp_path):
    code = "import sys; print('hello'); print('[decider-lab] FAILED nothing', flush=True); print('err', file=sys.stderr)"
    job = jm.start("eval", [PY, "-c", code], title="echo", cwd=str(tmp_path), env={"EXTRA_FLAG": "1"},
                   env_names=["HF_TOKEN"])
    assert job["job_id"].startswith("j_") and job["status"] in ("queued", "running")
    assert [s["name"] for s in job["stages"]] == ["prepare", "run"]
    done = jm.wait(job["job_id"], timeout=30)
    assert done["status"] == "succeeded" and done["exit_code"] == 0
    assert done["started_at"] and done["ended_at"] and done["duration_s"] is not None
    assert done["env_names"] == ["HF_TOKEN"]
    log = jm.read_log(job["job_id"])
    texts = [x["text"] for x in log["lines"]]
    assert texts == ["hello", "[decider-lab] FAILED nothing", "err"]
    assert [x["level"] for x in log["lines"]] == ["info", "error", "info"]
    assert log["total"] == 3 and log["next_offset"] == 3 and not log["truncated"]
    assert all(x["ts"].endswith("Z") for x in log["lines"])
    assert jm.read_log(job["job_id"], q="FAIL")["lines"][0]["seq"] == 1
    assert jm.read_log(job["job_id"], level="error")["lines"][0]["text"].startswith("[decider-lab] FAILED")
    assert jm.read_log(job["job_id"], offset=1, limit=1)["lines"][0]["seq"] == 1
    on_disk = json.loads(open(os.path.join(jm.dir, job["job_id"], "job.json")).read())
    assert on_disk["status"] == "succeeded"


def test_display_argv_hides_the_interpreter(jm, tmp_path):
    job = jm.start("run", JobManager.cli_argv("--version"), title="version", cwd=str(tmp_path))
    assert job["argv"] == ["decider-lab", "--version"] and job["command"] == "decider-lab --version"
    done = jm.wait(job["job_id"], timeout=60)
    assert done["status"] == "succeeded"
    assert jm.read_log(job["job_id"])["lines"][0]["text"] == "0.1.0"


def test_failed_job_records_last_error_line(jm, tmp_path):
    job = jm.start("eval", [PY, "-c", "print('working'); raise SystemExit('ValueError: bad lab')"], title="x",
                   cwd=str(tmp_path))
    done = jm.wait(job["job_id"], timeout=30)
    assert done["status"] == "failed" and done["exit_code"] == 1
    assert done["failures"] == ["ValueError: bad lab"] and done["failure_count"] == 1


def test_secrets_are_redacted_in_logs_and_records(jm, tmp_path, monkeypatch):
    monkeypatch.setenv("MY_API_KEY", "super-secret-value-123")
    m = JobManager(str(tmp_path / "s2"), Redactor(extra=["studio-token-xyz"]), poll_s=0.05,
                   redact_extra=["studio-token-xyz"])
    try:
        code = ("import os; print('key', os.environ['MY_API_KEY']); print('tok studio-token-xyz');"
                "print('https://user:pw@example.com/x?X-Amz-Signature=abcdef'); print('AKIAABCDEFGHIJKLMNOP')")
        job = m.start("eval", [PY, "-c", code], title="leak studio-token-xyz", cwd=str(tmp_path))
        m.wait(job["job_id"], timeout=30)
        raw = open(m.log_path(job["job_id"])).read()
        for secret in ("super-secret-value-123", "studio-token-xyz", "user:pw", "abcdef", "AKIAABCDEFGHIJKLMNOP"):
            assert secret not in raw
            assert secret not in json.dumps(m.get(job["job_id"]))
        assert "••••" in raw
    finally:
        m.shutdown(wait_s=5)


def test_cancel_running_job_sends_sigint(jm, tmp_path):
    code = ("import time, signal, sys\n"
            "def h(*a):\n    print('got SIGINT, releasing', flush=True); sys.exit(130)\n"
            "signal.signal(signal.SIGINT, h)\nprint('started', flush=True)\ntime.sleep(60)\n")
    job = jm.start("run", [PY, "-c", code], title="sleepy", cwd=str(tmp_path), backend="vast")
    _wait_for_line(jm, job["job_id"], "started")
    assert jm.cancel(job["job_id"]) == "cancelling"
    assert jm.get(job["job_id"])["status"] in ("cancelling", "cancelled")
    done = jm.wait(job["job_id"], timeout=20)
    assert done["status"] == "cancelled" and done["exit_code"] == 130
    assert "got SIGINT, releasing" in [x["text"] for x in jm.read_log(job["job_id"])["lines"]]
    with pytest.raises(ApiError) as e:
        jm.cancel(job["job_id"])
    assert e.value.code == "job_not_active"


def test_cancel_escalates_when_sigint_is_ignored(jm, tmp_path):
    code = "import signal,time; signal.signal(signal.SIGINT, signal.SIG_IGN); print('up', flush=True); time.sleep(60)"
    job = jm.start("eval", [PY, "-c", code], title="stubborn", cwd=str(tmp_path))
    _wait_for_line(jm, job["job_id"], "up")
    t0 = time.time()
    jm.cancel(job["job_id"])
    done = jm.wait(job["job_id"], timeout=20)
    assert done["status"] == "cancelled" and done["exit_code"] == -15  # SIGTERM after the grace period
    assert time.time() - t0 >= 2.5


def test_queue_and_cancel_queued(tmp_path):
    m = JobManager(str(tmp_path / "state"), max_concurrent=1, poll_s=0.05)
    try:
        a = m.start("eval", [PY, "-c", "import time; time.sleep(1.0)"], title="a", cwd=str(tmp_path))
        b = m.start("eval", [PY, "-c", "print('b')"], title="b", cwd=str(tmp_path))
        c = m.start("eval", [PY, "-c", "print('c')"], title="c", cwd=str(tmp_path))
        assert m.get(a["job_id"])["status"] == "running"
        assert m.get(b["job_id"])["queue_position"] == 1 and m.get(c["job_id"])["queue_position"] == 2
        assert m.cancel(c["job_id"]) == "cancelled"
        assert m.wait(b["job_id"], timeout=30)["status"] == "succeeded"
        assert m.get(c["job_id"])["status"] == "cancelled" and m.get(c["job_id"])["started_at"] is None
        assert [s["title"] for s in m.list(status="finished")] == ["c", "b", "a"]
        assert m.list(status="active") == []
    finally:
        m.shutdown(wait_s=5)


def test_reattach_after_server_restart(tmp_path):
    state = str(tmp_path / "state")
    m1 = JobManager(state, poll_s=0.05)
    job = m1.start("eval", [PY, "-c", "import time; print('a', flush=True); time.sleep(6); print('b')"],
                   title="survivor", cwd=str(tmp_path))
    _wait_for_line(m1, job["job_id"], "a")
    m1.shutdown(cancel_active=False)  # the server dies; the job keeps running
    m2 = JobManager(state, poll_s=0.05)
    try:
        assert m2.get(job["job_id"])["status"] == "running"
        done = m2.wait(job["job_id"], timeout=30)
        assert done["status"] == "succeeded"
        assert [x["text"] for x in m2.read_log(job["job_id"])["lines"]] == ["a", "b"]
    finally:
        m2.shutdown(wait_s=5)


def test_dead_job_becomes_lost(tmp_path):
    state = tmp_path / "state"
    m1 = JobManager(str(state), poll_s=0.05)
    job = m1.start("run", [PY, "-c", "print(1)"], title="ghost", cwd=str(tmp_path), backend="vast")
    m1.wait(job["job_id"], timeout=30)
    m1.shutdown()
    p = state / "jobs" / job["job_id"]
    rec = json.loads((p / "job.json").read_text())
    rec.update(status="running", ended_at=None, exit_code=None)
    rec["_internal"]["runner_pid"] = 999999
    (p / "job.json").write_text(json.dumps(rec))
    (p / "exit.json").unlink()
    m2 = JobManager(str(state), poll_s=0.05)
    try:
        lost = m2.get(job["job_id"])
        assert lost["status"] == "lost"
        assert "remote machine may still be running" in lost["failures"][-1]
    finally:
        m2.shutdown()


def test_line_and_finish_handlers(jm, tmp_path):
    seen = []

    def on_line(job, line):
        seen.append(line["text"])
        if line["text"].startswith("progress"):
            return {"progress": {**job["progress"], "label": line["text"], "fraction": 0.5}}
        return None

    jm.add_line_handler(on_line)
    jm.add_finish_handler(lambda job: {"status": "partial", "result": {"ok": 1}} if job["exit_code"] == 3 else None)
    job = jm.start("run", [PY, "-c", "print('progress 1/2'); raise SystemExit(3)"], title="h", cwd=str(tmp_path))
    done = jm.wait(job["job_id"], timeout=30)
    assert "progress 1/2" in seen
    assert done["progress"]["label"] == "progress 1/2" and done["status"] == "partial"
    assert done["result"] == {"ok": 1}


def test_delete_finished_job(jm, tmp_path):
    job = jm.start("eval", [PY, "-c", "print(1)"], title="d", cwd=str(tmp_path))
    jm.wait(job["job_id"], timeout=30)
    jm.delete(job["job_id"])
    assert not os.path.exists(os.path.join(jm.dir, job["job_id"]))
    with pytest.raises(ApiError):
        jm.get(job["job_id"])


def test_unknown_or_malformed_job_id(jm):
    for bad in ("j_000000000000", "../../etc", "nope"):
        with pytest.raises(ApiError) as e:
            jm.get(bad)
        assert e.value.status == 404


def test_sse_stream_replays_and_ends(jm, tmp_path):
    job = jm.start("eval", [PY, "-c", "import time\nfor i in range(3):\n    print('line', i, flush=True); time.sleep(0.1)"],
                   title="stream", cwd=str(tmp_path))

    async def collect(**kw):
        out = []
        async for chunk in job_event_stream(jm, job["job_id"], poll_s=0.05, **kw):
            out.append(chunk)
        return out

    chunks = asyncio.run(collect(from_start=True))
    events = [_parse(c) for c in chunks if not c.startswith(":")]
    kinds = [e["event"] for e in events]
    assert kinds[0] == "snapshot" and kinds[-2:] == ["status", "end"]
    logs = [e for e in events if e["event"] == "log"]
    assert [e["data"]["text"] for e in logs] == ["line 0", "line 1", "line 2"]
    assert [e["id"] for e in logs] == ["0", "1", "2"]
    assert events[-2]["data"]["status"] == "succeeded"
    # reconnect with Last-Event-ID replays only what came after
    again = [_parse(c) for c in asyncio.run(collect(last_event_id="1")) if not c.startswith(":")]
    assert [e["data"]["text"] for e in again if e["event"] == "log"] == ["line 2"]


def test_format_sse():
    assert format_sse("log", {"a": 1}, 7) == 'event: log\nid: 7\ndata: {"a":1}\n\n'


def _parse(chunk: str) -> dict:
    out: dict = {}
    for line in chunk.strip().splitlines():
        k, _, v = line.partition(": ")
        out[k] = json.loads(v) if k == "data" else v
    return out


def _wait_for_line(jm, job_id, text, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if any(x["text"] == text for x in jm.read_log(job_id)["lines"]):
            return
        time.sleep(0.05)
    raise AssertionError(f"{text!r} not in log: {jm.read_log(job_id)}")


def test_sse_helpers_over_http(app, client):
    """The helpers as a route would use them: sse_response(job_event_stream(...)) through the auth stack."""
    st = app.state.studio

    @app.get("/api/_test/jobs/{job_id}/events")
    async def events(job_id: str, request: Request):
        return sse_response(job_event_stream(st.jobs, job_id, from_start=True, poll_s=0.05,
                                             is_disconnected=request.is_disconnected))

    app.router.routes.insert(0, app.router.routes.pop())  # ahead of the /api catch-all 404
    job = st.jobs.start("eval", [PY, "-c", "print('over http')"], title="http", cwd=str(st.workspace.root))
    st.jobs.wait(job["job_id"], timeout=30)
    with client.stream("GET", f"/api/_test/jobs/{job['job_id']}/events") as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        body = "".join(r.iter_text())
    assert "event: snapshot" in body and '"text":"over http"' in body and body.rstrip().endswith("data: {}")
