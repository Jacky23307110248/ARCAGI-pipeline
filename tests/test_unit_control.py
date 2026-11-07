import tempfile
import unittest
from pathlib import Path

from arcagi_pipeline.production import (EngineVerdict, FrozenInputs, GeneratedTask,
    ModelSearch, UnitVerdict, run_production)


def evidence(status="pass"):
    return UnitVerdict(status, {"coverage": {"status": status,
        "evidence": {"kind": "counts", "data": {"observed": 3, "expected": 3, "session_id":"s1"}}}}, "reviewed")


class Context:
    def __init__(self, sid="s1"):
        self.sid = sid
    def receipt(self):
        return {"loaded_sources": [{"id": "src", "path": "machine.py", "sha256": "abc"}],
            "sessions": [{"session_id": self.sid, "source_id": "src", "machine_owned": True,
                "trajectory": [{"op": "reset", "result": {"state": [0, 1]}},
                               {"op": "step", "result": {"state": [1, 0]}}]}]}


class StrictPlugin:
    plugin_id = "strict-fixture"
    def __init__(self, statuses=("pass", "pass"), sid="s1"):
        self.statuses, self.sid = statuses, sid
    def generate(self, inputs, seed):
        return [GeneratedTask(f"slot-{i}", {"board": [[i]]}, {"slot_id": f"slot-{i}"}) for i in range(3)]
    def validate(self, task): return []
    def search(self, task, cap): return ModelSearch("candidate", ({"switch":1},))
    def replay(self, task, candidate):
        return EngineVerdict(True, trace=({"state": [0, 1]}, {"state": [1, 0]}), binding={"session_id": self.sid})
    def validate_unit(self, tasks, task_records, context):
        self.seen = (len(tasks), len(task_records), context)
        return evidence(self.statuses[0])
    def adapter_smoke(self, context): return evidence(self.statuses[1])


class UnitControlTests(unittest.TestCase):
    def execute(self, plugin, context=None):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / "rules").write_text("rules"); (root / "blueprint").write_text("blueprint")
            return run_production(plugin, FrozenInputs(root / "rules", root / "blueprint"), seed=1,
                output_dir=root / "out", runtime_context=context, require_unit_checks=True)

    def test_unit_and_smoke_required_and_full_shape_passes(self):
        plugin, context = StrictPlugin(), Context()
        result = self.execute(plugin, context)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["completion_scope"], "strict_unit")
        self.assertEqual(plugin.seen, (3, 3, context))
        self.assertEqual(result["runtime_receipt"], context.receipt())

    def test_unknown_is_limited_unknown(self):
        result = self.execute(StrictPlugin(("unknown", "pass")), Context())
        self.assertEqual(result["status"], "unknown")

    def test_overall_return_fails(self):
        result = self.execute(StrictPlugin(("pass", "return")), Context())
        self.assertEqual(result["status"], "return")

    def test_fake_session_does_not_accept_replay(self):
        result = self.execute(StrictPlugin(sid="missing"), Context())
        self.assertEqual(result["status"], "return")
        self.assertEqual(result["tasks"][0]["checks"]["engine_replay"], "return")

    def test_missing_hook_invalidates_strict_artifact(self):
        class Missing(StrictPlugin):
            adapter_smoke = None
        result = self.execute(Missing(), Context())
        self.assertEqual(result["status"], "return")
        self.assertEqual(result["feedback"][-1]["stage"], "adapter_smoke")


if __name__ == "__main__": unittest.main()
