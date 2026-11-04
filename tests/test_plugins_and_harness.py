from __future__ import annotations

import tempfile
import unittest
import os
from pathlib import Path

from arcagi_pipeline.contracts import CourseSlot
from arcagi_pipeline.curriculum import validate_course
from arcagi_pipeline.harness import HarnessRunSpec
from arcagi_pipeline.harness_adapters.duck import DuckAdapter
from arcagi_pipeline.harness_adapters.tycho import TychoAdapter


class DifferentGamePlugins:
    def __init__(self, plugin_id, levels, actions):
        self.plugin_id, self.levels, self.actions = plugin_id, levels, actions
        self.plugin_version = "1"

    def validate_extension(self, extension):
        assert extension["actions"] == self.actions

    def level_identity(self, level):
        return str(level["key"])

    def validate_level(self, level):
        return [] if set(level) == {"key", "state"} else ["unexpected level shape"]


class PluginHarnessTests(unittest.TestCase):
    def test_plugin_contract_allows_different_level_counts_and_state_shapes(self):
        two_level = DifferentGamePlugins("g-a", 2, ["north", "south"])
        three_level = DifferentGamePlugins("g-b", 3, ["tap", "wait", "drag"])
        self.assertEqual(two_level.validate_level({"key": "a", "state": [1, 2]}), [])
        self.assertEqual(three_level.validate_level({"key": "b", "state": {"x": 1}}), [])

    def test_course_prerequisites_are_ordered_without_fixed_size(self):
        slots = [CourseSlot("first", ("observe-state-change",)), CourseSlot("next", ("append-token",), ("first",))]
        self.assertEqual(validate_course(slots), [])
        self.assertTrue(validate_course([CourseSlot("later", ("x",), ("missing",))]))

    def test_harness_adapters_build_separate_preflight_and_run_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = HarnessRunSpec("game-alpha", root / "out", {
                "model": "local-model",
                "seed": 7,
                "environments_dir": str(root / "environment_files"),
                "max_actions": 12,
                "max_runtime_minutes": 2,
            })
            tycho = TychoAdapter(root / "tycho", python="python")
            duck = DuckAdapter(root / "duck", python="python")
            self.assertIn("--help", tycho.preflight_command(spec).command)
            self.assertIn("--game", duck.preflight_command(spec).command)
            self.assertIn("--approach", tycho.run_command(spec).command)
            self.assertIn("--model", duck.run_command(spec).command)
            self.assertIn("--experiment-dir", duck.run_command(spec).command)
            duck_command = duck.run_command(spec)
            self.assertIn("--max-actions", duck_command.command)
            self.assertIn("--max-runtime-minutes", duck_command.command)
            self.assertEqual(duck_command.command[duck_command.command.index("--max-runtime-minutes") + 1], "2")
            pythonpath = duck_command.environment_overrides["PYTHONPATH"].split(os.pathsep)
            self.assertIn(str(Path(__file__).resolve().parents[1] / "src"), pythonpath)
            self.assertIn(str((root / "duck" / "ARC3-Inference").resolve()), pythonpath)
            self.assertIn("--deployment-target", duck_command.command)
            self.assertNotIn("API_KEY", str(tycho.run_command(spec).command))
            self.assertNotIn("API_KEY", str(duck.run_command(spec).command))


if __name__ == "__main__":
    unittest.main()
