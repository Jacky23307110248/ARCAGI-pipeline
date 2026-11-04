from __future__ import annotations

import json
import tempfile
import unittest
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from arcagi_pipeline.engine_probe import (
    ProbeBinding,
    TaafGameProbe,
    TychoArcadeProbe,
    TychoReplayBinder,
    VersionBoundEngineProbe,
    run_probe,
    run_probe_suite,
)


class TychoReplayBinderTests(unittest.TestCase):
    def test_delegates_to_existing_version_bound_source_binder(self):
        adapter = object()
        source_path = Path("trusted-binding.json")
        expected = object()
        with patch("arcagi_pipeline.engine_probe.bind_probe_to_source_binding", return_value=expected) as bind:
            result = TychoReplayBinder().bind(adapter, source_binding_path=source_path)
        self.assertIs(result, expected)
        bind.assert_called_once_with(adapter, source_binding_path=source_path)


class GameAction(Enum):
    ACTION1 = 1
    ACTION6 = 6
    RESET = 0


class EngineProbeTests(unittest.TestCase):
    def setUp(self):
        self.source = {
            "status": "verified", "source_tree_sha256": "a" * 64,
            "game_id": "test-game", "backend": "fixture",
        }
        self.runtime = {"status": "verified", "dependency_inventory_sha256": "b" * 64}
        self.binding = ProbeBinding(self.source, self.runtime)

    @staticmethod
    def observation(step: int):
        return SimpleNamespace(
            game_id="test-game",
            state="PLAYING",
            levels_completed=0,
            win_levels=1,
            available_actions=[1, 6],
            frame=[[[step, 0], [0, step]]],
        )

    def test_tycho_adapter_resets_and_steps_original_environment(self):
        calls = []

        class Environment:
            def reset(self):
                calls.append(("reset",))
                return EngineProbeTests.observation(0)

            def step(self, action, *, data=None):
                calls.append(("step", action.name, data))
                return EngineProbeTests.observation(1)

        class Arcade:
            def make(self, game_id, *, seed):
                calls.append(("make", game_id, seed))
                return Environment()

        adapter = TychoArcadeProbe(Arcade(), "test-game", seed=9, game_action_enum=GameAction)
        initial = adapter.reset()
        next_state = adapter.step({"action": "ACTION6", "data": {"x": 3, "y": 4}})
        self.assertEqual(initial.frame[0][0], [0, 0])
        self.assertEqual(next_state.levels_completed, 0)
        self.assertEqual(calls, [
            ("make", "test-game", 9), ("reset",), ("step", "ACTION6", {"x": 3, "y": 4})
        ])

    def test_taaf_adapter_uses_game_start_and_execute_action_without_solver(self):
        games = []

        class ActionInput:
            def __init__(self, *, id, data):
                self.id = id
                self.data = data

        class Game:
            def __init__(self):
                games.append(self)

            def start_game(self, session=None):
                self.session = session
                return EngineProbeTests.observation(0)

            def execute_action(self, action):
                self.action = action
                return EngineProbeTests.observation(1)

        adapter = TaafGameProbe(
            Game,
            game_id="test-game",
            action_input_type=ActionInput,
            game_action_enum=GameAction,
            run_session_factory=lambda: "probe-session",
        )
        adapter.reset()
        adapter.step("ACTION1")
        self.assertEqual(games[0].session, "probe-session")
        self.assertEqual(games[0].action.id, GameAction.ACTION1)
        self.assertEqual(games[0].action.data, {})

    def test_version_bound_probe_checks_each_session_open_and_keeps_steps_single_action(self):
        class Adapter:
            backend = "fixture"
            game_id = "test-game"

            def reset(self):
                return EngineProbeTests.observation(0)

            def step(self, action):
                return EngineProbeTests.observation(1)

        current = {"game_source": self.source, "runtime": self.runtime}
        checks = []
        probe = VersionBoundEngineProbe(
            Adapter(), self.binding,
            lambda: checks.append("checked") or ProbeBinding(current["game_source"], current["runtime"]),
        )
        probe.reset()
        self.assertEqual(len(checks), 1)
        probe.step("ACTION1")
        self.assertEqual(len(checks), 1)
        current["runtime"] = {"status": "verified", "dependency_inventory_sha256": "c" * 64}
        with self.assertRaisesRegex(ValueError, "runtime changed"):
            probe.reset()
        with self.assertRaisesRegex(ValueError, "runtime changed"):
            probe.reset()

    def test_single_step_requires_prior_verified_reset(self):
        class Adapter:
            backend = "fixture"
            game_id = "test-game"

            def reset(self):
                return EngineProbeTests.observation(0)

            def step(self, action):
                return EngineProbeTests.observation(1)

        probe = VersionBoundEngineProbe(Adapter(), self.binding, lambda: self.binding)
        with self.assertRaisesRegex(RuntimeError, "call reset"):
            probe.step("ACTION1")

    def test_unverified_source_or_runtime_cannot_probe(self):
        with self.assertRaisesRegex(ValueError, "game source"):
            ProbeBinding({"status": "unverified"}, self.runtime)
        with self.assertRaisesRegex(ValueError, "dependencies"):
            ProbeBinding(self.source, {"status": "unverified"})

    def test_run_probe_records_each_step_and_refuses_overwrite(self):
        class Adapter:
            backend = "fixture"
            game_id = "test-game"

            def __init__(self):
                self.step_index = 0

            def reset(self):
                return EngineProbeTests.observation(0)

            def step(self, action):
                self.step_index += 1
                return EngineProbeTests.observation(self.step_index)

        probe = VersionBoundEngineProbe(Adapter(), self.binding, lambda: self.binding)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "probe.json"
            result = run_probe(
                probe, ["ACTION1", {"action": "ACTION6", "data": {"x": 1, "y": 2}}],
                game_id="test-game", backend="fixture", output_path=path,
            )
            self.assertEqual(result["action_count"], 2)
            self.assertEqual(len(result["events"]), 3)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["events"][2]["action"]["action"], "ACTION6")
            with self.assertRaises(FileExistsError):
                run_probe(probe, [], game_id="test-game", backend="fixture", output_path=path)

    def test_run_probe_rejects_game_or_backend_identity_mismatch_before_engine_calls(self):
        calls = []

        class Adapter:
            backend = "fixture"
            game_id = "test-game"

            def reset(self):
                calls.append("reset")
                return EngineProbeTests.observation(0)

            def step(self, action):
                calls.append("step")
                return EngineProbeTests.observation(1)

        probe = VersionBoundEngineProbe(Adapter(), self.binding, lambda: self.binding)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "probe.json"
            with self.assertRaisesRegex(ValueError, "game_id does not match"):
                run_probe(probe, [], game_id="other-game", backend="fixture", output_path=path)
            with self.assertRaisesRegex(ValueError, "backend does not match"):
                run_probe(probe, [], game_id="test-game", backend="duck", output_path=path)
            self.assertFalse(path.exists())
            self.assertEqual(calls, [])

    def test_adapter_backend_and_game_must_match_binding_before_reset(self):
        class MismatchedAdapter:
            backend = "tycho"
            game_id = "other-game"

            def reset(self):
                raise AssertionError("mismatched adapter must not reset")

            def step(self, action):
                raise AssertionError("mismatched adapter must not step")

        with self.assertRaisesRegex(ValueError, "adapter backend does not match"):
            VersionBoundEngineProbe(MismatchedAdapter(), self.binding, lambda: self.binding)

        class WrongGameAdapter(MismatchedAdapter):
            backend = "fixture"

        with self.assertRaisesRegex(ValueError, "adapter game_id does not match"):
            VersionBoundEngineProbe(WrongGameAdapter(), self.binding, lambda: self.binding)


    def test_probe_suite_records_competing_predictions_and_assertions(self):
        class Adapter:
            backend = "fixture"
            game_id = "test-game"

            def __init__(self):
                self.steps = 0

            def reset(self):
                self.steps = 0
                return EngineProbeTests.observation(0)

            def step(self, action):
                self.steps += 1
                return EngineProbeTests.observation(self.steps)

        source = {
            "status": "verified", "backend": "fixture", "game_id": "test-game",
            "environment": {"source_tree_sha256": "c" * 64},
        }
        runtime = {"status": "verified", "dependency_inventory_sha256": "d" * 64}
        binding = ProbeBinding(source, runtime)
        probe = VersionBoundEngineProbe(Adapter(), binding, lambda: binding)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "suite.json"
            result = run_probe_suite(
                probe,
                [{
                    "case_id": "trigger-enter", "category": "trigger",
                    "setup": {"description": "fresh test fixture"},
                    "reset_policy": "fresh reset before case",
                    "actions": ["ACTION1"],
                    "competing_predictions": ["the state changes on entry", "it does not change"],
                    "expected_assertions": [
                        {"event_index": 0, "path": "state", "equals": "PLAYING"},
                        {"event_index": 1, "path": "state", "equals": "PLAYING"},
                    ],
                }],
                game_id="test-game", game_version="test-v1", output_path=path,
            )
            self.assertEqual(result["summary"]["status"], "passed")
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["cases"][0]["competing_predictions"][0], "the state changes on entry")
            self.assertEqual(saved["cases"][0]["expected_assertions"][1]["matched"], True)
            with self.assertRaises(FileExistsError):
                run_probe_suite(
                    probe, [], game_id="test-game", game_version="test-v1", output_path=path
                )

    def test_probe_suite_persists_failed_assertion_without_calling_it_a_pass(self):
        class Adapter:
            backend = "fixture"
            game_id = "test-game"

            def reset(self):
                return EngineProbeTests.observation(0)

            def step(self, action):
                return EngineProbeTests.observation(1)

        source = {
            "status": "verified", "backend": "fixture", "game_id": "test-game",
            "environment": {"source_tree_sha256": "c" * 64},
        }
        runtime = {"status": "verified", "dependency_inventory_sha256": "d" * 64}
        binding = ProbeBinding(source, runtime)
        probe = VersionBoundEngineProbe(Adapter(), binding, lambda: binding)
        with tempfile.TemporaryDirectory() as directory:
            result = run_probe_suite(
                probe,
                [{
                    "case_id": "wrong-prediction", "category": "boundary",
                    "setup": {"description": "fixture"},
                    "reset_policy": "fresh reset before case", "actions": ["ACTION1"],
                    "competing_predictions": ["expected", "alternative"],
                    "expected_assertions": [{"event_index": 1, "path": "state", "equals": "WIN"}],
                }],
                game_id="test-game", game_version="test-v1",
                output_path=Path(directory) / "failed.json",
            )
            self.assertEqual(result["summary"]["status"], "failed")
            self.assertEqual(result["cases"][0]["status"], "failed")
            self.assertFalse(result["cases"][0]["expected_assertions"][0]["matched"])

if __name__ == "__main__":
    unittest.main()
