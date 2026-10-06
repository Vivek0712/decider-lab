"""Deterministic cloud fixtures for DECIDER_LAB_FAKE_CLOUD=1 (API.md section 2.6).

    if fake_cloud_enabled():
        cloud = FakeCloud()            # loads fakes/vast.json and fakes/aws.json
        cloud.vast_offers(gpu="A100_SXM4", max_price=0.8)
        cloud.vast_destroy(9876543)    # removes it until the server restarts

No method makes a network call, runs a CLI or imports boto3. Shapes match the API.md compute
section minus the fields that need Studio's job list (`job_id`, `idle` default to null/true; the
compute routes overlay them). Every response carries `"fake": true`.
"""

from __future__ import annotations

import copy
import json
import os
import threading
from datetime import datetime, timezone
from typing import Any

FIXTURES = os.path.join(os.path.dirname(__file__), "fakes")
ENV = "DECIDER_LAB_FAKE_CLOUD"


def fake_cloud_enabled(environ: dict[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return env.get(ENV, "").strip().lower() in ("1", "true", "yes", "on")


def _ts(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


class FakeCloud:
    """In-memory vast.ai and AWS state seeded from the fixture files. Thread-safe."""

    def __init__(self, fixtures_dir: str = FIXTURES, now: float | None = None) -> None:
        with open(os.path.join(fixtures_dir, "vast.json"), encoding="utf-8") as fh:
            self._vast = json.load(fh)
        with open(os.path.join(fixtures_dir, "aws.json"), encoding="utf-8") as fh:
            self._aws = json.load(fh)
        self._now = now
        self._lock = threading.Lock()

    def now(self) -> float:
        return self._now if self._now is not None else datetime.now(tz=timezone.utc).timestamp()

    def reset(self) -> None:
        self.__init__(now=self._now)  # type: ignore[misc]

    # ---- vast.ai -------------------------------------------------------------------------------

    def vast_status(self) -> dict[str, Any]:
        return {"fake": True, "cli": True, "api_key": True, "credit_usd": self._vast["user"]["credit_usd"],
                "as_of": self._vast["user"]["as_of"], "error": None}

    def vast_offers(self, gpu: str = "RTX_4090", num_gpus: int = 1, max_price: float = 0.8, disk_gb: int = 80,
                    min_gpu_ram_gb: float = 0) -> dict[str, Any]:
        items = [o for o in self._vast["offers"]
                 if o["gpu_name"] == gpu and o["num_gpus"] >= num_gpus and o["dph_total"] <= max_price
                 and o["disk_space_gb"] >= disk_gb and o["gpu_ram_gb"] >= min_gpu_ram_gb]
        items.sort(key=lambda o: (o["dph_total"], o["id"]))
        return {"fake": True, "items": copy.deepcopy(items[:50]), "credit_usd": self._vast["user"]["credit_usd"],
                "gpu_names": list(self._vast["gpu_names"])}

    def vast_instances(self) -> dict[str, Any]:
        now = self.now()
        with self._lock:
            items = copy.deepcopy(self._vast["instances"])
        for it in items:
            up = max(0.0, now - _ts(it["started_at"]))
            it["uptime_s"] = int(up)
            it["cost_so_far_usd"] = round(it["dph_total"] * up / 3600, 2)
            it.setdefault("job_id", None)
            it.setdefault("idle", True)
        return {"fake": True, "items": items}

    def vast_destroy(self, instance_id: int | str) -> dict[str, Any] | None:
        """None if no such decider-lab instance; else {"destroyed": True}."""
        with self._lock:
            before = len(self._vast["instances"])
            self._vast["instances"] = [i for i in self._vast["instances"] if str(i["id"]) != str(instance_id)]
            if len(self._vast["instances"]) == before:
                return None
        return {"destroyed": True}

    def vast_destroy_all(self) -> dict[str, Any]:
        with self._lock:
            ids = [i["id"] for i in self._vast["instances"]]
            self._vast["instances"] = []
        return {"results": [{"id": i, "destroyed": True} for i in ids]}

    # ---- AWS -----------------------------------------------------------------------------------

    def aws_profiles(self) -> dict[str, Any]:
        return {"fake": True, "items": list(self._aws["profiles"]), "env_profile": None, "boto3": True}

    def aws_identity(self, profile: str | None = None, region: str = "us-east-1") -> dict[str, Any]:
        return {"fake": True, "profile": profile or "default", "region": region,
                "account": self._aws["identity"]["account"], "arn": self._aws["identity"]["arn"], "ok": True,
                "error": None}

    def aws_quotas(self, profile: str | None = None, region: str = "us-east-1") -> dict[str, Any]:
        return {"fake": True, "items": copy.deepcopy(self._aws["quotas"]), "error": None}

    def aws_instances(self, profile: str | None = None, region: str = "us-east-1") -> dict[str, Any]:
        now = self.now()
        with self._lock:
            items = copy.deepcopy(self._aws["instances"])
        for it in items:
            it["uptime_s"] = int(max(0.0, now - _ts(it["launched_at"])))
            it.setdefault("job_id", None)
            it.setdefault("idle", True)
        return {"fake": True, "items": items}

    def aws_terminate(self, instance_id: str, profile: str | None = None,
                      region: str = "us-east-1") -> dict[str, Any] | None:
        """None if no such decider-lab instance; else {"terminating": [id]}."""
        with self._lock:
            before = len(self._aws["instances"])
            self._aws["instances"] = [i for i in self._aws["instances"] if i["id"] != instance_id]
            if len(self._aws["instances"]) == before:
                return None
        return {"terminating": [instance_id]}

    def aws_terminate_all(self, profile: str | None = None, region: str = "us-east-1") -> dict[str, Any]:
        with self._lock:
            ids = [i["id"] for i in self._aws["instances"]]
            self._aws["instances"] = []
        return {"terminating": ids}

    def bedrock_models(self, profile: str | None = None, region: str = "us-east-1",
                       q: str | None = None) -> dict[str, Any]:
        items = copy.deepcopy(self._aws["bedrock_models"])
        if q:
            ql = q.lower()
            items = [m for m in items if ql in m["name"].lower() or ql in m["model_id"].lower()
                     or ql in m["provider"].lower()]
        for m in items:
            m["spec_yaml"] = f"{{bedrock: {m['invoke_id']}, region: {region}}}"
        return {"fake": True, "items": items, "error": None}

    # ---- doctor --------------------------------------------------------------------------------

    def doctor_rows(self) -> list[tuple[str, str, str]]:
        """The rows `decider-lab doctor` would print for vast and aws, from the fixtures."""
        return [("ok", "vast.ai", f"credit ${self._vast['user']['credit_usd']:.2f} (fixtures)"),
                ("ok", "aws", f"credentials for account {self._aws['identity']['account']} (fixtures)")]
