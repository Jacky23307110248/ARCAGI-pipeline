import json,tempfile,unittest
from pathlib import Path
from arcagi_pipeline.machine import execute_handoff,materialize,run_machine,plan_machine
from arcagi_pipeline.blueprint import validate_design,validate_fulfillment
from arcagi_pipeline.production import GeneratedTask,FulfillmentVerdict
from examples.neutral_machine.demo import fixture,prepare,demo

class MachineTests(unittest.TestCase):
    def test_variable_count_and_nonmonotonic_slots(self):
        for count in (1,4):
            with self.subTest(count=count), tempfile.TemporaryDirectory() as td:
                root=Path(td)/"demo"
                result=demo(root,count)
                self.assertEqual(result["status"],"pass")
                production=json.loads((root/"execution/production/production.json").read_text())
                self.assertEqual(len(production["tasks"]),count)
                self.assertTrue(all(t["checks"]["fulfillment"]=="pass" for t in production["tasks"]))
    def test_failure_modes_preserve_evidence(self):
        for mode,status,stage in [("unknown","unknown","model_search"),("engine_reject","return","engine_replay"),("missing_check","return","fulfillment"),("bad_evidence","return","fulfillment")]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as td:
                root=Path(td)/"demo";result=demo(root,1,mode)
                self.assertEqual(result["status"],status)
                packet=json.loads((root/"execution/revision_packet.json").read_text())
                self.assertEqual(packet["production"]["feedback"][0]["stage"],stage)
    def test_workflow_hash_tamper_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/"demo";demo(root,1)
            record=json.loads((root/"workflow/workflow_run.json").read_text())
            artifact=root/"workflow"/record["tasks"][0]["output_path"]
            artifact.write_text("{}")
            result=execute_handoff(root/"workflow",dict(zip(["evidence","rules","blueprint","implementation"],["evidence","rules","blueprint","implementation"])),root/"second",runtime_config=root/"spec/runtime.json")
            self.assertEqual(result["status"],"return")
            self.assertEqual("artifact_changed",result["error"])
    def test_path_escape_rejected_before_writing(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            with self.assertRaises(ValueError):
                materialize({"entrypoint":"../escape.py","factory":"create","files":[{"path":"../escape.py","content":"bad"}]},root/"plugin")
            self.assertFalse((root/"escape.py").exists())
    def test_unknown_rule_and_forward_dependency_rejected(self):
        artifacts=fixture(2)
        artifacts[2]["slots"][0]["rule_ids"]=["missing"]
        artifacts[2]["slots"][0]["prerequisites"]=["slot-1"]
        errors=validate_design(*artifacts[:3])
        self.assertTrue(any("rule_ids" in e for e in errors))
        self.assertTrue(any("prerequisites" in e for e in errors))
    def test_revision_packet_consumed_by_next_workflow(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);demo(root/"first",1,"engine_reject")
            config,provider=prepare(root/"repair/spec",fixture(1))
            spec_path=config.parent/"workflow.json";spec=json.loads(spec_path.read_text())
            # Each revision is explicit and budgeted; no hidden retry loop.
            spec["tasks"][0]["inputs"]=[{"id":"feedback","role":"revision_feedback","path":"../../first/execution/revision_packet.json"}]
            spec_path.write_text(json.dumps(spec))
            result=run_machine(config,root/"repair/workflow",root/"repair/execution",provider=provider)
            self.assertEqual(result["status"],"pass")
    def test_missing_stage_inputs_fail_before_model_call(self):
        with tempfile.TemporaryDirectory() as td:
            config,provider=prepare(Path(td)/"spec",fixture(1))
            spec_path=config.parent/"workflow.json";spec=json.loads(spec_path.read_text())
            spec["tasks"][-1]["inputs"]=[item for item in spec["tasks"][-1]["inputs"] if "path" in item]
            spec_path.write_text(json.dumps(spec))
            with self.assertRaisesRegex(ValueError,"upstream"):
                plan_machine(config)
    def test_fulfillment_label_without_recorded_evidence_rejected(self):
        task=GeneratedTask("a",{}, {"claims":[{"id":"claim","statement":"reaches target","check":"compare replay"}]})
        self.assertTrue(validate_fulfillment(task,FulfillmentVerdict({"claim":{"status":"pass","evidence":"trust me"}})))
        self.assertTrue(validate_fulfillment(task,FulfillmentVerdict({"claim":{"status":"pass","evidence":{"kind":"label"}}})))
        self.assertFalse(validate_fulfillment(task,FulfillmentVerdict({"claim":{"status":"pass","evidence":{"kind":"observation","data":{"observed":0}}}})))
    def test_empty_and_duplicate_slots_rejected(self):
        artifacts=fixture(1);artifacts[2]["slots"]*=2
        self.assertTrue(validate_design(*artifacts[:3]))
        artifacts[2]["slots"]=[]
        self.assertTrue(validate_design(*artifacts[:3]))
if __name__=="__main__":unittest.main()
