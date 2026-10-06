"""Your own machine over ssh: a workstation, an on-prem GPU server, an EC2 instance you manage.

    compute: {on: ssh, ssh: {host: ubuntu@10.0.0.5, port: 22, key: ~/.ssh/id_ed25519}}

Nothing is created or destroyed: decider-lab uses a working directory on it
(~/decider-lab-work by default), installs a venv there, and leaves the machine as it was
otherwise. The user needs passwordless sudo only if git, gcc or python3-venv are missing.
"""

from __future__ import annotations

from typing import Any

from .base import CPU_STRANDS, DEFAULT_STRANDS, Host, Provider


class SshProvider(Provider):
    name = "ssh"
    ephemeral = False

    def __init__(self, host: str, port: int = 22, key: str | None = None, work: str | None = None,
                 gpu: bool = True) -> None:
        user, _, address = host.rpartition("@")
        if ":" in address:
            address, _, p = address.partition(":")
            port = int(p)
        self.host = Host(address, port, user or "root", key, work)
        self.gpu = gpu

    def default_strands_spec(self) -> str:
        return DEFAULT_STRANDS if self.gpu else CPU_STRANDS

    def check(self, max_hours: float) -> None:
        if not self.host.reachable():
            raise RuntimeError(f"cannot ssh to {self.host.target} on port {self.host.port} "
                               "(check the host, the key and that BatchMode ssh works without a password)")

    def acquire(self, log: Any) -> Host:
        return self.host

    def release(self, log: Any) -> None:
        log(f"[ssh] done; {self.host.target} is your machine and is left running (work dir {self.host.work})")
