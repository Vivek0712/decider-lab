"""A fake System One server that answers like `strands-decider serve`, with no model."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


def answer(q: dict, state) -> dict:
    """Deterministic, input-dependent answers: 'yes' if the state contains 'yes'."""
    s = json.dumps(state)
    if q["type"] == "noul":
        return {"type": "noul", "noul": 0.9 if "yes" in s else 0.1}
    if q["type"] == "choice":
        names = list(q["criteria"])
        probs = {n: (0.7 if i == 0 else 0.3 / (len(names) - 1)) for i, n in enumerate(names)}
        return {"type": "choice", "choice": names[0], "probabilities": probs, "confidence": 0.6}
    levels = q["criteria"]
    probs = {str(i): 1.0 / len(levels) for i in range(len(levels))}
    return {"type": "score", "score": (len(levels) - 1) / 2, "legend": {str(i): d for i, d in enumerate(levels)},
            "probabilities": probs, "confidence": 0.0}


class Handler(BaseHTTPRequestHandler):
    calls: list = []
    fail_next = 0

    def log_message(self, *a):  # quiet
        pass

    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"status": "ok", "model": "fake-decider", "checkpoint": "/fake", "vision": True})
        else:
            self._send(404, {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Handler.calls.append(body)
        if Handler.fail_next > 0:
            Handler.fail_next -= 1
            self._send(500, {"detail": "boom"})
            return
        if "bad" in json.dumps(body["state"]):
            self._send(422, {"detail": "invalid"})
            return
        self._send(200, {"model": "fake", "usage": {"input_tokens": 1, "output_tokens": 0},
                         "answers": {k: answer(q, body["state"]) for k, q in body["questions"].items()}})


@pytest.fixture
def fake_server():
    Handler.calls = []
    Handler.fail_next = 0
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", Handler
    srv.shutdown()


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    import decider_lab.suites as suites

    monkeypatch.setattr(suites, "CACHE", str(tmp_path / "cache"))
