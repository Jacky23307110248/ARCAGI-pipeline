import json,tempfile,unittest,importlib.util,sys
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch,Mock
from arcagi_pipeline.verification_program import check,compile_obligations,commitment_changes,validate,UnsupportedProgram
from arcagi_pipeline.generated_game import run_generated_production
from arcagi_pipeline.production import FrozenInputs
from arcagi_pipeline.revision_protocol import review_targets,ReviewProtocolError
from examples.neutral_machine.generated import Plugin,design
ROOT=Path(__file__).resolve().parents[1]
def read(trace,*path):return {'op':'read','trace':trace,'path':list(path)}
def lit(value):return {'op':'literal','value':value}
def expr(op,*args):return {'op':op,'args':list(args)}
def obligation(name,traces,expression):return {'id':name,'phase':'runtime','required':True,'function_ref':'0','evidence_refs':['e'],'proof_scope':'declared_comparison' if 'variant' in traces else 'trace_comparison' if len(traces)>1 else 'observed_witness','traces':traces,'expression':expression}
class Context:
 facts={'identity':'neutral-validation-env'}
 def load_source(self,sid):
  name='validation_source_'+str(id(self));path=ROOT/'examples/neutral_machine/generated_source.py';s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
 def receipt(self):return {'runtime':self.facts}
class ProgramTests(unittest.TestCase):
 def test_original_anchor_cannot_be_orphaned_or_cite_unknown_evidence(self):
  from examples.neutral_machine.validation_demo import artifacts
  b=artifacts()[2];b['function_anchors'].append({'id':'orphan','statement':'Original function','evidence_refs':['e']})
  self.assertTrue(any(r['code']=='original_function_anchor_unassigned' for r in compile_obligations(b)))
  b=artifacts()[2];b['function_anchors'][0]['evidence_refs']=['invented-evidence']
  self.assertTrue(any(r['code']=='function_anchor_evidence_not_in_slot' for r in compile_obligations(b)))
 def test_malformed_collections_are_diagnostics_not_compiler_crashes(self):
  for key in ('probe_plan','function_anchor_ids','behavior_requirements','validation_obligations'):
   b=design(1)[2];b['slots'][0][key]=None
   self.assertTrue(compile_obligations(b,strict=True))
 def test_configuration_contrast_cannot_be_two_ordinary_traces(self):
  b=design(1)[2];b['slots'][0]['probe_plan']=[{'id':'a','kind':'behavior'},{'id':'b','kind':'behavior'}]
  o=obligation('fake',['a','b'],expr('ne',read('a','goal_accepted'),read('b','goal_accepted')));o['proof_scope']='declared_comparison';b['slots'][0]['validation_obligations']=[o]
  self.assertTrue(any(r['code']=='configuration_contrast_pair_required' for r in compile_obligations(b)))
 def test_variant_is_not_original_configuration_witness(self):
  b=design(1)[2];b['slots'][0]['probe_plan']=[{'id':'base','kind':'behavior'},{'id':'v','kind':'counterfactual','baseline':'base'}]
  o=obligation('fake',['v'],expr('eq',read('v','goal_accepted'),lit(True)));b['slots'][0]['validation_obligations']=[o]
  self.assertTrue(compile_obligations(b))
 def test_blocking_runtime_call_is_killed_and_next_probe_runs(self):
  import time
  probes=[{'id':'blocked','kind':'behavior','max_elapsed_seconds':.4,'steps':[{'op':'step','action':{'wait_seconds':5}}]},
          {'id':'healthy','kind':'behavior','steps':[{'op':'step','action':'forward'}]}]
  o=obligation('bounded',['blocked'],expr('eq',read('blocked','goal_accepted'),lit(True)))
  start=time.monotonic();r=self.execute([o],probes)
  self.assertLess(time.monotonic()-start,4.8);self.assertEqual(r['status'],'unknown')
  self.assertEqual(r['tasks'][0]['probes']['blocked']['reason'],'probe_elapsed_limit')
  self.assertEqual(r['tasks'][0]['probes']['healthy']['status'],'complete');self.assertTrue(r['tasks'][0]['verified_solution'])
 def test_wrong_operator_literal_types_fail_compile(self):
  with self.assertRaises(UnsupportedProgram):validate(expr('count',lit(3)),set())
  with self.assertRaises(UnsupportedProgram):validate(expr('and',lit(3)),set())
 def test_projected_evidence_is_scoped_but_explicit_unit_view_is_available(self):
  trace={'target_position':0,'trajectory':[{'engine_position':1,'observation':{'projected':{'value':9}}}]}
  e=expr('eq',read('unit','trajectory',0,'observation','projected','value'),lit(9))
  self.assertEqual(check(e,{'unit':trace})['status'],'unknown')
  self.assertEqual(check(e,{'unit':trace},scope='unit')['status'],'pass')
 def test_strict_function_anchors_need_required_check_coverage(self):
  from examples.neutral_machine.validation_demo import artifacts
  b=artifacts()[2];self.assertFalse(compile_obligations(b,strict=True))
  b['slots'][0]['validation_obligations']=[]
  self.assertTrue(any(r['code']=='required_function_has_no_check' for r in compile_obligations(b,strict=True)))
 def test_missing_anchors_are_not_a_valid_new_design_contract(self):
  self.assertTrue(compile_obligations(design(1)[2],strict=True))
 def test_universal_necessity_is_not_promoted_from_one_comparison(self):
  b=design(1)[2];o=obligation('necessity',['candidate'],expr('eq',read('candidate','status'),lit('complete')));o['proof_scope']='finite_model_exhaustion';b['slots'][0]['validation_obligations']=[o]
  self.assertTrue(compile_obligations(b))
 def test_probe_timeout_or_fault_does_not_destroy_candidate_or_smoke(self):
  probe={'id':'bad','kind':'behavior','steps':[{'op':'step','action':{}}]}
  o=obligation('bad',['bad'],expr('eq',read('bad','goal_accepted'),lit(True)))
  r=self.execute([o],[probe]);self.assertEqual(r['status'],'unknown');self.assertTrue(r['tasks'][0]['verified_solution']);self.assertEqual(r['units'][0]['adapter_smoke']['status'],'pass')
  self.assertEqual(r['tasks'][0]['probes']['bad']['status'],'return')
 def test_probe_elapsed_budget_is_unknown(self):
  probe={'id':'timeout','kind':'behavior','max_elapsed_seconds':0.000000001,'steps':[{'op':'step','action':'forward'}]}
  o=obligation('timeout',['timeout'],expr('eq',read('timeout','goal_accepted'),lit(True)))
  r=self.execute([o],[probe]);self.assertEqual(r['status'],'unknown');self.assertEqual(r['tasks'][0]['probes']['timeout']['reason'],'probe_elapsed_limit')
 def test_phase_review_cannot_disguise_a_design_blocker_as_pass(self):
  tasks=[{'id':'b'},{'id':'implementation'}];task={'depends_on':['b'],'review_contract':'phase-review/v1'}
  with self.assertRaises(ReviewProtocolError):review_targets({'verdict':'pass','revision_targets':[],'findings':[{'category':'design_contradiction','target':'b'}]},task,tasks)
 def test_fake_binding_obligation_needs_reference(self):
  tasks=[{'id':'b'},{'id':'implementation'}];task={'depends_on':['b'],'review_contract':'phase-review/v1'}
  with self.assertRaises(ReviewProtocolError):review_targets({'verdict':'pass','revision_targets':[],'findings':[{'category':'runtime_obligation','target':'implementation'}]},task,tasks)
 def execute(self,obligations,probes=(),mode='normal'):
  class ProgramPlugin(Plugin):
   def generate_units(self,inputs,seed):
    for unit in super().generate_units(inputs,seed):
     levels=[]
     for level in unit.levels:
      construction={**level.construction,'claims':level.construction['claims']+[{'id':o['id'],'statement':'neutral executable obligation','check':'machine trace program'} for o in obligations if o['required']]}
      levels.append(replace(level,construction=construction,probes=tuple(probes)))
     yield replace(unit,levels=tuple(levels))
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);e,r,b=design(1);b['slots'][0]['validation_obligations']=obligations;b['slots'][0]['probe_plan']=[{k:p[k] for k in ('id','kind','baseline') if k in p} for p in probes]
   for name,value in [('e',e),('r',r),('b',b)]: (root/name).write_text(json.dumps(value),encoding='utf-8')
   return run_generated_production(ProgramPlugin(1,mode),FrozenInputs(root/'r',root/'b',root/'e'),seed=2,output_dir=root/'out',runtime_context=Context(),cap=50)
 def test_failure_reset_probe_is_independent_of_success_candidate(self):
  probe={'id':'failure','kind':'reset_failure','steps':[{'op':'step','action':{'delta':-20}},{'op':'reset'}]}
  o=obligation('failure-observed',['failure'],expr('and',expr('eq',read('failure','trajectory',1,'observation','failed'),lit(True)),expr('eq',read('failure','trajectory',2,'observation','failed'),lit(False))))
  result=self.execute([o],[probe]);self.assertEqual(result['status'],'pass',result['feedback'])
  row=result['tasks'][0];self.assertTrue(row['verified_solution']);self.assertEqual(row['obligations'][o['id']]['status'],'pass')
  self.assertNotEqual(row['replay']['session_id'],row['probes']['failure']['session_id'])
 def test_variant_comparison_uses_actual_changed_configuration(self):
  probes=[{'id':'base','kind':'behavior','steps':[{'op':'step','action':'forward'}]},
          {'id':'variant','kind':'counterfactual','baseline':'base','variant':{'configuration':{'start':10,'target':11}},'steps':[{'op':'step','action':'forward'}]}]
  o=obligation('different',['base','variant'],expr('ne',read('base','trajectory',1,'observation','value'),read('variant','trajectory',1,'observation','value')))
  result=self.execute([o],probes);self.assertEqual(result['status'],'pass',result['feedback'])
  self.assertNotEqual(result['tasks'][0]['probes']['base']['configuration_sha256'],result['tasks'][0]['probes']['variant']['configuration_sha256'])
 def test_future_unit_and_smoke_evidence_do_not_deadlock_unit(self):
  o=obligation('future',['unit','smoke'],expr('and',expr('eq',read('unit','status'),lit('complete')),expr('eq',read('smoke','status'),lit('complete'))))
  result=self.execute([o]);self.assertEqual(result['status'],'pass',result['feedback'])
 def test_missing_or_failed_probe_does_not_pass_and_candidate_survives(self):
  probe={'id':'limited','kind':'behavior','max_steps':1,'steps':[{'op':'step','action':'forward'},{'op':'step','action':'forward'}]}
  o=obligation('limited-check',['limited'],expr('eq',read('limited','trajectory',1,'observation','value'),lit(3)))
  result=self.execute([o],[probe]);self.assertEqual(result['status'],'unknown');self.assertTrue(result['tasks'][0]['verified_solution'])
 def test_unit_failure_cannot_be_replaced_by_probe_success(self):
  o=obligation('unit',['unit'],expr('eq',read('unit','status'),lit('complete')))
  result=self.execute([o],mode='unit_fail');self.assertEqual(result['status'],'return')
 def test_unsupported_operation_and_fake_constant_proof_rejected_before_run(self):
  b=design(1)[2];o=obligation('bad',['candidate'],{'op':'execute_python'})
  b['slots'][0]['validation_obligations']=[o];self.assertTrue(compile_obligations(b))
  o['expression']=lit(True);self.assertTrue(compile_obligations(b))
 def test_closed_expression_combinations_and_evidence_absence(self):
  traces={'a':{'values':[1,2],'index':1},'b':{'index':3}}
  program=expr('and',expr('eq',expr('count',read('a','values')),lit(2)),expr('subset',lit([1]),read('a','values')),{'op':'sequence','items':[read('a','index'),read('b','index')]})
  self.assertEqual(check(program,traces)['status'],'pass')
  self.assertEqual(check(expr('eq',read('a','missing'),lit(1)),traces)['status'],'unknown')
 def test_changed_commitment_requires_explanation_and_version(self):
  old=design(1)[2];new=json.loads(json.dumps(old));new['slots'][0]['game_function']='weaker'
  self.assertTrue(any(c.get('unacknowledged') for c in commitment_changes(old,new)))
  new['commitment_changes']=[{'slot_id':'0','reason':'source-backed revised inference'}]
  self.assertFalse(any(c.get('unacknowledged') for c in commitment_changes(old,new)))
 def test_phase_obligation_does_not_block_implementation(self):
  tasks=[{'id':'blueprint'},{'id':'implementation'}];task={'depends_on':['blueprint'],'review_contract':'phase-review/v1'}
  review={'verdict':'pass','revision_targets':[],'findings':[{'id':'pending','category':'runtime_obligation','target':'implementation','summary':'requires real execution','obligation_ref':{'slot_id':'s','id':'o'}}]}
  self.assertEqual(review_targets(review,task,tasks),[])
  review['findings'][0].update(category='design_contradiction',target='blueprint')
  with self.assertRaises(ReviewProtocolError):review_targets(review,task,tasks)
  review.update(verdict='return',revision_targets=['blueprint']);self.assertEqual(review_targets(review,task,tasks),['blueprint'])
