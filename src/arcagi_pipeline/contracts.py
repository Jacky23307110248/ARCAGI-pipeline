from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Mapping, Protocol, Sequence, runtime_checkable

StageName = Literal[
    "materials", "trajectory_analysis", "source_audit", "probe_design",
    "evidence_review", "rule_contract", "course_design", "plugin_implementation",
    "generation", "validation", "course_organization", "harness_export",
]


@dataclass(frozen=True)
class ArtifactRef:
    artifact_id: str
    path: str
    sha256: str
    media_type: str = "application/json"


@dataclass(frozen=True)
class GameRef:
    game_id: str
    game_version: str
    plugin_id: str
    plugin_version: str
    extension: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PipelineManifest:
    schema_version: str
    pipeline_id: str
    game: GameRef
    materials: tuple[dict[str, Any], ...]
    workflow: dict[str, Any]
    provenance: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PipelineManifest":
        game = raw["game"]
        return cls(
            schema_version=raw["schema_version"],
            pipeline_id=raw["pipeline_id"],
            game=GameRef(
                game_id=game["id"], game_version=game["version"],
                plugin_id=game["plugin_id"], plugin_version=game["plugin_version"],
                extension=dict(game.get("extension", {})),
            ),
            materials=tuple(raw["materials"]),
            workflow=dict(raw["workflow"]),
            provenance=dict(raw.get("provenance", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StageRecord:
    name: StageName
    status: Literal["complete", "pending", "blocked", "failed"]
    input_artifacts: tuple[str, ...] = ()
    output_artifacts: tuple[ArtifactRef, ...] = ()
    evidence: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchResult:
    status: Literal["solved", "unsolvable", "unknown"]
    actions: tuple[Any, ...] = ()
    expanded_states: int = 0
    reason: str = ""
    replay_accepted: bool | None = None


@dataclass(frozen=True)
class CourseSlot:
    slot_id: str
    objectives: tuple[str, ...]
    prerequisites: tuple[str, ...] = ()
    task_refs: tuple[str, ...] = ()
    learning_rationale: str = ""


@runtime_checkable
class GamePlugin(Protocol):
    """Game-specific mechanics live in a versioned plugin."""

    plugin_id: str
    plugin_version: str

    def validate_extension(self, extension: dict[str, Any]) -> None: ...
    def level_identity(self, level: Any) -> str: ...
    def validate_level(self, level: Any) -> list[str]: ...


@runtime_checkable
class SearchDomain(Protocol):
    """Deterministic finite-state transition model supplied by a game plugin."""

    deterministic: bool
    complete_finite_model: bool

    def initial_state(self) -> Any: ...
    def state_key(self, state: Any) -> Any: ...
    def actions(self, state: Any) -> Sequence[Any]: ...
    def transition(self, state: Any, action: Any) -> Any: ...
    def is_goal(self, state: Any) -> bool: ...
    def engine_replay_accepts(self, actions: Sequence[Any]) -> bool | None: ...


@runtime_checkable
class EngineProbeAdapter(Protocol):
    """Direct original-engine interface for a reset and one action at a time."""

    backend: str
    game_id: str

    def reset(self) -> Any: ...
    def step(self, action: str | Mapping[str, Any]) -> Any: ...


@runtime_checkable
class HarnessAdapter(Protocol):
    adapter_id: str

    def preflight_command(self, spec: Any) -> Any: ...
    def run_command(self, spec: Any) -> Any: ...
