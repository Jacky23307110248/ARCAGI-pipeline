from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

STAGE_ORDER = (
    "materials", "trajectory_analysis", "source_audit", "probe_design",
    "evidence_review", "rule_contract", "course_design", "plugin_implementation",
    "generation", "validation", "course_organization", "harness_export",
)
DEPENDENCIES = {name: STAGE_ORDER[:index] for index, name in enumerate(STAGE_ORDER)}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class PipelineRun:
    """Local stage tracker; agent-produced work remains explicit hashed artifacts."""

    def __init__(self, root: Path, pipeline_id: str, game: dict[str, Any]):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.record_path = self.root / "pipeline_run.json"
        self.data: dict[str, Any] = {
            "schema_version": "arcagi-pipeline-run/v1",
            "pipeline_id": pipeline_id,
            "game": game,
            "stages": [],
        }
        if self.record_path.exists():
            self.data = json.loads(self.record_path.read_text(encoding="utf-8"))

    def record_stage(self, name: str, artifact_paths: list[Path], *, details: dict[str, Any] | None = None) -> dict[str, Any]:
        if name not in STAGE_ORDER:
            raise ValueError(f"unknown stage: {name}")
        completed = {row["name"] for row in self.data["stages"] if row["status"] == "complete"}
        missing = [dep for dep in DEPENDENCIES[name] if dep not in completed]
        if missing:
            raise ValueError(f"{name} requires completed stages: {', '.join(missing)}")
        refs = []
        for path in artifact_paths:
            resolved = path.resolve()
            try:
                rel = resolved.relative_to(self.root).as_posix()
            except ValueError as exc:
                raise ValueError(f"stage artifact must be inside run root: {path}") from exc
            if not resolved.is_file():
                raise FileNotFoundError(resolved)
            refs.append({"artifact_id": resolved.stem, "path": rel, "sha256": sha256_file(resolved)})
        row = {"name": name, "status": "complete", "outputs": refs, "details": details or {}}
        self.data["stages"] = [item for item in self.data["stages"] if item["name"] != name] + [row]
        self.data["stages"].sort(key=lambda item: STAGE_ORDER.index(item["name"]))
        self.record_path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return row