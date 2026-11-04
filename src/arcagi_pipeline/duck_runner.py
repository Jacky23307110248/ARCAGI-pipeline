from __future__ import annotations

"""Replay a fixed action witness through Duck's native TAAF GameAPI environment."""

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any


def _load_actions(path: Path) -> tuple[list[str], int]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not rows:
        raise ValueError("scripted smoke requires a non-empty ordered action index")
    actions: list[str] = []
    for expected, row in enumerate(rows, 1):
        if not isinstance(row, dict) or row.get("ordinal") != expected:
            raise ValueError("scripted action index is not in course order")
        level_actions = row.get("actions")
        if not isinstance(level_actions, list) or any(not isinstance(value, str) for value in level_actions):
            raise ValueError("each action-index row must contain a list of action names")
        actions.extend(level_actions)
    return actions, len(rows)


def run_duck_script(*, game_id: str, environments_dir: Path, output_dir: Path, actions_path: Path) -> dict[str, Any]:
    from arcengine import ActionInput, GameAction, GameState as EngineState
    from taaf.game_api import ArcadeSpec, GameAPI

    environments_dir = environments_dir.expanduser().resolve(strict=True)
    output_dir = output_dir.expanduser().resolve()
    actions_path = actions_path.expanduser().resolve(strict=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    if any(output_dir.iterdir()):
        raise FileExistsError(f"Duck output directory must be empty: {output_dir}")
    os.environ["RECORDINGS_DIR"] = str(output_dir / "recordings")
    os.environ["ARCAGI_GAME_ENV_DIR"] = str(environments_dir)

    actions, expected_levels = _load_actions(actions_path)
    game = GameAPI(
        env_name=game_id,
        arcade_spec=ArcadeSpec(environments_dir=str(environments_dir)),
    )
    events: list[dict[str, Any]] = []
    try:
        state = game.start_game()
        raw = state.raw
        events.append({
            "index": 0, "action": None, "state": raw.state.name,
            "levels_completed": int(raw.levels_completed),
            "available_actions": [str(GameAction.from_id(int(value)).name) for value in (raw.available_actions or [])],
        })
        for index, action_name in enumerate(actions, 1):
            current = game.current_state.raw
            if current.state in {EngineState.WIN, EngineState.GAME_OVER}:
                break
            action = GameAction[action_name]
            available = {GameAction.from_id(int(value)) for value in (current.available_actions or [])}
            if action not in available:
                raise ValueError(f"scripted action {action_name} is unavailable at event {index}")
            state = game.execute_action(ActionInput(id=action, data={}))
            raw = state.raw
            events.append({
                "index": index, "action": action_name, "state": raw.state.name,
                "levels_completed": int(raw.levels_completed),
                "available_actions": [str(GameAction.from_id(int(value)).name) for value in (raw.available_actions or [])],
            })
        final = game.current_state.raw
        if final.state != EngineState.WIN or int(final.levels_completed) != expected_levels:
            raise RuntimeError("Duck TAAF GameAPI did not complete every level in the supplied action index")
        result = {
            "schema_version": "arcagi-duck-pseudo-session/v1",
            "game_id": game_id,
            "levels_completed": int(final.levels_completed),
            "total_actions": len(events) - 1,
            "terminal_state": final.state.name,
            "action_source": "accepted_engine_path",
            "events_sha256": hashlib.sha256(json.dumps(events, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        }
    finally:
        game.finish_game()

    (output_dir / "events.json").write_text(
        json.dumps(events, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "adapter-result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "run_config.json").write_text(json.dumps({
        "game_id": game_id, "environments_dir": str(environments_dir),
        "n_passes": 1, "concurrent_jobs": 1, "model_calls": 0,
        "action_source": "accepted_engine_path",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-id", required=True)
    parser.add_argument("--environments-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--actions", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run_duck_script(
            game_id=args.game_id, environments_dir=args.environments_dir,
            output_dir=args.output_dir, actions_path=args.actions,
        )
    except Exception as exc:
        print(json.dumps({"completed": False, "error": type(exc).__name__}, ensure_ascii=False))
        return 1
    print(json.dumps({"completed": True, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
