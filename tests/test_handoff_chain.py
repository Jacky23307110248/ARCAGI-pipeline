import hashlib
import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from examples.neutral_machine.validation_demo import artifacts
from arcagi_pipeline.verification_program import compile_obligations,commitment_changes,check
from arcagi_pipeline.generated_game import validate_target

class HandoffChainTests(unittest.TestCase):
 def execute_plugin(self,plugin,b,requests=()):
  from test_verification_program import Context
  from arcagi_pipeline.production import FrozenInputs
  from arcagi_pipeline.generated_game import run_generated_production
  e,r,_,_=artifacts()
  with tempfile.TemporaryDirectory() as td:
   root=Path(td)
   for name,v in [('e',e),('r',r),('b',b)]: (root/name).write_text(json.dumps(v))
   return run_generated_production(plugin,FrozenInputs(root/'r',root/'b',root/'e'),seed=1,output_dir=root/'out',runtime_context=Context(),cap=50,operation_requests=requests)
 def test_rich_only_runs_without_legacy_fields(self):
  from examples.neutral_machine.validation_demo import ValidationPlugin
  from dataclasses import replace
  b=artifacts()[2];s=b['slots'][0];s.pop('behavior_requirements')
  class Rich(ValidationPlugin):
   def generate_units(self,inputs,seed):
    for u in super().generate_units(inputs,seed):
     yield replace(u,levels=tuple(replace(l,construction={**l.construction,'claims':[x for x in l.construction['claims'] if x['id']!='progress']}) for l in u.levels))
  result=self.execute_plugin(Rich(1),b);self.assertEqual(result['status'],'pass',result['feedback'])
 def test_level_smoke_selects_each_target_and_one_bad_level_does_not_hide_others(self):
  from examples.neutral_machine.generated import Plugin,design
  from dataclasses import replace
  from arcagi_pipeline.generated_game import digest
  from arcagi_pipeline.production import ModelSearch
  e,r,b=design(3)
  for s in b['slots']:
   s['validation_obligations']=[{'id':'smoke-target','phase':'runtime','required':True,'function_ref':s['id'],'evidence_refs':['e'],'proof_scope':'observed_witness','traces':['level_smoke'],'expression':{'op':'eq','args':[{'op':'read','trace':'level_smoke','path':['trajectory',0,'engine_position']},{'op':'literal','value':int(s['id'])}]}}]
  class Smoke(Plugin):
   def generate_units(self,inputs,seed):
    for u in super().generate_units(inputs,seed):
     yield replace(u,levels=tuple(replace(l,construction={**l.construction,'claims':l.construction['claims']+[{'id':'smoke-target','statement':'target reset','check':'machine position'}]}) for l in u.levels))
  result=self.execute_plugin(Smoke(3),b,[{'task_id':'implementation','operation':'adapter_smoke'}]);self.assertEqual(result['status'],'pass',result['feedback'])
  self.assertEqual([x['stage'] for x in result['execution_order'][:4]],['adapter_smoke','level_smoke','level_smoke','level_smoke'])
  self.assertEqual([x['level_smoke']['target_position'] for x in result['tasks']],[0,1,2]);self.assertEqual(len({x['level_smoke']['session_id'] for x in result['tasks']}),3)
  class BadFirst(Smoke):
   def search_generated(self,level,cap):
    if level.task_id=='0':raise KeyError('missing_field')
    return super().search_generated(level,cap)
  result=self.execute_plugin(BadFirst(3),b);self.assertEqual(len(result['tasks']),3)
  self.assertTrue(result['tasks'][1]['verified_solution']);self.assertTrue(result['tasks'][0]['execution_error']['locations'])
 def test_v2_sequence_same_session_and_boolean_type_inference(self):
  from arcagi_pipeline.verification_program import VERSION
  t={'a':{'session_id':'one','trajectory':[{'event_index':0,'observation':{}},{'event_index':1,'observation':{}}]}}
  x={'op':'sequence','items':[{'op':'event_ref','trace':'a','index':0},{'op':'event_ref','trace':'a','index':1}]}
  self.assertEqual(check(x,t,version=VERSION)['status'],'pass')
  b=artifacts()[2];o=b['slots'][0]['validation_obligations'][0];o['expression']={'op':'and','args':[{'op':'count','args':[{'op':'read','trace':'failure','path':['trajectory']}]},{'op':'literal','value':True}]}
  self.assertTrue(compile_obligations(b,True))
 def test_wrong_blueprint_then_correct_replacement_reaches_engine_without_wrong_revision(self):
  import hashlib
  from examples.neutral_machine.controlled import prepare_control
  from examples.neutral_machine import validation_demo
  from arcagi_pipeline.verification_program import VERSION
  from arcagi_pipeline.controller import control_run
  from arcagi_pipeline.agent_workflow import Completion
  class Provider:
   def __init__(self):self.blueprints=0;self.calls=[]
   def complete(self,messages,*,model,max_tokens):
    request=json.loads(messages[-1]['content']);tid=request['task']['id'];self.calls.append(tid)
    value=copy.deepcopy(validation_demo.artifacts()[['evidence','rules','blueprint','implementation'].index(tid)]) if tid!='review' else {'verdict':'pass','findings':[],'revision_targets':[]}
    if tid=='blueprint':
     self.blueprints+=1
     if self.blueprints==1:value['slots'][0].pop('id')
    feedback=next((json.loads(i['content']) for i in request['input_artifacts'] if i['id']=='control_feedback'),None)
    if feedback:
     assert any(i['role']=='execution_evidence_fragments' for i in request['input_artifacts'])
     assert 'observations' not in feedback and 'facts' not in feedback
     assert 'checks' not in request['diagnosis_contract']
     c=feedback['checks'][0];value['diagnosis']={'stage':c['stage'],'root_cause_status':'unconfirmed','candidate_causes':['protocol correction'],'completed_checks':[{'check_id':c['check_id'],'evidence_ref':c['evidence_ref'],'observed_status':c['status']}],'excluded_causes':[],'missing_evidence':[],'next_check':{'operation':'engine_replay'},'fact_refs':feedback['evidence_refs']}
    return Completion(json.dumps(value),total_tokens=7)
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);cfg=prepare_control(root/'spec',count=1,max_revisions=4)
   core=Path(__file__).resolve().parents[1]/'src/arcagi_pipeline';sp=root/'spec/workflow.json';spec=json.loads(sp.read_text())
   for t in spec['tasks']:
    if t['id'] in ('blueprint','implementation'):
     t['validation_program']=VERSION;t['inputs'].append({'id':'validation_program_api','role':'validation_program_api','path':str(core/'verification_program.py'),'sha256':hashlib.sha256((core/'verification_program.py').read_bytes()).hexdigest()})
    if t['id']=='blueprint':t['validation_inputs']={'evidence':'evidence','rules':'rules'}
    if t['id']=='implementation':
     t['execution_contract']='generated-game/v2';t['depends_on'].append('review');t['inputs'].append({'id':'generated_game_api','role':'generated_game_api','path':str(core/'generated_game.py'),'sha256':hashlib.sha256((core/'generated_game.py').read_bytes()).hexdigest()})
   spec['tasks'].insert(3,{'id':'review','stage':'design_self_check','prompt':'prompt.md','depends_on':['blueprint'],'inputs':[{'id':'blueprint','role':'blueprint','from_task':'blueprint'}],'max_output_tokens':1000,'gate_review':True,'review_contract':'phase-review/v1','obligation_source_task':'blueprint','output_schema':{'type':'object'}})
   sp.write_text(json.dumps(spec));rp=root/'spec/runtime.json';runtime=json.loads(rp.read_text());source=core.parents[1]/'examples/neutral_machine/generated_source.py'
   runtime.update(import_roots=[str(core.parents[1])],sources=[{'id':'engine','path':str(source),'sha256':hashlib.sha256(source.read_bytes()).hexdigest()}]);rp.write_text(json.dumps(runtime))
   conf=json.loads(cfg.read_text());conf['production_contract']='generated-game/v2';cfg.write_text(json.dumps(conf));p=Provider();state=control_run(cfg,root/'run',provider=p)
   self.assertEqual(state['status'],'complete',state);self.assertEqual(p.blueprints,2);self.assertEqual(p.calls.count('implementation'),1)
   self.assertEqual(state['history'][0]['targets'],['blueprint'])
   report=json.loads(Path(state['result']).read_text());receipt=json.loads((Path(state['result']).parent/'diagnostic-operations.json').read_text())
   self.assertEqual(receipt['receipts'][0]['status'],'executed')
 def test_missing_target_and_function_are_design_errors(self):
  from arcagi_pipeline.agent_workflow import WorkflowRunner,WorkflowError
  from unittest.mock import Mock
  for field in ('production_target','game_function'):
   e,r,b,_=artifacts()
   if field=='production_target':b.pop(field)
   else:b['slots'][0].pop(field)
   with tempfile.TemporaryDirectory() as td:
    root=Path(td)
    for n,v in [('e',e),('r',r),('b',b)]: (root/n).write_text(json.dumps(v))
    runner=object.__new__(WorkflowRunner);runner.run_dir=root;runner.data={'tasks':[{'id':n,'output_path':n} for n in ('e','r','b')]};runner._save=Mock()
    task={'id':'b','stage':'blueprint','validation_program':'arcagi-validation-program/v2','validation_inputs':{'evidence':'e','rules':'r'}}
    with self.assertRaises(WorkflowError) as caught:runner._diagnosis_gate(task,{'output_path':'b'})
    self.assertEqual(caught.exception.revision_targets,['b'])
 def test_invalid_prior_cannot_poison_replacement(self):
  old=artifacts()[2];old['slots'][0].pop('id');new=artifacts()[2]
  self.assertFalse(commitment_changes(old,new))
 def test_count_is_not_boolean_obligation(self):
  b=artifacts()[2];b['slots'][0]['validation_obligations'][0]['expression']={'op':'count','args':[{'op':'read','trace':'failure','path':['trajectory']}]}
  self.assertTrue(any('boolean' in x['code'] for x in compile_obligations(b,True)))
 def test_v2_event_order_requires_one_session(self):
  from arcagi_pipeline.verification_program import VERSION
  self.assertEqual(VERSION,'arcagi-validation-program/v2')
  expr={'op':'sequence','items':[{'op':'event_ref','trace':'a','index':0},{'op':'event_ref','trace':'b','index':0}]}
  traces={'a':{'session_id':'a','trajectory':[{'event_index':1,'observation':{}}]},'b':{'session_id':'b','trajectory':[{'event_index':3,'observation':{}}]}}
  self.assertNotEqual(check(expr,traces,version=VERSION)['status'],'pass')
 def test_feedback_has_real_unit_and_smoke_statuses(self):
  from arcagi_pipeline.controller import _facts
  with tempfile.TemporaryDirectory() as td:
   out=Path(td);(out/'production').mkdir();(out/'machine.json').write_text('{}')
   p={'schema_version':'arcagi-production/v2','status':'return','feedback':[],'tasks':[],'units':[{'unit_id':'u','status':'return','unit_execution':{'status':'pass'},'adapter_smoke':{'status':'return','reason':'bad-step'}}]}
   (out/'production/production.json').write_text(json.dumps(p))
   f=_facts({'status':'return','feedback':[],'production_sha256':hashlib.sha256((out/'production/production.json').read_bytes()).hexdigest()},out);by={c['check_id']:c for c in f['checks']}
   self.assertEqual(by['u:unit_execution']['status'],'pass');self.assertEqual(by['u:adapter_smoke']['status'],'return')
 def test_large_feedback_retains_each_failure(self):
  from arcagi_pipeline.controller import _facts
  with tempfile.TemporaryDirectory() as td:
   out=Path(td);(out/'production').mkdir();(out/'machine.json').write_text('{}')
   p={'schema_version':'arcagi-production/v2','status':'return','feedback':[],'tasks':[{'task_id':str(i),'status':'return','obligations':{'o':{'status':'return','reason':'mismatch','evaluated_reads':[{'path':['x'],'value':'x'*2048} for _ in range(16)]}}} for i in range(8)],'units':[]}
   (out/'production/production.json').write_text(json.dumps(p))
   f=_facts({'status':'return','feedback':[],'production_sha256':hashlib.sha256((out/'production/production.json').read_bytes()).hexdigest()},out)
   self.assertTrue(all(x.get('obligations',{}).get('o',{}).get('reason')=='mismatch' for x in f['observations']))
 def test_operation_receipt_requires_actual_execution(self):
  from arcagi_pipeline.handoff_control import operation_receipt
  r=operation_receipt('engine_replay',{'tasks':[{'task_id':'a','search':{'status':'unknown'}}],'units':[]})
  self.assertEqual(r['status'],'prerequisite_missing');self.assertEqual(r['check_ids'],[])
  self.assertEqual(operation_receipt('execute_shell',{})['status'],'unsupported')
 def test_receipt_ids_are_real_feedback_checks_and_limits_are_explicit(self):
  from arcagi_pipeline.handoff_control import operation_receipt
  from arcagi_pipeline.controller import _facts
  p={'schema_version':'arcagi-production/v2','status':'unknown','feedback':[],'tasks':[{'task_id':'a','status':'unknown','checks':{'model_search':'unknown'},'search':{'status':'unknown','evidence':{'limit_kind':'state_cap'}}}],'units':[]}
  receipt=operation_receipt('search',p);self.assertEqual(receipt['outcome'],'resource_limited')
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);(root/'production').mkdir();(root/'machine.json').write_text('{}');(root/'production/production.json').write_text(json.dumps(p))
   f=_facts({'status':'unknown','feedback':[],'production_sha256':hashlib.sha256((root/'production/production.json').read_bytes()).hexdigest()},root)
   self.assertTrue(set(receipt['check_ids'])<=set(c['check_id'] for c in f['checks']))
   self.assertEqual([c['check_id'] for c in f['checks']].count('a:model_search'),1)
 def test_blocked_check_cannot_be_claimed_completed(self):
  from arcagi_pipeline.diagnosis_protocol import validate
  ref={'path':'unused','sha256':'0'*64};feedback={'checks':[{'check_id':'unit','status':'blocked','executed':False,'evidence_ref':ref}],'evidence_refs':[],'revision_targets':[]}
  bundle={'diagnosis':{'stage':'unit','root_cause_status':'unconfirmed','candidate_causes':['missing candidate'],'completed_checks':[{'check_id':'unit','observed_status':'blocked','evidence_ref':ref}],'excluded_causes':[],'missing_evidence':[],'next_check':{'operation':'search'},'fact_refs':[]}}
  self.assertTrue(any(i['code']=='diagnosis_check_not_executed' for i in validate(bundle,feedback)))
 def test_new_producer_resets_cap_but_resume_does_not(self):
  from arcagi_pipeline.handoff_control import bind_search_resources
  s={'search_cap_index':2};bind_search_resources(s,'old');self.assertEqual(s['search_cap_index'],0)
  s['search_cap_index']=1;bind_search_resources(s,'old');self.assertEqual(s['search_cap_index'],1)
  bind_search_resources(s,'new');self.assertEqual(s['search_cap_index'],0)

if __name__=='__main__':unittest.main()
