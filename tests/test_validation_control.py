import json,tempfile,hashlib,unittest
from pathlib import Path
from unittest.mock import patch,Mock
from examples.neutral_machine import validation_demo
from examples.neutral_machine.controlled import prepare_control,ScriptedControlProvider
from arcagi_pipeline.agent_workflow import WorkflowRunner,WorkflowError
from arcagi_pipeline.controller import control_run,_confirm_infrastructure
from arcagi_pipeline.verification_program import verify_trace_files

class ValidationControlTests(unittest.TestCase):
 def test_repeated_unsupported_capability_stops_without_implementation_or_supervisor(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);cfg=prepare_control(root/'spec',max_revisions=10);c=json.loads(cfg.read_text());c['capability_revision_limit']=2;cfg.write_text(json.dumps(c))
   sp=root/'spec/workflow.json';s=json.loads(sp.read_text());task=next(t for t in s['tasks'] if t['id']=='blueprint');task['validation_program']='arcagi-validation-program/v1';sp.write_text(json.dumps(s))
   provider=ScriptedControlProvider(failures=0);state=control_run(cfg,root/'run',provider=provider)
   self.assertEqual(state['status'],'limited');self.assertEqual(state['reason'],'capability_revision_limit');self.assertEqual(provider.implementations,0)
 def test_unsupported_blueprint_stops_before_implementation_provider_call(self):
  original=validation_demo.artifacts
  def bad():
   a=original();a[2]['slots'][0]['validation_obligations'][0]['expression']={'op':'shell'};return a
  with tempfile.TemporaryDirectory() as td,patch.object(validation_demo,'artifacts',bad):
   root=Path(td)/'demo'
   with self.assertRaises(WorkflowError) as caught:validation_demo.run(root)
   self.assertEqual(caught.exception.revision_targets,['blueprint'])
   record=json.loads((root/'workflow/workflow_run.json').read_text(encoding='utf-8'))
   self.assertEqual(next(t for t in record['tasks'] if t['id']=='implementation')['status'],'pending')
 def test_phase_gate_carries_real_obligations_but_rejects_missing_reference(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);(root/'b.json').write_text(json.dumps(validation_demo.artifacts()[2]))
   review={'verdict':'pass','revision_targets':[],'findings':[{'id':'f','category':'runtime_obligation','target':'implementation','summary':'execute after binding','obligation_ref':{'slot_id':'0','id':'failure-reset'}}]}
   r=object.__new__(WorkflowRunner);r.run_dir=root;r.data={'tasks':[{'id':'b','output_path':'b.json'}]};r._save=Mock()
   r.spec={'tasks':[{'id':'b'},{'id':'implementation'}]};task={'id':'review','depends_on':['b'],'review_contract':'phase-review/v1','obligation_source_task':'b'}
   (root/'review.json').write_text(json.dumps(review));r._review_gate(task,{'output_path':'review.json'})
   self.assertEqual(r.data['deferred_obligations']['review'],[{'slot_id':'0','id':'failure-reset'}])
   review['findings'][0]['obligation_ref']['id']='invented';(root/'review.json').write_text(json.dumps(review))
   with self.assertRaises(WorkflowError):r._review_gate(task,{'output_path':'review.json'})
 def test_exact_program_and_worker_outputs_pass_then_journal_drift_blocks(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td)/'demo';report=validation_demo.run(root);self.assertEqual(report['status'],'pass')
   out=root/'execution';result=out/'production/production.json';verify_trace_files(result)
   row=json.loads(result.read_text(encoding='utf-8'))['tasks'][0];journal=Path(row['probes']['failure']['journal_path']);journal.write_text(journal.read_text(encoding='utf-8')+'tamper',encoding='utf-8')
   state={'baseline':{},'execution_intent':str(out),'execution_hash':hashlib.sha256((out/'machine.json').read_bytes()).hexdigest(),'current_workflow':str(root/'workflow')}
   code,checks=_confirm_infrastructure(state,root/'spec/runtime.json')
   self.assertEqual(code,'artifact_changed');self.assertEqual(checks[0]['check'],'execution_evidence_integrity')
 def test_generated_module_drift_is_independently_confirmed(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td)/'demo';report=validation_demo.run(root);self.assertEqual(report['status'],'pass')
   out=root/'execution';ref=report['files'][0];module=out/'plugin'/ref['path'];module.write_bytes(module.read_bytes()+b'\n# drift\n')
   state={'baseline':{},'execution_intent':str(out),'execution_hash':hashlib.sha256((out/'machine.json').read_bytes()).hexdigest(),'current_workflow':str(root/'workflow')}
   code,checks=_confirm_infrastructure(state,root/'spec/runtime.json')
   self.assertEqual(code,'artifact_changed');self.assertEqual(checks[0]['check'],'execution_evidence_integrity')
 def test_bounded_search_cap_retry_uses_no_new_model_requests(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);cfg=prepare_control(root/'spec');c=json.loads(cfg.read_text());c.update(unknown_policy='stop',search_cap_schedule=[50,100]);cfg.write_text(json.dumps(c))
   calls=[]
   def execute(workflow,roles,out,**kw):
    from arcagi_pipeline.machine import collect
    out.mkdir();(out/'production').mkdir();calls.append(kw['cap'])
    p={'status':'unknown','schema_version':'arcagi-production/v1','tasks':[{'task_id':'x','status':'unknown','search':{'status':'unknown','evidence':{'limit_kind':'state_cap'}}}],'feedback':[]}
    path=out/'production/production.json';path.write_text(json.dumps(p))
    report={'status':'unknown','stage':'execution','feedback':[],'production_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'workflow_outputs':{role:{k:v for k,v in value.items() if k!='value'} for role,value in collect(workflow,roles).items()}}
    (out/'machine.json').write_text(json.dumps(report));return report
   provider=ScriptedControlProvider(failures=0)
   with patch('arcagi_pipeline.controller.execute_handoff',side_effect=execute):state=control_run(cfg,root/'run',provider=provider)
   self.assertEqual(state['status'],'limited');self.assertEqual(calls,[50,100]);self.assertEqual(provider.calls,4)
 def test_invalid_cap_schedule_rejected_without_provider(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);cfg=prepare_control(root/'spec');c=json.loads(cfg.read_text());c['search_cap_schedule']=[100,50];cfg.write_text(json.dumps(c))
   provider=ScriptedControlProvider(failures=0)
   with self.assertRaises(Exception):control_run(cfg,root/'run',provider=provider)
   self.assertEqual(provider.calls,0)
