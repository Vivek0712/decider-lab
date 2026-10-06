"""Where a lab runs: `local` (this machine), `ssh` (a machine you have), `aws` (an EC2
instance launched for the run), `vast` (a rented vast.ai GPU). Every remote backend shares one
flow (base.run_on): acquire, copy, bootstrap, run, fetch, release.

Configured in lab.yaml (or overridden with `decider-lab run lab.yaml --on <backend>`):

    compute:
      backend: aws                 # local (default) | ssh | aws | vast
      max_hours: 2            # hard deadline; the machine is released at the latest then
      strands_decider: "..."  # pip spec installed on the machine (default: pinned GitHub main)
      aws:  {instance_type: g6e.xlarge, region: us-east-1, profile: default}
      vast: {gpu: A100_SXM4, max_price: 0.8, ssh_key: ~/.ssh/id_ed25519}
      ssh:  {host: ubuntu@10.0.0.5, key: ~/.ssh/id_ed25519}
"""

from __future__ import annotations

from typing import Any

from .base import Host, Provider, run_on

BACKENDS = ("local", "ssh", "aws", "vast")

__all__ = ["BACKENDS", "Host", "Provider", "make_provider", "run_on"]


def make_provider(on: str, cfg: dict[str, Any] | None = None, *, label: str = "lab") -> Provider:
    cfg = dict(cfg or {})
    if on == "vast":
        from .vast import VastProvider

        return VastProvider(label=label, **cfg)
    if on == "aws":
        from .aws import AwsProvider

        return AwsProvider(label=label, **cfg)
    if on == "ssh":
        from .ssh import SshProvider

        if "host" not in cfg:
            raise ValueError("compute.ssh.host is required (user@address[:port])")
        return SshProvider(**cfg)
    raise ValueError(f"unknown compute backend {on!r}; one of {BACKENDS}")
