import tempfile,unittest,json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from arcagi_pipeline.agent_workflow import WorkflowRunner
from arcagi_pipeline.generated_game import NativeDriver,behavior_check
from arcagi_pipeline.diagnostics import Fault,CODES

class DesignBoundaryTests(unittest.TestCase):
 def test_native_projection_uses_actual_position_after_auto_transition(self):
  e=SimpleNamespace(level_index=0,_score=0,_state=SimpleNamespace(name='ACTIVE'),camera=SimpleNamespace(render=lambda sprites:[]),current_level=SimpleNamespace(get_sprites=lambda:[]),left=4,right=9)
  d=object.__new__(NativeDriver);d.engine=e;d.observation_bindings={'old':['absent']}
  d.observation_bindings_by_position={0:{'left':['left']},1:{'right':['right']}}
  self.assertEqual(d.observe()['projected'],{'left':4});e.level_index=1
  self.assertEqual(d.observe()['projected'],{'right':9});e.level_index=2
  self.assertNotIn('projected',d.observe())
 def test_next_level_projection_cannot_prove_previous_level_change(self):
  trace=[{'engine_position':0,'observation':{'projected':{'value':3}}},{'engine_position':1,'observation':{'projected':{'value':9}}}]
  r=behavior_check({'kind':'behavior','observation_path':['projected','value'],'predicate':'changes'},trace,0)
  self.assertEqual(r['status'],'unknown');self.assertEqual(r['trace_indices'],[0])
 def test_observed_witness_is_valid_even_when_transition_projection_missing(self):
  trace=[{'engine_position':0,'observation':{'projected':{'value':3}}},{'engine_position':0,'observation':{'projected':{'value':4}}},{'engine_position':1,'observation':{}}]
  r=behavior_check({'kind':'behavior','observation_path':['projected','value'],'predicate':'changes'},trace,0)
  self.assertEqual(r['status'],'pass');self.assertEqual(r['trace_indices'],[0,1])
 def test_completed_self_revision_proceeds_to_actual_review(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'artifact.json';p.write_text('{}')
   r=object.__new__(WorkflowRunner);r.run_dir=Path(td);r.data={};r._save=Mock();r.diagnosis_receipts={};r.diagnosis_observer=None
   task={'id':'design','diagnosis_contract':{'allow_stage_revision':True}}
   r.spec={'tasks':[task]}
   with patch('arcagi_pipeline.diagnosis_protocol.effective_bundle',return_value={'diagnosis':{'revise_task_id':'design'}}),patch('arcagi_pipeline.diagnosis_protocol.feedback_for',return_value={}),patch('arcagi_pipeline.diagnosis_protocol.validate',return_value=[]):
    r._diagnosis_gate(task,{'output_path':p.name});r._diagnosis_gate(task,{'output_path':p.name})
   self.assertEqual(r._save.call_count,1)
   self.assertEqual(r.data['applied_stage_revision_requests'][0]['reason'],'replacement_artifact_already_produced')
 def test_stage_revision_has_distinct_diagnostic_classification(self):
  self.assertIn('model_revision_requested',CODES)
  self.assertEqual(CODES['model_revision_requested'][0],'artifact')
 def test_projection_reads_actual_state_and_behavior_changes(self):
  engine=SimpleNamespace(level_index=0,_score=0,_state=SimpleNamespace(name='ACTIVE'),camera=SimpleNamespace(render=lambda sprites:[]),current_level=SimpleNamespace(get_sprites=lambda:[]),custom={'values':[3]})
  d=object.__new__(NativeDriver);d.engine=engine;d.observation_bindings={'meter':['custom','values',0]}
  a=d.observe();engine.custom['values'][0]=8;b=d.observe()
  self.assertEqual(a['projected']['meter'],3);self.assertEqual(b['projected']['meter'],8)
  result=behavior_check({'kind':'behavior','observation_path':['projected','meter'],'predicate':'changes'},[{'observation':a},{'observation':b}])
  self.assertEqual(result['status'],'pass')
 def test_projection_rejects_method_calls(self):
  engine=SimpleNamespace(level_index=0,_score=0,_state=SimpleNamespace(name='ACTIVE'),camera=SimpleNamespace(render=lambda sprites:[]),current_level=SimpleNamespace(get_sprites=lambda:[]),custom=lambda:True)
  d=object.__new__(NativeDriver);d.engine=engine;d.observation_bindings={'fake':['custom']}
  with self.assertRaises(Fault):d.observe()
 def test_missing_runtime_observation_does_not_prove_behavior(self):
  result=behavior_check({'kind':'behavior','observation_path':['projected','planned'],'predicate':'changes'},[{'observation':{}}])
  self.assertEqual(result['status'],'unknown')
 def test_missing_binding_names_its_declared_alias_without_raw_state(self):
  engine=SimpleNamespace(level_index=0,_score=0,_state=SimpleNamespace(name='ACTIVE'),camera=SimpleNamespace(render=lambda sprites:[]),current_level=SimpleNamespace(get_sprites=lambda:[]))
  d=object.__new__(NativeDriver);d.engine=engine;d.observation_bindings={'planned':['absent']}
  with self.assertRaisesRegex(Fault,'observation_binding_unavailable:planned'):d.observe()
