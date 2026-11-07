import json
import tempfile
import unittest
from pathlib import Path

from arcagi_pipeline.production import (
    EngineVerdict, FrozenInputs, GeneratedTask, ModelSearch, run_production,
)


class NeutralPlugin:
    plugin_id = "neutral-counter"

    def __init__(self, search=None, accepted=True, invalid=False):
        self.result = search or ModelSearch("candidate", ("advance",))
        self.accepted, self.invalid = accepted, invalid
        self.replayed = False

    def generate(self, inputs, seed):
        return [GeneratedTask("sample", {"seed": seed})]

    def validate(self, task):
        return ["bad structure"] if self.invalid else []

    def search(self, task, cap):
        return self.result

    def replay(self, task, candidate):
        self.replayed = True
        return EngineVerdict(self.accepted, "legal_goal" if self.accepted else "illegal_action",
                             ({"action": "advance"},), None if self.accepted else 1)


class ProductionTests(unittest.TestCase):
    def execute(self, plugin):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rule, blueprint = root/"rules", root/"blueprint"
            rule.write_text("reviewed rules")
            blueprint.write_text("reviewed design")
            result = run_production(plugin, FrozenInputs(rule, blueprint), seed=9, output_dir=root/"out")
            self.assertEqual(json.loads((root/"out/production.json").read_text()), result)
            return result

    def test_candidate_requires_engine_acceptance(self):
        plugin = NeutralPlugin(accepted=False)
        result = self.execute(plugin)
        self.assertEqual(result["status"], "return")
        self.assertEqual(result["feedback"][0]["stage"], "engine_replay")
        self.assertEqual(result["feedback"][0]["failed_action_index"], 1)
        self.assertEqual(result["feedback"][0]["actions"], ["advance"])

    def test_accepted_replay_passes(self):
        self.assertEqual(self.execute(NeutralPlugin())["status"], "pass")

    def test_unknown_does_not_replay(self):
        plugin = NeutralPlugin(ModelSearch("unknown", reason="cap_hit"))
        self.assertEqual(self.execute(plugin)["status"], "unknown")
        self.assertFalse(plugin.replayed)

    def test_incomplete_unsolvability_is_unknown(self):
        self.assertEqual(self.execute(NeutralPlugin(ModelSearch("unsolvable", exhausted=True)))["status"], "unknown")

    def test_complete_exhaustion_returns_to_generation(self):
        plugin = NeutralPlugin(ModelSearch("unsolvable", complete_model=True, exhausted=True))
        result = self.execute(plugin)
        self.assertEqual(result["status"], "return")
        self.assertEqual(result["feedback"][0]["stage"], "model_search")
        self.assertFalse(plugin.replayed)

    def test_structure_rejection_does_not_search_or_replay(self):
        plugin = NeutralPlugin(invalid=True)
        self.assertEqual(self.execute(plugin)["feedback"][0]["stage"], "structure")
        self.assertFalse(plugin.replayed)

    def test_engine_exception_is_feedback(self):
        class Broken(NeutralPlugin):
            def replay(self, task, candidate):
                raise RuntimeError("engine unavailable")
        result = self.execute(Broken())
        self.assertEqual(result["status"], "return")
        self.assertEqual("RuntimeError", result["feedback"][0]["reason"])

    def test_zero_action_candidate_still_replays(self):
        plugin = NeutralPlugin(ModelSearch("candidate"))
        self.assertEqual(self.execute(plugin)["status"], "pass")
        self.assertTrue(plugin.replayed)

    def test_generation_exception_returns_feedback(self):
        class Broken(NeutralPlugin):
            def generate(self, inputs, seed):
                raise ValueError("cannot generate")
        result = self.execute(Broken())
        self.assertEqual(result["status"], "return")
        self.assertEqual(result["feedback"][0]["stage"], "generation")

    def test_input_drift_invalidates_run(self):
        class Drifting(NeutralPlugin):
            def generate(self, inputs, seed):
                inputs.blueprint.write_text("modified")
                return super().generate(inputs, seed)
        result = self.execute(Drifting())
        self.assertEqual(result["status"], "return")
        self.assertEqual(result["feedback"][-1]["stage"], "inputs")


if __name__ == "__main__":
    unittest.main()
