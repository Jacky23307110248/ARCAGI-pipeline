import contextlib,io,json,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from arcagi_pipeline.diagnostics import Archive,ArchiveWriteError,exclusive_json,file_ref,scrub
from arcagi_pipeline.machine import run_machine,execute_handoff
from arcagi_pipeline.agent_workflow import WorkflowRunner,WorkflowError,Completion,ProviderError
from arcagi_pipeline.cli import main
from examples.neutral_machine.demo import fixture,prepare,demo,ScriptedProvider

class DiagnosticsTests(unittest.TestCase):
    def archive(self,root):return Archive(root/"logs")
    def read_all(self,root):return "\n".join(p.read_text(encoding="utf-8") for p in root.rglob("*.json"))
    def test_facts_exclusive_and_attribution_append_only(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);a=self.archive(root);c=a.start("test").branch("replay")
            ref=c.record("engine_mismatch","engine_replay",expected={"position":1},observed={"position":2})
            fact=a.root/"events"/(ref["event_id"]+".json");before=fact.read_bytes()
            with self.assertRaises(ArchiveWriteError):exclusive_json(fact,{"overwritten":True})
            new=a.attribute(ref["event_id"],"artifact",basis=[{"probe":{"expected":1,"observed":2}}],source="independent_review",confidence="confirmed",supersedes=ref["attribution_id"])
            view=a.view(ref["event_id"])
            self.assertEqual(len(view["attributions"]),2)
            self.assertEqual(view["current_attribution"]["id"],new)
            self.assertEqual(view["current_packet"]["attribution_id"],new)
            self.assertEqual(view["current_packet"]["route"],"artifact_revision")
            self.assertEqual(len(view["route_packets"]),2)
            self.assertEqual(fact.read_bytes(),before)
            with self.assertRaises(ValueError):a.attribute(ref["event_id"],"runtime",basis=[{"claim":True}],source="plugin",supersedes=new)
    def test_repeated_or_plugin_claims_do_not_escalate(self):
        with tempfile.TemporaryDirectory() as td:
            a=self.archive(Path(td));c=a.start("test").branch("worker")
            for _ in range(4):
                ref=c.record("worker_timeout","worker",claims={"category":"runtime","repair_main":True})
                self.assertEqual(ref["category"],"undetermined")
                self.assertEqual(ref["route"],"diagnosis_required")
    def test_exception_messages_and_sensitive_fields_not_archived(self):
        with tempfile.TemporaryDirectory() as td:
            a=self.archive(Path(td));c=a.start("test").branch("worker")
            try:raise RuntimeError("PRIVATE_EXCEPTION_SENTINEL")
            except RuntimeError as exc:
                c.record("exception","worker",exc=exc,observed={"api_key":"PRIVATE_KEY","stdout":"PRIVATE_STDOUT","safe":"Bearer abcdefghijklmno"})
            text=self.read_all(a.root)
            for marker in ["PRIVATE_EXCEPTION_SENTINEL","PRIVATE_KEY","PRIVATE_STDOUT","abcdefghijklmno"]:self.assertNotIn(marker,text)
    def test_explicit_continuation_and_resolution_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);a=self.archive(root);c=a.start("first").branch("replay");ref=c.record("engine_mismatch","replay")
            successor=a.start("second",previous_run=c.run_id,pending_events=[ref["event_id"]])
            successor.branch("check").outcome("pass")
            evidence=root/"actual.json";evidence.write_text('{"status":"pass"}')
            check=root/"check.json";check.write_text(json.dumps({"event_id":ref["event_id"],"run_id":successor.run_id,"status":"pass","check":"replayed relevant candidate","evidence_refs":[file_ref(evidence)]}))
            a.resolve(ref["event_id"],run_id=successor.run_id,check_refs=[check])
            self.assertTrue(a.view(ref["event_id"])["resolved"])
            evidence.write_text('{"changed":true}')
            view=a.view(ref["event_id"]);self.assertFalse(view["resolved"])
            self.assertEqual(len(view["resolutions"]),1)
            self.assertEqual(view["successor_runs"][0]["previous_run"],c.run_id)
    def test_fact_and_report_tampering_are_detected(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);a=self.archive(root);ref=a.start("test").record("engine_mismatch","replay")
            report=root/"report.json";report.write_text('{"status":"return"}')
            a.attach(ref["event_id"],[file_ref(report)])
            self.assertTrue(a.view(ref["event_id"])["attachments"][0]["evidence_intact"])
            report.write_text('{"changed":true}')
            self.assertFalse(a.view(ref["event_id"])["attachments"][0]["evidence_intact"])
            fact=a.root/"events"/(ref["event_id"]+".json")
            content=fact.read_text(encoding="utf-8");fact.write_text(content+" ",encoding="utf-8")
            with self.assertRaisesRegex(ValueError,"fact integrity"):
                a.view(ref["event_id"])
    def test_failure_or_unlinked_check_cannot_resolve(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);a=self.archive(root);c=a.start("first");ref=c.record("exception","check")
            unlinked=a.start("other")
            check=root/"failure.json";check.write_text('{"status":"return"}')
            with self.assertRaises(ValueError):a.resolve(ref["event_id"],run_id=unlinked.run_id,check_refs=[check])
            linked=a.start("repair",pending_events=[ref["event_id"]])
            with self.assertRaises(ValueError):a.resolve(ref["event_id"],run_id=linked.run_id,check_refs=[check])
    def test_read_only_view_does_not_create_archive(self):
        with tempfile.TemporaryDirectory() as td:
            a=self.archive(Path(td));self.assertEqual(a.view(),[]);self.assertFalse(a.root.exists())
    def test_preflight_failure_has_facts(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);config,provider=prepare(root/"spec",fixture(1));data=json.loads(config.read_text());data["roles"]={};config.write_text(json.dumps(data))
            with self.assertRaises(ValueError) as caught:run_machine(config,root/"workflow",root/"execution",provider=provider,log_dir=root/"logs")
            ref=caught.exception.diagnostic_ref;self.assertEqual(ref["route"],"machine_diagnosis")
            self.assertTrue((root/"logs/events"/(ref["event_id"]+".json")).is_file())
            self.assertFalse((root/"workflow").exists())
    def test_archive_failure_is_explicit(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);blocker=root/"not-directory";blocker.write_text("occupied")
            with self.assertRaises(ArchiveWriteError):Archive(blocker).start("test")
            with contextlib.redirect_stdout(io.StringIO()) as stream:
                code=main(["machine-plan","--config",str(root/"missing.json"),"--log-dir",str(blocker)])
            self.assertEqual(code,2);self.assertIn("diagnostic_archive_write_failed",stream.getvalue())
    def test_route_packets_keep_engine_mismatch_undetermined(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/"demo";result=demo(root,1,"engine_reject")
            ref=result["feedback"][0];self.assertEqual(ref["route"],"diagnosis_required")
            packet=json.loads((root/"execution/diagnosis_required.json").read_text())
            self.assertEqual(packet["events"][0]["event_id"],ref["event_id"])
            revision=json.loads((root/"execution/revision_packet.json").read_text())
            self.assertNotIn("Revise the responsible",revision["instruction"])
    def test_unknown_is_limited_result_not_machine_defect(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/"demo";result=demo(root,1,"unknown");ref=result["feedback"][0]
            event=Archive(ref["archive"]).read("events",ref["event_id"])
            self.assertEqual(event["kind"],"limited_result")
            self.assertEqual(ref["category"],"undetermined")
    def test_worker_timeout_and_crash_have_diagnostic_routes(self):
        for mode in ["timeout","crash","module"]:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as td:
                root=Path(td);artifacts=fixture(1)
                first={"timeout":"import time; time.sleep(5)","crash":"import os;os._exit(7)","module":"import intentionally_absent_dependency"}[mode]
                artifacts[-1]["files"][0]["content"]=first+"\n"+artifacts[-1]["files"][0]["content"]
                config,provider=prepare(root/"spec",artifacts)
                data=json.loads(config.read_text());data["timeout_seconds"]=1 if mode=="timeout" else 10;config.write_text(json.dumps(data))
                result=run_machine(config,root/"workflow",root/"execution",provider=provider)
                self.assertEqual(result["status"],"return")
                self.assertEqual(result["feedback"][0]["route"],"diagnosis_required")
    def test_model_protocol_error_and_retry_keep_budget(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);config,_=prepare(root/"spec",fixture(1));spec_path=config.parent/"workflow.json";spec=json.loads(spec_path.read_text());spec["budget"]["max_attempts"]=6;spec_path.write_text(json.dumps(spec))
            class Bad:
                provider_id="fixture"
                def complete(self,*args,**kwargs):return Completion("PRIVATE_INVALID_JSON",total_tokens=7)
            runner=WorkflowRunner(spec_path,root/"workflow",provider=Bad())
            with self.assertRaises(WorkflowError) as caught:runner.run()
            ref=caught.exception.diagnostic_ref;self.assertEqual(ref["route"],"artifact_revision")
            self.assertEqual(runner.data["budget"]["tokens_used"],7)
            runner.provider=ScriptedProvider(fixture(1));runner.run(retry_failed=True)
            self.assertEqual(runner.data["budget"]["attempts_used"],5)
            self.assertEqual(runner.data["budget"]["tokens_used"],407)
            self.assertFalse(Archive(ref["archive"]).view(ref["event_id"])["resolved"])
            self.assertNotIn("PRIVATE_INVALID_JSON",self.read_all(Path(ref["archive"])))
    def test_provider_transport_is_runtime_not_core_repair_permission(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);config,_=prepare(root/"spec",fixture(1))
            class Offline:
                provider_id="fixture"
                def complete(self,*a,**kw):raise ProviderError("PRIVATE_PROVIDER_TEXT",code="provider_transport")
            with self.assertRaises(WorkflowError) as caught:
                WorkflowRunner(config.parent/"workflow.json",root/"workflow",provider=Offline()).run()
            ref=caught.exception.diagnostic_ref;self.assertEqual(ref["route"],"machine_diagnosis")
            self.assertNotIn("PRIVATE_PROVIDER_TEXT",self.read_all(Path(ref["archive"])))
    def test_structural_claim_protocol_failure_routes_to_artifact(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/"demo";result=demo(root,1,"bad_evidence")
            self.assertEqual(result["feedback"][0]["route"],"artifact_revision")

if __name__=="__main__":unittest.main()
