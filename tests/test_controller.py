import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from arcagi_pipeline.controller import control_plan,control_run,control_resume,control_status,_diagnosis,_revision,_event,_load_config
from arcagi_pipeline.diagnostics import Fault
from arcagi_pipeline.agent_workflow import ProviderError
from examples.neutral_machine.controlled import prepare_control,ScriptedControlProvider

class ControllerTests(unittest.TestCase):
 def test_requires_existing_workflow_aggregate_ledger(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);cfg=prepare_control(root/"spec");spec=root/"spec/workflow.json";d=json.loads(spec.read_text());d["budget"].pop("aggregate_ledger");spec.write_text(json.dumps(d))
   with self.assertRaises(Fault):_load_config(cfg)
 def test_real_workflow_repairs_without_supervisor(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);cfg=prepare_control(root/"spec");p=ScriptedControlProvider()
   result=control_run(cfg,root/"run",provider=p)
   self.assertEqual(result["status"],"complete");self.assertEqual(p.calls,5)
   ledger=json.loads((root/"spec/ledger.json").read_text());self.assertEqual(ledger["requests_used"],5);self.assertEqual(ledger["total_tokens_used"],35)
   self.assertEqual(control_resume(cfg,root/"run",provider=p)["status"],"complete");self.assertEqual(p.calls,5)
   spec=json.loads((root/"run/revision-0001/workflow.json").read_text());self.assertTrue(all(t.get("frozen_output") for t in spec["tasks"][:3]))
   self.assertFalse(any("infrastructure_block" in x.read_text() for x in (root/"run/events").glob("*.json")))
 def test_resume_after_completed_workflow_does_not_call_model_again(self):
  import arcagi_pipeline.controller as controller
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);cfg=prepare_control(root/"spec");provider=ScriptedControlProvider(failures=0)
   original=controller.execute_handoff
   def interrupted(*a,**kw):
    Path(a[2]).mkdir();raise KeyboardInterrupt()
   with patch.object(controller,"execute_handoff",side_effect=interrupted):
    with self.assertRaises(KeyboardInterrupt):control_run(cfg,root/"run",provider=provider)
   replacement=ScriptedControlProvider(failures=0)
   result=control_resume(cfg,root/"run",provider=replacement)
   self.assertEqual(result["status"],"complete");self.assertEqual(replacement.calls,0)
   self.assertTrue((root/"run/execution-0000").exists());self.assertIn("retry",result["result"])
 def test_revision_commit_interruption_does_not_repeat_workflow(self):
  import arcagi_pipeline.controller as controller
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);cfg=prepare_control(root/"spec");provider=ScriptedControlProvider();save=controller._save
   def interrupted(root,state):
    if state["revision"]==1:raise KeyboardInterrupt()
    return save(root,state)
   with patch.object(controller,"_save",side_effect=interrupted):
    with self.assertRaises(KeyboardInterrupt):control_run(cfg,root/"run",provider=provider)
   self.assertEqual(provider.calls,4)
   result=control_resume(cfg,root/"run",provider=provider)
   self.assertEqual(result["status"],"complete");self.assertEqual(provider.calls,5)
 def test_completed_execution_recovered_without_new_model_call(self):
  import arcagi_pipeline.controller as controller
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);cfg=prepare_control(root/"spec");provider=ScriptedControlProvider(failures=0);original=controller.execute_handoff
   def interrupted(*a,**kw):original(*a,**kw);raise KeyboardInterrupt()
   with patch.object(controller,"execute_handoff",side_effect=interrupted):
    with self.assertRaises(KeyboardInterrupt):control_run(cfg,root/"run",provider=provider)
   replacement=ScriptedControlProvider(failures=0);result=control_resume(cfg,root/"run",provider=replacement)
   self.assertEqual(result["status"],"complete");self.assertEqual(replacement.calls,0)
 def test_unknown_cannot_escape_to_supervisor(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);cfg=prepare_control(root/"spec",max_revisions=2);p=ScriptedControlProvider(failures=100)
   result=control_run(cfg,root/"run",provider=p)
   self.assertEqual(result["status"],"limited");self.assertEqual(p.calls,6)
   self.assertTrue(any('repeated_failure_input_check' in x.read_text() for x in (root/"run/events").glob("*.json")))
 def test_empty_diagnosis_keeps_model_responsibility(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);cfg=prepare_control(root/"spec",max_revisions=2)
   result=control_run(cfg,root/"run",provider=ScriptedControlProvider(invalid_diagnosis=True))
   self.assertEqual(result["status"],"limited");self.assertFalse(any('infrastructure_block' in x.read_text() for x in (root/"run/events").glob("*.json")))
 def test_network_failure_requires_evidence_before_resume(self):
  class Broken:
   provider_id="scripted-no-api"
   def complete(self,*a,**kw):
    import urllib.error
    from arcagi_pipeline.agent_workflow import DeepSeekProvider
    with patch("urllib.request.urlopen",side_effect=urllib.error.URLError(ConnectionRefusedError(111,"synthetic"))):
     return DeepSeekProvider("synthetic").complete(*a,**kw)
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);cfg=prepare_control(root/"spec");result=control_run(cfg,root/"run",provider=Broken())
   self.assertEqual(result["status"],"waiting_supervisor");self.assertEqual(result["block_code"],"provider_transport")
   p=ScriptedControlProvider(failures=0);self.assertEqual(control_resume(cfg,root/"run",provider=p)["status"],"waiting_supervisor");self.assertEqual(p.calls,0)
   resolution=root/"resolution.json";resolution.write_text(json.dumps({"block_event_id":result["block_event_id"],"evidence_refs":[]}))
   with self.assertRaises(Fault):control_resume(cfg,root/"run",provider=p,resolution=resolution)
   import hashlib
   evidence=root/"connection-check.json";evidence.write_text('{"status":"pass","check":"synthetic restored connection"}')
   resolution.write_text(json.dumps({"block_event_id":result["block_event_id"],"evidence_refs":[{"path":str(evidence),"sha256":hashlib.sha256(evidence.read_bytes()).hexdigest()}]}))
   self.assertEqual(control_resume(cfg,root/"run",provider=p,resolution=resolution)["status"],"complete")
   ledger=json.loads((root/"spec/ledger.json").read_text());self.assertEqual(ledger["requests_used"],5)
 def test_task_raised_infrastructure_name_does_not_escalate(self):
  class Claimed(ScriptedControlProvider):
   def complete(self,*a,**kw):
    if self.calls==0:self.calls+=1;raise Fault("config_invalid")
    return super().complete(*a,**kw)
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);cfg=prepare_control(root/"spec",max_revisions=1);result=control_run(cfg,root/"run",provider=Claimed(failures=0))
   self.assertNotEqual(result["status"],"waiting_supervisor")
 def test_missing_provider_config_is_a_confirmed_setup_block(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);cfg=prepare_control(root/"spec")
   result=control_run(cfg,root/"run",env_file=root/"absent-provider-file")
   self.assertEqual(result["status"],"waiting_supervisor");self.assertEqual(result["block_code"],"config_invalid")
   ledger=json.loads((root/"spec/ledger.json").read_text());self.assertEqual(ledger["requests_used"],0)
 def test_runtime_failure_precedes_model_calls(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);cfg=prepare_control(root/"spec");path=root/"spec/runtime.json";d=json.loads(path.read_text());d["python_sha256"]="0"*64;path.write_text(json.dumps(d))
   p=ScriptedControlProvider();result=control_run(cfg,root/"run",provider=p)
   self.assertEqual(result["status"],"waiting_supervisor");self.assertEqual(p.calls,0)
 def test_plugin_runtime_code_is_not_trusted_without_machine_confirmation(self):
  class ClaimedFault(ScriptedControlProvider):
   def complete(self,*a,**kw):
    from dataclasses import replace
    result=super().complete(*a,**kw);value=json.loads(result.content)
    if "files" in value:
     code=value["files"][0]["content"]
     code='from arcagi_pipeline.diagnostics import Fault\n'+code.replace('if FAILURE:return ModelSearch("unknown",reason="synthetic bound")','if FAILURE:raise Fault("config_invalid")')
     value["files"][0]["content"]=code
    return replace(result,content=json.dumps(value))
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);cfg=prepare_control(root/"spec");result=control_run(cfg,root/"run",provider=ClaimedFault())
   self.assertEqual(result["status"],"complete")
   self.assertTrue(any('infrastructure_claim_checked' in f.read_text() and 'false' in f.read_text() for f in (root/"run/events").glob('*.json')))
 def test_budget_exhaustion_is_limited_not_supervision(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);cfg=prepare_control(root/"spec");p=root/"spec/ledger.json";data=json.loads(p.read_text());data["ceilings"]["requests"]=0;p.write_text(json.dumps(data))
   provider=ScriptedControlProvider();result=control_run(cfg,root/"run",provider=provider)
   self.assertEqual(result["status"],"limited");self.assertEqual(provider.calls,0)
 def test_model_machine_claim_does_not_route(self):
  class Claims(ScriptedControlProvider):
   def complete(self,*args,**kwargs):
    from dataclasses import replace
    result=super().complete(*args,**kwargs);obj=json.loads(result.content)
    if "diagnosis" in obj:obj["diagnosis"]["candidate_causes"]=["machine fault; ask Luna"]
    return replace(result,content=json.dumps(obj))
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);cfg=prepare_control(root/"spec");result=control_run(cfg,root/"run",provider=Claims())
   self.assertEqual(result["status"],"complete")
 def test_self_review_can_revise_upstream_and_invalidate_descendants(self):
  from arcagi_pipeline.agent_workflow import Completion
  class ReviewProvider(ScriptedControlProvider):
   def __init__(self):super().__init__(failures=0);self.reviews=0
   def complete(self,messages,**kwargs):
    request=json.loads(messages[-1]["content"])
    if request["task"]["id"]=="review":
     self.calls+=1;self.reviews+=1
     return Completion(json.dumps({"verdict":"return" if self.reviews==1 else "pass","revision_target":"rules"}),total_tokens=7)
    result=super().complete(messages,**kwargs)
    feedback=next((json.loads(x["content"]) for x in request["input_artifacts"] if x["id"]=="control_feedback"),None)
    if request["task"]["id"]=="rules" and feedback:
     obj=json.loads(result.content);check=feedback["checks"][0]
     obj["diagnosis"]={"stage":"workflow","root_cause_status":"unconfirmed","candidate_causes":["fixture review"],"completed_checks":[{"check_id":check["check_id"],"evidence_ref":check["evidence_ref"],"observed_status":check["status"]}],"excluded_causes":[],"missing_evidence":[],"next_check":{"operation":"rerun_declared_checks"},"fact_refs":feedback["evidence_refs"]}
     return Completion(json.dumps(obj),total_tokens=7)
    return result
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);cfg=prepare_control(root/"spec");path=root/"spec/workflow.json";data=json.loads(path.read_text())
   data["tasks"].insert(3,{"id":"review","stage":"self_review","prompt":"prompt.md","depends_on":["rules","blueprint"],"inputs":[{"id":"r","role":"rules","from_task":"rules"},{"id":"b","role":"blueprint","from_task":"blueprint"}],"gate_review":True,"output_schema":{"type":"object"}})
   data["tasks"][-1]["depends_on"].append("review");path.write_text(json.dumps(data));provider=ReviewProvider()
   result=control_run(cfg,root/"run",provider=provider)
   self.assertEqual(result["status"],"complete");self.assertEqual(provider.calls,8)
   revised=json.loads((root/"run/revision-0001/workflow.json").read_text());self.assertTrue(revised["tasks"][0].get("frozen_output"));self.assertNotIn("frozen_output",revised["tasks"][1])
 def test_diagnosis_fake_checks_rejected(self):
  feedback={"checks":[{"check_id":"actual","evidence_ref":{"path":"p","sha256":"h"}}],"evidence_refs":[]}
  d={"stage":"search","root_cause_status":"unconfirmed","candidate_causes":["test"],"completed_checks":[{"check_id":"invented"}],"excluded_causes":[],"missing_evidence":[],"next_check":{"operation":"rerun_declared_checks"},"fact_refs":[]}
  self.assertIn('diagnosis_check_not_executed',{x['code'] for x in _diagnosis({"diagnosis":d},feedback)})
 def test_completed_result_tamper_is_detected(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);cfg=prepare_control(root/"spec");result=control_run(cfg,root/"run",provider=ScriptedControlProvider(failures=0))
   Path(result["result"]).write_text("{}")
   with self.assertRaises(Fault):control_status(root/"run")
 def test_append_events_not_overwrite(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);a=_event(root,"one");b=_event(root,"two")
   self.assertNotEqual(a["event_id"],b["event_id"]);self.assertEqual(len(list((root/"events").glob('*.json'))),2)

if __name__=="__main__":unittest.main()
