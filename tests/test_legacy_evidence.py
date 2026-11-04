from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from arcagi_pipeline.legacy_evidence import expand_trajectory_observations, freeze_legacy_claims, import_legacy_run
from arcagi_pipeline.cli import main
from arcagi_pipeline.game_provenance import _tree_digest
from arcagi_pipeline.rule_contract import freeze_rule_contract


def dump(path: Path, value) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def test_tycho_import_excludes_reasoning_and_freeze_requires_saved_note(tmp_path: Path) -> None:
    record = dump(tmp_path / "game_gridgame.json", {
        "game_id": "gridgame-synthetic-v1", "levels_completed": 2, "n_levels": 4, "partial": False,
        "trace": [
            {"turn": 1, "frame": [[0, 1], [2, 3]], "action": "ACTION1", "reasoning": "SECRET_REASONING_SENTINEL", "state": {"internal": 9}},
            {"turn": 2, "frame": [[0, 1], [2, 4]], "action": "ACTION2", "reasoning": "SECOND_SECRET"},
        ],
    })
    manifest = dump(tmp_path / "manifest.json", {"games": ["gridgame"], "source_sha256": None})
    packet_path = tmp_path / "packet.json"
    imported = import_legacy_run(
        backend="tycho", run_id="historic-tycho", expected_game_id="gridgame-synthetic-v1",
        artifact_paths=[record, manifest], output_path=packet_path,
    )
    packet_text = packet_path.read_text(encoding="utf-8")
    packet = json.loads(packet_text)
    assert imported["trace_steps"] == 2
    assert "SECRET_REASONING" not in packet_text and '"state"' not in packet_text
    assert packet["source_binding"]["status"] == "unbound"
    assert packet["coverage"]["saved_notes"] == 0
    assert expand_trajectory_observations(packet)["trace"][0]["observation"] == [[0, 1], [2, 3]]
    assert packet["trace"][0]["action"]["name"] == "ACTION1"

    claims = dump(tmp_path / "claims.json", {
        "schema_version": "arcagi-trajectory-claims/v1", "run_id": "historic-tycho",
        "game_id": "gridgame-synthetic-v1", "packet_sha256": hashlib.sha256(packet_path.read_bytes()).hexdigest(),
        "claims": [{
            "claim_id": "claim-1", "statement": "A post-run hypothesis.", "kind": "post_run_hypothesis",
            "evidence_refs": ["ev-000001"], "alternatives": ["An alternative."],
            "prediction": "A distinguishing prediction.", "confidence": 0.3,
        }],
    })
    ledger_path = tmp_path / "ledger.json"
    result = freeze_legacy_claims(packet_path=packet_path, claims_path=claims, output_path=ledger_path)
    assert result["frozen"] is True
    assert json.loads(ledger_path.read_text(encoding="utf-8"))["source_binding"]["status"] == "unbound"
    with pytest.raises(FileExistsError):
        freeze_legacy_claims(packet_path=packet_path, claims_path=claims, output_path=ledger_path)

    claims_bad = dump(tmp_path / "claims_bad.json", {
        "schema_version": "arcagi-trajectory-claims/v1", "run_id": "historic-tycho",
        "game_id": "gridgame-synthetic-v1", "packet_sha256": hashlib.sha256(packet_path.read_bytes()).hexdigest(),
        "claims": [{
            "claim_id": "claim-1", "statement": "Pretend original inference.", "kind": "gameplay_recorded_inference",
            "evidence_refs": ["ev-000001"], "alternatives": [], "prediction": None,
        }],
    })
    with pytest.raises(ValueError, match="original saved note"):
        freeze_legacy_claims(packet_path=packet_path, claims_path=claims_bad, output_path=tmp_path / "bad-ledger.json")


def test_tycho_import_preserves_explicit_json_saved_notes(tmp_path: Path) -> None:
    record = dump(tmp_path / "game_gridgame.json", {
        "game_id": "gridgame-synthetic-v1", "levels_completed": 1, "n_levels": 1,
        "partial": False, "trace": [{"turn": 1, "frame": [[0]], "action": "ACTION1", "reasoning": "PRIVATE"}],
    })
    manifest = dump(tmp_path / "manifest.json", {"games": ["gridgame"]})
    note_text = '{\n  "rule_hypothesis": "only the explicitly saved note",\n  "confidence": 0.4\n}\n'
    note = tmp_path / "saved_memory.json"
    note.write_text(note_text, encoding="utf-8")
    output = tmp_path / "packet.json"
    import_legacy_run(
        backend="tycho", run_id="historic-tycho-json-note", expected_game_id="gridgame-synthetic-v1",
        artifact_paths=[record, manifest], saved_note_paths=[note], output_path=output,
    )
    packet_text = output.read_text(encoding="utf-8")
    packet = json.loads(packet_text)
    saved = packet["saved_notes"][0]
    assert set(saved) == {"evidence_id", "text", "source_ref"}
    assert saved["text"] == note_text
    from arcagi_pipeline.agent_workflow import _validate_trajectory_packet
    _validate_trajectory_packet(output)
    assert saved["source_ref"]["sha256"] == hashlib.sha256(note.read_bytes()).hexdigest()
    assert packet["coverage"]["saved_notes"] == 1
    assert "PRIVATE" not in packet_text

def test_duck_import_skips_analysis_lines_and_checks_game_id(tmp_path: Path) -> None:
    events = tmp_path / "gridgame-synthetic-v1_p0_events.jsonl"
    events.write_text(
        '{"type":"initial","board":[[1,2]],"action_display":"RESET"}\n'
        '{"type":"analysis","analysis_text":"DUCK_TRANSCRIPT_SENTINEL"}\n'
        '{"type":"action","board":[[1,3]],"action_display":"RIGHT","reward":0,"board_changed":true}\n',
        encoding="utf-8",
    )
    config = dump(tmp_path / "run_config.json", {"games": ["gridgame-synthetic-v1"]})
    viewer = dump(tmp_path / "gridgame-synthetic-v1_p0_viewer_data.json", {
        "game_id": "gridgame-synthetic-v1", "status": "partial", "levels_completed": 1,
    })
    packet_path = tmp_path / "duck-packet.json"
    import_legacy_run(
        backend="duck", run_id="historic-duck", expected_game_id="gridgame-synthetic-v1",
        artifact_paths=[events, config, viewer], output_path=packet_path,
    )
    packet_text = packet_path.read_text(encoding="utf-8")
    packet = json.loads(packet_text)
    assert "DUCK_TRANSCRIPT_SENTINEL" not in packet_text
    assert packet["excluded_material"]["event_types"] == {"analysis": 1}
    assert expand_trajectory_observations(packet)["trace"][1]["observation"] == [[1, 3]]; assert packet["trace"][1]["action"]["name"] == "RIGHT"
    assert packet["outcome_evidence"][0]["result"]["status"] == "partial"

    wrong = dump(tmp_path / "wrong-run" / "run_config.json", {"games": ["sequencegame-synthetic-v2"]})
    with pytest.raises(ValueError, match="run_config"):
        import_legacy_run(
            backend="duck", run_id="wrong", expected_game_id="gridgame-synthetic-v1",
            artifact_paths=[events, wrong], output_path=tmp_path / "wrong-packet.json",
        )
    transcript = tmp_path / "transcript.json"
    transcript.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="transcript"):
        import_legacy_run(
            backend="duck", run_id="bad", expected_game_id="gridgame-synthetic-v1",
            artifact_paths=[events, config, transcript], output_path=tmp_path / "bad-packet.json",
        )


def _source_binding(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "game-source"
    root.mkdir()
    files = []
    for name, value in (("metadata.json", '{"game_id":"gridgame-synthetic-v1"}'), ("gridgame.py", "class GridGame: pass\n")):
        path = root / name
        path.write_text(value, encoding="utf-8")
        files.append({"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    source_sha = _tree_digest(files)
    binding = dump(tmp_path / "binding.json", {
        "schema_version": "arcagi-game-source-binding/v1", "game_id": "gridgame-synthetic-v1",
        "game_version": "synthetic-v1", "status": "verified",
        "source": {"status": "verified", "source_root": str(root), "source_tree_sha256": source_sha, "source_files": files},
        "runtime": {"status": "verified", "dependency_inventory": [], "dependency_inventory_sha256": hashlib.sha256(b"[]").hexdigest()},
    })
    return binding, source_sha


def test_rule_contract_freeze_binds_inputs_and_accepts_other_game_ids(tmp_path: Path) -> None:
    binding, source_sha = _source_binding(tmp_path)
    game_id, version = "gridgame-synthetic-v1", "synthetic-v1"
    ledger = dump(tmp_path / "ledger.json", {
        "schema_version": "arcagi-trajectory-ledger/v1", "game_id": game_id, "trajectory_frozen": True,
        "claims": [{"claim_id": "claim-1"}],
    })
    findings = dump(tmp_path / "findings.json", {
        "schema_version": "arcagi-source-audit-findings/v1", "game_id": game_id,
        "game_version": version, "source_sha256": source_sha,
        "findings": [{"finding_id": "src-1"}],
    })
    probes = dump(tmp_path / "probes.json", {
        "schema_version": "arcagi-engine-probe-suite/v1", "game_id": game_id,
        "game_version": version, "source_sha256": source_sha,
        "cases": [{"case_id": "probe-1", "status": "pass"}],
    })
    contract = dump(tmp_path / "contract.json", {
        "schema_version": "arcagi-rule-contract/v1", "game_id": game_id,
        "game_version": version, "source_sha256": source_sha,
        "rules": [{
            "rule_id": "sequence-append", "statement": "Appending a token adds it to the end of a sequence.",
            "condition": "An APPEND action supplies one token.",
            "state_changes": ["sequence length increases by one while preserving order"],
            "boundary_cases": ["an empty sequence accepts an append"],
            "reachable_interactions": ["removing the first token later reveals the next stored token"],
            "status": "supported", "evidence_refs": ["claim-1", "src-1", "probe-1"],
        }],
    })
    result = freeze_rule_contract(
        contract_path=contract, source_binding_path=binding, trajectory_ledger_path=ledger,
        source_findings_path=findings, probe_suite_path=probes, output_path=tmp_path / "frozen.json",
    )
    frozen = json.loads((tmp_path / "frozen.json").read_text(encoding="utf-8"))
    assert result["frozen"]
    assert frozen["inputs"][1]["role"] == "source_binding"
    with pytest.raises(FileExistsError):
        freeze_rule_contract(
            contract_path=contract, source_binding_path=binding, trajectory_ledger_path=ledger,
            source_findings_path=findings, probe_suite_path=probes, output_path=tmp_path / "frozen.json",
        )


def test_contract_rejects_unbound_or_unreferenced_evidence_and_changed_source(tmp_path: Path) -> None:
    binding, source_sha = _source_binding(tmp_path)
    contract = dump(tmp_path / "contract.json", {
        "schema_version": "arcagi-rule-contract/v1", "game_id": "gridgame-synthetic-v1",
        "game_version": "synthetic-v1", "source_sha256": source_sha,
        "rules": [{
            "rule_id": "r", "statement": "x", "condition": "c", "state_changes": [],
            "boundary_cases": [], "reachable_interactions": [], "status": "supported",
            "evidence_refs": ["missing"],
        }],
    })
    ledger = dump(tmp_path / "ledger.json", {
        "schema_version": "arcagi-trajectory-ledger/v1", "game_id": "gridgame-synthetic-v1",
        "trajectory_frozen": True, "claims": [{"claim_id": "claim-1"}],
    })
    with pytest.raises(ValueError, match="absent evidence"):
        freeze_rule_contract(
            contract_path=contract, source_binding_path=binding, trajectory_ledger_path=ledger,
            output_path=tmp_path / "out.json",
        )
    (tmp_path / "game-source" / "gridgame.py").write_text("changed\n", encoding="utf-8")
    contract_data = json.loads(contract.read_text(encoding="utf-8")); contract_data["rules"][0]["status"] = "unknown"
    contract_data["rules"][0]["evidence_refs"] = []
    dump(tmp_path / "contract.json", contract_data)
    with pytest.raises(ValueError, match="changed since binding"):
        freeze_rule_contract(
            contract_path=tmp_path / "contract.json", source_binding_path=binding,
            output_path=tmp_path / "changed-out.json",
        )

def test_cli_legacy_import_command(tmp_path: Path, capsys) -> None:
    record = dump(tmp_path / "game_gridgame.json", {
        "game_id": "gridgame-synthetic-v1",
        "trace": [{"turn": 1, "frame": [[0]], "action": "ACTION1", "reasoning": "must not leak"}],
    })
    output = tmp_path / "cli-packet.json"
    status = main([
        "legacy-trajectory-import", "--backend", "tycho", "--run-id", "cli-run",
        "--game-id", "gridgame-synthetic-v1", "--artifact", str(record),
        "--output", str(output),
    ])
    assert status == 0
    assert json.loads(capsys.readouterr().out)["imported"] is True
    assert "must not leak" not in output.read_text(encoding="utf-8")
def test_rule_contract_core_does_not_hardcode_arcagi_game_ids(tmp_path: Path) -> None:
    binding, source_sha = _source_binding(tmp_path)
    binding_data = json.loads(binding.read_text(encoding="utf-8"))
    game_id, version = "puzzle-alpha-r7", "r7"
    source_root = Path(binding_data["source"]["source_root"])
    metadata = source_root / "metadata.json"
    metadata.write_text('{"game_id":"puzzle-alpha-r7"}', encoding="utf-8")
    binding_data["game_id"] = game_id
    binding_data["game_version"] = version
    for item in binding_data["source"]["source_files"]:
        item_path = source_root / item["path"]
        item["sha256"] = hashlib.sha256(item_path.read_bytes()).hexdigest()
    binding_data["source"]["source_tree_sha256"] = _tree_digest(binding_data["source"]["source_files"])
    source_sha = binding_data["source"]["source_tree_sha256"]
    dump(binding, binding_data)
    contract = dump(tmp_path / "generic-contract.json", {
        "schema_version": "arcagi-rule-contract/v1", "game_id": game_id,
        "game_version": version, "source_sha256": source_sha,
        "rules": [{
            "rule_id": "unresolved", "statement": "Mechanic is not yet established.",
            "condition": "unknown", "state_changes": [], "boundary_cases": [],
            "reachable_interactions": [], "status": "unknown", "evidence_refs": [],
        }],
    })
    result = freeze_rule_contract(
        contract_path=contract, source_binding_path=binding,
        output_path=tmp_path / "generic-frozen.json",
    )
    assert result["game_id"] == game_id

def test_legacy_relative_source_refs_are_bundle_local_and_freezable(tmp_path: Path) -> None:
    from arcagi_pipeline.agent_workflow import WorkflowError, _validate_trajectory_packet

    bundle = tmp_path / "bundle"
    source_dir = bundle / "source_artifacts"
    packet_dir = bundle / "trajectories"
    record = dump(source_dir / "game_gridgame.json", {
        "game_id": "gridgame-synthetic-v1", "levels_completed": 1, "n_levels": 1,
        "partial": False, "trace": [{"turn": 1, "frame": [[0]], "action": "ACTION1"}],
    })
    manifest = dump(source_dir / "manifest.json", {"games": ["gridgame"]})
    note = source_dir / "saved_note.json"
    note.write_text('{"saved": true}\n', encoding="utf-8")
    packet_path = packet_dir / "packet.json"
    import_legacy_run(
        backend="tycho", run_id="historic-relative", expected_game_id="gridgame-synthetic-v1",
        artifact_paths=[record, manifest], saved_note_paths=[note], output_path=packet_path,
    )
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    assert all(not Path(ref["path"]).is_absolute() for ref in packet["source_artifacts"])
    assert all(not Path(row["source_ref"]["path"]).is_absolute() for row in packet["trace"])
    assert all(not Path(row["source_ref"]["path"]).is_absolute() for row in packet["saved_notes"])
    assert all(not Path(row["source_ref"]["path"]).is_absolute() for row in packet["outcome_evidence"])

    _validate_trajectory_packet(packet_path)
    claims = dump(bundle / "claims.json", {
        "schema_version": "arcagi-trajectory-claims/v1", "run_id": "historic-relative",
        "game_id": "gridgame-synthetic-v1", "packet_sha256": hashlib.sha256(packet_path.read_bytes()).hexdigest(),
        "claims": [{
            "claim_id": "claim-relative", "statement": "A post-run hypothesis.", "kind": "post_run_hypothesis",
            "evidence_refs": ["ev-000001"], "alternatives": ["An alternative."],
            "prediction": "A distinguishing prediction.", "confidence": 0.4,
        }],
    })
    result = freeze_legacy_claims(packet_path=packet_path, claims_path=claims, output_path=bundle / "ledger.json")
    assert result["frozen"] is True

    escaped = dict(packet)
    escaped["source_artifacts"] = [dict(ref) for ref in packet["source_artifacts"]]
    escaped["source_artifacts"][0]["path"] = "../../outside.json"
    bad_packet = packet_dir / "escaped.json"
    bad_packet.write_text(json.dumps(escaped), encoding="utf-8")
    with pytest.raises(WorkflowError, match="escapes"):
        _validate_trajectory_packet(bad_packet)

    absolute = json.loads(packet_path.read_text(encoding="utf-8"))
    absolute["source_artifacts"][0]["path"] = str(record.resolve())
    absolute_packet = packet_dir / "absolute.json"
    absolute_packet.write_text(json.dumps(absolute), encoding="utf-8")
    with pytest.raises(WorkflowError, match="must be relative"):
        _validate_trajectory_packet(absolute_packet)
    with pytest.raises(ValueError, match="must be relative"):
        freeze_legacy_claims(packet_path=absolute_packet, claims_path=claims, output_path=bundle / "absolute-ledger.json")


def test_legacy_import_rejects_source_files_outside_input_bundle(tmp_path: Path) -> None:
    outside = tmp_path / "external-source"
    record = dump(outside / "game_gridgame.json", {
        "game_id": "gridgame-synthetic-v1", "levels_completed": 1, "n_levels": 1,
        "partial": False, "trace": [{"turn": 1, "frame": [[0]], "action": "ACTION1"}],
    })
    bundle_packet = tmp_path / "bundle" / "trajectories" / "packet.json"
    with pytest.raises(ValueError, match="staged inside the trajectory input bundle"):
        import_legacy_run(
            backend="tycho", run_id="external-source", expected_game_id="gridgame-synthetic-v1",
            artifact_paths=[record], output_path=bundle_packet,
        )
    assert not bundle_packet.exists()
