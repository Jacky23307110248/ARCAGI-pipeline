import json
from pathlib import Path

import pytest

from arcagi_pipeline.duck_runner import _load_actions
from arcagi_pipeline.harness import HarnessRunSpec
from arcagi_pipeline.harness_adapters.duck import DuckAdapter
from arcagi_pipeline.harness_adapters.tycho import TychoAdapter
from arcagi_pipeline.tycho_runner import _load_action_path


ROWS = [
    {"ordinal": 1, "actions": ["NORTH", "WAIT"]},
    {"ordinal": 2, "actions": ["INTERACT"]},
]


def _patch_rows(monkeypatch, rows):
    monkeypatch.setattr(Path, "read_text", lambda self, encoding=None: json.dumps(rows))


def test_tycho_loader_accepts_course_length_from_engine(monkeypatch):
    _patch_rows(monkeypatch, ROWS)
    assert _load_action_path(Path("action-index.json"), expected_levels=2) == ["NORTH", "WAIT", "INTERACT"]


def test_tycho_loader_rejects_mismatch_with_loaded_engine(monkeypatch):
    _patch_rows(monkeypatch, ROWS)
    with pytest.raises(ValueError, match="level count"):
        _load_action_path(Path("action-index.json"), expected_levels=3)


def test_duck_loader_returns_generic_level_count(monkeypatch):
    _patch_rows(monkeypatch, ROWS)
    assert _load_actions(Path("action-index.json")) == (["NORTH", "WAIT", "INTERACT"], 2)


def test_loaders_reject_non_ordered_rows(monkeypatch):
    _patch_rows(monkeypatch, [{"ordinal": 2, "actions": ["NORTH"]}])
    with pytest.raises(ValueError, match="course order"):
        _load_action_path(Path("action-index.json"))
    with pytest.raises(ValueError, match="course order"):
        _load_actions(Path("action-index.json"))


def test_tycho_custom_environment_command_uses_generic_bridge():
    spec = HarnessRunSpec("game-alpha", Path("out"), {
        "environments_dir": "env-root", "actions_path": "actions.json", "require_win": True,
    })
    command = TychoAdapter(Path("tycho-root"), python="python").run_command(
        spec, config_path=Path("config.json")
    )
    assert "arcagi_pipeline.tycho_runner" in command.command
    assert "--environment-dir" in command.command
    assert "--actions" in command.command
    assert "--require-win" in command.command
    assert "--games" not in command.command


def test_tycho_custom_environment_resume_is_explicitly_unsupported():
    spec = HarnessRunSpec("game-alpha", Path("out"), {"environments_dir": "env-root"})
    with pytest.raises(ValueError, match="do not support resume"):
        TychoAdapter(Path("tycho-root"), python="python").run_command(
            spec, config_path=Path("config.json"), resume=True
        )


def test_duck_scripted_command_uses_generic_pipeline_runner():
    spec = HarnessRunSpec("game-beta", Path("out"), {"environments_dir": "env-root"})
    command = DuckAdapter(Path("duck-root"), python="python").scripted_smoke_command(
        spec, actions_path=Path(__file__)
    )
    assert "arcagi_pipeline.duck_runner" in command.command
    assert "--game-id" in command.command
    assert "--actions" in command.command
    assert "--model" not in command.command