from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from .engine_probe import VersionBoundEngineProbe
from .rule_contract import validate_frozen_contract_inputs
from .game_provenance import sha256_file


@dataclass(frozen=True)
class ReplayProvenance:
    """Claims carried by a generated level; claims are checked against trusted files."""
    game_id: str
    game_version: str
    level_id: str
    level_sha256: str
    source_binding_sha256: str
    source_tree_sha256: str
    runtime_sha256: str
    rule_contract_sha256: str


@dataclass(frozen=True)
class GeneratedLevelReplayResult:
    accepted: bool
    trace: tuple[dict[str, Any], ...] = ()
    reason: str = ""
    provenance: ReplayProvenance | None = None


class GeneratedLevelReplayBinder(Protocol):
    """Optional backend capability that binds an adapter to trusted engine provenance."""
    def bind(self, adapter: Any, *, source_binding_path: Path) -> VersionBoundEngineProbe: ...


class GeneratedLevelReplayProvider(Protocol):
    """Optional game-specific generated-level replay surface; not a GamePlugin requirement."""
    def replay_provenance(self, level: Any) -> ReplayProvenance: ...
    def level_identity(self, level: Any) -> str: ...
    def level_sha256(self, level: Any) -> str: ...
    def make_engine_adapter(self, level: Any) -> Any: ...
    def is_goal_observation(self, level: Any, observation: Mapping[str, Any]) -> bool: ...


def canonical_sha256(value: Any) -> str:
    wire = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(wire.encode("utf-8")).hexdigest()


def run_generated_level_replay(
    provider: GeneratedLevelReplayProvider,
    level: Any,
    actions: Sequence[Any],
    *,
    source_binding_path: Path,
    rule_contract_path: Path,
    binder: GeneratedLevelReplayBinder,
) -> GeneratedLevelReplayResult:
    """Validate trusted provenance, then replay each action in a backend-bound original engine."""
    trace: list[dict[str, Any]] = []
    try:
        envelope = provider.replay_provenance(level)
        binding_path = source_binding_path.expanduser().resolve(strict=True)
        contract_path = rule_contract_path.expanduser().resolve(strict=True)
        binding_sha = sha256_file(binding_path)
        contract_sha = sha256_file(contract_path)
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        source = binding.get("source", {})
        runtime = binding.get("runtime", {})
        if binding.get("status") != "verified" or source.get("status") != "verified" or runtime.get("status") != "verified":
            raise ValueError("unverified source or runtime binding")
        if contract.get("schema_version") != "arcagi-rule-contract-result/v1" or contract.get("frozen") is not True:
            raise ValueError("replay requires a frozen rule contract")
        expected = (
            binding.get("game_id"), str(binding.get("game_version")),
            binding_sha, source.get("source_tree_sha256"),
            runtime.get("dependency_inventory_sha256"), contract_sha,
        )
        actual = (
            envelope.game_id, envelope.game_version, envelope.source_binding_sha256,
            envelope.source_tree_sha256, envelope.runtime_sha256, envelope.rule_contract_sha256,
        )
        if actual != expected:
            raise ValueError("generated-level provenance does not match trusted binding and frozen contract")
        if envelope.level_id != provider.level_identity(level) or envelope.level_sha256 != provider.level_sha256(level):
            raise ValueError("generated-level identity or hash does not match the actual level")
        if contract.get("game_id") != envelope.game_id or str(contract.get("game_version")) != envelope.game_version:
            raise ValueError("frozen contract game identity does not match replay provenance")
        if contract.get("source_sha256") != envelope.source_tree_sha256 or contract.get("runtime_binding_sha256") != envelope.runtime_sha256:
            raise ValueError("frozen contract source/runtime fingerprints do not match replay provenance")
        binding_ref = contract.get("source_binding", {})
        if binding_ref.get("sha256") != binding_sha:
            raise ValueError("frozen contract does not bind the selected source binding")
        validated_inputs = validate_frozen_contract_inputs(contract)
        if not any(item["role"] == "source_binding" and item["sha256"] == binding_sha for item in validated_inputs):
            raise ValueError("frozen contract inputs do not contain the selected source binding")
        probe = binder.bind(provider.make_engine_adapter(level), source_binding_path=binding_path)
        if not isinstance(probe, VersionBoundEngineProbe):
            raise TypeError("generated-level replay binder must return a VersionBoundEngineProbe with session drift checks")
        probe_source = probe.binding.game_source
        probe_environment = probe_source.get("environment", {})
        if (probe_source.get("game_id") != envelope.game_id
                or probe_environment.get("source_tree_sha256") != envelope.source_tree_sha256
                or probe.binding.runtime.get("dependency_inventory_sha256") != envelope.runtime_sha256):
            raise ValueError("backend binder probe does not match trusted replay provenance")
        observation = probe.reset()
        trace.append({"index": 0, "action": None, "observation": observation})
        if provider.is_goal_observation(level, observation):
            return GeneratedLevelReplayResult(True, tuple(trace), "goal_reached", envelope)
        for index, action in enumerate(actions, 1):
            observation = probe.step(action)
            trace.append({"index": index, "action": action, "observation": observation})
            if provider.is_goal_observation(level, observation):
                return GeneratedLevelReplayResult(True, tuple(trace), "goal_reached", envelope)
            if isinstance(observation, Mapping) and observation.get("state") in {"GAME_OVER", "LOSE"}:
                break
        return GeneratedLevelReplayResult(False, tuple(trace), "goal_not_reached", envelope)
    except Exception as exc:
        return GeneratedLevelReplayResult(False, tuple(trace), f"replay_mismatch:{type(exc).__name__}:{exc}")
