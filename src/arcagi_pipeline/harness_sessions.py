from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Any

from .harness import HarnessCommand, HarnessExecution, HarnessRunSpec
from .harness_adapters.tycho import TychoAdapter
from .game_provenance import inspect_runtime_packages, inspect_tycho_environment


SESSION_SCHEMA = "arcagi-harness-session/v2"
LEGACY_SESSION_SCHEMA = "arcagi-harness-session/v1"
DEFAULT_RUNS_ROOT = Path(__file__).resolve().parents[2] / "runs"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_output(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout


def _git_source_provenance(root: Path, *, backend: str = "tycho") -> dict[str, Any]:
    """Identify the exact Git checkout, including tracked and untracked local edits."""
    try:
        git_root = Path(_git_output(root, "rev-parse", "--show-toplevel").decode("utf-8").strip()).resolve()
        if os.path.normcase(str(git_root)) != os.path.normcase(str(root.resolve())):
            return {
                "status": "unverified",
                "reason": f"{backend}_root_is_not_git_root",
                "source_commit": None,
                "working_tree_state": "unknown",
                "working_tree_sha256": None,
            }
        commit = _git_output(root, "rev-parse", "--verify", "HEAD").decode("ascii").strip()
        status = _git_output(root, "status", "--porcelain=v1", "--untracked-files=all", "-z")
        diff = _git_output(root, "diff", "HEAD", "--binary", "--no-ext-diff")
        untracked = _git_output(root, "ls-files", "--others", "--exclude-standard", "-z")
    except (OSError, subprocess.CalledProcessError, UnicodeDecodeError):
        return {
            "status": "unverified",
            "reason": "git_source_identity_unavailable",
            "source_commit": None,
            "working_tree_state": "unknown",
            "working_tree_sha256": None,
        }

    digest = hashlib.sha256()
    digest.update(f"arcagi-{backend}-worktree/v1\0".encode("ascii"))
    for value in (status, diff):
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
    for raw_path in sorted(path for path in untracked.split(b"\0") if path):
        path = root / os.fsdecode(raw_path)
        if path.is_symlink():
            content = b"symlink\0" + os.fsencode(os.readlink(path))
        elif path.is_file():
            content = b"file\0" + path.read_bytes()
        else:
            content = b"missing-or-special\0"
        digest.update(len(raw_path).to_bytes(8, "big"))
        digest.update(raw_path)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)

    return {
        "status": "verified",
        "source_commit": commit,
        "working_tree_state": "dirty" if status else "clean",
        "working_tree_sha256": digest.hexdigest(),
    }


def _assert_source_matches_session(session: dict[str, Any]) -> None:
    if session.get("schema_version") == LEGACY_SESSION_SCHEMA:
        raise ValueError(
            "legacy session source version is unverified; prepare a new session before execution or resume"
        )
    backend = session.get("backend")
    source = session.get(backend) if backend in {"tycho", "duck"} else None
    if not isinstance(source, dict) or not source.get("root"):
        raise ValueError("session has no verifiable harness source root")
    backend_label = "Tycho" if backend == "tycho" else "Duck"
    if backend == "tycho" and source.get("environments_dir"):
        recorded_root = Path(source["environments_dir"]).resolve()
        configured_root = Path(session.get("run_config", {}).get("environments_dir", "")).resolve()
        if configured_root != recorded_root:
            raise ValueError("Tycho environment root changed since harness-prepare")
    recorded = source.get("source_provenance")
    if not isinstance(recorded, dict) or recorded.get("status") != "verified":
        raise ValueError(
            f"{backend_label} source could not be verified at prepare; execution is disabled for this session"
        )
    current = _git_source_provenance(Path(source["root"]), backend=backend)
    if current != recorded:
        raise ValueError(f"{backend_label} source changed since harness-prepare; prepare a new session")
    recorded_environment = source.get("environment_provenance")
    if not isinstance(recorded_environment, dict) or recorded_environment.get("status") != "verified":
        raise ValueError(
            f"{backend_label} game source/version was not verified at prepare; execution is disabled"
        )
    try:
        if backend == "duck":
            from .duck_sessions import inspect_duck_environment

            current_environment = inspect_duck_environment(
                Path(source["environments_dir"]), session["game"]["id"]
            )
        else:
            current_environment = inspect_tycho_environment(
                Path(source["root"]),
                session["game"]["id"],
                source_dir=Path(recorded_environment["source_root"]),
            )
    except (KeyError, OSError, ValueError) as exc:
        raise ValueError(f"{backend_label} game environment cannot be verified since harness-prepare") from exc
    if current_environment != recorded_environment:
        raise ValueError(f"{backend_label} game environment changed since harness-prepare; prepare a new session")

    runtime = source.get("runtime_provenance")
    if not isinstance(runtime, dict) or runtime.get("status") != "verified":
        raise ValueError(
            f"{backend_label} engine/dependency versions were not verified at prepare; execution is disabled"
        )
    current_runtime = inspect_runtime_packages(str(source.get("python") or ""), backend)
    if current_runtime != runtime:
        raise ValueError(f"{backend_label} runtime packages changed since harness-prepare; prepare a new session")


def _source_provenance_summary(session: dict[str, Any]) -> dict[str, Any]:
    if session.get("schema_version") == LEGACY_SESSION_SCHEMA:
        return {
            "status": "legacy_unverified",
            "declared_source_commit": session.get("tycho", {}).get("source_commit"),
            "matches_prepared_source": False,
        }
    backend = session.get("backend")
    source = session.get(backend) if backend in {"tycho", "duck"} else None
    if not isinstance(source, dict) or not source.get("root"):
        return {"status": "unverified", "matches_prepared_source": False}
    recorded = source.get("source_provenance")
    current = _git_source_provenance(Path(source["root"]), backend=backend)
    matches = (
        isinstance(recorded, dict)
        and recorded.get("status") == "verified"
        and current.get("status") == "verified"
        and recorded == current
    )
    if matches:
        status = "verified"
    elif (
        isinstance(recorded, dict)
        and recorded.get("status") == "verified"
        and current.get("status") == "verified"
    ):
        status = "mismatch"
    else:
        status = current.get("status", "unverified")
    summary = {
        "status": status,
        "recorded": recorded,
        "current": current,
        "matches_prepared_source": matches,
    }
    recorded_environment = source.get("environment_provenance")
    try:
        if backend == "duck":
            from .duck_sessions import inspect_duck_environment

            current_environment = inspect_duck_environment(
                Path(source["environments_dir"]), session["game"]["id"]
            )
        else:
            current_environment = inspect_tycho_environment(
                Path(source["root"]),
                session["game"]["id"],
                source_dir=Path(recorded_environment["source_root"])
                if isinstance(recorded_environment, dict) and recorded_environment.get("source_root")
                else None,
            )
    except (KeyError, OSError, ValueError):
        current_environment = {"status": "unverified"}
    environment_matches = (
        isinstance(recorded_environment, dict)
        and recorded_environment.get("status") == "verified"
        and current_environment.get("status") == "verified"
        and recorded_environment == current_environment
    )
    environment_status = "verified" if environment_matches else (
        "mismatch" if (
            isinstance(recorded_environment, dict)
            and recorded_environment.get("status") == "verified"
            and current_environment.get("status") == "verified"
        ) else "unverified"
    )
    summary["environment"] = {
        "status": environment_status,
        "recorded": recorded_environment,
        "current": current_environment,
        "matches_prepared_source": environment_matches,
    }

    runtime = source.get("runtime_provenance")
    try:
        current_runtime = inspect_runtime_packages(str(source.get("python") or ""), backend)
    except (OSError, ValueError):
        current_runtime = {"status": "unverified"}
    runtime_matches = (
        isinstance(runtime, dict)
        and runtime.get("status") == "verified"
        and current_runtime.get("status") == "verified"
        and runtime == current_runtime
    )
    runtime_status = "verified" if runtime_matches else (
        "mismatch" if (
            isinstance(runtime, dict)
            and runtime.get("status") == "verified"
            and current_runtime.get("status") == "verified"
        ) else "unverified"
    )
    summary["runtime"] = {
        "status": runtime_status,
        "recorded": runtime,
        "current": current_runtime,
        "matches_prepared_runtime": runtime_matches,
    }
    nested_statuses = (environment_status, runtime_status)
    if summary["status"] == "verified" and any(value != "verified" for value in nested_statuses):
        summary["status"] = "mismatch" if "mismatch" in nested_statuses else "unverified"
    return summary


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _validate_id(value: str, label: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{label} must contain 1-64 letters, digits, underscores, or hyphens")
    return value


def _runs_root(runs_root: Path | None) -> Path:
    return (runs_root or DEFAULT_RUNS_ROOT).expanduser().resolve()


def _session_path(run_id: str, runs_root: Path | None = None) -> Path:
    safe_id = _validate_id(run_id, "run_id")
    return _runs_root(runs_root) / safe_id / "session.json"


def _load_session(run_id: str, runs_root: Path | None = None) -> tuple[Path, dict[str, Any]]:
    session_path = _session_path(run_id, runs_root)
    try:
        session = json.loads(session_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"prepared run not found: {run_id}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"run session file is invalid: {session_path}") from exc
    if session.get("schema_version") not in {SESSION_SCHEMA, LEGACY_SESSION_SCHEMA} or session.get("run_id") != run_id:
        raise ValueError(f"unsupported or mismatched run session: {run_id}")
    return session_path.parent, session


def _public_env(environment: dict[str, str]) -> dict[str, str]:
    return {
        key: "<redacted>" if any(
            marker in key.lower()
            for marker in ("key", "secret", "token", "password", "authorization")
        ) else value
        for key, value in environment.items()
    }


def _build_spec(session: dict[str, Any], run_dir: Path) -> HarnessRunSpec:
    config = {
        "approach": "tycho",
        "seed": session["run_config"]["seed"],
        "operation_mode": session["run_config"]["operation_mode"],
    }
    if session["run_config"].get("environments_dir"):
        config["environments_dir"] = session["run_config"]["environments_dir"]
    if session["run_config"].get("max_actions") is not None:
        config["max_actions"] = int(session["run_config"]["max_actions"])
    return HarnessRunSpec(
        session["game"]["id"],
        run_dir / session["tycho"]["output_dir"],
        config,
    )


def prepare_tycho_session(
    *,
    run_id: str,
    tycho_root: Path,
    game_id: str,
    config_path: Path,
    python: str | None = None,
    seed: int = 0,
    operation_mode: str = "offline",
    runs_root: Path | None = None,
    game_source_dir: Path | None = None,
    environments_dir: Path | None = None,
    max_actions: int | None = None,
) -> dict[str, Any]:
    """Prepare a secret-free local session; this only builds files and a command preview."""
    _validate_id(run_id, "run_id")
    _validate_id(game_id, "game_id")
    if operation_mode not in {"normal", "offline", "online"}:
        raise ValueError("operation_mode must be normal, offline, or online")
    if max_actions is not None and (isinstance(max_actions, bool) or int(max_actions) <= 0):
        raise ValueError("Tycho --max-actions must be a positive integer when supplied")

    root = tycho_root.expanduser().resolve()
    if not root.is_dir() or not (root / "tycho" / "harness" / "run_parallel.py").is_file():
        raise FileNotFoundError(f"Tycho harness source not found under: {root}")
    source_provenance = _git_source_provenance(root, backend="tycho")
    environments_root = environments_dir.expanduser().resolve(strict=True) if environments_dir else None
    if environments_root is not None and game_source_dir is None:
        matches = []
        for metadata_path in environments_root.rglob("metadata.json"):
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(metadata, dict) and metadata.get("game_id") == game_id:
                matches.append(metadata_path.parent)
        if len(matches) != 1:
            raise ValueError(f"expected exactly one Tycho environment for {game_id} under {environments_root}")
        game_source_dir = matches[0]
    environment_provenance = inspect_tycho_environment(root, game_id, source_dir=game_source_dir)
    config = config_path.expanduser().resolve()
    if not config.is_file():
        raise FileNotFoundError(f"Tycho config file not found: {config}")
    if config.suffix.lower() not in {".yaml", ".yml", ".json"}:
        raise ValueError("Tycho config must be YAML or JSON")

    base = _runs_root(runs_root)
    run_dir = base / run_id
    if run_dir.exists() and any(run_dir.iterdir()):
        raise FileExistsError(f"run directory is not empty: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)
    output_dir = run_dir / "tycho"
    output_dir.mkdir(parents=True, exist_ok=True)

    run_config = {"seed": int(seed), "operation_mode": operation_mode}
    if environments_root is not None:
        run_config["environments_dir"] = str(environments_root)
    if max_actions is not None:
        run_config["max_actions"] = int(max_actions)
    spec_config = {
        "approach": "tycho",
        "seed": run_config["seed"],
        "operation_mode": operation_mode,
    }
    if environments_root is not None:
        spec_config["environments_dir"] = str(environments_root)
    if max_actions is not None:
        spec_config["max_actions"] = int(max_actions)
    spec = HarnessRunSpec(game_id, output_dir, spec_config)
    adapter = TychoAdapter(root, python=python)
    runtime_provenance = inspect_runtime_packages(adapter.python, "tycho")
    command = adapter.run_command(spec, config_path=config, resume=False)

    session: dict[str, Any] = {
        "schema_version": SESSION_SCHEMA,
        "run_id": run_id,
        "backend": "tycho",
        "game": {
            "id": game_id,
            "kind": "original_game",
            "version": (
                environment_provenance.get("version", "unknown")
                if environment_provenance.get("status") == "verified"
                else "unknown"
            ),
        },
        "tycho": {
            "root": str(root),
            "environments_dir": str(environments_root) if environments_root is not None else None,
            "source_commit": source_provenance["source_commit"],
            "source_provenance": source_provenance,
            "environment_provenance": environment_provenance,
            "runtime_provenance": runtime_provenance,
            "output_dir": "tycho",
            "python": adapter.python,
        },
        "config": {
            "path": str(config),
            "sha256": sha256_file(config),
            "credential_values_persisted": False,
            "credential_source": "inherited_process_environment",
        },
        "run_config": run_config,
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


def _native_state_exists(output_dir: Path, game_id: str) -> bool:
    short_id = game_id.split("-", maxsplit=1)[0]
    return (
        (output_dir / "manifest.json").is_file()
        or (output_dir / "run_spec.json").is_file()
        or (output_dir / f"game_{short_id}.json").is_file()
        or (output_dir / "status").is_dir()
    )


def _native_resume_needed(output_dir: Path, game_id: str) -> bool:
    """Use Tycho's own record/status to distinguish clean completion from resumable partial work."""
    short_id = game_id.split("-", maxsplit=1)[0]
    record_path = output_dir / f"game_{short_id}.json"
    if record_path.is_file():
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return True
        if record.get("partial") or record.get("error"):
            return True
        return False

    status_path = output_dir / "status" / short_id / "status.json"
    if status_path.is_file():
        try:
            state = json.loads(status_path.read_text(encoding="utf-8")).get("state")
        except (OSError, json.JSONDecodeError):
            return True
        return state not in {"finished", "completed", "complete"}
    return _native_state_exists(output_dir, game_id)

def _artifact_ref(path: Path, run_dir: Path) -> dict[str, str]:
    return {
        "path": path.resolve().relative_to(run_dir.resolve()).as_posix(),
        "sha256": sha256_file(path),
    }


def _collect_trajectory_index(run_dir: Path, session: dict[str, Any]) -> dict[str, Any]:
    output_dir = run_dir / session["tycho"]["output_dir"]
    game_id = session["game"]["id"]
    short_id = game_id.split("-", maxsplit=1)[0]
    artifacts: dict[str, dict[str, str]] = {}

    manifest_path = output_dir / "manifest.json"
    if manifest_path.is_file():
        artifacts["manifest"] = _artifact_ref(manifest_path, run_dir)

    record_path = output_dir / f"game_{short_id}.json"
    game_summary: dict[str, Any] | None = None
    if record_path.is_file():
        artifacts["game_record"] = _artifact_ref(record_path, run_dir)
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
            trace = record.get("trace") if isinstance(record, dict) else None
            game_summary = {
                key: record.get(key)
                for key in ("game_id", "n_levels", "levels_completed", "total_actions", "final_state", "partial")
                if key in record
            }
            game_summary["trace_steps"] = len(trace) if isinstance(trace, list) else None
            game_summary["has_error"] = bool(record.get("error")) if isinstance(record, dict) else False
        except (OSError, json.JSONDecodeError):
            game_summary = {"trace_steps": None, "record_parse_error": True}

    status_dir = output_dir / "status" / short_id
    status_path = status_dir / "status.json"
    if status_path.is_file():
        artifacts["game_status"] = _artifact_ref(status_path, run_dir)
    events_path = status_dir / "events.jsonl"
    if events_path.is_file():
        artifacts["game_status_events"] = _artifact_ref(events_path, run_dir)

    notes: list[dict[str, str]] = []
    for notes_dir in sorted(path for path in output_dir.rglob("notes") if path.is_dir()):
        for path in sorted(candidate for candidate in notes_dir.rglob("*") if candidate.is_file()):
            notes.append(_artifact_ref(path, run_dir))

    return {
        "schema_version": "arcagi-trajectory-index/v1",
        "provenance": "tycho_original_game_run",
        "game_id": game_id,
        "raw_trace_preserved": "game_record" in artifacts,
        "analysis_performed": False,
        "game_summary": game_summary,
        "artifacts": artifacts,
        "saved_notes": notes,
    }


def _summary(run_dir: Path, session: dict[str, Any]) -> dict[str, Any]:
    summary = {
        "run_id": session["run_id"],
        "backend": session["backend"],
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
            valid = True
            index_ref = session.get("trajectory_index") or {}
            if index_ref.get("sha256") != sha256_file(index_path):
                valid = False
            for refs in [index.get("artifacts", {}).values(), [*index.get("saved_notes", [])]]:
                for ref in refs:
                    target = run_dir / ref["path"]
                    if not target.is_file() or sha256_file(target) != ref["sha256"]:
                        valid = False
            summary["artifact_hashes_valid"] = valid
            summary["game_summary"] = index.get("game_summary")
            summary["saved_note_count"] = len(index.get("saved_notes", []))
            native_status = index.get("artifacts", {}).get("game_status")
            if native_status:
                status_file = run_dir / native_status["path"]
                try:
                    raw_status = json.loads(status_file.read_text(encoding="utf-8"))
                    summary["native_game_status"] = {
                        key: raw_status[key]
                        for key in ("state", "attempt", "action_count", "levels_completed", "current_level_index", "heartbeat_at")
                        if key in raw_status
                    }
                except (OSError, json.JSONDecodeError):
                    summary["native_game_status"] = {"status_file_parse_error": True}
        except (OSError, KeyError, json.JSONDecodeError):
            summary["artifact_hashes_valid"] = False
    return summary


def execute_tycho_session(
    *,
    run_id: str,
    resume: bool = False,
    runs_root: Path | None = None,
    executor: Callable[[HarnessCommand], HarnessExecution] | None = None,
) -> dict[str, Any]:
    """Execute only when explicitly called; tests can inject an offline fake executor."""
    run_dir, session = _load_session(run_id, runs_root)
    _assert_source_matches_session(session)
    output_dir = run_dir / session["tycho"]["output_dir"]
    config_path = Path(session["config"]["path"])
    if not config_path.is_file() or sha256_file(config_path) != session["config"]["sha256"]:
        raise ValueError("Tycho config is missing or changed since harness-prepare")

    if resume:
        if session["status"] not in {"failed", "interrupted", "running", "completed"}:
            raise ValueError("resume requires a failed, interrupted, running, or partial completed session")
        if not _native_state_exists(output_dir, session["game"]["id"]):
            raise ValueError("Tycho native run state is missing; this session cannot be resumed")
        if session["status"] == "completed" and not _native_resume_needed(output_dir, session["game"]["id"]):
            raise ValueError("Tycho reports a clean completed game; refusing a redundant resume")
    else:
        if session["status"] != "prepared":
            raise ValueError("this session has already been executed; use --resume after a failed run")
        if any(output_dir.iterdir()):
            raise FileExistsError("Tycho output directory is not empty; refusing to overwrite it")

    tycho_root = Path(session["tycho"]["root"])
    adapter = TychoAdapter(tycho_root, python=session["tycho"].get("python"))
    spec = _build_spec(session, run_dir)
    command = adapter.run_command(spec, config_path=config_path, resume=resume)

    attempt: dict[str, Any] = {
        "attempt": len(session["attempts"]) + 1,
        "resume": resume,
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
    index = _collect_trajectory_index(run_dir, session)
    _write_json(run_dir / "trajectory_index.json", index)
    session["trajectory_index"] = {
        "path": "trajectory_index.json",
        "sha256": sha256_file(run_dir / "trajectory_index.json"),
    }
    _write_json(run_dir / "session.json", session)
    return _summary(run_dir, session)


def get_tycho_session_status(*, run_id: str, runs_root: Path | None = None) -> dict[str, Any]:
    run_dir, session = _load_session(run_id, runs_root)
    return _summary(run_dir, session)


def execute_harness_session(
    *,
    run_id: str,
    resume: bool = False,
    runs_root: Path | None = None,
) -> dict[str, Any]:
    _run_dir, session = _load_session(run_id, runs_root)
    backend = session.get("backend")
    if backend == "tycho":
        return execute_tycho_session(run_id=run_id, resume=resume, runs_root=runs_root)
    if backend == "duck":
        from .duck_sessions import execute_duck_session

        return execute_duck_session(run_id=run_id, resume=resume, runs_root=runs_root)
    raise ValueError(f"unsupported harness backend in run session: {backend}")


def get_harness_session_status(*, run_id: str, runs_root: Path | None = None) -> dict[str, Any]:
    _run_dir, session = _load_session(run_id, runs_root)
    backend = session.get("backend")
    if backend == "tycho":
        return get_tycho_session_status(run_id=run_id, runs_root=runs_root)
    if backend == "duck":
        from .duck_sessions import get_duck_session_status

        return get_duck_session_status(run_id=run_id, runs_root=runs_root)
    raise ValueError(f"unsupported harness backend in run session: {backend}")
