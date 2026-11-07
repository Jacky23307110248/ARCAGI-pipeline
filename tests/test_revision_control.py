import copy,json,tempfile,unittest,hashlib
from pathlib import Path
from unittest.mock import patch
from arcagi_pipeline.revision_protocol import review_targets,review_schema,ReviewProtocolError
from arcagi_pipeline.controller import (_revision,_read,_hash,control_run,control_resume,
    control_pause,control_status,control_plan,_load_config)
from arcagi_pipeline.agent_workflow import WorkflowRunner,WorkflowError,Completion,reconcile_workflow
from arcagi_pipeline.control_checkpoint import control_fork
from arcagi_pipeline.diagnostics import Fault
from examples.neutral_machine.controlled import prepare_control,ScriptedControlProvider
import test_agent_workflow as workflow_tests
FakeProvider=workflow_tests.FakeProvider

class RevisionControlTests(unittest.TestCase):
 def test_revision_spec_drift_is_blocked_before_another_request(self):
  import arcagi_pipeline.controller as controller
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);cfg=prepare_control(root/"spec");p=ScriptedControlProvider();original=controller._revision
   def stop_after_commit(*args,**kwargs):original(*args,**kwargs);raise KeyboardInterrupt()
   with patch.object(controller,'_revision',side_effect=stop_after_commit):
    with self.assertRaises(KeyboardInterrupt):control_run(cfg,root/"run",provider=p)
   state=_read(root/"run/state.json");spec=Path(state["workflow_spec"]);data=_read(spec);data["workflow_id"]+="-drift";spec.write_text(json.dumps(data))
   next_provider=ScriptedControlProvider(failures=0);result=control_resume(cfg,root/"run",provider=next_provider)
   self.assertEqual(result["status"],"waiting_supervisor");self.assertEqual(result["block_code"],"artifact_changed");self.assertEqual(next_provider.calls,0)
 def test_worker_log_bytes_do_not_trigger_locale_decode_errors(self):
  from dataclasses import replace
  class Logs(ScriptedControlProvider):
   def complete(self,*args,**kwargs):
    result=super().complete(*args,**kwargs);value=json.loads(result.content)
    if "files" in value:value["files"][0]["content"]="import sys\nsys.stdout.buffer.write(bytes([255]))\nsys.stderr.buffer.write(bytes([254]))\n"+value["files"][0]["content"]
    return replace(result,content=json.dumps(value))
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);cfg=prepare_control(root/"spec");thread_errors=[]
   with patch('threading.excepthook',side_effect=thread_errors.append):result=control_run(cfg,root/"run",provider=Logs(failures=0))
   self.assertEqual(result["status"],"complete");self.assertEqual(thread_errors,[])
 def test_long_blind_lineage_is_iterative_and_rejects_source_input(self):
  from arcagi_pipeline.agent_workflow import _validate_blind_import
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);previous=None
   for index in range(125):
    directory=root/str(index);directory.mkdir();artifact=directory/"claims.json";artifact.write_text('{"claims":[]}')
    task={"id":"claims","evidence_lane":"trajectory_blind","inputs":[]}
    if previous:task.update(previous)
    spec=directory/"spec.json";spec.write_text(json.dumps({"tasks":[task]}))
    record=directory/"workflow_run.json";record.write_text(json.dumps({"spec_sha256":_hash(spec),"tasks":[{"id":"claims","status":"complete","output_path":"claims.json","output_sha256":_hash(artifact)}]}))
    previous={"frozen_origin":{"record_path":str(record),"record_sha256":_hash(record),"spec_path":str(spec),"spec_sha256":_hash(spec),"task_id":"claims"},"frozen_output":{"path":str(artifact),"sha256":_hash(artifact)}}
   _validate_blind_import(previous)
   data=_read(spec);data["tasks"][0]["inputs"]=[{"role":"source_code","path":str(spec)}];spec.write_text(json.dumps(data))
   state=_read(record);state["spec_sha256"]=_hash(spec);record.write_text(json.dumps(state));previous["frozen_origin"].update(record_sha256=_hash(record),spec_sha256=_hash(spec))
   with self.assertRaises(WorkflowError):_validate_blind_import(previous)
 def test_failed_schema_check_details_survive_no_request_resume(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);spec,_,_=workflow_tests.AgentWorkflowTests().make_bundle(root)
   p=FakeProvider([Completion('{"claims":"wrong type"}',total_tokens=7)])
   runner=WorkflowRunner(spec,root/"run",provider=p)
   for index in range(2):
    with self.assertRaises(WorkflowError) as got:runner.run()
    self.assertEqual(got.exception.protocol_facts["check"],"output_schema")
    self.assertTrue(got.exception.diagnostic_ref["archive"])
   self.assertEqual(len(p.calls),1)
 def test_offline_reconciliation_is_idempotent_for_interrupted_request(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);spec=self.gate_spec(root)
   p=FakeProvider([KeyboardInterrupt()])
   with self.assertRaises(KeyboardInterrupt):WorkflowRunner(spec,root/"run",provider=p).run()
   before=_read(root/"ledger.json");self.assertEqual(before["requests_used"],1)
   reconcile_workflow(spec,root/"run");first=_read(root/"ledger.json")
   reconcile_workflow(spec,root/"run");second=_read(root/"ledger.json")
   self.assertEqual(first["tokens_inflight_reserved"],0);self.assertEqual(first["total_tokens_used"],second["total_tokens_used"])
   self.assertEqual(second["requests_used"],1);self.assertEqual(len(p.calls),1)
 def test_malformed_then_multi_target_review_completes_without_supervisor(self):
  class Reviews(ScriptedControlProvider):
   def __init__(self):super().__init__(failures=0);self.reviews=0;self.seen=[]
   def complete(self,messages,**kwargs):
    from dataclasses import replace
    request=json.loads(messages[-1]["content"]);tid=request["task"]["id"];self.seen.append(tid)
    if tid=="review":
     self.calls+=1;self.reviews+=1
     value=({"verdict":"return","revision_target":"rules; blueprint"} if self.reviews==1 else {"verdict":"return","revision_targets":["rules","blueprint"]} if self.reviews==2 else {"verdict":"pass","revision_targets":[]})
     result=Completion(json.dumps(value),total_tokens=7)
    else:result=super().complete(messages,**kwargs);value=json.loads(result.content)
    feedback=next((json.loads(x["content"]) for x in request["input_artifacts"] if x["id"]=="control_feedback"),None)
    if feedback:
     check=feedback["checks"][0]
     value["diagnosis"]={"stage":check["stage"],"root_cause_status":"unconfirmed","candidate_causes":["fixture"],"completed_checks":[{"check_id":check["check_id"],"evidence_ref":check["evidence_ref"],"observed_status":check["status"]}],"excluded_causes":[],"missing_evidence":[],"next_check":{"operation":"rerun_declared_checks"},"fact_refs":feedback["evidence_refs"]}
     result=replace(result,content=json.dumps(value))
    return result
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);cfg=prepare_control(root/"spec",max_revisions=5);spec=root/"spec/workflow.json";data=_read(spec)
   data["tasks"].insert(3,{"id":"review","stage":"self_review","depends_on":["rules","blueprint"],"prompt":"prompt.md","inputs":[{"id":"r","role":"rules","from_task":"rules"},{"id":"b","role":"blueprint","from_task":"blueprint"}],"gate_review":True,"output_schema":{"type":"object"}})
   data["tasks"][-1]["depends_on"].append("review");spec.write_text(json.dumps(data));p=Reviews()
   result=control_run(cfg,root/"run",provider=p)
   self.assertEqual(result["status"],"complete");self.assertEqual(result["revision"],2)
   events=[_read(p) for p in (root/"run/events").glob('*.json')]
   diagnoses=[e for e in events if e["kind"]=="diagnosis_checked"]
   self.assertEqual({e["task_id"] for e in diagnoses},{"rules","blueprint","review"})
   for event in diagnoses:
    self.assertEqual(Path(event["artifact"]["path"]).stem,event["task_id"])
    self.assertEqual(_hash(event["artifact"]["path"]),event["artifact"]["sha256"])
   self.assertEqual(p.seen,["evidence","rules","blueprint","review","review","rules","blueprint","review","implementation"])
   self.assertEqual(p.calls,9)
   first=_read(root/"run/revision-0001/feedback.json");self.assertEqual(first["protocol"]["reason"],"invalid_legacy_target")
   second=_read(root/"run/revision-0002/workflow.json");self.assertTrue(all(any(i["id"]=="control_self_review" for i in t["inputs"]) for t in second["tasks"] if t["id"] in {"rules","blueprint"}))
 def review_fixture(self):
  tasks=[{"id":"a"},{"id":"b"},{"id":"review","depends_on":["a","b"],"output_schema":{"type":"object"}}]
  return tasks,tasks[-1]
 def test_closed_targets_do_not_extract_ids_from_prose(self):
  tasks,t=self.review_fixture()
  for target in ("a; b","a, b","a (please fix)","missing",["a"],{},None):
   with self.subTest(target=target),self.assertRaises(ReviewProtocolError):review_targets({"verdict":"return","revision_target":target},t,tasks)
  self.assertEqual(review_targets({"verdict":"return","revision_target":"a"},t,tasks),["a"])
 def test_array_contract_and_conflicts(self):
  tasks,t=self.review_fixture()
  self.assertEqual(review_targets({"verdict":"return","revision_targets":["a","b"]},t,tasks),["a","b"])
  for value in ({"verdict":"return","revision_targets":[]},{"verdict":"pass","revision_targets":["a"]},{"verdict":[],"revision_targets":[]},{"verdict":"return","revision_targets":["a","a"]},{"verdict":"return","revision_targets":["a"],"revision_target":"a"}):
   with self.subTest(value=value),self.assertRaises(ReviewProtocolError):review_targets(value,t,tasks)
 def test_source_review_cannot_address_blind_stage(self):
  tasks,t=self.review_fixture();tasks[0]["evidence_lane"]="trajectory_blind"
  with self.assertRaises(ReviewProtocolError):review_targets({"verdict":"return","revision_targets":["a"]},t,tasks)
  self.assertEqual(review_schema(t,tasks)["properties"]["revision_targets"]["items"]["enum"],["b"])
 def test_config_rejects_errors_before_requests(self):
  for field,value in (("roles",{"evidence":"evidence","rules":"evidence","blueprint":"blueprint","implementation":"implementation"}),("revision_targets","rules"),("revision_targets",["missing"]),("seed",True),("cap",0),("timeout_seconds",float('nan'))):
   with self.subTest(field=field,value=value),tempfile.TemporaryDirectory() as td:
    cfg=prepare_control(Path(td)/"spec");data=_read(cfg);data[field]=value;cfg.write_text(json.dumps(data))
    with self.assertRaises(Fault):_load_config(cfg)
 def gate_spec(self,root,blind=False):
  spec,_,_=workflow_tests.AgentWorkflowTests().make_bundle(root,budget_tokens=100000)
  data=_read(spec);a,b=data["tasks"];b.update(gate_review=True,evidence_lane="trajectory_blind" if blind else "general",output_schema={"type":"object"})
  ledger=root/"ledger.json";ledger.write_text(json.dumps({"ceilings":{"requests":100,"total_tokens":1000000,"input_tokens_per_request":100000,"output_tokens_per_request":100000,"elapsed_seconds":3600},"requests_used":0,"total_tokens_used":0}))
  data["budget"]["aggregate_ledger"]=str(ledger)
  if not blind:a["evidence_lane"]="general"
  spec.write_text(json.dumps(data));return spec
 def test_fresh_and_cached_bad_gate_have_identical_protocol_feedback(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);spec=self.gate_spec(root);provider=FakeProvider([Completion('{"claims":[]}',total_tokens=7),Completion('{"verdict":"return","revision_target":"trajectory-claims; another"}',total_tokens=7)])
   runner=WorkflowRunner(spec,root/"run",provider=provider)
   for retry in range(2):
    with self.assertRaises(WorkflowError) as got:runner.run()
    self.assertEqual(got.exception.diagnostic_code,"output_protocol_invalid")
    self.assertEqual(got.exception.revision_targets,["rule-contract-review"])
    self.assertEqual(got.exception.protocol_facts["reason"],"invalid_legacy_target")
   self.assertEqual(len(provider.calls),2)
   request=json.loads(provider.calls[-1]["messages"][-1]["content"])
   self.assertIn("revision_targets",request["required_output_schema"]["required"])
   self.assertIn("overrides legacy singular",provider.calls[-1]["messages"][0]["content"])
 def test_blind_self_feedback_is_actual_input_with_verified_origin(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);spec=self.gate_spec(root,blind=True)
   p=FakeProvider([Completion('{"claims":[{"claim":"old"}]}',total_tokens=7),Completion('{"verdict":"return","revision_targets":["trajectory-claims"],"findings":["synthetic DS self observation"]}',total_tokens=7)])
   runner=WorkflowRunner(spec,root/"run0",provider=p)
   with self.assertRaises(WorkflowError):runner.run()
   record=root/"run0/workflow_run.json";rows={r["id"]:r for r in runner.data["tasks"]};r=rows["rule-contract-review"]
   feedback={"self_review":{"path":str(root/"run0"/r["output_path"]),"sha256":r["output_sha256"],"origin":{"record_path":str(record),"record_sha256":_hash(record),"spec_path":str(spec),"spec_sha256":_hash(spec),"task_id":"rule-contract-review"}}}
   state={"workflow_spec":str(spec),"initial_spec":str(spec),"current_workflow":str(root/"run0"),"revision":0,"history":[]};cfg={"roles":{"implementation":"rule-contract-review"}}
   _revision(root,state,cfg,runner.data,feedback,["trajectory-claims"])
   self.assertEqual(_read(root/"revision-0001/feedback.json")["revision_targets"],[])
   new_spec=Path(state["workflow_spec"]);p2=FakeProvider([Completion('{"claims":[{"claim":"new"}]}',total_tokens=7),Completion('{"verdict":"pass","revision_targets":[]}',total_tokens=7)])
   WorkflowRunner(new_spec,root/"run1",provider=p2).run()
   inputs=json.loads(p2.calls[0]["messages"][-1]["content"])["input_artifacts"]
   self.assertEqual({i["role"] for i in inputs},{"trajectory_packet","trajectory_review_feedback","prior_trajectory_claims"})
   self.assertTrue(any('synthetic DS self observation' in i["content"] for i in inputs))
   data=_read(new_spec);item=data["tasks"][0]["inputs"][-2];item["blind_revision_origin"]["record_sha256"]="0"*64;new_spec.write_text(json.dumps(data))
   with self.assertRaises(WorkflowError):WorkflowRunner(new_spec,plan_only=True)
 def test_union_invalidation_and_no_silent_target_fallback(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);cfg_path=prepare_control(root/"spec");cfg=_read(cfg_path);spec=root/"spec/workflow.json"
   runner=WorkflowRunner(spec,root/"workflow",provider=ScriptedControlProvider(failures=0));runner.run()
   state={"workflow_spec":str(spec),"initial_spec":str(spec),"current_workflow":str(root/"workflow"),"revision":0,"history":[]}
   with self.assertRaises(Fault):_revision(root,state,cfg,runner.data,{},"missing")
   ref={'path':str(root/'workflow/workflow_run.json'),'sha256':_hash(root/'workflow/workflow_run.json')}
   feedback={"checks":[{'check_id':'workflow-finished','status':'complete','evidence_ref':ref}],"evidence_refs":[ref]};_revision(root,state,cfg,runner.data,feedback,["rules","blueprint"])
   tasks={t["id"]:t for t in _read(state["workflow_spec"])["tasks"]}
   self.assertIn("frozen_output",tasks["evidence"])
   for tid in ("rules","blueprint","implementation"):self.assertNotIn("frozen_output",tasks[tid])
   self.assertEqual(state["diagnosis_tasks"],["rules","blueprint"])
 def test_old_downstream_feedback_is_not_replayed_after_upstream_revision(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);cfg_path=prepare_control(root/"spec");cfg=_read(cfg_path);spec=root/"spec/workflow.json"
   runner=WorkflowRunner(spec,root/"workflow",provider=ScriptedControlProvider(failures=0));runner.run()
   from arcagi_pipeline.controller import _absolute_spec
   altered=_absolute_spec(spec);t=altered["tasks"][-1];t["inputs"].append({"id":"control_feedback","role":"revision_feedback","path":str(spec)});t["output_schema"].setdefault("required",[]).append("diagnosis")
   previous=root/"previous.json";previous.write_text(json.dumps(altered))
   state={"workflow_spec":str(previous),"initial_spec":str(spec),"current_workflow":str(root/"workflow"),"revision":0,"history":[]}
   ref={'path':str(root/'workflow/workflow_run.json'),'sha256':_hash(root/'workflow/workflow_run.json')}
   _revision(root,state,cfg,runner.data,{'checks':[{'check_id':'workflow-finished','status':'complete','evidence_ref':ref}],'evidence_refs':[ref]},"rules")
   implementation=_read(state["workflow_spec"])["tasks"][-1]
   self.assertNotIn("control_feedback",[i["id"] for i in implementation["inputs"]]);self.assertNotIn("diagnosis",implementation["output_schema"].get("required",[]))
 def test_pause_between_paid_tasks_and_resume_without_repetition(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);cfg=prepare_control(root/"spec")
   class Pausing(ScriptedControlProvider):
    def complete(self,*a,**kw):
     result=super().complete(*a,**kw)
     if self.calls==1:control_pause(root/"run")
     return result
   p=Pausing(failures=0);state=control_run(cfg,root/"run",provider=p)
   self.assertEqual(state["status"],"paused");self.assertEqual(p.calls,1)
   self.assertEqual(control_run(cfg,root/"run",provider=p)["status"],"paused")
   result=control_resume(cfg,root/"run",provider=p);self.assertEqual(result["status"],"complete");self.assertEqual(p.calls,4)
 def test_checked_fork_is_offline_and_reuses_completed_inputs(self):
  import shutil
  import arcagi_pipeline.controller as controller
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);cfg=prepare_control(root/"spec");p=ScriptedControlProvider(failures=0)
   with patch.object(controller,"execute_handoff",side_effect=KeyboardInterrupt):
    with self.assertRaises(KeyboardInterrupt):control_run(cfg,root/"old",provider=p)
   core=Path(controller.__file__).parent;shutil.copytree(core,root/"old-core",ignore=shutil.ignore_patterns('__pycache__'))
   before=(root/"spec/ledger.json").read_bytes()
   result=control_fork(cfg,root/"old",root/"new",root/"old-core")
   self.assertEqual(result["model_requests"],0);self.assertEqual((root/"spec/ledger.json").read_bytes(),before)
   self.assertEqual(control_status(root/"new")["status"],"paused")
   newp=ScriptedControlProvider(failures=0);done=control_resume(Path(result["config"]),root/"new",provider=newp)
   self.assertEqual(done["status"],"complete");self.assertEqual(newp.calls,0)
   self.assertEqual(_read(root/"old/state.json")["status"],"retired")
   self.assertEqual(control_resume(cfg,root/"old",provider=newp)["status"],"retired");self.assertEqual(newp.calls,0)
 def test_successful_control_commands_have_success_exit_codes(self):
  from arcagi_pipeline.cli import main
  import io
  with patch('sys.stdout',new_callable=io.StringIO):
   with patch('arcagi_pipeline.controller.control_pause',return_value={'status':'pause_requested'}):
    self.assertEqual(main(['control-pause','--run-dir','unused']),0)
   with patch('arcagi_pipeline.controller.control_status',return_value={'status':'running'}):
    self.assertEqual(main(['control-status','--run-dir','unused']),0)
   with patch('arcagi_pipeline.control_checkpoint.control_fork',return_value={'status':'paused'}):
    self.assertEqual(main(['control-fork','--config','unused','--source-run','old','--run-dir','new','--previous-core','core']),0)
 def test_fork_rejects_changed_input_and_unsettled_request(self):
  import shutil
  import arcagi_pipeline.controller as controller
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);cfg=prepare_control(root/"spec")
   with patch.object(controller,"execute_handoff",side_effect=KeyboardInterrupt):
    with self.assertRaises(KeyboardInterrupt):control_run(cfg,root/"old",provider=ScriptedControlProvider(failures=0))
   shutil.copytree(Path(controller.__file__).parent,root/"core",ignore=shutil.ignore_patterns('__pycache__'))
   ledger=root/"spec/ledger.json";original=ledger.read_bytes();data=_read(ledger);data['runtime_requests']['unsettled']={'status':'reserved'};ledger.write_text(json.dumps(data))
   with self.assertRaises(Fault):control_fork(cfg,root/"old",root/"new",root/"core")
   ledger.write_bytes(original)
   prompt=root/"spec/prompt.md";prompt.write_text(prompt.read_text()+" changed")
   with self.assertRaises(Fault):control_fork(cfg,root/"old",root/"new",root/"core")
   self.assertFalse((root/"new").exists())
 def test_canonical_output_satisfies_legacy_strict_review_schema(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);spec=self.gate_spec(root);data=_read(spec)
   data["tasks"][-1]["output_schema"]={"type":"object","required":["verdict","revision_target"],"additionalProperties":False,"properties":{"verdict":{"type":"string"},"revision_target":{"type":"string"}}};spec.write_text(json.dumps(data))
   p=FakeProvider([Completion('{"claims":[]}',total_tokens=7),Completion('{"verdict":"pass","revision_targets":[]}',total_tokens=7)])
   self.assertEqual(WorkflowRunner(spec,root/"run",provider=p).run()["status"],"complete")
 def test_diagnosis_malformed_types_return_protocol_errors(self):
  from arcagi_pipeline.controller import _diagnosis
  base={"stage":"check","root_cause_status":"unconfirmed","candidate_causes":["fixture"],"completed_checks":[{"check_id":"ok","evidence_ref":None,"observed_status":"return"}],"excluded_causes":[],"missing_evidence":[],"next_check":{"operation":"rerun_declared_checks"},"fact_refs":[]}
  feedback={"checks":[{"check_id":"ok","evidence_ref":None,"status":"return"}],"evidence_refs":[]}
  for field,value in (("root_cause_status",[]),("next_check",{"operation":[]}),("completed_checks",[{"check_id":[]}])):
   row=copy.deepcopy(base);row[field]=value
   with self.subTest(field=field):self.assertTrue(_diagnosis({"diagnosis":row},feedback))

if __name__=="__main__":unittest.main()
