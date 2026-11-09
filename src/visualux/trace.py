"""Atomic, local checkpoints. A persistence error stops execution."""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .rubric import digest


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic(path: Path, value: str | dict) -> None:
    content = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    fd, temporary = tempfile.mkstemp(prefix=".visualux-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class TaskTrace:
    def __init__(self, root: Path, task, config, rubric):
        self.root = root / "tasks" / task.id
        self.root.mkdir(parents=True, mode=0o700)
        (self.root / "screenshots").mkdir(mode=0o700)
        self.data = {
            "schema_version": 1, "task": task.model_dump(mode="json"),
            "started_at": utc(), "execution_status": "running", "events": [],
            "observations": [], "decisions": [], "bindings": [], "measurements": [], "steps": [],
            "handoffs": [], "model_calls": [], "downloads": [], "errors": [],
            "result": None, "verification": {"status": "not-run", "limitations": ["Visual evidence alone; no independent validator ran."]},
            "evaluation": {"status": "unavailable", "findings": [], "dispositions": [], "covered_observations": [], "covered_criteria": [], "limitations": []},
            "penalty": {"rule": "penalty-v1", "status": "unavailable", "total": None},
            "metrics": {}, "config": config.public(), "rubric_hash": rubric["hash"],
        }
        self.save()

    def save(self):
        atomic(self.root / "trace.json", self.data)

    def event(self, kind: str, **fields):
        self.data["events"].append({"kind": kind, "at": utc(), **fields})
        self.save()

    def observation(self, png: bytes, viewport: dict, **fields) -> dict:
        oid = f"obs-{len(self.data['observations']):03}"
        path = self.root / "screenshots" / f"{oid}.png"
        path.write_bytes(png)
        item = {"id": oid, "at": utc(), "path": f"screenshots/{oid}.png", "sha256": digest(png), "viewport": viewport, **fields}
        self.data["observations"].append(item)
        self.save()
        return item

    def model_call(self, item: dict):
        self.data["model_calls"].append(item)
        self.save()


def validate_evidence(refs, trace: dict):
    observations = {o["id"] for o in trace["observations"]}
    steps = {s["id"] for s in trace["steps"]}
    measurements = {m["id"] for m in trace["measurements"]}
    for ref in refs:
        value = ref.model_dump() if hasattr(ref, "model_dump") else ref
        if value["observation_id"] not in observations:
            raise ValueError("Unknown observation reference")
        if value.get("step_id") and value["step_id"] not in steps:
            raise ValueError("Unknown step reference")
        if value.get("step_id"):
            step = next(s for s in trace["steps"] if s["id"] == value["step_id"])
            if value["observation_id"] not in {step.get("before"), step.get("after")}:
                raise ValueError("Step does not reference this observation")
        if value.get("measurement_id") and value["measurement_id"] not in measurements:
            raise ValueError("Unknown measurement reference")
        if value.get("measurement_id"):
            measured = next(m for m in trace["measurements"] if m["id"] == value["measurement_id"])
            if measured["observation_id"] != value["observation_id"]:
                raise ValueError("Measurement belongs to another observation")
