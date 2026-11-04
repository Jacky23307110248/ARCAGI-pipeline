from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .duck_sessions import inspect_duck_environment
from .game_provenance import inspect_runtime_packages, inspect_tycho_environment
from .harness_sessions import (
    _git_source_provenance,
    _load_session,
    sha256_file,
)
from .trajectory_handoff import (
    PACKET_SCHEMA,
    _canonical_digest,
    _load_index,
    _load_json,
    _write_new_json,
)


TASK_SCHEMA = "arcagi-source-audit-task/v1"
FINDINGS_SCHEMA = "arcagi-source-audit-findings/v1"
FINDINGS_OUTPUT_SCHEMA = "arcagi-source-findings/v1"
_RELATIONS = {"confirms", "extends", "conflicts", "not_covered", "unknown"}


def _source_root_and_materials(
    session: dict[str, Any],
) -> tuple[Path, dict[str, Any], list[dict[str, Any]]]:
    backend = session.get("backend")
    if backend not in {"tycho", "duck"}:
        raise ValueError(f"unsupported harness backend for source audit: {backend}")
    source = session.get(backend)
    if not isinstance(source, dict):
        raise ValueError("harness session has no backend source record")
    harness_provenance = source.get("source_provenance")
    current_harness = _git_source_provenance(Path(source.get("root", "")), backend=backend)
    if (
        not isinstance(harness_provenance, dict)
        or harness_provenance.get("status") != "verified"
        or current_harness != harness_provenance
    ):
        raise ValueError("harness source is not verified or changed since the run")

    environment = source.get("environment_provenance")
    if not isinstance(environment, dict) or environment.get("status") != "verified":
        raise ValueError(
            "game source version is unbound; prepare a new run with a verified game environment"
        )
    try:
        if backend == "tycho":
            current_environment = inspect_tycho_environment(
                Path(source["root"]),
                session["game"]["id"],
                source_dir=Path(environment["source_root"]),
            )
            source_root = Path(environment["source_root"]).expanduser().resolve()
        else:
            current_environment = inspect_duck_environment(
                Path(source["environments_dir"]), session["game"]["id"]
            )
            source_root = Path(environment["source_dir"]).expanduser().resolve()
    except (KeyError, OSError, ValueError) as exc:
        raise ValueError("game source or level data cannot be reverified") from exc
    if current_environment != environment:
        raise ValueError("game source or level data changed since harness-prepare")

    runtime = source.get("runtime_provenance")
    if not isinstance(runtime, dict) or runtime.get("status") != "verified":
        raise ValueError("engine/runtime package versions are unbound; source audit is not certifiable")
    current_runtime = inspect_runtime_packages(str(source.get("python") or ""), backend)
    if current_runtime != runtime:
        raise ValueError("engine/runtime packages changed since harness-prepare")

    raw_files = environment.get("source_files")
    if not isinstance(raw_files, list) or not raw_files:
        raise ValueError("verified game environment has no source file manifest")
    materials: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for number, row in enumerate(raw_files, start=1):
        if not isinstance(row, dict):
            raise ValueError("game environment source file manifest is invalid")
        relative = row.get("path")
        expected = row.get("sha256")
        if not isinstance(relative, str) or not relative or not _valid_sha(expected):
            raise ValueError("game environment source file reference is invalid")
        if relative in seen_paths:
            raise ValueError(f"duplicate game source file in manifest: {relative}")
        seen_paths.add(relative)
        unresolved_path = source_root / Path(relative)
        if unresolved_path.is_symlink():
            raise ValueError(f"symlinks are not allowed in source materials: {relative}")
        path = unresolved_path.resolve()
        try:
            path.relative_to(source_root)
        except ValueError as exc:
            raise ValueError(f"game source path escapes its root: {relative}") from exc
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError(f"game source material is missing or unsafe: {relative}")
        actual = sha256_file(path)
        if actual != expected:
            raise ValueError(f"game source SHA-256 mismatch: {relative}")
        material = {
            "material_id": f"material-{number:04d}",
            "path": Path(relative).as_posix(),
            "sha256": actual,
            "kind": _material_kind(relative),
            "source_version": str(environment.get("version") or session["game"].get("version") or "unknown"),
        }
        if Path(relative).suffix.lower() in {".py", ".pyi", ".json", ".yaml", ".yml", ".csv", ".txt", ".md"}:
            try:
                material["line_count"] = len(path.read_text(encoding="utf-8").splitlines())
            except (UnicodeDecodeError, OSError):
                pass
        materials.append(material)
    return source_root, environment, materials


def _material_kind(relative: str) -> str:
    path = Path(relative)
    name = path.name.lower()
    if name == "metadata.json":
        return "metadata"
    if path.suffix.lower() in {".py", ".pyi"}:
        return "game_source"
    if "level" in str(path).lower() or path.suffix.lower() in {".json", ".yaml", ".yml", ".csv"}:
        return "level_or_rule_data"
    return "game_asset"


def _valid_sha(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _load_frozen_trajectory(
    run_id: str,
    run_dir: Path,
    session: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    if session.get("status") not in {"completed", "failed", "interrupted"}:
        raise ValueError("source audit requires a completed, failed, or interrupted harness run")
    ledger_path = run_dir / "analysis" / "trajectory_ledger.json"
    if not ledger_path.is_file():
        raise FileNotFoundError("freeze the trajectory ledger before exporting a source audit task")
    packet_path = run_dir / "analysis" / "trajectory_packet.json"
    if not packet_path.is_file():
        raise FileNotFoundError("source audit requires the exported trajectory packet")
    ledger = _load_json(ledger_path, "trajectory ledger")
    packet = _load_json(packet_path, "trajectory packet")
    if (
        not isinstance(ledger, dict)
        or ledger.get("schema_version") != "arcagi-trajectory-ledger/v1"
        or ledger.get("trajectory_frozen") is not True
        or ledger.get("run_id") != run_id
        or ledger.get("game_id") != session.get("game", {}).get("id")
    ):
        raise ValueError("trajectory ledger is not frozen for this harness run")
    if (
        not isinstance(packet, dict)
        or packet.get("schema_version") != PACKET_SCHEMA
        or packet.get("run_id") != run_id
        or packet.get("backend") != session.get("backend")
        or not isinstance(packet.get("game"), dict)
        or packet.get("game", {}).get("id") != session.get("game", {}).get("id")
        or packet.get("run_status") != session.get("status")
    ):
        raise ValueError("trajectory packet does not match the harness session")

    packet_sha = sha256_file(packet_path)
    ledger_packet = ledger.get("packet")
    if (
        not isinstance(ledger_packet, dict)
        or ledger_packet.get("path") != "trajectory_packet.json"
        or ledger_packet.get("sha256") != packet_sha
    ):
        raise ValueError("frozen trajectory ledger no longer matches its packet")
    claims = ledger.get("claims")
    claims_input = ledger.get("claims_input")
    if not isinstance(claims, list) or not claims or not isinstance(claims_input, dict):
        raise ValueError("frozen trajectory ledger has invalid claims or input provenance")
    if not _valid_sha(claims_input.get("sha256")):
        raise ValueError("frozen trajectory ledger has an invalid claims input hash")
    expected_digest = _canonical_digest({
        "packet_sha256": packet_sha,
        "claims_sha256": claims_input["sha256"],
        "claims": claims,
    })
    if ledger.get("trajectory_digest") != expected_digest:
        raise ValueError("frozen trajectory digest is invalid")

    session_index = session.get("trajectory_index")
    packet_index = packet.get("trajectory_index")
    if (
        not isinstance(session_index, dict)
        or not isinstance(packet_index, dict)
        or packet_index.get("path") != session_index.get("path")
        or packet_index.get("sha256") != session_index.get("sha256")
    ):
        raise ValueError("trajectory packet index no longer matches the harness session")
    _index, verified_artifacts = _load_index(run_dir, session)
    source_artifacts = packet.get("source_artifacts")
    if source_artifacts != verified_artifacts:
        raise ValueError("trajectory packet source artifacts no longer match the verified trajectory index")

    ledger_ref = {
        "path": ledger_path.relative_to(run_dir).as_posix(),
        "sha256": sha256_file(ledger_path),
    }
    return ledger, packet, packet_sha, ledger_ref["sha256"]


def _task_path(run_dir: Path) -> Path:
    return run_dir / "analysis" / "source_audit_task.json"


def _build_source_audit_task(
    *,
    run_id: str,
    session: dict[str, Any],
    ledger: dict[str, Any],
    packet_sha: str,
    ledger_sha: str,
    source_root: Path,
    environment: dict[str, Any],
    materials: list[dict[str, Any]],
) -> dict[str, Any]:
    source = session[session["backend"]]
    repository_root = Path(__file__).resolve().parents[2]
    prompt_path = repository_root / "workflows" / "prompts" / "source-audit.md"
    if not prompt_path.is_file():
        raise FileNotFoundError("source audit prompt is missing from the pipeline repository")
    game_version = str(environment.get("version") or session["game"].get("version") or "unknown")
    if game_version == "unknown":
        raise ValueError("game source version is unknown")
    return {
        "schema_version": TASK_SCHEMA,
        "run_id": run_id,
        "backend": session["backend"],
        "game": {
            "id": session["game"]["id"],
            "version": game_version,
            "environment_game_id": environment.get("environment_game_id", session["game"]["id"]),
        },
        "trajectory": {
            "ledger": {
                "path": "analysis/trajectory_ledger.json",
                "sha256": ledger_sha,
            },
            "packet": {
                "path": "analysis/trajectory_packet.json",
                "sha256": packet_sha,
            },
            "digest": ledger["trajectory_digest"],
            "claims": ledger["claims"],
        },
        "harness_provenance": {
            "commit": source["source_provenance"].get("source_commit"),
            "working_tree_sha256": source["source_provenance"].get("working_tree_sha256"),
        },
        "runtime_provenance": {
            "packages": source["runtime_provenance"].get("packages", {}),
            "required_packages": source["runtime_provenance"].get("required_packages", []),
        },
        "source_root": str(source_root),
        "game_source_tree_sha256": environment["source_tree_sha256"],
        "materials": materials,
        "material_coverage": {
            "level_data_material_ids": [
                row["material_id"] for row in materials if row["kind"] == "level_or_rule_data"
            ],
            "level_data_status": (
                "present" if any(row["kind"] == "level_or_rule_data" for row in materials)
                else "not_separately_present"
            ),
            "note": (
                None if any(row["kind"] == "level_or_rule_data" for row in materials)
                else "No separate level/rule data file was identified in the bound game environment."
            ),
        },
        "prompt_ref": {
            "path": prompt_path.relative_to(repository_root).as_posix(),
            "sha256": sha256_file(prompt_path),
        },
        "rules": {
            "source_findings_are_separate_from_trajectory_claims": True,
            "source_findings_do_not_rewrite_player_inference": True,
            "unbound_or_conflicting_claims_remain_unknown": True,
        },
    }


def export_source_audit_task(*, run_id: str, runs_root: Path | None = None) -> dict[str, Any]:
    run_dir, session = _load_session(run_id, runs_root)
    ledger, _packet, packet_sha, ledger_sha = _load_frozen_trajectory(run_id, run_dir, session)
    source_root, environment, materials = _source_root_and_materials(session)
    task = _build_source_audit_task(
        run_id=run_id,
        session=session,
        ledger=ledger,
        packet_sha=packet_sha,
        ledger_sha=ledger_sha,
        source_root=source_root,
        environment=environment,
        materials=materials,
    )
    destination = _task_path(run_dir)
    _write_new_json(destination, task)
    return {
        "run_id": run_id,
        "task_path": destination.relative_to(run_dir).as_posix(),
        "task_sha256": sha256_file(destination),
        "material_count": len(materials),
        "trajectory_digest": ledger["trajectory_digest"],
    }


def _validate_findings(
    task: dict[str, Any],
    document: Any,
    task_sha256: str,
) -> list[dict[str, Any]]:
    if not isinstance(document, dict) or document.get("schema_version") != FINDINGS_SCHEMA:
        raise ValueError(f"findings schema_version must be {FINDINGS_SCHEMA}")
    expected = {
        "run_id": task["run_id"],
        "game_id": task["game"]["id"],
        "game_version": task["game"]["version"],
        "task_sha256": task_sha256,
        "trajectory_digest": task["trajectory"]["digest"],
    }
    for key, value in expected.items():
        if document.get(key) != value:
            raise ValueError(f"findings {key} does not match the exported source audit task")

    raw_findings = document.get("findings")
    if not isinstance(raw_findings, list) or not raw_findings:
        raise ValueError("findings must be a non-empty list")
    material_by_id = {
        row["material_id"]: row
        for row in task["materials"]
    }
    claim_ids = {
        row["claim_id"]
        for row in task["trajectory"]["claims"]
        if isinstance(row, dict) and isinstance(row.get("claim_id"), str)
    }
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for number, raw in enumerate(raw_findings, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"findings[{number - 1}] must be an object")
        finding_id = raw.get("finding_id")
        statement = raw.get("statement")
        if not isinstance(finding_id, str) or not finding_id.strip() or finding_id.strip() in seen:
            raise ValueError(f"findings[{number - 1}] finding_id is missing or duplicated")
        finding_id = finding_id.strip()
        if not isinstance(statement, str) or not statement.strip():
            raise ValueError(f"findings[{number - 1}] statement is required")
        if raw.get("source_version") != task["game"]["version"]:
            raise ValueError(f"findings[{number - 1}] source_version does not match the task")
        refs = raw.get("source_refs")
        if (
            not isinstance(refs, list)
            or not refs
            or any(not isinstance(ref, str) or ref not in material_by_id for ref in refs)
        ):
            raise ValueError(f"findings[{number - 1}] must cite source material IDs from the task")
        location = raw.get("location")
        if not isinstance(location, dict) or location.get("path") not in {
            material_by_id[ref]["path"] for ref in refs
        }:
            raise ValueError(f"findings[{number - 1}] location must identify a cited source file")
        start_line = location.get("start_line")
        end_line = location.get("end_line")
        if (
            isinstance(start_line, bool) or not isinstance(start_line, int) or start_line < 1
            or isinstance(end_line, bool) or not isinstance(end_line, int) or end_line < start_line
        ):
            raise ValueError(f"findings[{number - 1}] location requires valid one-based line numbers")
        cited_material = next(
            material_by_id[ref] for ref in refs if material_by_id[ref]["path"] == location["path"]
        )
        line_count = cited_material.get("line_count")
        if cited_material.get("kind") == "game_asset" or line_count is None:
            raise ValueError(f"findings[{number - 1}] location must cite readable source or level data")
        if end_line > line_count:
            raise ValueError(f"findings[{number - 1}] location exceeds the cited file line count")
        if location.get("symbol") is not None and not isinstance(location.get("symbol"), str):
            raise ValueError(f"findings[{number - 1}] location symbol must be a string or null")
        for field in ("condition",):
            if not isinstance(raw.get(field), str) or not raw[field].strip():
                raise ValueError(f"findings[{number - 1}] {field} is required")
        for field in ("state_changes", "boundary_cases", "reachable_interactions"):
            value = raw.get(field)
            if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
                raise ValueError(f"findings[{number - 1}] {field} must be a list of non-empty strings")
        uncovered = raw.get("uncovered_by_play")
        relation = raw.get("trajectory_relation")
        if not isinstance(uncovered, bool) or relation not in _RELATIONS:
            raise ValueError(f"findings[{number - 1}] must state its trajectory coverage and relation")
        claim_refs = raw.get("trajectory_claim_refs", [])
        if (
            not isinstance(claim_refs, list)
            or any(not isinstance(ref, str) or ref not in claim_ids for ref in claim_refs)
        ):
            raise ValueError(f"findings[{number - 1}] has invalid trajectory claim references")
        if relation == "not_covered" and (not uncovered or claim_refs):
            raise ValueError(f"findings[{number - 1}] not_covered must be marked uncovered with no claim refs")
        if relation in {"confirms", "extends", "conflicts"} and (uncovered or not claim_refs):
            raise ValueError(f"findings[{number - 1}] relation requires cited gameplay claims")
        if relation == "unknown" and claim_refs and uncovered:
            raise ValueError(f"findings[{number - 1}] unknown relation cannot claim uncovered and cite claims")
        output.append({
            "finding_id": finding_id.strip(),
            "statement": statement.strip(),
            "source_version": raw["source_version"],
            "source_refs": list(dict.fromkeys(refs)),
            "location": {
                "path": location["path"],
                "start_line": start_line,
                "end_line": end_line,
                "symbol": location.get("symbol"),
            },
            "condition": raw["condition"].strip(),
            "state_changes": list(raw["state_changes"]),
            "boundary_cases": list(raw["boundary_cases"]),
            "reachable_interactions": list(raw["reachable_interactions"]),
            "uncovered_by_play": uncovered,
            "trajectory_relation": relation,
            "trajectory_claim_refs": list(dict.fromkeys(claim_refs)),
        })
        seen.add(finding_id)
    return output


def import_source_audit_findings(
    *,
    run_id: str,
    findings_path: Path,
    runs_root: Path | None = None,
) -> dict[str, Any]:
    run_dir, session = _load_session(run_id, runs_root)
    task_path = _task_path(run_dir)
    if not task_path.is_file():
        raise FileNotFoundError("export the source audit task before importing findings")
    destination = run_dir / "analysis" / "source_findings.json"
    if destination.exists():
        raise FileExistsError("source findings already exist; refusing to overwrite")
    ledger, _packet, packet_sha, ledger_sha = _load_frozen_trajectory(run_id, run_dir, session)
    source_root, environment, materials = _source_root_and_materials(session)
    task = _load_json(task_path, "source audit task")
    expected_task = _build_source_audit_task(
        run_id=run_id,
        session=session,
        ledger=ledger,
        packet_sha=packet_sha,
        ledger_sha=ledger_sha,
        source_root=source_root,
        environment=environment,
        materials=materials,
    )
    task_sha = sha256_file(task_path)
    if task != expected_task:
        raise ValueError("source audit task was altered or no longer matches the frozen trajectory and game source")
    prompt_ref = task.get("prompt_ref")
    repository_root = Path(__file__).resolve().parents[2]
    if not isinstance(prompt_ref, dict) or not isinstance(prompt_ref.get("path"), str):
        raise ValueError("source audit task has no prompt reference")
    prompt_path = (repository_root / prompt_ref["path"]).resolve()
    try:
        prompt_path.relative_to(repository_root)
    except ValueError as exc:
        raise ValueError("source audit prompt path escapes the repository") from exc
    if not prompt_path.is_file() or sha256_file(prompt_path) != prompt_ref.get("sha256"):
        raise ValueError("source audit prompt changed since task export")
    findings_file = findings_path.expanduser().resolve()
    document = _load_json(findings_file, "source audit findings")
    findings_input_sha = sha256_file(findings_file)
    findings = _validate_findings(task, document, task_sha)
    artifact = {
        "schema_version": FINDINGS_OUTPUT_SCHEMA,
        "run_id": run_id,
        "game_id": session["game"]["id"],
        "game_version": task["game"]["version"],
        "task": {"path": "source_audit_task.json", "sha256": task_sha},
        "trajectory_digest": ledger["trajectory_digest"],
        "findings_input": {"filename": findings_file.name, "sha256": findings_input_sha},
        "source_tree_sha256": task["game_source_tree_sha256"],
        "findings": findings,
        "trajectory_ledger_modified": False,
    }
    _write_new_json(destination, artifact)
    return {
        "run_id": run_id,
        "findings_path": destination.relative_to(run_dir).as_posix(),
        "findings_sha256": sha256_file(destination),
        "finding_count": len(findings),
        "trajectory_digest": ledger["trajectory_digest"],
    }
