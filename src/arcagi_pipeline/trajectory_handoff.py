from __future__ import annotations

import gzip
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .harness_sessions import _load_session, sha256_file


PACKET_SCHEMA = "arcagi-trajectory-packet/v1"
CLAIMS_SCHEMA = "arcagi-trajectory-claims/v1"
LEDGER_SCHEMA = "arcagi-trajectory-ledger/v1"
EXPORTABLE_STATUSES = {"completed", "failed", "interrupted"}
_SHA256_LENGTH = 64


def _canonical_digest(value: Any) -> str:
    wire = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(wire.encode("utf-8")).hexdigest()


def _write_new_json(path: Path, value: Any) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing analysis artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    if temporary.exists():
        raise FileExistsError(f"temporary analysis artifact already exists: {temporary}")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _safe_artifact(run_dir: Path, ref: dict[str, Any], label: str) -> tuple[Path, dict[str, str]]:
    if not isinstance(ref, dict):
        raise ValueError(f"{label} must be an object")
    relative = ref.get("path")
    digest = ref.get("sha256")
    if not isinstance(relative, str) or not relative or not isinstance(digest, str) or len(digest) != _SHA256_LENGTH:
        raise ValueError(f"{label} has an invalid path or SHA-256 reference")
    if any(character not in "0123456789abcdef" for character in digest):
        raise ValueError(f"{label} has an invalid SHA-256 reference")
    path = (run_dir / Path(relative)).resolve()
    root = run_dir.resolve()
    try:
        stored_relative = path.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError(f"{label} points outside its run directory") from exc
    if not path.is_file():
        raise FileNotFoundError(f"{label} is missing: {stored_relative}")
    actual = sha256_file(path)
    if actual != digest:
        raise ValueError(f"{label} SHA-256 mismatch: {stored_relative}")
    return path, {"path": stored_relative, "sha256": actual}


def _load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid UTF-8 JSON: {path}") from exc


def _load_index(run_dir: Path, session: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    index_ref = session.get("trajectory_index")
    if not isinstance(index_ref, dict):
        raise ValueError("run has no trajectory index")
    index_path, index_file_ref = _safe_artifact(run_dir, index_ref, "trajectory index")
    index = _load_json(index_path, "trajectory index")
    if not isinstance(index, dict) or index.get("schema_version") != "arcagi-trajectory-index/v1":
        raise ValueError("unsupported trajectory index schema")
    expected_provenance = f"{session.get('backend')}_original_game_run"
    if index.get("provenance") != expected_provenance or index.get("game_id") != session.get("game", {}).get("id"):
        raise ValueError("trajectory index does not match the harness session")

    refs: list[dict[str, Any]] = []
    raw_artifacts = index.get("artifacts")
    if session.get("backend") == "tycho":
        if not isinstance(raw_artifacts, dict):
            raise ValueError("Tycho trajectory index artifacts must be an object")
        if any(not isinstance(ref, dict) for ref in raw_artifacts.values()):
            raise ValueError("Tycho trajectory index contains an invalid artifact reference")
        refs.extend({"kind": kind, **ref} for kind, ref in raw_artifacts.items())
        raw_notes = index.get("saved_notes", [])
        if not isinstance(raw_notes, list):
            raise ValueError("Tycho saved_notes index must be a list")
        if any(not isinstance(ref, dict) for ref in raw_notes):
            raise ValueError("Tycho trajectory index contains an invalid saved-note reference")
        refs.extend({"kind": "saved_note", **ref} for ref in raw_notes)
    elif session.get("backend") == "duck":
        if not isinstance(raw_artifacts, list):
            raise ValueError("Duck trajectory index artifacts must be a list")
        if any(not isinstance(item, dict) for item in raw_artifacts):
            raise ValueError("Duck trajectory index contains an invalid artifact reference")
        refs.extend(raw_artifacts)
    else:
        raise ValueError(f"unsupported harness backend: {session.get('backend')}")

    verified: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for number, ref in enumerate(refs, start=1):
        _path, safe_ref = _safe_artifact(run_dir, ref, f"trajectory artifact #{number}")
        if safe_ref["path"] in seen_paths:
            raise ValueError(f"trajectory index contains a duplicate artifact path: {safe_ref['path']}")
        seen_paths.add(safe_ref["path"])
        verified.append({"kind": str(ref.get("kind", "native_artifact")), **safe_ref})
    if not verified:
        raise ValueError("trajectory index contains no verifiable native artifacts")
    return {**index, "_index_ref": index_file_ref}, verified


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid event sidecar JSON at line {line_number}: {path.name}") from exc
        if isinstance(row, dict):
            events.append(row)
    return events


def _read_duck_events(path: Path) -> list[dict[str, Any]]:
    if path.name.endswith(".jsonl"):
        return _read_jsonl(path)
    if path.name.endswith(".json.gz"):
        try:
            payload = gzip.decompress(path.read_bytes()).decode("utf-8")
            events = json.loads(payload)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid compressed Duck event sidecar: {path.name}") from exc
        if isinstance(events, list):
            return [event for event in events if isinstance(event, dict)]
        raise ValueError(f"Duck event sidecar must contain a JSON array: {path.name}")
    raise ValueError(f"unsupported Duck event sidecar format: {path.name}")


def _event_ref_id(number: int) -> str:
    return f"ev-{number:06d}"


def _outcome_evidence(backend: str, outcome: dict[str, Any], artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_path = {item["path"]: item for item in artifacts}
    outcomes: list[dict[str, Any]] = []
    if backend == "tycho":
        source = next((item for item in artifacts if item["kind"] == "game_record"), None)
        if source is not None and outcome:
            outcomes.append({
                "evidence_id": "outcome-0001",
                "result": outcome,
                "source_ref": {"path": source["path"], "sha256": source["sha256"]},
            })
    else:
        for path, summary in sorted(outcome.items()):
            source = by_path.get(path)
            if source is None or source["kind"] != "viewer_data" or not summary:
                continue
            outcomes.append({
                "evidence_id": f"outcome-{len(outcomes) + 1:04d}",
                "result": summary,
                "source_ref": {"path": source["path"], "sha256": source["sha256"]},
            })
    return outcomes


def _tycho_packet(run_dir: Path, session: dict[str, Any], index: dict[str, Any], artifacts: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    by_kind = {item["kind"]: item for item in artifacts}
    record_ref = by_kind.get("game_record")
    if record_ref is None:
        raise ValueError("Tycho run has no game_record; there is no trajectory to export")
    record_path = run_dir / record_ref["path"]
    record = _load_json(record_path, "Tycho game record")
    if not isinstance(record, dict) or not isinstance(record.get("trace"), list):
        raise ValueError("Tycho game record has no trace list")

    trace: list[dict[str, Any]] = []
    observation_count = 0
    action_count = 0
    for index_number, row in enumerate(record["trace"], start=1):
        if not isinstance(row, dict):
            continue
        entry: dict[str, Any] = {
            "evidence_id": _event_ref_id(len(trace) + 1),
            "step": row.get("turn", index_number),
            "source_ref": {"path": record_ref["path"], "sha256": record_ref["sha256"], "record_index": index_number - 1},
            "observation": row.get("frame"),
            "action": None,
            "feedback": None,
            "terminal_result": None,
        }
        if entry["observation"] is not None:
            observation_count += 1
        action = row.get("action")
        if action is not None:
            entry["action"] = {"name": action, "x": row.get("x"), "y": row.get("y")}
            action_count += 1
        entry["feedback"] = None
        trace.append(entry)

    notes: list[dict[str, Any]] = []
    note_count = 0
    note_artifacts_not_imported = 0
    for ref_number, artifact in enumerate((item for item in artifacts if item["kind"] == "saved_note"), start=1):
        path = run_dir / artifact["path"]
        if path.suffix.lower() not in {".md", ".txt"}:
            note_artifacts_not_imported += 1
            continue
        note_text = path.read_text(encoding="utf-8")
        note_id = f"note-{ref_number:04d}"
        notes.append({
            "evidence_id": note_id,
            "text": note_text,
            "source_ref": {"path": artifact["path"], "sha256": artifact["sha256"]},
        })
        note_count += 1

    outcome = {
        key: record[key]
        for key in ("levels_completed", "n_levels", "partial", "stop_reason")
        if key in record
    }
    coverage = {
        "native_trace_steps": len(record["trace"]),
        "exported_trace_steps": len(trace),
        "observations": observation_count,
        "actions": action_count,
        "saved_notes": note_count,
        "saved_note_artifacts_not_imported": note_artifacts_not_imported,
        "reasoning_field_imported": False,
    }
    return trace, notes, {"outcome": outcome, "coverage": coverage}


def _duck_packet(run_dir: Path, session: dict[str, Any], index: dict[str, Any], artifacts: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    viewer_refs = [item for item in artifacts if item["kind"] == "viewer_data"]
    sidecars = [item for item in artifacts if item["kind"] == "raw_event_sidecar"]
    if not sidecars:
        raise ValueError("Duck run has no raw event sidecar; there is no trajectory to export")

    trace: list[dict[str, Any]] = []
    excluded_analysis_events = 0
    excluded_event_types: dict[str, int] = {}
    observation_count = 0
    action_count = 0
    for sidecar in sidecars:
        events = _read_duck_events(run_dir / sidecar["path"])
        for index_number, event in enumerate(events):
            event_type = str(event.get("type", "")).strip().lower()
            if event_type == "analysis":
                excluded_analysis_events += 1
                continue
            if event_type not in {"initial", "action", "reset"}:
                excluded_event_types[event_type or "missing_type"] = excluded_event_types.get(event_type or "missing_type", 0) + 1
                continue
            observation = event.get("board")
            action_name = event.get("action_display") or event.get("action_name")
            if event_type == "reset":
                action_name = action_name or "RESET"
            item: dict[str, Any] = {
                "evidence_id": _event_ref_id(len(trace) + 1),
                "step": event.get("action_num", index_number),
                "source_ref": {"path": sidecar["path"], "sha256": sidecar["sha256"], "record_index": index_number},
                "observation": observation,
                "action": action_name,
                "feedback": {
                    key: event[key]
                    for key in ("reward", "board_changed")
                    if key in event
                } or None,
                "terminal_result": {
                    key: event[key]
                    for key in ("done", "level_completed", "game_over", "run_complete")
                    if key in event
                } or None,
            }
            if observation is not None:
                observation_count += 1
            if action_name is not None:
                action_count += 1
            trace.append(item)

    if not trace:
        raise ValueError("Duck event sidecars contain no supported initial/action/reset events")

    viewer_summary: dict[str, Any] = {}
    for viewer in viewer_refs:
        payload = _load_json(run_dir / viewer["path"], "Duck viewer data")
        if isinstance(payload, dict):
            viewer_summary[viewer["path"]] = {
                key: payload[key]
                for key in ("game_id", "status", "levels_completed", "total_levels", "final_score")
                if key in payload
            }

    coverage = {
        "exported_trace_steps": len(trace),
        "observations": observation_count,
        "actions": action_count,
        "saved_notes": 0,
        "analysis_events_excluded": excluded_analysis_events,
        "unsupported_event_types_excluded": excluded_event_types,
        "transcript_artifacts_imported": False,
    }
    return trace, [], {"outcome": viewer_summary, "coverage": coverage}


def export_trajectory_packet(*, run_id: str, runs_root: Path | None = None) -> dict[str, Any]:
    run_dir, session = _load_session(run_id, runs_root)
    if session.get("status") not in EXPORTABLE_STATUSES:
        raise ValueError("trajectory export requires a completed, failed, or interrupted harness run")
    index, artifacts = _load_index(run_dir, session)
    if session["backend"] == "tycho":
        trace, notes, parsed = _tycho_packet(run_dir, session, index, artifacts)
    elif session["backend"] == "duck":
        trace, notes, parsed = _duck_packet(run_dir, session, index, artifacts)
    else:
        raise ValueError(f"unsupported harness backend: {session.get('backend')}")

    environment_provenance = session.get(session["backend"], {}).get("environment_provenance")
    safe_environment_provenance = None
    if isinstance(environment_provenance, dict):
        safe_environment_provenance = {
            key: environment_provenance[key]
            for key in (
                "game_id", "environment_game_id", "version", "metadata_path",
                "metadata_sha256", "game_source_path", "source_tree_sha256",
            )
            if key in environment_provenance
        }
    packet = {
        "schema_version": PACKET_SCHEMA,
        "run_id": run_id,
        "backend": session["backend"],
        "game": {key: value for key, value in session.get("game", {}).items() if key in {"id", "kind", "version"}},
        "run_status": session["status"],
        "trajectory_index": index["_index_ref"],
        "source_provenance": {
            "harness_commit": session.get(session["backend"], {}).get("source_commit"),
            "game_environment": safe_environment_provenance,
            "runtime_packages": {
                "status": session.get(session["backend"], {}).get("runtime_provenance", {}).get("status"),
                "packages": session.get(session["backend"], {}).get("runtime_provenance", {}).get("packages", {}),
            },
        },
        "trace": trace,
        "saved_notes": notes,
        "run_outcome": parsed["outcome"],
        "outcome_evidence": _outcome_evidence(session["backend"], parsed["outcome"], artifacts),
        "coverage": parsed["coverage"],
        "interpretation_limits": {
            "source_code_included": False,
            "engine_internal_fields_imported": False,
            "raw_model_reasoning_imported": False,
            "duck_transcript_text_imported": False,
            "saved_notes_are_not_a_complete_reasoning_record": True,
            "unimported_reasoning_status": "not_reviewed; this does not establish whether reasoning was absent from the original run",
        },
        "source_artifacts": artifacts,
    }
    destination = run_dir / "analysis" / "trajectory_packet.json"
    _write_new_json(destination, packet)
    packet_sha = sha256_file(destination)
    return {"run_id": run_id, "packet_path": destination.relative_to(run_dir).as_posix(), "packet_sha256": packet_sha, "trace_steps": len(trace), "saved_notes": len(notes)}


def _validate_claims(packet: dict[str, Any], packet_sha256: str, claims_document: Any) -> list[dict[str, Any]]:
    if not isinstance(claims_document, dict) or claims_document.get("schema_version") != CLAIMS_SCHEMA:
        raise ValueError(f"claims file schema_version must be {CLAIMS_SCHEMA}")
    for key in ("run_id", "game_id", "packet_sha256"):
        expected = packet["run_id"] if key == "run_id" else packet["game"]["id"] if key == "game_id" else packet_sha256
        if claims_document.get(key) != expected:
            raise ValueError(f"claims file {key} does not match exported trajectory packet")
    claims = claims_document.get("claims")
    if not isinstance(claims, list) or not claims:
        raise ValueError("claims must be a non-empty list")
    evidence: dict[str, dict[str, Any]] = {}
    for row in packet["trace"]:
        evidence[row["evidence_id"]] = {"kind": "trace", "row": row}
    for row in packet["saved_notes"]:
        evidence[row["evidence_id"]] = {"kind": "saved_note", "row": row}
    for row in packet.get("outcome_evidence", []):
        evidence[row["evidence_id"]] = {"kind": "outcome", "row": row}

    seen_claims: set[str] = set()
    validated: list[dict[str, Any]] = []
    allowed_kinds = {"direct_observation", "gameplay_recorded_inference", "post_run_hypothesis"}
    for index, raw in enumerate(claims, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"claims[{index - 1}] must be an object")
        claim_id = raw.get("claim_id")
        statement = raw.get("statement")
        kind = raw.get("kind")
        refs = raw.get("evidence_refs")
        if not isinstance(claim_id, str) or not claim_id.strip() or claim_id.strip() in seen_claims:
            raise ValueError(f"claims[{index - 1}] claim_id is missing or duplicated")
        claim_id = claim_id.strip()
        if not isinstance(statement, str) or not statement.strip():
            raise ValueError(f"claims[{index - 1}] statement is required")
        if kind not in allowed_kinds:
            raise ValueError(f"claims[{index - 1}] has unsupported kind")
        if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) or ref not in evidence for ref in refs):
            raise ValueError(f"claims[{index - 1}] must reference evidence IDs in the packet")
        ref_kinds = {evidence[ref]["kind"] for ref in refs}
        if kind == "direct_observation" and not ({"trace", "outcome"} & ref_kinds):
            raise ValueError(f"claims[{index - 1}] direct observation must cite trace or outcome evidence")
        if kind == "direct_observation" and "outcome" not in ref_kinds and not any(
            evidence[ref]["kind"] == "trace"
            and any(evidence[ref]["row"].get(field) is not None for field in ("observation", "feedback", "terminal_result"))
            for ref in refs
        ):
            raise ValueError(f"claims[{index - 1}] direct observation must cite an observed packet field or run outcome")
        if kind == "gameplay_recorded_inference" and "saved_note" not in ref_kinds:
            raise ValueError(f"claims[{index - 1}] recorded inference must cite an original saved note")
        if kind == "post_run_hypothesis":
            if "trace" not in ref_kinds:
                raise ValueError(f"claims[{index - 1}] post-run hypothesis must cite observed trace evidence")
            alternatives = raw.get("alternatives")
            prediction = raw.get("prediction")
            if not isinstance(alternatives, list) or not alternatives or any(not isinstance(item, str) or not item.strip() for item in alternatives):
                raise ValueError(f"claims[{index - 1}] post-run hypothesis requires alternatives")
            if not isinstance(prediction, str) or not prediction.strip():
                raise ValueError(f"claims[{index - 1}] post-run hypothesis requires a distinguishing prediction")
        else:
            alternatives = raw.get("alternatives", [])
            prediction = raw.get("prediction")
            if not isinstance(alternatives, list) or any(not isinstance(item, str) for item in alternatives):
                raise ValueError(f"claims[{index - 1}] alternatives must be a list of strings")
            if prediction is not None and not isinstance(prediction, str):
                raise ValueError(f"claims[{index - 1}] prediction must be a string or null")
        confidence = raw.get("confidence")
        if confidence is not None and (
            isinstance(confidence, bool) or not isinstance(confidence, (int, float))
            or not math.isfinite(float(confidence)) or not 0 <= float(confidence) <= 1
        ):
            raise ValueError(f"claims[{index - 1}] confidence must be between 0 and 1")
        seen_claims.add(claim_id)
        validated.append({
            "claim_id": claim_id,
            "statement": statement,
            "kind": kind,
            "evidence_refs": list(dict.fromkeys(refs)),
            "alternatives": alternatives,
            "prediction": prediction,
            "confidence": confidence,
        })
    return validated


def import_trajectory_claims(*, run_id: str, claims_path: Path, runs_root: Path | None = None) -> dict[str, Any]:
    run_dir, session = _load_session(run_id, runs_root)
    packet_path = run_dir / "analysis" / "trajectory_packet.json"
    if not packet_path.is_file():
        raise FileNotFoundError("export trajectory packet before importing claims")
    ledger_path = run_dir / "analysis" / "trajectory_ledger.json"
    if ledger_path.exists():
        raise FileExistsError("trajectory ledger is already frozen; refusing to overwrite it")
    packet = _load_json(packet_path, "trajectory packet")
    if (
        not isinstance(packet, dict)
        or packet.get("schema_version") != PACKET_SCHEMA
        or packet.get("run_id") != run_id
        or not isinstance(packet.get("game"), dict)
    ):
        raise ValueError("trajectory packet is invalid or belongs to another run")
    packet_sha = sha256_file(packet_path)
    packet_index_ref = packet.get("trajectory_index")
    session_index_ref = session.get("trajectory_index")
    if packet.get("backend") != session.get("backend") or packet.get("game", {}).get("id") != session.get("game", {}).get("id"):
        raise ValueError("trajectory packet no longer matches its harness session")
    if packet.get("run_status") != session.get("status"):
        raise ValueError("trajectory packet run status no longer matches its harness session")
    if not isinstance(packet_index_ref, dict) or not isinstance(session_index_ref, dict):
        raise ValueError("trajectory packet or harness session has no trajectory index reference")
    if packet_index_ref.get("path") != session_index_ref.get("path") or packet_index_ref.get("sha256") != session_index_ref.get("sha256"):
        raise ValueError("trajectory packet index reference no longer matches its harness session")
    _safe_artifact(run_dir, packet_index_ref, "trajectory packet index")
    source_artifacts = packet.get("source_artifacts")
    if not isinstance(source_artifacts, list):
        raise ValueError("trajectory packet has no source artifact list")
    for ref in source_artifacts:
        _safe_artifact(run_dir, ref, "trajectory packet source artifact")
    claims_document = _load_json(claims_path.expanduser().resolve(), "claims file")
    claims_sha = sha256_file(claims_path.expanduser().resolve())
    claims = _validate_claims(packet, packet_sha, claims_document)
    digest_payload = {"packet_sha256": packet_sha, "claims_sha256": claims_sha, "claims": claims}
    ledger = {
        "schema_version": LEDGER_SCHEMA,
        "run_id": run_id,
        "game_id": packet["game"]["id"],
        "trajectory_frozen": True,
        "packet": {"path": "trajectory_packet.json", "sha256": packet_sha},
        "claims_input": {"filename": claims_path.name, "sha256": claims_sha},
        "trajectory_digest": _canonical_digest(digest_payload),
        "claims": claims,
        "source_audit": [],
        "probes": [],
    }
    _write_new_json(ledger_path, ledger)
    return {"run_id": run_id, "ledger_path": ledger_path.relative_to(run_dir).as_posix(), "trajectory_digest": ledger["trajectory_digest"], "claim_count": len(claims), "trajectory_frozen": True}
