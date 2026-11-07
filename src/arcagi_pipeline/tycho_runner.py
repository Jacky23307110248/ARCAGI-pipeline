from __future__ import annotations

"""Run Tycho's native game loop against an explicitly selected local environment root."""

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any


class _SingleTurnLimit:
    def __init__(self, delegate: Any, limit: int):
        self.delegate = delegate
        self.limit = limit
        self.actions = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)

    def reset(self, game_id: str, available_actions: list[Any], **kwargs: Any) -> None:
        self.actions = 0
        self.delegate.reset(game_id, available_actions, **kwargs)

    def choose_action(self, frames: list[Any], latest_frame: Any, available_actions: list[Any]) -> Any:
        if self.actions >= self.limit:
            raise RuntimeError("configured action limit reached before requested stop")
        choice = self.delegate.choose_action(frames, latest_frame, available_actions)
        self.actions += 1
        return choice

    def is_done(self, frames: list[Any], latest_frame: Any) -> bool:
        return self.actions >= self.limit or bool(self.delegate.is_done(frames, latest_frame))


class _ScriptedAgent:
    name = "arcagi_pipeline_scripted_smoke"

    def __init__(self, actions: list[str]):
        self.actions = actions
        self.index = 0

    def reset(self, game_id: str, available_actions: list[Any]) -> None:
        self.index = 0

    def choose_action(self, frames: list[Any], latest_frame: Any, available_actions: list[Any]) -> Any:
        from arcengine import GameAction
        from tycho.harness.agent import ActionChoice

        if self.index >= len(self.actions):
            raise RuntimeError("scripted action path ended before the game reached a terminal state")
        name = self.actions[self.index]
        action = GameAction[name]
        if action not in available_actions:
            raise ValueError(f"scripted action {name} is not currently available")
        self.index += 1
        return ActionChoice(action=action)

    def is_done(self, frames: list[Any], latest_frame: Any) -> bool:
        return False


def _load_action_path(path: Path, *, expected_levels: int | None = None) -> list[str]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not rows:
        raise ValueError("scripted smoke requires a non-empty ordered action index")
    if expected_levels is not None and len(rows) != expected_levels:
        raise ValueError("scripted action index level count differs from the loaded game")
    actions: list[str] = []
    for expected, row in enumerate(rows, 1):
        if not isinstance(row, dict) or row.get("ordinal") != expected:
            raise ValueError("scripted action index is not in course order")
        level_actions = row.get("actions")
        if not isinstance(level_actions, list) or any(not isinstance(value, str) for value in level_actions):
            raise ValueError("each action-index row must contain a list of action names")
        actions.extend(level_actions)
    return actions


def run_tycho_session(
    *, tycho_root: Path, game_id: str, environment_dir: Path, output_dir: Path, config_path: Path,
    seed: int = 0, actions_path: Path | None = None, max_actions: int | None = None,
    require_win: bool = False,
) -> dict[str, Any]:
    from arc_agi import Arcade, OperationMode
    from tycho.config import apply_config_file
    from tycho.harness.harness import run_env
    from tycho.harness.record_slim import slim_record
    from tycho.harness.run_parallel import _atomic_result

    environment_dir = environment_dir.expanduser().resolve(strict=True)
    tycho_root = tycho_root.expanduser().resolve(strict=True)
    output_dir = output_dir.expanduser().resolve()
    config_path = config_path.expanduser().resolve(strict=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    short_id = game_id.split("-", maxsplit=1)[0]
    expected_artifacts = (f"game_{short_id}.json", "adapter-result.json", "manifest.json")
    occupied = [name for name in expected_artifacts if (output_dir / name).exists()]
    if occupied:
        raise FileExistsError(f"Tycho output artifacts already exist: {', '.join(occupied)}")

    # Tycho's regular CLI pins ENV_DIR to the upstream checkout. This adapter invokes the same
    # native run_env loop with an explicit Arcade environments_dir, preserving source code.
    apply_config_file(config_path)
    os.environ["ARCAGI_GAME_ENV_DIR"] = str(environment_dir)
    # Credentials are explicitly inherited; never discover a neighboring .env.

    arcade = Arcade(
        arc_api_key="", operation_mode=OperationMode.OFFLINE,
        environments_dir=str(environment_dir), recordings_dir=str(output_dir),
    )
    env_info = next((item for item in arcade.get_environments() if item.game_id == game_id), None)
    if env_info is None:
        raise ValueError(f"Tycho did not discover the prepared game environment {game_id}")
    baselines = list(env_info.baseline_actions or [])
    if not baselines:
        raise ValueError("Tycho environment metadata must provide one baseline per course level")

    if actions_path is not None:
        agent: Any = _ScriptedAgent(_load_action_path(actions_path, expected_levels=len(baselines)))
        action_source = "accepted_engine_path"
    else:
        from tycho.harness.run import _agent_factory
        agent = _agent_factory("tycho")()
        if max_actions is not None:
            if isinstance(max_actions, bool) or max_actions <= 0:
                raise ValueError("max_actions must be positive")
            agent = _SingleTurnLimit(agent, max_actions)
        action_source = "tycho_agent"

    record = run_env(
        agent, arcade, game_id, baselines, seed=seed, keep_trace=True, keep_frames=True,
    )
    record_data = slim_record(asdict(record))
    record_path = output_dir / f"game_{short_id}.json"
    _atomic_result(record_path, record_data, indent=2)

    result = {
        "schema_version": "arcagi-tycho-pseudo-session/v1",
        "game_id": game_id,
        "action_source": action_source,
        "game_record": record_path.name,
        "state": record_data.get("stop_reason"),
        "levels_completed": record_data.get("levels_completed"),
        "total_actions": record_data.get("total_actions"),
        "error": record_data.get("error"),
        "record_sha256": __import__("hashlib").sha256(record_path.read_bytes()).hexdigest(),
    }
    (output_dir / "adapter-result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "manifest.json").write_text(
        json.dumps({
            "backend": "tycho", "game_id": game_id,
            "runner": "arcagi-pipeline explicit-environment bridge to Tycho run_env",
            "action_source": action_source,
            "environment_dir": str(environment_dir),
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if record_data.get("error"):
        raise RuntimeError(f"Tycho native run_env returned a partial game record: {record_data['error']}")
    if require_win and (str(record_data.get("stop_reason", "")).lower() != "win" or record_data.get("levels_completed") != len(baselines)):
        raise RuntimeError("Tycho scripted run did not complete the full generated course")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-id", required=True)
    parser.add_argument("--tycho-root", type=Path, required=True)
    parser.add_argument("--environment-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--actions", type=Path)
    parser.add_argument("--max-actions", type=int)
    parser.add_argument("--require-win", action="store_true")
    args = parser.parse_args()
    try:
        result = run_tycho_session(
            tycho_root=args.tycho_root, game_id=args.game_id, environment_dir=args.environment_dir,
            output_dir=args.output_dir, config_path=args.config, seed=args.seed,
            actions_path=args.actions, max_actions=args.max_actions,
            require_win=args.require_win,
        )
    except Exception as exc:
        print(json.dumps({"completed": False, "error": type(exc).__name__}, ensure_ascii=False))
        return 1
    print(json.dumps({"completed": True, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
