"""Server-wide state shared by every router: `get_state(request)` returns a `StudioState`.

    from .state import StudioState, get_state

    @router.get("/thing")
    def thing(st: StudioState = Depends(get_state)):
        st.workspace   Workspace (root, state_dir, labs(), run_roots(), resolve(), encode_id(), decode_id())
        st.jobs        JobManager
        st.bus         EventBus (publish("labs.changed", {...}) etc.)
        st.redact      Redactor (callable; .obj() for JSON values)
        st.cloud       FakeCloud when DECIDER_LAB_FAKE_CLOUD=1, else None
        st.settings    SettingsStore (get(), update(), flag(), set_flag())
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import Request

from .cloud_fake import FakeCloud
from .errors import ApiError
from .jobs import EventBus, JobManager
from .redact import Redactor
from .workspace import Workspace

DEFAULT_SETTINGS: dict[str, Any] = {
    "theme": "system",
    "density": "comfortable",
    "reduce_motion": "system",
    "max_concurrent_jobs": 2,
    "require_pinned_default": True,
    "require_sha256": False,
    "default_backend": "local",
    "runs_dir": None,
    "onboarding_dismissed": False,
}
_CHOICES = {"theme": ("system", "dark", "light"), "density": ("comfortable", "compact"),
            "reduce_motion": ("system", "on"), "default_backend": ("local", "ssh", "aws", "vast")}


class SettingsStore:
    """settings.json (person-editable, API.md section 4) and state.json (flags such as doctor_seen)."""

    def __init__(self, workspace: Workspace) -> None:
        self.ws = workspace
        self.path = os.path.join(workspace.state_dir, "settings.json")
        self.flags_path = os.path.join(workspace.state_dir, "state.json")
        self._lock = threading.Lock()

    def _read(self, path: str) -> dict[str, Any]:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, path: str, data: dict[str, Any]) -> None:
        self.ws.ensure_state_dir()
        tmp = f"{path}.tmp{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, path)

    def get(self) -> dict[str, Any]:
        stored = self._read(self.path)
        return {k: stored.get(k, v) for k, v in DEFAULT_SETTINGS.items()}

    def validate(self, patch: dict[str, Any]) -> dict[str, Any]:
        fields = []
        clean: dict[str, Any] = {}
        for k, v in patch.items():
            if k not in DEFAULT_SETTINGS:
                fields.append({"field": k, "message": "unknown setting"})
            elif k in _CHOICES and v not in _CHOICES[k]:
                fields.append({"field": k, "message": "must be one of " + ", ".join(_CHOICES[k])})
            elif k == "max_concurrent_jobs" and not (isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 8):
                fields.append({"field": k, "message": "must be an integer from 1 to 8"})
            elif k in ("require_pinned_default", "require_sha256", "onboarding_dismissed") and not isinstance(v, bool):
                fields.append({"field": k, "message": "must be true or false"})
            elif k == "runs_dir" and v is not None:
                if not isinstance(v, str) or os.path.isabs(v):
                    fields.append({"field": k, "message": "must be null or a workspace-relative path"})
                else:
                    self.ws.resolve(v)
                    clean[k] = v
                continue
            else:
                clean[k] = v
        if fields:
            raise ApiError(422, "bad_request", "Some settings are not valid.", detail={"fields": fields})
        return clean

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        clean = self.validate(patch)
        with self._lock:
            stored = self._read(self.path)
            stored.update(clean)
            self._write(self.path, stored)
        return self.get()

    def flag(self, name: str, default: Any = None) -> Any:
        return self._read(self.flags_path).get(name, default)

    def set_flag(self, name: str, value: Any) -> None:
        with self._lock:
            data = self._read(self.flags_path)
            if data.get(name) != value:
                data[name] = value
                self._write(self.flags_path, data)


@dataclass
class StudioState:
    workspace: Workspace
    token: str
    redact: Redactor
    jobs: JobManager
    bus: EventBus
    settings: SettingsStore
    cloud: FakeCloud | None = None
    port: int | None = None
    host: str = "127.0.0.1"
    static_dir: str | None = None
    started_at: float = field(default_factory=time.time)
    extra: dict[str, Any] = field(default_factory=dict)  # free slot for area modules (caches, etc.)

    @property
    def fake_cloud(self) -> bool:
        return self.cloud is not None


def get_state(request: Request) -> StudioState:
    return request.app.state.studio
