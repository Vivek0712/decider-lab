"""Redaction (API.md 2.5)."""

from __future__ import annotations

from decider_lab.ui.redact import MASK, Redactor


def test_env_values_patterns_and_urls():
    r = Redactor(extra=["the-studio-token"], environ={"HF_TOKEN": "hf_value_long_enough", "SHORT_KEY": "abc",
                                                      "HOME": "/Users/me"})
    text = ("token hf_value_long_enough and the-studio-token; key abc; home /Users/me; "
            "AKIAABCDEFGHIJKLMNOP; sk-abcdefghijklmnopqrstuvwx; hf_" + "a" * 34 + "; "
            "https://bucket.s3.amazonaws.com/x?X-Amz-Credential=AK%2F123&X-Amz-Signature=deadbeef&ok=1; "
            "git+https://user:pass@github.com/x/y; Authorization: Bearer abc.def")
    out = r(text)
    for leaked in ("hf_value_long_enough", "the-studio-token", "AKIAABCDEFGHIJKLMNOP", "sk-abcdefghijklmnopqrstuvwx",
                   "deadbeef", "AK%2F123", "user:pass", "abc.def", "hf_aaaa"):
        assert leaked not in out, leaked
    assert "key abc" in out and "/Users/me" in out and "ok=1" in out
    assert "https://••••@github.com" in out and "Authorization: Bearer ••••" in out
    assert r(out) == out  # idempotent
    assert MASK in out


def test_obj_and_non_strings():
    r = Redactor(extra=["secret-token-1"])
    assert r.obj({"a": ["x secret-token-1"], "b": 3, "c": None}) == {"a": [f"x {MASK}"], "b": 3, "c": None}
    assert r(None) is None and r(5) == 5
