from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Sequence

from .game_provenance import inspect_runtime_packages, inspect_tycho_environment

PACKET_SCHEMA = "arcagi-trajectory-packet/v1"
LEDGER_SCHEMA = "arcagi-trajectory-ledger/v1"
BINDING_SCHEMA = "arcagi-game-source-binding/v1"
EVENT_TYPE = re.compile(r'"type"\s*:\s*"([^"]+)"', re.IGNORECASE)
VISIBLE_DUCK_TYPES = {"initial", "action", "reset"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not readable JSON: {path}") from exc


def write_new_json(path: Path, value: dict[str, Any]) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def file_ref(path: Path, role: str) -> dict[str, str]:
    path = path.expanduser().resolve(strict=True)
    if not path.is_file():
        raise FileNotFoundError(f"{role} must be a file: {path}")
    return {"role": role, "path": str(path), "sha256": sha256_file(path)}


def create_tycho_binding_manifest(
    *, game_id: str, tycho_root: Path, source_dir: Path, python: str, output_path: Path
) -> dict[str, Any]:
    env = inspect_tycho_environment(tycho_root, game_id, source_dir=source_dir)
    runtime = inspect_runtime_packages(python, "tycho")
    if env.get("status") != "verified":
        raise ValueError(f"game source is not verified: {env.get('reason')}")
    value = {
        "schema_version": BINDING_SCHEMA,
        "game_id": game_id,
        "game_version": env["version"],
        "status": "verified" if runtime.get("status") == "verified" else "unverified",
        "source": env,
        "runtime": runtime,
        "historical_runs": {"binding": "not_implied"},
    }
    write_new_json(output_path, value)
    return {
        "path": str(output_path.resolve()),
        "sha256": sha256_file(output_path.resolve()),
        "status": value["status"],
        "game_id": game_id,
        "game_version": value["game_version"],
        "source_tree_sha256": env["source_tree_sha256"],
        "runtime_status": runtime.get("status"),
    }


def _version_label(game_id: str) -> str | None:
    return game_id.split("-", 1)[1] if "-" in game_id else None


def _duck_events(path: Path) -> tuple[list[tuple[int, dict[str, Any]]], dict[str, int]]:
    visible: list[tuple[int, dict[str, Any]]] = []
    excluded: dict[str, int] = {}
    with path.open("r", encoding="utf-8") as stream:
        for line_no, line in enumerate(stream):
            if not line.strip():
                continue
            match = EVENT_TYPE.search(line[:65536])
            kind = match.group(1).lower() if match else "unclassified"
            if kind not in VISIBLE_DUCK_TYPES:
                excluded[kind] = excluded.get(kind, 0) + 1
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"malformed visible Duck event at line {line_no + 1}") from exc
            if not isinstance(row, dict) or str(row.get("type", "")).lower() != kind:
                raise ValueError(f"malformed Duck {kind} event at line {line_no + 1}")
            visible.append((line_no, row))
    return visible, excluded


def _is_grid(value: Any) -> bool:
    return (
        isinstance(value, list) and bool(value)
        and all(isinstance(row, list) for row in value)
        and len({len(row) for row in value}) == 1
        and all(not isinstance(cell, (list, dict)) for row in value for cell in row)
    )


def _encode_observations(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    previous_grid = None
    previous_id = None
    encoded = []
    for source_row in trace:
        row = dict(source_row)
        observation = row.get("observation")
        if _is_grid(observation):
            shape = [len(observation), len(observation[0])]
            if previous_grid is not None and [len(previous_grid), len(previous_grid[0])] == shape:
                changes = [
                    [r, c, value]
                    for r, (old_row, new_row) in enumerate(zip(previous_grid, observation))
                    for c, (old, value) in enumerate(zip(old_row, new_row))
                    if old != value
                ]
                row["observation"] = {
                    "encoding": "grid-delta/v1", "shape": shape,
                    "base_evidence_id": previous_id, "changes": changes,
                }
            else:
                row["observation"] = {"encoding": "grid-full/v1", "shape": shape, "grid": observation}
            previous_grid = observation
            previous_id = row["evidence_id"]
        elif observation is not None:
            row["observation"] = {"encoding": "opaque-json/v1", "value": observation}
            previous_grid = None
            previous_id = None
        else:
            previous_grid = None
            previous_id = None
        encoded.append(row)
    return encoded


def expand_trajectory_observations(packet: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with any losslessly delta-encoded visible grids expanded."""
    import copy
    result = copy.deepcopy(packet)
    previous_grid = None
    previous_id = None
    for row in result.get("trace", []):
        value = row.get("observation")
        if not isinstance(value, dict) or "encoding" not in value:
            previous_grid = None
            previous_id = None
        elif value["encoding"] == "grid-full/v1":
            previous_grid = value["grid"]
            row["observation"] = previous_grid
            previous_id = row.get("evidence_id")
        elif value["encoding"] == "grid-delta/v1":
            if previous_grid is None or value.get("base_evidence_id") != previous_id:
                raise ValueError("trajectory grid delta has no matching base observation")
            grid = [list(line) for line in previous_grid]
            if value.get("shape") != [len(grid), len(grid[0]) if grid else 0]:
                raise ValueError("trajectory grid delta shape does not match its base")
            for change in value.get("changes", []):
                if not isinstance(change, list) or len(change) != 3:
                    raise ValueError("trajectory grid delta contains an invalid cell update")
                r, c, cell = change
                if not isinstance(r, int) or not isinstance(c, int) or not (0 <= r < len(grid) and 0 <= c < len(grid[r])):
                    raise ValueError("trajectory grid delta cell is out of bounds")
                grid[r][c] = cell
            row["observation"] = grid
            previous_grid = grid
            previous_id = row.get("evidence_id")
        elif value["encoding"] == "opaque-json/v1":
            row["observation"] = value.get("value")
            previous_grid = None
            previous_id = None
        else:
            raise ValueError(f"unsupported trajectory observation encoding: {value['encoding']}")
    return result


def _base_packet(run_id: str, backend: str, game_id: str, trace: list[dict[str, Any]],
                 notes: list[dict[str, Any]], outcome: dict[str, Any],
                 artifacts: list[dict[str, str]], outcome_evidence: list[dict[str, Any]],
                 event_count: int, excluded: dict[str, Any]) -> dict[str, Any]:
    encoded_trace = _encode_observations(trace)
    return {
        "schema_version": PACKET_SCHEMA, "run_id": run_id, "backend": backend,
        "game": {"id": game_id, "kind": "original", "version": _version_label(game_id)},
        "run_status": "legacy_recorded",
        "source_binding": {
            "status": "unbound", "basis": "game_id_only",
            "game_id_version_label": _version_label(game_id), "source_sha256": None,
            "reason": "historic record has no trusted hash binding to the exact game source tree",
        },
        "source_provenance": {"recorded_game_id": game_id, "code_version_status": "unbound"},
        "trajectory_index": None, "observation_encoding": "grid-full/v1 + grid-delta/v1", "trace": encoded_trace, "saved_notes": notes,
        "run_outcome": outcome, "outcome_evidence": outcome_evidence,
        "coverage": {
            "raw_event_count": event_count, "exported_trace_steps": len(trace),
            "saved_notes": len(notes),
            "saved_note_status": "explicit_notes_imported" if notes else "no_explicit_saved_notes_supplied",
        },
        "interpretation_limits": {
            "source_code_included": False, "engine_internal_fields_imported": False,
            "raw_model_reasoning_imported": False, "duck_transcript_text_imported": False,
            "action_sequence_is_not_reasoning_evidence": True,
            "saved_notes_are_not_a_complete_reasoning_record": True,
            "unimported_reasoning_status": "not reviewed; exclusion does not establish whether reasoning was saved elsewhere",
        },
        "excluded_material": excluded,
        "source_artifacts": artifacts,
    }


def _explicit_notes(paths: Sequence[Path], refs: list[dict[str, str]]) -> list[dict[str, Any]]:
    notes = []
    for number, arg in enumerate(paths, 1):
        path = arg.expanduser().resolve(strict=True)
        note_format = path.suffix.lower().lstrip(".")
        if note_format not in {"md", "txt", "json"}:
            raise ValueError("saved notes must be explicit Markdown, text, or JSON files")
        forbidden = {"transcripts", "prompts", "solver_analysis"}
        if forbidden.intersection(part.lower() for part in path.parts) or any(
            word in path.name.lower() for word in ("reasoning", "transcript", "analysis", "prompt")
        ):
            raise ValueError("reasoning, transcript, analysis, and prompt files are not saved-note inputs")
        note_text = path.read_text(encoding="utf-8")
        ref = file_ref(path, "saved_note")
        refs.append(ref)
        notes.append({
            "evidence_id": f"note-{number:04d}", "text": note_text,
            "source_ref": {"path": ref["path"], "sha256": ref["sha256"]},
        })
    return notes


def import_legacy_run(
    *, backend: str, run_id: str, artifact_paths: Sequence[Path], expected_game_id: str,
    output_path: Path, saved_note_paths: Sequence[Path] = (),
) -> dict[str, Any]:
    """Normalize visible historical observations/actions; never import internal reasoning/transcripts."""
    if backend not in {"tycho", "duck"}:
        raise ValueError("backend must be tycho or duck")
    if not run_id.strip() or not expected_game_id.strip() or not artifact_paths:
        raise ValueError("run_id, expected_game_id, and artifact_paths are required")
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite trajectory packet: {output_path}")
    refs: list[dict[str, str]] = []
    trace: list[dict[str, Any]] = []
    outcome: dict[str, Any] = {}
    outcome_evidence: list[dict[str, Any]] = []
    excluded: dict[str, Any] = {}

    if backend == "tycho":
        record_path = None
        manifest = None
        prefix = expected_game_id.split("-", 1)[0].lower()
        for arg in artifact_paths:
            path = arg.expanduser().resolve(strict=True)
            if any(part in path.name.lower() for part in ("transcript", "reasoning", "analysis", "prompt")):
                raise ValueError("Tycho transcript/reasoning/analysis artifacts cannot be imported")
            if path.name.lower() == f"game_{prefix}.json":
                if record_path is not None:
                    raise ValueError("provide only one Tycho game record")
                record_path = path
                refs.append(file_ref(path, "game_record"))
            elif path.name.lower() == "manifest.json":
                manifest = read_json(path, "Tycho manifest")
                refs.append(file_ref(path, "run_manifest"))
            else:
                raise ValueError(f"unsupported Tycho evidence artifact: {path.name}")
        if record_path is None:
            raise ValueError(f"Tycho import requires game_{prefix}.json")
        record = read_json(record_path, "Tycho game record")
        if not isinstance(record, dict) or record.get("game_id") != expected_game_id:
            raise ValueError("Tycho game record is missing or has a different game_id")
        if not isinstance(record.get("trace"), list):
            raise ValueError("Tycho game record trace must be a list")
        if manifest is not None:
            games = manifest.get("games")
            if not isinstance(games, list) or prefix not in games and expected_game_id not in games:
                raise ValueError("Tycho manifest games do not match expected_game_id")
        record_ref = next(ref for ref in refs if ref["role"] == "game_record")
        for index, row in enumerate(record["trace"]):
            if not isinstance(row, dict):
                continue
            action = row.get("action")
            normalized_action = {"name": action} if isinstance(action, (str, int, float, bool)) else None
            if normalized_action is not None:
                for key in ("x", "y"):
                    if row.get(key) is not None:
                        normalized_action[key] = row[key]
            trace.append({
                "evidence_id": f"ev-{len(trace)+1:06d}", "step": row.get("turn", index+1),
                "observation": row.get("frame"), "action": normalized_action,
                "feedback": None, "terminal_result": None,
                "source_ref": {"path": record_ref["path"], "sha256": record_ref["sha256"], "record_index": index},
            })
        notes = _explicit_notes(saved_note_paths, refs)
        outcome = {key: record[key] for key in ("levels_completed", "n_levels", "partial", "stop_reason") if key in record}
        if outcome:
            outcome_evidence.append({
                "evidence_id": "outcome-0001", "result": outcome,
                "source_ref": {"path": record_ref["path"], "sha256": record_ref["sha256"]},
            })
        excluded = {"reasoning_field": "excluded_without_import", "engine_internal_fields": "excluded"}

    else:
        if saved_note_paths:
            raise ValueError("Duck saved notes cannot be imported from transcript/analysis artifacts")
        event_path = None
        config_path = None
        viewer_paths: list[Path] = []
        for arg in artifact_paths:
            path = arg.expanduser().resolve(strict=True)
            lowered = path.name.lower()
            if any(word in lowered for word in ("transcript", "analysis", "reasoning", "prompt")):
                raise ValueError("Duck transcript/analysis/reasoning artifacts cannot be imported")
            if lowered.endswith("_events.jsonl"):
                if event_path is not None:
                    raise ValueError("provide only one Duck event sidecar")
                if not lowered.startswith(expected_game_id.lower() + "_"):
                    raise ValueError("Duck sidecar filename must begin with the exact game_id")
                event_path = path
                refs.append(file_ref(path, "raw_event_sidecar"))
            elif lowered == "run_config.json":
                if config_path is not None:
                    raise ValueError("provide only one Duck run_config")
                config_path = path
                refs.append(file_ref(path, "run_config"))
            elif lowered.endswith("_viewer_data.json"):
                viewer_paths.append(path)
                refs.append(file_ref(path, "viewer_data"))
            else:
                raise ValueError(f"unsupported Duck evidence artifact: {path.name}")
        if event_path is None or config_path is None:
            raise ValueError("Duck import requires *_events.jsonl and run_config.json")
        config = read_json(config_path, "Duck run_config")
        if not isinstance(config, dict) or not isinstance(config.get("games"), list) or expected_game_id not in config["games"]:
            raise ValueError("Duck run_config games must contain the exact expected_game_id")
        rows, excluded_types = _duck_events(event_path)
        event_ref = next(ref for ref in refs if ref["role"] == "raw_event_sidecar")
        for line_no, row in rows:
            kind = str(row.get("type", "")).lower()
            action_name = row.get("action_display") or row.get("action_name")
            if kind == "reset":
                action_name = action_name or "RESET"
            trace.append({
                "evidence_id": f"ev-{len(trace)+1:06d}", "step": row.get("action_num", line_no),
                "observation": row.get("board"),
                "action": {"name": action_name} if isinstance(action_name, (str, int, float)) else None,
                "feedback": {key: row[key] for key in ("reward", "board_changed") if key in row} or None,
                "terminal_result": {key: row[key] for key in ("done", "level_completed", "game_over", "run_complete") if key in row} or None,
                "source_ref": {"path": event_ref["path"], "sha256": event_ref["sha256"], "record_index": line_no},
            })
        outcome = {"declared_games": config["games"], "run_outcome": "not_inferred_from_process_or_partial_events"}
        for index, viewer_path in enumerate(viewer_paths, 1):
            viewer = read_json(viewer_path, "Duck viewer data")
            if not isinstance(viewer, dict):
                continue
            summary = {key: viewer[key] for key in ("game_id", "status", "levels_completed", "total_levels", "final_score") if key in viewer}
            if summary:
                outcome[f"viewer_{index}"] = summary
                ref = next(ref for ref in refs if ref["path"] == str(viewer_path.resolve()))
                outcome_evidence.append({
                    "evidence_id": f"outcome-{index:04d}", "result": summary,
                    "source_ref": {"path": ref["path"], "sha256": ref["sha256"]},
                })
        excluded = {
            "event_types": excluded_types,
            "analysis_text": "analysis rows skipped before JSON parsing",
            "transcript_text": "excluded_without_import",
            "engine_internal_fields": "excluded",
        }
        notes = []

    if not trace:
        raise ValueError("historical run has no usable visible gameplay events")
    packet = _base_packet(
        run_id=run_id, backend=backend, game_id=expected_game_id, trace=trace, notes=notes,
        outcome=outcome, artifacts=refs, outcome_evidence=outcome_evidence,
        event_count=len(trace) + sum(excluded.get("event_types", {}).values()) if isinstance(excluded.get("event_types"), dict) else len(trace),
        excluded=excluded,
    )
    bundle_root = output_path.expanduser().resolve().parent.parent

    def make_bundle_relative(ref: dict[str, Any]) -> None:
        source = Path(ref["path"]).expanduser().resolve(strict=True)
        if not source.is_relative_to(bundle_root):
            raise ValueError("legacy source artifacts must be staged inside the trajectory input bundle")
        ref["path"] = source.relative_to(bundle_root).as_posix()

    for ref in packet["source_artifacts"]:
        make_bundle_relative(ref)
    for row in packet["trace"]:
        make_bundle_relative(row["source_ref"])
    for row in packet["saved_notes"]:
        make_bundle_relative(row["source_ref"])
    for row in packet["outcome_evidence"]:
        make_bundle_relative(row["source_ref"])
    write_new_json(output_path, packet)
    return {
        "imported": True, "path": str(output_path.resolve()), "sha256": sha256_file(output_path.resolve()),
        "game_id": expected_game_id, "backend": backend, "trace_steps": len(trace),
        "saved_notes": len(notes), "source_binding": packet["source_binding"],
    }


def freeze_legacy_claims(*, packet_path: Path, claims_path: Path, output_path: Path) -> dict[str, Any]:
    from .trajectory_handoff import _validate_claims
    from .agent_workflow import _validate_trajectory_packet

    packet_path = packet_path.expanduser().resolve(strict=True)
    claims_path = claims_path.expanduser().resolve(strict=True)
    output_path = output_path.expanduser().resolve()
    if output_path.exists():
        raise FileExistsError(f"trajectory ledger already frozen: {output_path}")
    packet = read_json(packet_path, "trajectory packet")
    if not isinstance(packet, dict) or packet.get("schema_version") != PACKET_SCHEMA:
        raise ValueError(f"packet schema_version must be {PACKET_SCHEMA}")
    if not isinstance(packet.get("game"), dict) or not isinstance(packet.get("run_id"), str):
        raise ValueError("trajectory packet lacks run/game identity")
    _validate_trajectory_packet(packet_path, packet)
    packet_sha = sha256_file(packet_path)
    claims = _validate_claims(packet, packet_sha, read_json(claims_path, "claims file"))
    claims_sha = sha256_file(claims_path)
    digest_input = {"packet_sha256": packet_sha, "claims_sha256": claims_sha, "claims": claims}
    digest = hashlib.sha256(json.dumps(digest_input, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    ledger = {
        "schema_version": LEDGER_SCHEMA, "run_id": packet["run_id"], "game_id": packet["game"]["id"],
        "trajectory_frozen": True, "source_binding": packet.get("source_binding"),
        "packet": {"path": str(packet_path), "sha256": packet_sha},
        "claims_input": {"path": str(claims_path), "sha256": claims_sha},
        "trajectory_digest": digest, "claims": claims,
        "provenance_limits": {
            "source_findings_included": False, "engine_probe_results_included": False,
            "action_sequence_treated_as_reasoning": False,
        },
    }
    write_new_json(output_path, ledger)
    return {"frozen": True, "path": str(output_path), "sha256": sha256_file(output_path), "trajectory_digest": digest, "claim_count": len(claims)}
