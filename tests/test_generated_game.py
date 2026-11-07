import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
import importlib.util
import sys

from arcagi_pipeline.generated_game import run_generated_production, CONTRACT, behavior_check
from arcagi_pipeline.production import FrozenInputs
from examples.neutral_machine.generated import Plugin, design

ROOT = Path(__file__).resolve().parents[1]

class Context:
    """No provider: source is loaded from the original neutral fixture."""
    facts = {"identity": "offline-fixture"}
    def load_source(self, source_id):
        path = ROOT / "examples/neutral_machine/generated_source.py"
        name = "neutral_bound_" + str(id(self))
        spec = importlib.util.spec_from_file_location(name, path)
        m = importlib.util.module_from_spec(spec); sys.modules[name] = m; spec.loader.exec_module(m)
        self.module = m
        return m
    def receipt(self): return {"runtime": self.facts}

class GeneratedGameTests(unittest.TestCase):
    def test_v2_workflow_handoff_executes_in_declared_worker(self):
        from examples.neutral_machine.demo import prepare
        from arcagi_pipeline.machine import run_machine
        from arcagi_pipeline.execution_contract import GENERATED_FILES
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundle = {"entrypoint":"plugin.py","factory":"create_plugin","files":[{"path":"plugin.py","content":"from examples.neutral_machine.generated import create_plugin\n"}]}
            config, provider = prepare(root / "spec", [*design(), bundle])
            cfg = json.loads(config.read_text()); cfg["production_contract"] = CONTRACT
            config.write_text(json.dumps(cfg))
            specpath = config.parent / "workflow.json"; spec = json.loads(specpath.read_text())
            task = spec["tasks"][-1]; task["execution_contract"] = CONTRACT
            api = ROOT / "src/arcagi_pipeline/generated_game.py"
            task["inputs"].append({"id":"generated_game_api","role":"generated_game_api","path":str(api),"sha256":hashlib.sha256(api.read_bytes()).hexdigest()})
            specpath.write_text(json.dumps(spec))
            rp = config.parent / "runtime.json"; runtime = json.loads(rp.read_text())
            source = ROOT / "examples/neutral_machine/generated_source.py"
            runtime["import_roots"] = [str(ROOT)]
            runtime["sources"] = [{"id":"engine","path":str(source),"sha256":hashlib.sha256(source.read_bytes()).hexdigest()}]
            rp.write_text(json.dumps(runtime))
            report = run_machine(config, root/"workflow",root/"execution",provider=provider)
            self.assertEqual(report["status"],"pass",report)
            self.assertEqual(report["completion_scope"],"generated_unit_verified/v2")

    def run_fixture(self, mode="normal", count=3, change_design=None):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = design(count, mode)
            if change_design: change_design(data)
            for name, value in zip(("evidence", "rules", "blueprint"), data):
                (root / name).write_text(json.dumps(value), encoding="utf-8")
            return run_generated_production(Plugin(count, mode), FrozenInputs(root / "rules", root / "blueprint", root / "evidence"),
                                            seed=4, output_dir=root / "production", runtime_context=Context())
    def test_variable_counts_actions_nonmonotonic_and_bound_records(self):
        for count in (1, 4):
            r = self.run_fixture(count=count)
            self.assertEqual(r["status"], "pass", r["feedback"])
            self.assertEqual(r["completion_scope"], "generated_unit_verified/v2")
            self.assertEqual(len(r["tasks"]), count)
            self.assertTrue(all(t["search"]["status"] == "candidate" and t["verified_solution"] for t in r["tasks"]))
            self.assertEqual(r["units"][0]["unit_execution"]["status"], "pass")
    def test_old_instance_wrong_search_and_label_only_rejected(self):
        for mode in ("old_instance", "wrong_search", "labels", "reject"):
            with self.subTest(mode=mode):
                r = self.run_fixture(mode, count=1)
                self.assertEqual(r["status"], "return", r)
    def test_per_level_success_does_not_prove_whole_unit(self):
        r = self.run_fixture("unit_fail")
        self.assertTrue(all(t["status"] == "pass" for t in r["tasks"]))
        self.assertEqual(r["status"], "return")
        self.assertEqual(r["units"][0]["unit_execution"]["status"], "return")
    def test_bounded_search_retains_unknown(self):
        self.assertEqual(self.run_fixture("unknown")["status"], "unknown")
    def test_self_echo_is_not_behavior_evidence(self):
        r = self.run_fixture(change_design=lambda d: d[2]["slots"][0]["behavior_requirements"][0].update(kind="field_comparison"))
        self.assertNotEqual(r["status"], "pass")
    def test_audit_target_cannot_be_playable_delivery(self):
        r = self.run_fixture(change_design=lambda d: d[2]["production_target"].update(object="audit_checklist"))
        self.assertEqual(r["status"], "return")
        self.assertEqual(r["tasks"], [])
    def test_initial_goal_allowed(self):
        def initial(d):
            d[2]["slots"][0]["behavior_requirements"][0].update(observation_path=["value"], value=2)
        r = self.run_fixture("initial", count=1, change_design=initial)
        # This fixture's native complete() does not certify an initial goal.
        self.assertTrue(r["tasks"][0]["verified_solution"])
        self.assertEqual(r["tasks"][0]["search"]["actions"], ())
        self.assertEqual(r["status"], "return")

if __name__ == "__main__": unittest.main()
