"""Redaction of secrets in anything Studio stores, streams or returns (API.md section 2.5).

    r = Redactor(extra=[token])
    r("Authorization: Bearer abc...")  -> "Authorization: ••••"

Replaces with MASK: (a) values of environment variables whose name looks secret and whose value is
at least 8 characters, (b) well-known key patterns (AWS access key ids, Hugging Face and OpenAI-style
tokens), (c) presigned URL query values, (d) Authorization header values, (e) URL user info, (f) any
extra literal (the Studio token). Redaction is idempotent and never raises.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from typing import Any

MASK = "••••"
SECRET_NAME = re.compile(r"(?i)(token|secret|key|password|passwd|credential|session)")
PATTERNS = [
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bhf_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
]
PRESIGNED = re.compile(r"(?i)((?:X-Amz-(?:Signature|Credential|Security-Token))=)[^&\s\"'#]+")
AUTH_HEADER = re.compile(r"(?i)(authorization\s*[:=]\s*)(?:(bearer|basic|token)\s+)?[^\s\"',;]+")
USERINFO = re.compile(r"((?:[a-z][a-z0-9+.-]*)://)([^/\s:@\"']+(?::[^/\s@\"']*)?)@")


def secret_env_values(environ: Mapping[str, str] | None = None, min_len: int = 8) -> list[str]:
    env = os.environ if environ is None else environ
    return [v for k, v in env.items() if SECRET_NAME.search(k) and v and len(v) >= min_len]


class Redactor:
    """Callable redactor. Built once at server start from the server's environment plus extras."""

    def __init__(self, extra: Iterable[str] = (), environ: Mapping[str, str] | None = None) -> None:
        values = {v for v in secret_env_values(environ)} | {v for v in extra if v and len(v) >= 4}
        # longest first, so a value that contains another is masked whole
        self.values = sorted(values, key=len, reverse=True)

    def add(self, value: str) -> None:
        if value and len(value) >= 4 and value not in self.values:
            self.values = sorted([*self.values, value], key=len, reverse=True)

    def __call__(self, text: Any) -> Any:
        if not isinstance(text, str) or not text:
            return text
        for v in self.values:
            if v in text:
                text = text.replace(v, MASK)
        text = PRESIGNED.sub(lambda m: m.group(1) + MASK, text)
        text = AUTH_HEADER.sub(lambda m: m.group(1) + (m.group(2) + " " if m.group(2) else "") + MASK
                               if MASK not in m.group(0)[len(m.group(1)):] else m.group(0), text)
        text = USERINFO.sub(lambda m: m.group(1) + MASK + "@" if m.group(2) != MASK else m.group(0), text)
        for p in PATTERNS:
            text = p.sub(MASK, text)
        return text

    def obj(self, value: Any) -> Any:
        """Redact every string inside a JSON-like value (dicts, lists, tuples)."""
        if isinstance(value, str):
            return self(value)
        if isinstance(value, Mapping):
            return {k: self.obj(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.obj(v) for v in value]
        return value
