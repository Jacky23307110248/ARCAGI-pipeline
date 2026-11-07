import copy, hashlib, http.client, json, tempfile, unittest, urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from arcagi_pipeline.agent_workflow import WorkflowRunner, DeepSeekProvider, ProviderError, WorkflowError
from arcagi_pipeline.machine import execute_handoff, materialize
from arcagi_pipeline.blueprint import validate_design
from arcagi_pipeline.production import FrozenInputs,GeneratedTask,ModelSearch,EngineVerdict,FulfillmentVerdict,run_production
from examples.neutral_machine.demo import prepare,fixture,demo

class NeverProvider:
    provider_id="scripted-no-api"
    def complete(self,*a,**kw):raise AssertionError("unexpected model request")

class SystemRiskTests(unittest.TestCase):
    def imports(self,root):
        c,_=prepare(root/"spec",fixture(2));p=c.parent/"workflow.json";s=json.loads(p.read_text())
        for task,value in zip(s["tasks"],fixture(2)):
            f=p.parent/(task["id"]+".json");f.write_text(json.dumps(value))
            task["frozen_output"]={"path":f.name,"sha256":hashlib.sha256(f.read_bytes()).hexdigest()}
        p.write_text(json.dumps(s));return p,s
    def test_frozen_import_has_no_requests_and_preserves_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);p,s=self.imports(root);r=WorkflowRunner(p,root/"run",provider=NeverProvider());result=r.run()
            self.assertEqual(result["attempts_used"],0);self.assertEqual(result["tokens_used"],0)
            for t in s["tasks"]:self.assertEqual((root/"run/artifacts"/(t["id"]+".json")).read_bytes(),(p.parent/t["frozen_output"]["path"]).read_bytes())
            self.assertEqual(r.run()["attempts_used"],0)
    def test_frozen_imports_preserve_aggregate_ledger_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);p,s=self.imports(root);ledger=root/"ledger.json"
            ledger.write_text(json.dumps({"ceilings":{"requests":100,"total_tokens":1000000,"input_tokens_per_request":500000,"output_tokens_per_request":100000,"elapsed_seconds":86400},"requests_used":7,"total_tokens_used":42,"runtime_requests":{}}))
            original=ledger.read_bytes();s["budget"]["aggregate_ledger"]=str(ledger);p.write_text(json.dumps(s))
            result=WorkflowRunner(p,root/"run",provider=NeverProvider()).run()
            self.assertEqual(result["attempts_used"],0);self.assertEqual(ledger.read_bytes(),original)

    def test_explicit_provider_file_is_not_overridden_by_environment(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"provider.conf";p.write_text("DEEPSEEK_API_KEY=synthetic-file-key\nDEEPSEEK_BASE_URL=https://file.invalid/v1\nDEEPSEEK_MODEL=file-alias\n")
            provider,model=DeepSeekProvider.from_environment(p,environ={"DEEPSEEK_API_KEY":"synthetic-env-key","DEEPSEEK_BASE_URL":"https://env.invalid/v1","DEEPSEEK_MODEL":"env-alias"})
            self.assertEqual(provider.base_url,"https://file.invalid/v1");self.assertEqual(model,"file-alias")

    def test_import_only_run_does_not_load_provider_credentials(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);p,s=self.imports(root)
            with patch.object(DeepSeekProvider,"from_environment",side_effect=AssertionError("credentials should not be read")):
                result=WorkflowRunner(p,root/"run").run()
            self.assertEqual(result["attempts_used"],0)

    def test_import_recovery_after_published_output_has_no_charge(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);p,s=self.imports(root);r=WorkflowRunner(p,root/"run",provider=NeverProvider());original=r._save
            count=[0]
            def crash():
                count[0]+=1
                if count[0]==3:raise KeyboardInterrupt()
                return original()
            with patch.object(r,"_save",side_effect=crash):
                with self.assertRaises(KeyboardInterrupt):r.run()
            recovered=WorkflowRunner(p,root/"run",provider=NeverProvider()).run()
            self.assertEqual(recovered["status"],"complete");self.assertEqual(recovered["attempts_used"],0)

    def test_changed_terminal_output_blocks_resume(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);p,s=self.imports(root);r=WorkflowRunner(p,root/"run",provider=NeverProvider());r.run()
            (root/"run/artifacts/implementation.json").write_text("{}")
            with self.assertRaisesRegex(WorkflowError,"missing or changed"):r.run()
    def test_upstream_tamper_and_path_escape_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);p,s=self.imports(root);r=WorkflowRunner(p,root/"run",provider=NeverProvider());r.run()
            state={x["id"]:x for x in r.data["tasks"]};(root/"run/artifacts/evidence.json").write_text("{}")
            with self.assertRaisesRegex(WorkflowError,"hash"):r._input_payload(s["tasks"][1],state)
            state["evidence"]["output_path"]="../external.json"
            with self.assertRaisesRegex(WorkflowError,"in-run"):r._input_payload(s["tasks"][1],state)
    def test_frozen_hash_and_schema_fail_without_model(self):
        for mode in ("hash","schema","blind"):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as td:
                root=Path(td);p,s=self.imports(root);t=s["tasks"][0]
                if mode=="hash":t["frozen_output"]["sha256"]="0"*64
                if mode=="schema":t["output_schema"]={"type":"object","required":["absent"]}
                if mode=="blind":t["evidence_lane"]="trajectory_blind"
                p.write_text(json.dumps(s))
                with self.assertRaises(ValueError):WorkflowRunner(p,root/"run",provider=NeverProvider()).run()
    def test_bad_input_definition_is_not_silently_ignored(self):
        for item in ({"id":"x","role":"x","from_task":"evidence","path":3},3):
            with self.subTest(item=item),tempfile.TemporaryDirectory() as td:
                c,_=prepare(Path(td)/"spec",fixture(1));p=c.parent/"workflow.json";s=json.loads(p.read_text());s["tasks"][-1]["inputs"].append(item);p.write_text(json.dumps(s))
                with self.assertRaises(ValueError):WorkflowRunner(p,plan_only=True)
    def test_transport_records_codes_without_error_message(self):
        errors=[urllib.error.URLError(PermissionError(13,"private secret text")),http.client.IncompleteRead(b"private body",5)]
        for error in errors:
            with self.subTest(error=type(error).__name__),patch("urllib.request.urlopen",side_effect=error):
                with self.assertRaises(ProviderError) as caught:DeepSeekProvider("synthetic-key").complete([],model="synthetic",max_tokens=1)
                data=caught.exception.diagnostic_observed
                self.assertIn("exception_type",data);self.assertNotIn("private",json.dumps(data));self.assertEqual(caught.exception.diagnostic_code,"provider_transport")
    def test_bad_design_is_validation_error(self):
        d=fixture(1);self.assertTrue(validate_design([],d[1],d[2]))
        d[1]["rules"][0]["evidence_refs"]=[{}];self.assertTrue(validate_design(*d[:3]))
    def test_invalid_python_rejected_before_materialization(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):materialize({"entrypoint":"p.py","factory":"create","files":[{"path":"p.py","content":"def invalid("}]},Path(td)/"plugin")
            self.assertFalse((Path(td)/"plugin").exists())
    def test_malformed_bundle_fields_are_protocol_errors(self):
        for bundle in ({"files":[3]}, {"files":[{"path":"p.py","content":"pass"}],"entrypoint":"p.py","factory":7}):
            with self.subTest(bundle=bundle),tempfile.TemporaryDirectory() as td:
                with self.assertRaises(ValueError) as caught:materialize(bundle,Path(td)/"plugin")
                self.assertEqual(caught.exception.diagnostic_code,"output_protocol_invalid")

    def test_worker_malformed_output_preserves_reports(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);demo(root/"base",1)
            for i,raw in enumerate(("{", "{}",json.dumps({"status":"invented","tasks":[],"feedback":[]}),json.dumps({"schema_version":"arcagi-production/v1","status":"pass","tasks":[],"feedback":[]}))):
                out=root/("bad"+str(i))
                def fake(*a,**kw):
                    p=out/"production";p.mkdir();(p/"production.json").write_text(raw);return SimpleNamespace(returncode=0)
                with patch("arcagi_pipeline.machine.subprocess.run",side_effect=fake),patch("arcagi_pipeline.runtime_control.preflight_runtime",return_value={"ok":True}):
                    result=execute_handoff(root/"base/workflow",dict(zip(("evidence","rules","blueprint","implementation"),("evidence","rules","blueprint","implementation"))),out,runtime_config=root/"base/spec/runtime.json",production_contract="design-production/v1")
                self.assertEqual(result["status"],"return");self.assertTrue((out/"machine.json").exists());self.assertIn("invalid_production_ref",json.loads((out/"revision_packet.json").read_text()))
    def test_malformed_plugin_fields_archive_protocol_failure(self):
        class Plugin:
            plugin_id="neutral"
            def generate(self,i,s):return [GeneratedTask("task",{})]
            def validate(self,t):return []
            def search(self,t,c):return ModelSearch("candidate")
            def replay(self,t,c):return EngineVerdict(True,trace=({"initial_goal":True},))
        for mode in ("generation","payload","search","verdict"):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as td:
                root=Path(td);rule=root/"r";bp=root/"b";rule.write_text("{}");bp.write_text("{}");p=Plugin()
                if mode=="generation":p.generate=lambda i,s:[{"wrong":"type"}]
                if mode=="payload":p.generate=lambda i,s:[GeneratedTask("task",{ "bad":object()})]
                if mode=="search":p.search=lambda t,c:ModelSearch("candidate",complete_model="yes")
                if mode=="verdict":p.replay=lambda t,c:EngineVerdict("yes")
                result=run_production(p,FrozenInputs(rule,bp),seed=0,output_dir=root/"out")
                self.assertEqual(result["status"],"return");self.assertEqual(result["feedback"][0]["route"],"artifact_revision");self.assertTrue((root/"out/production.json").exists())

if __name__=="__main__":unittest.main()
