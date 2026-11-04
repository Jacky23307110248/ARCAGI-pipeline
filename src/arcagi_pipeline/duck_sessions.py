from __future__ import annotations

import hashlib
import json
import math
import subprocess
from pathlib import Path
from typing import Any, Callable

from .harness import HarnessCommand, HarnessExecution, HarnessRunSpec
from .harness_adapters.duck import DuckAdapter
from .game_provenance import inspect_runtime_packages
from .harness_sessions import (
    SESSION_SCHEMA,
    _assert_source_matches_session,
    _git_source_provenance,
    _load_session,
    _now,
    _public_env,
    _runs_root,
    _source_provenance_summary,
    _validate_id,
    _write_json,
    sha256_file,
)


def _directory_fingerprint(root: Path) -> tuple[str, list[dict[str, str]]]:
    files: list[dict[str, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix.lower() == ".pyc":
            continue
        files.append({
            "path": path.relative_to(root).as_posix(),
            "sha256": sha256_file(path),
        })
    if not files:
        raise ValueError(f"Duck game source directory contains no files: {root}")
    payload = json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest(), files


def inspect_duck_environment(environments_dir: Path, game_id: str) -> dict[str, Any]:
    """Bind one official Duck game ID to its exact local metadata and source tree."""
    root = environments_dir.expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Duck environments directory not found: {root}")

    matches: list[tuple[Path, dict[str, Any]]] = []
    for metadata_path in root.rglob("metadata.json"):
        if "__pycache__" in metadata_path.parts:
            continue
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(metadata, dict) and metadata.get("game_id") == game_id:
            matches.append((metadata_path, metadata))
    if len(matches) != 1:
        raise ValueError(
            f"expected exactly one metadata.json for official game {game_id}; found {len(matches)}"
        )

    metadata_path, metadata = matches[0]
    source_dir = metadata_path.parent.resolve()
    class_name = str(metadata.get("class_name") or "").strip()
    if not class_name:
        base_id = game_id.split("-", maxsplit=1)[0]
        class_name = base_id[:1].upper() + base_id[1:]
    candidates = [source_dir / f"{class_name.lower()}.py", source_dir / f"{class_name}.py"]
    game_source = next((candidate for candidate in candidates if candidate.is_file()), None)
    if game_source is None:
        raise FileNotFoundError(
            f"game source for {game_id} not found; expected one of: "
            + ", ".join(str(path) for path in candidates)
        )

    tree_sha256, source_files = _directory_fingerprint(source_dir)
    return {
        "status": "verified",
        "game_id": game_id,
        "environments_dir": str(root),
        "metadata_path": metadata_path.relative_to(root).as_posix(),
        "metadata_sha256": sha256_file(metadata_path),
        "source_dir": str(source_dir),
        "game_source_path": game_source.relative_to(source_dir).as_posix(),
        "source_tree_sha256": tree_sha256,
        "source_files": source_files,
    }


def prepare_duck_session(
    *,
    run_id: str,
    duck_root: Path,
    game_id: str,
    environments_dir: Path,
    model: str,
    max_actions: int,
    max_runtime_minutes: float = 10.0,
    python: str | None = None,
    runs_root: Path | None = None,
    preflight_executor: Callable[[HarnessCommand], HarnessExecution] | None = None,
) -> dict[str, Any]:
    """Create a preview session after no-model official-ID and local-source checks."""
    _validate_id(run_id, "run_id")
    _validate_id(game_id, "game_id")
    model = str(model).strip()
    if not model:
        raise ValueError("Duck requires --model")
    if isinstance(max_actions, bool) or int(max_actions) <= 0:
        raise ValueError("Duck requires a positive --max-actions")
    if isinstance(max_runtime_minutes, bool):
        raise ValueError("Duck requires a positive --max-runtime-minutes")
    try:
        max_runtime_minutes = float(max_runtime_minutes)
    except (TypeError, ValueError) as exc:
        raise ValueError("Duck requires a positive --max-runtime-minutes") from exc
    if not math.isfinite(max_runtime_minutes) or max_runtime_minutes <= 0:
        raise ValueError("Duck requires a positive --max-runtime-minutes")

    root = duck_root.expanduser().resolve()
    runner = root / "ARC3-Inference" / "inference" / "framework" / "run.py"
    if not root.is_dir() or not runner.is_file():
        raise FileNotFoundError(f"Duck run entrypoint not found under: {root}")
    source_provenance = _git_source_provenance(root, backend="duck")
    if source_provenance.get("status") != "verified":
        raise ValueError("Duck source Git revision/worktree could not be verified")

    environment_root = environments_dir.expanduser().resolve()
    if not environment_root.is_dir():
        raise FileNotFoundError(f"Duck environments directory not found: {environment_root}")

    base = _runs_root(runs_root)
    run_dir = base / run_id
    if run_dir.exists() and any(run_dir.iterdir()):
        raise FileExistsError(f"run directory is not empty: {run_dir}")
    output_dir = run_dir / "duck"
    spec = HarnessRunSpec(game_id, output_dir, {"environments_dir": str(environment_root)})
    adapter = DuckAdapter(root, python=python)
    preflight = adapter.preflight_command(spec)
    try:
        check = (
            preflight_executor(preflight)
            if preflight_executor is not None
            else adapter.execute(preflight, timeout=30)
        )
    except (subprocess.TimeoutExpired, TimeoutError) as exc:
        raise ValueError("Duck --list-games preflight timed out") from exc
    if check.exit_code != 0:
        raise ValueError("Duck --list-games preflight failed")
    resolved_games = [line.strip() for line in check.stdout.splitlines() if line.strip()]
    if len(resolved_games) != 1:
        raise ValueError("Duck --list-games did not resolve exactly one official game ID")
    resolved_game_id = _validate_id(resolved_games[0], "resolved game_id")

    environment_provenance = inspect_duck_environment(environment_root, resolved_game_id)
    config = {
        "agent": "inference",
        "model": model,
        "run_name": f"arcagi-{run_id}",
        "environments_dir": str(environment_root),
        "max_actions": int(max_actions),
        "max_runtime_minutes": max_runtime_minutes,
    }
    run_spec = HarnessRunSpec(resolved_game_id, output_dir, config)
    command = adapter.run_command(run_spec)
    runtime_provenance = inspect_runtime_packages(adapter.python, "duck")

    run_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    session: dict[str, Any] = {
        "schema_version": SESSION_SCHEMA,
        "run_id": run_id,
        "backend": "duck",
        "game": {
            "id": resolved_game_id,
            "requested_id": game_id,
            "kind": "official_game_id_local_environment",
            "version": resolved_game_id,
        },
        "duck": {
            "root": str(root),
            "source_commit": source_provenance["source_commit"],
            "source_provenance": source_provenance,
            "environments_dir": str(environment_root),
            "environment_provenance": environment_provenance,
            "runtime_provenance": runtime_provenance,
            "output_dir": "duck",
            "python": adapter.python,
        },
        "config": {
            "model": model,
            "credential_values_persisted": False,
            "credential_source": "inherited_process_environment",
        },
        "run_config": {
            "max_actions": int(max_actions),
            "max_runtime_minutes": max_runtime_minutes,
            "n_passes": 1,
            "concurrent_jobs": 1,
            "deployment_target": "inline",
            "operation_mode": "offline",
        },
        "preflight": {
            "performed": True,
            "command": list(preflight.command),
            "exit_code": check.exit_code,
            "resolved_game_id": resolved_game_id,
            "model_or_game_execution_started": False,
        },
        "status": "prepared",
        "created_at": _now(),
        "updated_at": _now(),
        "attempts": [],
        "command_preview": {
            "argv": list(command.command),
            "working_directory": str(command.working_directory),
            "environment_overrides": _public_env(command.environment_overrides),
            "execution_started": False,
        },
        "trajectory_index": None,
    }
    _write_json(run_dir / "session.json", session)
    return session


def _artifact_kind(path: Path) -> str:
    name = path.name.lower()
    if name == "run_config.json":
        return "run_config"
    if name.endswith("_viewer_data.json"):
        return "viewer_data"
    if "_events.jsonl" in name or "_events.json.gz" in name:
        return "raw_event_sidecar"
    if path.parts and path.parts[0] == "transcripts":
        return "saved_transcript"
    if path.parts and path.parts[0] == "solver_analysis":
        return "analysis_html"
    if "_runtime_state.json" in name:
        return "runtime_state"
    return "native_artifact"


def _collect_duck_trajectory_index(run_dir: Path, session: dict[str, Any]) -> dict[str, Any]:
    output_dir = run_dir / session["duck"]["output_dir"]
    candidates: set[Path] = set()
    run_config = output_dir / "run_config.json"
    if run_config.is_file():
        candidates.add(run_config)
    for dirname in ("artifacts", "transcripts", "solver_analysis"):
        directory = output_dir / dirname
        if directory.is_dir():
            candidates.update(path for path in directory.rglob("*") if path.is_file())

    artifacts: list[dict[str, str]] = []
    game_summaries: list[dict[str, Any]] = []
    has_viewer = False
    has_events = False
    for path in sorted(candidates):
        relative = path.relative_to(run_dir).as_posix()
        kind = _artifact_kind(path.relative_to(output_dir))
        artifacts.append({"kind": kind, "path": relative, "sha256": sha256_file(path)})
        if kind == "viewer_data":
            has_viewer = True
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                game_summaries.append({
                    key: raw[key]
                    for key in ("game_id", "status", "levels_completed", "total_levels", "final_score")
                    if key in raw
                })
            except (OSError, json.JSONDecodeError):
                game_summaries.append({"record_parse_error": True})
        elif kind == "raw_event_sidecar":
            has_events = True

    trajectory_status = "complete" if has_viewer and has_events else (
        "partial" if has_viewer or has_events else "missing"
    )
    return {
        "schema_version": "arcagi-trajectory-index/v1",
        "provenance": "duck_original_game_run",
        "game_id": session["game"]["id"],
        "raw_events_preserved": has_events,
        "trajectory_status": trajectory_status,
        "analysis_performed": False,
        "game_summaries": game_summaries,
        "artifacts": artifacts,
    }


def _duck_summary(run_dir: Path, session: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "run_id": session["run_id"],
        "backend": "duck",
        "game": session["game"],
        "status": session["status"],
        "attempt_count": len(session.get("attempts", [])),
        "run_dir": str(run_dir),
        "trajectory_index": session.get("trajectory_index"),
        "source_provenance": _source_provenance_summary(session),
    }
    if session.get("attempts"):
        summary["last_exit_code"] = session["attempts"][-1].get("exit_code")
    index_path = run_dir / "trajectory_index.json"
    if index_path.is_file():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
            index_ref = session.get("trajectory_index") or {}
            valid = index_ref.get("sha256") == sha256_file(index_path)
            for artifact in index.get("artifacts", []):
                target = run_dir / artifact["path"]
                if not target.is_file() or sha256_file(target) != artifact["sha256"]:
                    valid = False
            summary["artifact_hashes_valid"] = valid
            summary["trajectory_status"] = index.get("trajectory_status", "missing")
            summary["game_summaries"] = index.get("game_summaries", [])
            summary["saved_transcript_count"] = sum(
                artifact.get("kind") == "saved_transcript"
                for artifact in index.get("artifacts", [])
            )
        except (OSError, KeyError, json.JSONDecodeError):
            summary["artifact_hashes_valid"] = False
    else:
        summary["trajectory_status"] = "missing"
        summary["artifact_hashes_valid"] = None
    return summary


def execute_duck_session(
    *,
    run_id: str,
    resume: bool = False,
    runs_root: Path | None = None,
    executor: Callable[[HarnessCommand], HarnessExecution] | None = None,
) -> dict[str, Any]:
    run_dir, session = _load_session(run_id, runs_root)
    if session.get("backend") != "duck":
        raise ValueError(f"run {run_id} is not a Duck session")
    if resume:
        raise ValueError("Duck does not support native resume; prepare a new run_id")
    _assert_source_matches_session(session)

    output_dir = run_dir / session["duck"]["output_dir"]
    if session["status"] != "prepared":
        raise ValueError("Duck session has already been executed; prepare a new run_id")
    if any(output_dir.iterdir()):
        raise FileExistsError("Duck output directory is not empty; refusing to overwrite it")

    adapter = DuckAdapter(Path(session["duck"]["root"]), python=session["duck"]["python"])
    spec = HarnessRunSpec(session["game"]["id"], output_dir, {
        "agent": "inference",
        "model": session["config"]["model"],
        "run_name": f"arcagi-{run_id}",
        "environments_dir": session["duck"]["environments_dir"],
        "max_actions": session["run_config"]["max_actions"],
        "max_runtime_minutes": session["run_config"]["max_runtime_minutes"],
    })
    command = adapter.run_command(spec)
    attempt: dict[str, Any] = {
        "attempt": len(session["attempts"]) + 1,
        "resume": False,
        "started_at": _now(),
        "exit_code": None,
    }
    session["status"] = "running"
    session["updated_at"] = _now()
    session["command_preview"] = {
        "argv": list(command.command),
        "working_directory": str(command.working_directory),
        "environment_overrides": _public_env(command.environment_overrides),
        "execution_started": True,
    }
    session["attempts"].append(attempt)
    _write_json(run_dir / "session.json", session)

    try:
        result = executor(command) if executor is not None else adapter.execute(command)
    except (subprocess.TimeoutExpired, TimeoutError) as exc:
        attempt["finished_at"] = _now()
        attempt["error_type"] = type(exc).__name__
        session["status"] = "interrupted"
    except KeyboardInterrupt:
        attempt["finished_at"] = _now()
        attempt["error_type"] = "KeyboardInterrupt"
        session["status"] = "interrupted"
        session["updated_at"] = _now()
        index = _collect_duck_trajectory_index(run_dir, session)
        _write_json(run_dir / "trajectory_index.json", index)
        session["trajectory_index"] = {
            "path": "trajectory_index.json",
            "sha256": sha256_file(run_dir / "trajectory_index.json"),
        }
        _write_json(run_dir / "session.json", session)
        raise
    except Exception as exc:
        attempt["finished_at"] = _now()
        attempt["error_type"] = type(exc).__name__
        session["status"] = "failed"
    else:
        attempt["finished_at"] = _now()
        attempt["exit_code"] = int(result.exit_code)
        session["status"] = "completed" if result.exit_code == 0 else "failed"

    session["updated_at"] = _now()
    index = _collect_duck_trajectory_index(run_dir, session)
    _write_json(run_dir / "trajectory_index.json", index)
    session["trajectory_index"] = {
        "path": "trajectory_index.json",
        "sha256": sha256_file(run_dir / "trajectory_index.json"),
    }
    _write_json(run_dir / "session.json", session)
    return _duck_summary(run_dir, session)


def get_duck_session_status(*, run_id: str, runs_root: Path | None = None) -> dict[str, Any]:
    run_dir, session = _load_session(run_id, runs_root)
    if session.get("backend") != "duck":
        raise ValueError(f"run {run_id} is not a Duck session")
    return _duck_summary(run_dir, session)
