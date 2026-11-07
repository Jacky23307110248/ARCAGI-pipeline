import copy, hashlib, json, tempfile, unittest
from pathlib import Path
from arcagi_pipeline.execution_contract import validate_execution_inputs
from arcagi_pipeline.agent_workflow import WorkflowRunner
from arcagi_pipeline.machine import plan_machine
from examples.neutral_machine.demo import prepare, fixture

class ContractTests(unittest.TestCase):
    def make(self, root):
        config, provider = prepare(root / "spec", fixture(2))
        spec = config.parent / "workflow.json"
        data = json.loads(spec.read_text())
        return config, provider, spec, data

    def test_machine_missing_marker_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            c,p,s,d=self.make(Path(td));d["tasks"][-1].pop("execution_contract");s.write_text(json.dumps(d))
            with self.assertRaisesRegex(ValueError,"execution_contract"):plan_machine(c)

    def test_missing_or_path_only_or_wrong_version_rejected(self):
        for mode in ("missing", "hash", "content"):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as td:
                c,p,s,d=self.make(Path(td));t=d["tasks"][-1]
                row=next(x for x in t["inputs"] if x["role"]=="production_api")
                if mode=="missing":t["inputs"].remove(row)
                elif mode=="hash":row.pop("sha256")
                else:
                    wrong=s.parent/"wrong.py";wrong.write_text("# old runtime")
                    row.update(path=str(wrong),sha256=hashlib.sha256(wrong.read_bytes()).hexdigest())
                s.write_text(json.dumps(d))
                with self.assertRaises(ValueError):WorkflowRunner(s,plan_only=True)

    def test_bundle_schema_cannot_omit_contract_on_direct_workflow(self):
        with tempfile.TemporaryDirectory() as td:
            c,p,s,d=self.make(Path(td));t=d["tasks"][-1];t.pop("execution_contract")
            t["output_schema"]={"type":"object","required":["entrypoint","factory","files"]}
            s.write_text(json.dumps(d))
            with self.assertRaisesRegex(ValueError,"execution_contract"):WorkflowRunner(s,plan_only=True)

    def test_actual_payload_contains_api(self):
        with tempfile.TemporaryDirectory() as td:
            c,p,s,d=self.make(Path(td));runner=WorkflowRunner(s,plan_only=True)
            t=d["tasks"][-1];t["inputs"]=[x for x in t["inputs"] if "path" in x]
            payload,hashes=runner._input_payload(t,{})
            wire=json.dumps(payload)
            self.assertIn("Sequence[GeneratedTask]",wire)
            self.assertIn("dict[str, Any]",wire)
            self.assertEqual(len(hashes),3)

    def test_tampered_input_rechecked_before_request(self):
        with tempfile.TemporaryDirectory() as td:
            c,p,s,d=self.make(Path(td));api=next(x for x in d["tasks"][-1]["inputs"] if x["role"]=="production_api")
            local=s.parent/"api.py";local.write_bytes(Path(api["path"]).read_bytes());api["path"]=str(local)
            s.write_text(json.dumps(d));runner=WorkflowRunner(s,Path(td)/"run",provider=p)
            local.write_text("# changed after planning")
            with self.assertRaisesRegex(ValueError,"differs"):
                runner._execute_task(d["tasks"][-1],{}, {})
            self.assertEqual(runner.data["budget"]["attempts_used"],0)

if __name__=="__main__":unittest.main()
