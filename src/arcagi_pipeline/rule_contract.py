from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

from .game_provenance import _tree_digest
from .legacy_evidence import (
    BINDING_SCHEMA,
    sha256_file,
    read_json,
    write_new_json,
)

CONTRACT_SCHEMA = "arcagi-rule-contract/v1"
RESULT_SCHEMA = "arcagi-rule-contract-result/v1"
PROBE_SUITE_SCHEMA = "arcagi-engine-probe-suite/v1"
SHA256_CHARS = set("0123456789abcdef")


def _valid_sha(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= SHA256_CHARS




def validate_frozen_contract_inputs(contract: dict[str, Any]) -> list[dict[str, str]]:
    """Recheck every frozen input path/hash without interpreting evidence semantics."""
    inputs = contract.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        raise ValueError("frozen rule contract must contain a non-empty inputs list")
    validated: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(inputs):
        if not isinstance(item, dict):
            raise ValueError(f"frozen contract inputs[{index}] must be an object")
        role, raw_path, expected = item.get("role"), item.get("path"), item.get("sha256")
        if not isinstance(role, str) or not role.strip() or not isinstance(raw_path, str) or not raw_path.strip() or not _valid_sha(expected):
            raise ValueError(f"frozen contract inputs[{index}] has missing role/path/hash")
        path = Path(raw_path).expanduser().resolve(strict=True)
        actual = sha256_file(path)
        if actual != expected:
            raise ValueError(f"frozen contract input hash changed: {path}")
        key = (role, str(path))
        if key in seen:
            raise ValueError(f"frozen contract contains a duplicate input reference: {path}")
        seen.add(key)
        validated.append({"role": role, "path": str(path), "sha256": actual})
    return validated

def _validate_game_id(value: Any) -> None:
    if not isinstance(value, str) or not value.strip() or "/" in value or "\\" in value:
        raise ValueError("rule contract game_id must be a non-empty game identifier")


def _load_binding(path_arg: Path) -> tuple[dict[str, Any], str]:
    path = path_arg.expanduser().resolve(strict=True)
    data = read_json(path, "source binding")
    if not isinstance(data, dict) or data.get("schema_version") != BINDING_SCHEMA:
        raise ValueError(f"source binding schema_version must be {BINDING_SCHEMA}")
    source, runtime = data.get("source"), data.get("runtime")
    if data.get("status") != "verified" or not isinstance(source, dict) or source.get("status") != "verified":
        raise ValueError("rule contract requires a verified game source tree")
    if not isinstance(runtime, dict) or runtime.get("status") != "verified":
        raise ValueError("rule contract requires verified engine/runtime dependencies")
    if not _valid_sha(source.get("source_tree_sha256")):
        raise ValueError("source binding has no valid source_tree_sha256")
    source_files = source.get("source_files")
    if not isinstance(source_files, list) or not source_files:
        raise ValueError("source binding has no source file hash inventory")
    if _tree_digest(source_files) != source["source_tree_sha256"]:
        raise ValueError("source binding tree hash does not match its file inventory")
    inventory = runtime.get("dependency_inventory")
    inventory_sha = runtime.get("dependency_inventory_sha256")
    if not isinstance(inventory, list) or not _valid_sha(inventory_sha):
        raise ValueError("runtime binding has no dependency inventory hash")
    inventory_wire = json.dumps(inventory, sort_keys=True, separators=(",", ":"))
    if hashlib.sha256(inventory_wire.encode("utf-8")).hexdigest() != inventory_sha:
        raise ValueError("runtime dependency inventory hash does not match its inventory")
    for item in source_files:
        relative = item.get("path") if isinstance(item, dict) else None
        expected = item.get("sha256") if isinstance(item, dict) else None
        root = Path(source["source_root"]).resolve()
        if not isinstance(relative, str) or not _valid_sha(expected):
            raise ValueError("source binding contains an invalid source file reference")
        file_path = (root / relative).resolve(strict=True)
        try:
            file_path.relative_to(root)
        except ValueError as exc:
            raise ValueError("source binding file escapes source root") from exc
        if sha256_file(file_path) != expected:
            raise ValueError(f"source file changed since binding: {relative}")
    return data, sha256_file(path)


def _add_ids(
    *, payload: dict[str, Any], role: str, game_id: str, game_version: str, source_sha256: str
) -> set[str]:
    if payload.get("game_id") != game_id:
        raise ValueError(f"{role} game_id does not match rule contract")
    if role != "trajectory_ledger":
        if str(payload.get("game_version")) != game_version or payload.get("source_sha256") != source_sha256:
            raise ValueError(f"{role} version/source binding does not match rule contract")
    if role == "trajectory_ledger":
        if payload.get("schema_version") != "arcagi-trajectory-ledger/v1" or payload.get("trajectory_frozen") is not True:
            raise ValueError("trajectory ledger must be frozen")
        rows, id_key = payload.get("claims"), "claim_id"
    elif role == "source_findings":
        if payload.get("schema_version") != "arcagi-source-audit-findings/v1":
            raise ValueError("source findings schema is unsupported")
        rows, id_key = payload.get("findings"), "finding_id"
    elif role == "probe_suite":
        if payload.get("schema_version") != PROBE_SUITE_SCHEMA:
            raise ValueError(f"probe suite schema must be {PROBE_SUITE_SCHEMA}")
        rows, id_key = payload.get("cases"), "case_id"
    else:
        raise AssertionError(role)
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{role} must contain at least one evidence record")
    ids: set[str] = set()
    for row in rows:
        value = row.get(id_key) if isinstance(row, dict) else None
        if not isinstance(value, str) or not value.strip() or value in ids:
            raise ValueError(f"{role} contains a missing or duplicate {id_key}")
        ids.add(value)
    return ids


def freeze_rule_contract(
    *,
    contract_path: Path,
    source_binding_path: Path,
    output_path: Path,
    trajectory_ledger_path: Path | Sequence[Path] | None = None,
    source_findings_path: Path | None = None,
    probe_suite_path: Path | None = None,
    supporting_material_paths: Sequence[tuple[str, Path]] = (),
) -> dict[str, Any]:
    """Structure/provenance gate for a versioned rule contract; it does not infer correctness."""
    contract_path = contract_path.expanduser().resolve(strict=True)
    output_path = output_path.expanduser().resolve()
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite frozen rule contract: {output_path}")
    contract = read_json(contract_path, "rule contract")
    if not isinstance(contract, dict) or contract.get("schema_version") != CONTRACT_SCHEMA:
        raise ValueError(f"contract schema_version must be {CONTRACT_SCHEMA}")
    game_id = contract.get("game_id")
    _validate_game_id(game_id)
    version = contract.get("game_version")
    if not isinstance(version, (str, int, float)) or not str(version).strip():
        raise ValueError("contract game_version is required")
    version = str(version)
    binding, binding_sha = _load_binding(source_binding_path)
    if binding.get("game_id") != game_id or str(binding.get("game_version")) != version:
        raise ValueError("contract game identity/version does not match source binding")
    source_sha = binding["source"]["source_tree_sha256"]
    if contract.get("source_sha256") != source_sha:
        raise ValueError("contract source_sha256 does not match verified source tree")

    ledger_paths: list[Path] = []
    if isinstance(trajectory_ledger_path, Path):
        ledger_paths = [trajectory_ledger_path]
    elif trajectory_ledger_path is not None:
        ledger_paths = list(trajectory_ledger_path)
    input_paths = [
        *(("trajectory_ledger", path) for path in ledger_paths),
        ("source_findings", source_findings_path),
        ("probe_suite", probe_suite_path),
    ]
    evidence_ids: set[str] = set()
    evidence_sources: list[dict[str, str]] = []
    for role, path_arg in input_paths:
        if path_arg is None:
            continue
        path = path_arg.expanduser().resolve(strict=True)
        payload = read_json(path, role)
        if not isinstance(payload, dict):
            raise ValueError(f"{role} input must be a JSON object")
        current_ids = _add_ids(
            payload=payload, role=role, game_id=game_id, game_version=version, source_sha256=source_sha
        )
        if evidence_ids.intersection(current_ids):
            raise ValueError("evidence IDs must be unique across trajectory, source, and probe inputs")
        evidence_ids.update(current_ids)
        evidence_sources.append({"role": role, "path": str(path), "sha256": sha256_file(path)})

    rules = contract.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ValueError("rules must be a non-empty list")
    seen_rules: set[str] = set()
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise ValueError(f"rules[{index}] must be an object")
        rule_id = rule.get("rule_id")
        if not isinstance(rule_id, str) or not rule_id.strip() or rule_id in seen_rules:
            raise ValueError(f"rules[{index}] rule_id is missing or duplicated")
        seen_rules.add(rule_id)
        for key in ("statement", "condition"):
            if not isinstance(rule.get(key), str) or not rule[key].strip():
                raise ValueError(f"rules[{index}] {key} is required")
        for key in ("state_changes", "boundary_cases", "reachable_interactions", "evidence_refs"):
            value = rule.get(key)
            if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
                raise ValueError(f"rules[{index}] {key} must be a list of non-empty strings")
        status = rule.get("status")
        if status not in {"supported", "conflict", "unknown"}:
            raise ValueError(f"rules[{index}] status must be supported, conflict, or unknown")
        if status in {"supported", "conflict"} and not rule["evidence_refs"]:
            raise ValueError(f"rules[{index}] {status} rule requires evidence_refs")
        missing = [ref for ref in rule["evidence_refs"] if ref not in evidence_ids]
        if missing:
            raise ValueError(f"rules[{index}] cites absent evidence IDs: {missing}")

    inputs = [
        {"role": "contract_input", "path": str(contract_path), "sha256": sha256_file(contract_path)},
        {"role": "source_binding", "path": str(source_binding_path.resolve()), "sha256": binding_sha},
        *evidence_sources,
    ]
    for role, path in supporting_material_paths:
        if not isinstance(role, str) or not role.strip():
            raise ValueError("supporting material role is required")
        path = path.expanduser().resolve(strict=True)
        inputs.append({"role": role, "path": str(path), "sha256": sha256_file(path)})

    result = {
        "schema_version": RESULT_SCHEMA,
        "game_id": game_id,
        "game_version": version,
        "source_sha256": source_sha,
        "runtime_binding_sha256": binding["runtime"].get("dependency_inventory_sha256"),
        "source_binding": {"path": str(source_binding_path.resolve()), "sha256": binding_sha},
        "frozen": True,
        "validation_scope": "structure_and_provenance_only",
        "semantic_correctness_requires_independent_evidence_review": True,
        "inputs": inputs,
        "rules": rules,
    }
    write_new_json(output_path, result)
    return {
        "frozen": True, "path": str(output_path), "sha256": sha256_file(output_path),
        "game_id": game_id, "game_version": version, "source_sha256": source_sha,
        "rule_count": len(rules),
    }


# Compatibility name retained for the first draft of this module.
import_rule_contract = freeze_rule_contract
