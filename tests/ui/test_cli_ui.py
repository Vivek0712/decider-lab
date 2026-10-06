"""`decider-lab ui` argument handling (the server itself is exercised by the other tests and e2e)."""

from __future__ import annotations

from decider_lab.cli import build_parser, main
from decider_lab.ui.server import is_loopback


def test_parser_defaults():
    a = build_parser().parse_args(["ui"])
    assert (a.workspace, a.host, a.port, a.token, a.no_browser) == (".", "127.0.0.1", 7861, None, False)


def test_non_loopback_needs_explicit_token(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("DECIDER_LAB_UI_TOKEN", "from-env-token-123")
    assert main(["ui", "--host", "0.0.0.0", "--workspace", str(tmp_path), "--no-browser"]) == 2
    assert "loopback only" in capsys.readouterr().err


def test_missing_workspace(capsys, tmp_path):
    assert main(["ui", "--workspace", str(tmp_path / "nope"), "--no-browser"]) == 2


def test_port_in_use(capsys, tmp_path):
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen()
    try:
        port = s.getsockname()[1]
        assert main(["ui", "--port", str(port), "--workspace", str(tmp_path), "--no-browser"]) == 2
        assert "in use" in capsys.readouterr().err
    finally:
        s.close()


def test_is_loopback():
    assert is_loopback("127.0.0.1") and is_loopback("::1") and is_loopback("localhost") and is_loopback("[::1]")
    assert not is_loopback("0.0.0.0") and not is_loopback("10.0.0.5")
