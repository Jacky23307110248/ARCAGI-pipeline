from __future__ import annotations

import hashlib
import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

from .cli import validate_manifest
from .contracts import CourseSlot, SearchDomain
from .curriculum import validate_course
from .evidence import (
    EvidenceLedger,
    ProbeRecord,
    SourceFinding,
    TrajectoryEntry,
    trajectory_analysis_packet,
)
from .harness import HarnessRunSpec, write_run_spec
from .harness_adapters.duck import DuckAdapter
from .plugins import validate_plugin
from .search import exact_bfs
from .workflow import PipelineRun, STAGE_ORDER


GAME_ID = "synthetic-two-level-demo"
GAME_VERSION = "synthetic-v1"
PLUGIN_ID = "synthetic-sequence"
PLUGIN_VERSION = "1.0"
PROVENANCE = "synthetic_demo_fixture"
TOKEN_ALPHABET = ("symbol-a", "symbol-b", "symbol-c", "symbol-d")


@dataclass(frozen=True)
class SyntheticLevel:
    level_id: str
    target_tokens: tuple[str, ...]


@dataclass(frozen=True)
class SequenceState:
    tokens: tuple[str, ...] = ()
    committed: bool = False


@dataclass(frozen=True)
class SequenceAction:
    kind: str
    token: str = ""

    def wire(self) -> dict[str, str]:
        return {"kind": self.kind, "token": self.token}


class SyntheticPlugin:
    plugin_id = PLUGIN_ID
    plugin_version = PLUGIN_VERSION

    def validate_extension(self, extension: dict[str, Any]) -> None:
        if extension.get("level_count") != 2:
            raise ValueError("synthetic demo requires exactly two levels")
        if not isinstance(extension.get("seed"), int):
            raise ValueError("synthetic demo seed must be an integer")

    def level_identity(self, level: SyntheticLevel) -> str:
        return level.level_id

    def validate_level(self, level: SyntheticLevel) -> list[str]:
        if not 2 <= len(level.target_tokens) <= 3:
            return [f"{level.level_id} target length must be two or three"]
        if len(set(level.target_tokens)) != len(level.target_tokens):
            return [f"{level.level_id} target tokens must be distinct"]
        if any(token not in TOKEN_ALPHABET for token in level.target_tokens):
            return [f"{level.level_id} uses an unknown token"]
        return []


class SequenceDomain:
    deterministic = True
    complete_finite_model = True

    def __init__(self, target_tokens: tuple[str, ...]):
        self.target_tokens = target_tokens
        self.alphabet = TOKEN_ALPHABET

    def initial_state(self) -> SequenceState:
        return SequenceState()

    def state_key(self, state: SequenceState) -> tuple[tuple[str, ...], bool]:
        return state.tokens, state.committed

    def actions(self, state: SequenceState) -> Sequence[SequenceAction]:
        if state.committed:
            return ()
        append_actions = tuple(SequenceAction("append", token) for token in self.alphabet)
        return (*append_actions, SequenceAction("commit"))

    def transition(self, state: SequenceState, action: SequenceAction) -> SequenceState:
        if state.committed:
            return state
        if action.kind == "append" and action.token in self.alphabet and len(state.tokens) < len(self.target_tokens):
            return SequenceState(state.tokens + (action.token,), False)
        if action.kind == "commit" and state.tokens == self.target_tokens:
            return SequenceState(state.tokens, True)
        return state

    def is_goal(self, state: SequenceState) -> bool:
        return state.committed

    def engine_replay_accepts(self, actions: Sequence[SequenceAction]) -> bool:
        tokens: list[str] = []
        committed = False
        for action in actions:
            if committed:
                return False
            if action.kind == "append" and action.token in self.alphabet and len(tokens) < len(self.target_tokens):
                tokens.append(action.token)
            elif action.kind == "commit" and tuple(tokens) == self.target_tokens:
                committed = True
            else:
                return False
        return committed


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _level_wire(level: SyntheticLevel) -> dict[str, Any]:
    return {
        "level_id": level.level_id,
        "target_tokens": list(level.target_tokens),
        "provenance": PROVENANCE,
    }


def _action_wire(action: Any) -> Any:
    if isinstance(action, SequenceAction):
        return action.wire()
    return action


def _make_levels(seed: int) -> list[SyntheticLevel]:
    selected = random.Random(seed).sample(TOKEN_ALPHABET, 3)
    return [
        SyntheticLevel("lesson-01-short-sequence", tuple(selected[:2])),
        SyntheticLevel("lesson-02-long-sequence", tuple(selected)),
    ]


def _domain_for(level: SyntheticLevel) -> SearchDomain:
    return SequenceDomain(level.target_tokens)


def run_demo(output_dir: Path, *, seed: int = 17) -> dict[str, Any]:
    """Run a deterministic synthetic end-to-end slice without processes, network, or model calls."""
    root = output_dir.expanduser()
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise ValueError(f"output directory must be empty: {root}")
    root.mkdir(parents=True, exist_ok=True)

    levels = _make_levels(seed)
    plugin = SyntheticPlugin()
    extension = {"level_count": 2, "seed": seed, "provenance": PROVENANCE}
    level_wires = [_level_wire(level) for level in levels]
    first_token, second_token = levels[0].target_tokens

    source_material = {
        "provenance": PROVENANCE,
        "source_version": GAME_VERSION,
        "synthetic_rules": [
            "append adds the supplied token to the end of the sequence",
            "commit succeeds only when the sequence equals the level target",
        ],
    }
    trace_material = {
        "game_id": GAME_ID,
        "run_id": "synthetic-run-001",
        "outcome": "demonstration_in_progress",
        "trace": [
            {
                "step": 1,
                "observation": {"sequence": [], "committed": False},
                "action": {"kind": "append", "token": first_token},
                "feedback": "sequence length is now one",
                "engine_internal": "PRIVATE_ENGINE_STATE_MUST_NOT_ENTER_TRAJECTORY_PACKET",
            },
            {
                "step": 2,
                "observation": {"sequence": [first_token], "committed": False},
                "action": {"kind": "append", "token": second_token},
                "feedback": "sequence length is now two",
                "source_code": "PRIVATE_SOURCE_SENTINEL_MUST_NOT_ENTER_TRAJECTORY_PACKET",
            },
        ],
        "hidden_engine_state": "HIDDEN_FIXTURE_SENTINEL",
    }
    saved_notes = [{
        "text": "The order of appended tokens may be preserved.",
        "provenance": PROVENANCE,
        "created_at": "synthetic fixture",
        "private_chain_of_thought": "PRIVATE_REASONING_SENTINEL_MUST_NOT_BE_EXPOSED",
    }]
    inputs = root / "inputs"
    source_path = inputs / "synthetic_source_notes.json"
    trace_path = inputs / "synthetic_player_run.json"
    notes_path = inputs / "synthetic_saved_notes.json"
    levels_path = inputs / "synthetic_level_data.json"
    _write_json(source_path, source_material)
    _write_json(trace_path, trace_material)
    _write_json(notes_path, saved_notes)
    _write_json(levels_path, {"provenance": PROVENANCE, "levels": level_wires})

    materials = []
    for path, role in (
        (source_path, "source"),
        (trace_path, "play_trace"),
        (notes_path, "saved_note"),
        (levels_path, "level_data"),
    ):
        materials.append({
            "role": role,
            "path": path.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "provenance": PROVENANCE,
        })
    manifest = {
        "schema_version": "arcagi-pipeline-manifest/v2",
        "pipeline_id": "synthetic-two-level-offline-demo",
        "game": {
            "id": GAME_ID,
            "version": GAME_VERSION,
            "plugin_id": PLUGIN_ID,
            "plugin_version": PLUGIN_VERSION,
            "extension": extension,
        },
        "materials": materials,
        "workflow": {"stage_order": list(STAGE_ORDER), "mode": "offline_synthetic_demo"},
        "provenance": {"kind": PROVENANCE, "model_calls": 0, "harness_execution": False},
    }
    manifest_path = root / "materials_manifest.json"
    _write_json(manifest_path, manifest)
    manifest_errors = validate_manifest(manifest_path)
    if manifest_errors:
        raise ValueError(f"synthetic manifest is invalid: {manifest_errors}")

    run = PipelineRun(
        root,
        "synthetic-two-level-offline-demo",
        {"id": GAME_ID, "version": GAME_VERSION, "provenance": PROVENANCE},
    )
    run.record_stage("materials", [manifest_path], details={"provenance": PROVENANCE})

    raw_run_record = json.loads(trace_path.read_text(encoding="utf-8"))
    packet = trajectory_analysis_packet(raw_run_record, saved_notes)
    ledger = EvidenceLedger(GAME_ID)
    ledger.add_trajectory(TrajectoryEntry(
        "trace-observation-01",
        "direct_observation",
        "Appending a token increased sequence length by one.",
        ("synthetic-run-001:step-1",),
        confidence=1.0,
    ))
    ledger.add_trajectory(TrajectoryEntry(
        "trace-inference-01",
        "agent_inference",
        "The sequence may retain token order after another append.",
        ("synthetic-run-001:step-2",),
        alternatives=("The latest token may replace the earlier token.",),
        confidence=0.7,
    ))
    ledger.add_trajectory(TrajectoryEntry(
        "saved-note-01",
        "saved_note",
        saved_notes[0]["text"],
        ("synthetic_saved_notes.json:0",),
    ))
    frozen_trajectory_digest = ledger.freeze_trajectory()
    trajectory_path = root / "trajectory_analysis.json"
    _write_json(trajectory_path, {
        "provenance": PROVENANCE,
        "source_context_provided": False,
        "packet": packet,
        "ledger": ledger.to_dict(),
        "frozen_trajectory_digest": frozen_trajectory_digest,
    })
    run.record_stage(
        "trajectory_analysis",
        [trajectory_path],
        details={"source_blind": True, "frozen_digest": frozen_trajectory_digest},
    )

    source_finding = SourceFinding(
        "source-finding-01",
        "The synthetic reference appends one supplied token per append action.",
        "inputs/synthetic_source_notes.json",
        GAME_VERSION,
        uncovered_by_play=True,
    )
    ledger.add_source_finding(source_finding)
    source_audit_path = root / "source_audit.json"
    _write_json(source_audit_path, {
        "provenance": PROVENANCE,
        "performed_after_trajectory_freeze": True,
        "trajectory_digest_before_audit": frozen_trajectory_digest,
        "finding": asdict(source_finding),
        "source_material_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
    })
    run.record_stage("source_audit", [source_audit_path], details={"trajectory_ledger_unchanged": True})

    probe = ProbeRecord(
        "probe-sequence-append",
        {"tokens": [first_token], "committed": False},
        {"kind": "append", "token": second_token},
        ("sequence length increases by one", "sequence remains unchanged"),
        "sequence length increases by one",
        "synthetic-engine/v1",
        replay_evidence="synthetic-engine:probe-sequence-append",
    )
    ledger.add_probe(probe)
    probes_path = root / "probe_records.json"
    _write_json(probes_path, {
        "provenance": PROVENANCE,
        "execution": "simulated_engine_probe",
        "records": [asdict(probe)],
        "trajectory_digest_after_probe": ledger.trajectory_digest(),
    })
    run.record_stage("probe_design", [probes_path], details={"probe_execution_is_synthetic": True})

    if ledger.trajectory_digest() != frozen_trajectory_digest:
        raise AssertionError("source audit or probes altered the frozen trajectory ledger")
    ledger_path = root / "evidence_ledger.json"
    _write_json(ledger_path, {"provenance": PROVENANCE, "ledger": ledger.to_dict()})
    review_path = root / "evidence_review.json"
    _write_json(review_path, {
        "provenance": PROVENANCE,
        "trajectory_digest": frozen_trajectory_digest,
        "trajectory_frozen_before_source_audit": ledger.trajectory_frozen,
        "source_and_probe_findings_kept_separate": True,
        "unresolved_alternatives": ["A later append may replace the previous token."],
        "review_status": "synthetic_demo_only",
    })
    run.record_stage("evidence_review", [ledger_path, review_path], details={"trajectory_digest_preserved": True})

    rule_contract_path = root / "rule_contract.json"
    _write_json(rule_contract_path, {
        "provenance": PROVENANCE,
        "game_id": GAME_ID,
        "game_version": GAME_VERSION,
        "rules": [
            {
                "rule_id": "append-token",
                "statement": "append adds the supplied token to the end of the sequence",
                "evidence_refs": ["trace-observation-01", "trace-inference-01", "source-finding-01", "probe-sequence-append"],
                "status": "synthetic_fixture_rule",
            },
            {
                "rule_id": "commit-target",
                "statement": "commit succeeds only when the sequence equals the level target",
                "evidence_refs": ["source-finding-01", "synthetic-level-data"],
                "status": "synthetic_fixture_rule",
            },
        ],
        "unknowns": [],
        "not_a_claim_about_real_agent_learning": True,
    })
    run.record_stage("rule_contract", [rule_contract_path], details={"reviewed_fixture_contract": True})

    course_slots = [
        CourseSlot("slot-01", ("append a two-token sequence",), (), ("lesson-01-short-sequence",),
                   "Introduce ordered entries and a terminal condition."),
        CourseSlot("slot-02", ("append a three-token sequence",), ("slot-01",),
                   ("lesson-02-long-sequence",),
                   "Increase the target length while retaining the same operation."),
    ]
    course_errors = validate_course(course_slots)
    if course_errors:
        raise ValueError(f"synthetic course is invalid: {course_errors}")
    course_design_path = root / "course_design.json"
    _write_json(course_design_path, {
        "provenance": PROVENANCE,
        "learning_gain_claimed": False,
        "slots": [asdict(slot) for slot in course_slots],
    })
    run.record_stage("course_design", [course_design_path], details={"course_is_a_fixture_hypothesis": True})

    plugin_errors = validate_plugin(plugin, extension, levels)
    if plugin_errors:
        raise ValueError(f"synthetic plugin rejected its levels: {plugin_errors}")
    plugin_path = root / "plugin_contract.json"
    _write_json(plugin_path, {
        "provenance": PROVENANCE,
        "plugin_id": PLUGIN_ID,
        "plugin_version": PLUGIN_VERSION,
        "state_shapes": {level.level_id: "SequenceState(tokens: tuple[str, ...], committed: bool)" for level in levels},
        "action_shapes": {level.level_id: "SequenceAction(kind, token)" for level in levels},
        "validation_errors": plugin_errors,
    })
    run.record_stage("plugin_implementation", [plugin_path], details={"plugin_validation": "passed"})

    campaign_path = root / "generated_campaign.json"
    _write_json(campaign_path, {
        "provenance": PROVENANCE,
        "seed": seed,
        "game": {"id": GAME_ID, "version": GAME_VERSION},
        "levels": level_wires,
    })
    run.record_stage("generation", [campaign_path], details={"seed": seed, "level_count": len(levels)})

    validation_rows = []
    for level in levels:
        result = exact_bfs(_domain_for(level))
        validation_rows.append({
            "level_id": level.level_id,
            "status": result.status,
            "actions": [_action_wire(action) for action in result.actions],
            "expanded_states": result.expanded_states,
            "reason": result.reason,
            "replay_accepted": result.replay_accepted,
        })
    if any(row["status"] != "solved" or row["replay_accepted"] is not True for row in validation_rows):
        raise ValueError("synthetic BFS and engine replay validation did not pass")
    validation_path = root / "validation_results.json"
    _write_json(validation_path, {
        "provenance": PROVENANCE,
        "all_levels_solved_and_replayed": True,
        "results": validation_rows,
    })
    run.record_stage("validation", [validation_path], details={"bfs_and_engine_replay": "passed"})

    organization_path = root / "course_organization.json"
    _write_json(organization_path, {
        "provenance": PROVENANCE,
        "ordered_slots": [
            {"slot_id": slot.slot_id, "level_id": slot.task_refs[0], "objectives": list(slot.objectives)}
            for slot in course_slots
        ],
        "sequence_valid": not course_errors,
    })
    run.record_stage("course_organization", [organization_path], details={"ordered_level_count": 2})

    spec = HarnessRunSpec(
        GAME_ID,
        Path("<EXPERIMENT_OUTPUT>"),
        {"execution_mode": "preview_only", "registered_game": False, "provenance": PROVENANCE},
    )
    spec_path = root / "harness_session.json"
    write_run_spec(spec_path, spec)
    adapter = DuckAdapter(Path("<DUCK_ROOT>"), python="<PYTHON>")
    command = adapter.preflight_command(spec)
    command_preview_path = root / "harness_command_preview.json"
    _write_json(command_preview_path, {
        "provenance": PROVENANCE,
        "backend": adapter.adapter_id,
        "command": list(command.command),
        "working_directory": "<DUCK_ROOT>/ARC3-Inference",
        "environment_overrides": {"PYTHONPATH": "<DUCK_ROOT>/ARC3-Inference"},
        "preview_only": True,
        "execution_started": False,
        "runnable": False,
        "reason": "The synthetic game is not registered with Duck; this is an interface-shape preview only.",
    })
    run.record_stage(
        "harness_export",
        [spec_path, command_preview_path],
        details={"execution_started": False, "preview_only": True, "registered_game": False},
    )

    if [row["name"] for row in run.data["stages"]] != list(STAGE_ORDER):
        raise AssertionError("the demo did not complete the declared stage order")
    return {
        "pipeline_id": "synthetic-two-level-offline-demo",
        "game_id": GAME_ID,
        "seed": seed,
        "stage_order": list(STAGE_ORDER),
        "completed_stages": len(run.data["stages"]),
        "validation": validation_rows,
        "trajectory_digest": frozen_trajectory_digest,
        "output_dir": str(root.resolve()),
        "model_calls": 0,
        "harness_execution": False,
    }
