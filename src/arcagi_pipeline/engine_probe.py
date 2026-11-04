from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol
from .game_provenance import inspect_runtime_packages, inspect_tycho_environment, sha256_file


class EngineProbeAdapter(Protocol):
    """Small game-specific bridge to the original engine's reset/step API."""

    backend: str
    game_id: str

    def reset(self) -> Any: ...

    def step(self, action: str | Mapping[str, Any]) -> Any: ...


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.name
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "tolist"):
        return _jsonable(value.tolist())
    if hasattr(value, "item"):
        return _jsonable(value.item())
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    raise TypeError(f"engine observation contains unsupported value: {type(value).__name__}")


def summarize_observation(observation: Any) -> dict[str, Any]:
    """Keep the player-visible state needed by rule probes, not engine internals."""
    if observation is None:
        raise ValueError("engine returned no observation")
    raw = getattr(observation, "raw", observation)
    frames = getattr(raw, "frame", None)
    return {
        "game_id": _jsonable(getattr(raw, "game_id", None)),
        "state": _jsonable(getattr(raw, "state", None)),
        "levels_completed": _jsonable(getattr(raw, "levels_completed", None)),
        "win_levels": _jsonable(getattr(raw, "win_levels", None)),
        "available_actions": _jsonable(getattr(raw, "available_actions", None)),
        "frames": _jsonable(frames) if frames is not None else None,
    }


def _parse_action(action: str | Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    if isinstance(action, str):
        name, data = action, {}
    elif isinstance(action, Mapping):
        name = action.get("action", action.get("name"))
        data = action.get("data", {})
    else:
        raise TypeError("probe action must be an action name or an object")
    if not isinstance(name, str) or not name or name != name.upper():
        raise ValueError("probe action must have an uppercase engine action name")
    if not isinstance(data, Mapping):
        raise ValueError("probe action data must be an object")
    return name, dict(data)


class TychoArcadeProbe:
    """Direct offline ``arc_agi.Arcade`` adapter; it never invokes a Tycho agent."""

    backend = "tycho"

    def __init__(self, arcade: Any, game_id: str, *, seed: int = 0, game_action_enum: Any = None):
        if not isinstance(game_id, str) or not game_id:
            raise ValueError("Tycho engine probe requires its bound game_id")
        self.arcade = arcade
        self.game_id = game_id
        self.seed = int(seed)
        self.game_action_enum = game_action_enum
        self.environment: Any = None

    def _actions(self) -> Any:
        if self.game_action_enum is None:
            try:
                from arcengine import GameAction
            except ImportError as exc:
                raise RuntimeError("Tycho engine probe requires arcengine in the selected Python environment") from exc
            self.game_action_enum = GameAction
        return self.game_action_enum

    def reset(self) -> Any:
        # arc.make creates a fresh seeded environment; reset() obtains its initial visible frame.
        self.environment = self.arcade.make(self.game_id, seed=self.seed)
        if self.environment is None:
            raise RuntimeError(f"arcade.make returned no environment for {self.game_id}")
        observation = self.environment.reset()
        if observation is None:
            raise RuntimeError("original engine reset returned no observation")
        return observation

    def step(self, action: str | Mapping[str, Any]) -> Any:
        if self.environment is None:
            raise RuntimeError("call reset() before step()")
        name, data = _parse_action(action)
        try:
            engine_action = self._actions()[name]
        except KeyError as exc:
            raise ValueError(f"unknown engine action: {name}") from exc
        return self.environment.step(engine_action, data=data or None)


class TaafGameProbe:
    """Direct TAAF ``Game.start_game/execute_action`` adapter; no Duck solver is started."""

    backend = "duck"

    def __init__(
        self,
        game_factory: Callable[[], Any],
        *,
        game_id: str,
        action_input_type: Any = None,
        game_action_enum: Any = None,
        run_session_factory: Callable[[], Any] | None = None,
    ):
        if not isinstance(game_id, str) or not game_id:
            raise ValueError("TAAF engine probe requires its bound game_id")
        self.game_factory = game_factory
        self.game_id = game_id
        self.action_input_type = action_input_type
        self.game_action_enum = game_action_enum
        self.run_session_factory = run_session_factory
        self.game: Any = None

    def _engine_types(self) -> tuple[Any, Any]:
        if self.action_input_type is None or self.game_action_enum is None:
            try:
                import arcengine
            except ImportError as exc:
                raise RuntimeError("TAAF engine probe requires arcengine in the selected Python environment") from exc
            self.action_input_type = self.action_input_type or arcengine.ActionInput
            self.game_action_enum = self.game_action_enum or arcengine.GameAction
        return self.action_input_type, self.game_action_enum

    def reset(self) -> Any:
        self.game = self.game_factory()
        if self.game is None:
            raise RuntimeError("TAAF game factory returned no game instance")
        session = self.run_session_factory() if self.run_session_factory else None
        observation = self.game.start_game(session)
        if observation is None:
            raise RuntimeError("TAAF game start_game returned no observation")
        return observation

    def step(self, action: str | Mapping[str, Any]) -> Any:
        if self.game is None:
            raise RuntimeError("call reset() before step()")
        name, data = _parse_action(action)
        action_input_type, game_action_enum = self._engine_types()
        try:
            engine_action = game_action_enum[name]
        except KeyError as exc:
            raise ValueError(f"unknown engine action: {name}") from exc
        return self.game.execute_action(action_input_type(id=engine_action, data=data))


@dataclass(frozen=True)
class ProbeBinding:
    game_source: dict[str, Any]
    runtime: dict[str, Any]

    def __post_init__(self) -> None:
        if self.game_source.get("status") != "verified":
            raise ValueError("engine probes require verified, version-bound game source")
        if not isinstance(self.game_source.get("game_id"), str) or not self.game_source["game_id"]:
            raise ValueError("engine probe source binding must include its game_id")
        if not isinstance(self.game_source.get("backend"), str) or not self.game_source["backend"]:
            raise ValueError("engine probe source binding must include its backend")
        if self.runtime.get("status") != "verified":
            raise ValueError("engine probes require verified, version-bound engine dependencies")


class VersionBoundEngineProbe:
    """Re-check provenance when opening each engine session, then expose cheap single steps."""

    def __init__(
        self,
        adapter: EngineProbeAdapter,
        binding: ProbeBinding,
        current_binding: Callable[[], ProbeBinding],
    ):
        adapter_backend = getattr(adapter, "backend", None)
        adapter_game_id = getattr(adapter, "game_id", None)
        if adapter_backend != binding.game_source["backend"]:
            raise ValueError("engine probe adapter backend does not match its verified source binding")
        if adapter_game_id != binding.game_source["game_id"]:
            raise ValueError("engine probe adapter game_id does not match its verified source binding")
        self.adapter = adapter
        self.binding = binding
        self.current_binding = current_binding
        self._session_bound = False

    def _check_binding(self) -> None:
        current = self.current_binding()
        if current.game_source != self.binding.game_source:
            raise ValueError("game source changed since the engine probe was bound")
        if current.runtime != self.binding.runtime:
            raise ValueError("engine/dependency runtime changed since the engine probe was bound")

    def reset(self) -> dict[str, Any]:
        self._check_binding()
        summarize = getattr(self.adapter, "summarize", summarize_observation)
        observation = summarize(self.adapter.reset())
        self._session_bound = True
        return observation

    def step(self, action: str | Mapping[str, Any]) -> dict[str, Any]:
        if not self._session_bound:
            raise RuntimeError("call reset() to open a version-verified engine probe session before step()")
        summarize = getattr(self.adapter, "summarize", summarize_observation)
        return summarize(self.adapter.step(action))


def bind_probe_to_harness_session(
    adapter: EngineProbeAdapter,
    *,
    run_id: str,
    runs_root: Path | None = None,
) -> VersionBoundEngineProbe:
    """Build a probe guard from an existing Tycho/Duck session's verified provenance."""
    from .harness_sessions import _assert_source_matches_session, _load_session

    _run_dir, session = _load_session(run_id, runs_root)
    backend = session.get("backend")
    if backend not in {"tycho", "duck"}:
        raise ValueError("engine probe requires a Tycho or Duck harness session")
    source = session.get(backend)
    if not isinstance(source, dict):
        raise ValueError("harness session has no backend source record")
    if getattr(adapter, "backend", None) != backend:
        raise ValueError("engine probe adapter backend does not match the harness session")
    if getattr(adapter, "game_id", None) != session.get("game", {}).get("id"):
        raise ValueError("engine probe adapter game_id does not match the harness session")
    source_python = Path(str(source.get("python") or "")).expanduser().resolve()
    current_python = Path(sys.executable).expanduser().resolve()
    if source_python != current_python:
        raise ValueError(
            "engine probe must run inside the exact Python interpreter recorded by harness-prepare"
        )
    _assert_source_matches_session(session)
    game_source = {
        "status": "verified",
        "backend": backend,
        "game_id": session["game"]["id"],
        "harness": source["source_provenance"],
        "environment": source["environment_provenance"],
    }
    runtime = source["runtime_provenance"]
    binding = ProbeBinding(game_source, runtime)

    def current_binding() -> ProbeBinding:
        _current_dir, current_session = _load_session(run_id, runs_root)
        if current_session != session:
            raise ValueError("harness session provenance changed since the engine probe was bound")
        _assert_source_matches_session(current_session)
        return binding

    return VersionBoundEngineProbe(adapter, binding, current_binding)


def run_probe(
    probe: VersionBoundEngineProbe,
    actions: Sequence[str | Mapping[str, Any]],
    *,
    game_id: str,
    backend: str,
    output_path: Path,
) -> dict[str, Any]:
    """Run a short, explicit reset/step probe and persist its evidence without overwriting."""
    if probe.binding.game_source["game_id"] != game_id:
        raise ValueError("engine probe result game_id does not match its verified source binding")
    if probe.binding.game_source["backend"] != backend:
        raise ValueError("engine probe result backend does not match its verified source binding")
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite engine probe result: {output_path}")
    initial = probe.reset()
    events = [{"index": 0, "action": None, "observation": initial, "sha256": _digest(initial)}]
    for index, action in enumerate(actions, start=1):
        observation = probe.step(action)
        events.append({
            "index": index,
            "action": _jsonable(action),
            "observation": observation,
            "sha256": _digest(observation),
        })
    result = {
        "schema_version": "arcagi-engine-probe/v1",
        "game_id": game_id,
        "backend": backend,
        "source_binding": probe.binding.game_source,
        "runtime_binding": probe.binding.runtime,
        "events": events,
        "action_count": len(actions),
    }
    output_path = output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive create prevents races and accidental replacement of prior probe evidence.
    with output_path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return result



def bind_probe_to_source_binding(
    adapter: EngineProbeAdapter,
    *,
    source_binding_path: Path,
) -> VersionBoundEngineProbe:
    """Bind an original-engine probe to a separately verified game/runtime manifest."""
    source_binding_path = source_binding_path.expanduser().resolve(strict=True)
    binding_sha = sha256_file(source_binding_path)
    manifest = json.loads(source_binding_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "arcagi-game-source-binding/v1":
        raise ValueError("unsupported game source binding manifest")
    if manifest.get("status") != "verified":
        raise ValueError("engine probe requires verified game source and runtime binding")
    game_id = manifest.get("game_id")
    source = manifest.get("source")
    runtime = manifest.get("runtime")
    if not isinstance(game_id, str) or not isinstance(source, dict) or not isinstance(runtime, dict):
        raise ValueError("source binding manifest is incomplete")
    if getattr(adapter, "backend", None) != "tycho":
        raise ValueError("this source binding adapter currently supports Tycho source manifests")
    if getattr(adapter, "game_id", None) != game_id:
        raise ValueError("probe adapter game_id does not match source binding")
    if source.get("status") != "verified" or runtime.get("status") != "verified":
        raise ValueError("engine probe requires verified game source and runtime")
    source_root = Path(str(source.get("source_root") or "")).resolve()
    python = str(runtime.get("python_executable") or "")
    if not source_root.is_dir() or not python:
        raise ValueError("source binding does not identify its source root and Python interpreter")
    if Path(sys.executable).resolve() != Path(python).resolve():
        raise ValueError("engine probe must run inside the exact Python interpreter recorded by source binding")
    game_source = {"status": "verified", "backend": "tycho", "game_id": game_id, "environment": source}
    binding = ProbeBinding(game_source, runtime)

    def current_binding() -> ProbeBinding:
        if sha256_file(source_binding_path) != binding_sha:
            raise ValueError("source binding manifest changed since probe preparation")
        current_source = inspect_tycho_environment(
            source_root.parent, game_id, source_dir=source_root
        )
        current_runtime = inspect_runtime_packages(python, "tycho")
        return ProbeBinding(
            {"status": "verified", "backend": "tycho", "game_id": game_id, "environment": current_source},
            current_runtime,
        )

    return VersionBoundEngineProbe(adapter, binding, current_binding)



class TychoReplayBinder:
    """Backend capability for binding generated-level adapters to Tycho provenance."""

    def bind(self, adapter: EngineProbeAdapter, *, source_binding_path: Path) -> VersionBoundEngineProbe:
        return bind_probe_to_source_binding(adapter, source_binding_path=source_binding_path)

def _get_json_path(value: Any, path: str) -> Any:
    current = value
    if path == "":
        return current
    for part in path.split("."):
        if isinstance(current, dict):
            if part not in current:
                raise KeyError(path)
            current = current[part]
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            if index >= len(current):
                raise KeyError(path)
            current = current[index]
        else:
            raise KeyError(path)
    return current


def run_probe_suite(
    probe: VersionBoundEngineProbe,
    cases: Sequence[Mapping[str, Any]],
    *,
    game_id: str,
    game_version: str,
    output_path: Path,
) -> dict[str, Any]:
    """Run deterministic reset/step probe cases and record exact assertions and outcomes."""
    if probe.binding.game_source["game_id"] != game_id:
        raise ValueError("probe suite game_id does not match verified source binding")
    environment = probe.binding.game_source.get("environment", {})
    source_sha = environment.get("source_tree_sha256")
    runtime_sha = probe.binding.runtime.get("dependency_inventory_sha256")
    if not isinstance(source_sha, str) or len(source_sha) != 64:
        raise ValueError("probe suite requires a verified source tree hash")
    if not isinstance(runtime_sha, str) or len(runtime_sha) != 64:
        raise ValueError("probe suite requires a verified dependency inventory hash")
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite engine probe suite: {output_path}")
    if not cases:
        raise ValueError("probe suite requires at least one case")
    allowed_categories = {"trigger", "non_trigger", "boundary", "reachable_interaction"}
    seen: set[str] = set()
    case_results: list[dict[str, Any]] = []
    for number, spec in enumerate(cases, start=1):
        case_id = spec.get("case_id")
        category = spec.get("category")
        actions = spec.get("actions")
        assertions = spec.get("expected_assertions")
        predictions = spec.get("competing_predictions")
        reset_policy = spec.get("reset_policy")
        if not isinstance(case_id, str) or not case_id.strip() or case_id in seen:
            raise ValueError(f"cases[{number-1}] case_id is missing or duplicated")
        seen.add(case_id)
        if category not in allowed_categories:
            raise ValueError(f"cases[{number-1}] has an unsupported category")
        if not isinstance(actions, list) or any(not isinstance(item, (str, Mapping)) for item in actions):
            raise ValueError(f"cases[{number-1}] actions must be a list of engine actions")
        if not isinstance(predictions, list) or len(predictions) < 2 or any(not isinstance(item, str) or not item.strip() for item in predictions):
            raise ValueError(f"cases[{number-1}] must record competing rule predictions")
        if not isinstance(reset_policy, str) or not reset_policy.strip():
            raise ValueError(f"cases[{number-1}] reset_policy is required")
        if not isinstance(assertions, list) or not assertions:
            raise ValueError(f"cases[{number-1}] expected_assertions must be non-empty")
        setup = spec.get("setup")
        configure_case = getattr(probe.adapter, "configure_case", None)
        if setup is not None and callable(configure_case):
            configure_case(setup)
        observations = [probe.reset()]
        events = [{"index": 0, "action": None, "observation": observations[0], "sha256": _digest(observations[0])}]
        for action in actions:
            observation = probe.step(action)
            observations.append(observation)
            events.append({
                "index": len(events), "action": _jsonable(action),
                "observation": observation, "sha256": _digest(observation),
            })
        assertion_results = []
        for assertion_index, assertion in enumerate(assertions):
            if not isinstance(assertion, Mapping):
                raise ValueError(f"cases[{number-1}] expected_assertions[{assertion_index}] must be an object")
            event_index = assertion.get("event_index")
            path = assertion.get("path")
            if isinstance(event_index, bool) or not isinstance(event_index, int) or not 0 <= event_index < len(observations):
                raise ValueError(f"cases[{number-1}] assertion event_index is out of range")
            if not isinstance(path, str) or "equals" not in assertion:
                raise ValueError(f"cases[{number-1}] assertion requires path and equals")
            try:
                actual = _get_json_path(observations[event_index], path)
                matched = actual == assertion["equals"]
                error = None
            except KeyError:
                actual = None
                matched = False
                error = "observation_path_missing"
            assertion_results.append({
                "event_index": event_index, "path": path,
                "expected": assertion["equals"], "actual": actual,
                "matched": matched, "error": error,
            })
        case_results.append({
            "case_id": case_id, "category": category,
            "setup": spec.get("setup"), "reset_policy": reset_policy,
            "actions": [_jsonable(action) for action in actions],
            "competing_predictions": list(predictions),
            "expected_assertions": assertion_results,
            "events": events,
            "status": "passed" if all(item["matched"] for item in assertion_results) else "failed",
        })
    result = {
        "schema_version": "arcagi-engine-probe-suite/v1",
        "game_id": game_id, "game_version": game_version,
        "source_sha256": source_sha, "runtime_sha256": runtime_sha,
        "source_binding": probe.binding.game_source,
        "runtime_binding": probe.binding.runtime,
        "cases": case_results,
        "summary": {
            "case_count": len(case_results),
            "passed": sum(item["status"] == "passed" for item in case_results),
            "failed": sum(item["status"] == "failed" for item in case_results),
            "status": "passed" if all(item["status"] == "passed" for item in case_results) else "failed",
        },
    }
    output_path = output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return result

def _digest(value: Any) -> str:
    wire = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(wire.encode("utf-8")).hexdigest()
