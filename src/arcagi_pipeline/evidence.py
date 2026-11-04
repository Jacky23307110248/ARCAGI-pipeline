from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

TrajectoryKind = Literal["direct_observation", "agent_inference", "saved_note", "prediction"]


@dataclass(frozen=True)
class TrajectoryEntry:
    entry_id: str
    kind: TrajectoryKind
    statement: str
    evidence_refs: tuple[str, ...]
    alternatives: tuple[str, ...] = ()
    confidence: float | None = None


@dataclass(frozen=True)
class SourceFinding:
    finding_id: str
    statement: str
    source_ref: str
    source_version: str
    uncovered_by_play: bool = False


@dataclass(frozen=True)
class ProbeRecord:
    probe_id: str
    setup: Any
    action: Any
    expected_alternatives: tuple[Any, ...]
    observed_result: Any
    engine_version: str
    replay_evidence: str | None = None


@dataclass
class EvidenceLedger:
    game_id: str
    trajectory: list[TrajectoryEntry] = field(default_factory=list)
    source_audit: list[SourceFinding] = field(default_factory=list)
    probes: list[ProbeRecord] = field(default_factory=list)
    trajectory_frozen: bool = False

    def add_trajectory(self, entry: TrajectoryEntry) -> None:
        if self.trajectory_frozen:
            raise ValueError("trajectory ledger is frozen")
        if entry.kind not in {"direct_observation", "agent_inference", "saved_note", "prediction"}:
            raise ValueError("invalid trajectory evidence kind")
        self.trajectory.append(entry)

    def freeze_trajectory(self) -> str:
        self.trajectory_frozen = True
        return self.trajectory_digest()

    def add_source_finding(self, finding: SourceFinding) -> None:
        if not self.trajectory_frozen:
            raise ValueError("freeze trajectory evidence before source audit")
        self.source_audit.append(finding)

    def add_probe(self, probe: ProbeRecord) -> None:
        if not self.trajectory_frozen:
            raise ValueError("freeze trajectory evidence before engine probes")
        self.probes.append(probe)

    def trajectory_digest(self) -> str:
        import hashlib
        import json
        payload = [asdict(item) for item in self.trajectory]
        wire = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(wire.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "arcagi-evidence-ledger/v1",
            "game_id": self.game_id,
            "trajectory_frozen": self.trajectory_frozen,
            "trajectory_digest": self.trajectory_digest(),
            "trajectory": [asdict(item) for item in self.trajectory],
            "source_audit": [asdict(item) for item in self.source_audit],
            "probes": [asdict(item) for item in self.probes],
        }


def trajectory_analysis_packet(run_record: dict[str, Any], saved_notes: list[dict[str, Any]]) -> dict[str, Any]:
    """Whitelist player-visible data; source paths, code, and hidden engine fields are omitted."""
    trace = []
    for row in run_record.get("trace", []):
        if not isinstance(row, dict):
            continue
        trace.append({
            key: row[key]
            for key in ("observation", "feedback", "action", "terminal_result", "step")
            if key in row
        })
    return {
        "game_id": run_record.get("game_id"),
        "run_id": run_record.get("run_id"),
        "trace": trace,
        "saved_notes": [
            {"text": note.get("text", ""), "created_at": note.get("created_at")}
            for note in saved_notes if isinstance(note, dict)
        ],
        "run_outcome": run_record.get("outcome"),
    }