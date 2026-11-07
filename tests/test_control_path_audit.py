import json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from arcagi_pipeline.agent_workflow import WorkflowRunner,WorkflowError
from arcagi_pipeline.controller import _checked_execution,_facts,_confirm_infrastructure,_check_execution_evidence
from arcagi_pipeline.diagnostics import Fault
import hashlib

class ControlPathTests(unittest.TestCase):
 def test_cached_corruption_is_independently_confirmed_infrastructure(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);(root/'production').mkdir();p=root/'production/production.json';p.write_text(json.dumps({'schema_version':'arcagi-production/v1','status':'pass','feedback':[],'tasks':[{'task_id':'one','status':'pass'}]}))
   outputs={role:{'task_id':role,'sha256':'original'} for role in ('evidence','rules','blueprint','implementation')}
   report={'status':'pass','production_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'workflow_outputs':outputs}
   m=root/'machine.json';m.write_text(json.dumps(report))
   state={'baseline':{},'execution_intent':str(root),'execution_hash':hashlib.sha256(m.read_bytes()).hexdigest(),'current_workflow':str(root)}
   with patch('arcagi_pipeline.controller.collect',return_value=outputs):
    _check_execution_evidence(state)
    for changed in (p,m):
     old=changed.read_bytes();changed.write_text('{"changed":true}')
     code,checks=_confirm_infrastructure(state,root/'runtime.json')
     self.assertEqual(code,'artifact_changed');self.assertEqual(checks[0]['check'],'execution_evidence_integrity')
     changed.write_bytes(old)
   drift={role:{**value,'sha256':'new'} for role,value in outputs.items()}
   with patch('arcagi_pipeline.controller.collect',return_value=drift):
    code,checks=_confirm_infrastructure(state,root/'runtime.json')
    self.assertEqual(code,'artifact_changed');self.assertEqual(checks[0]['check'],'execution_evidence_integrity')
 def test_cached_review_dispatch_is_consumed_only_for_scheduled_targets(self):
  for frozen,active,allowed in [(True,True,True),(True,False,False),(False,True,False)]:
   with self.subTest(frozen=frozen,active=active),tempfile.TemporaryDirectory() as td:
    root=Path(td);(root/'review.json').write_text('{"verdict":"return"}')
    r=object.__new__(WorkflowRunner);r.run_dir=root;r.data={};r._save=Mock()
    target={'id':'implementation'}
    if not active:target['frozen_output']={}
    # Frozen declarations are nonempty in real workflow specs.
    if not active:target['frozen_output']={'path':'old'}
    r.spec={'tasks':[target]};t={'id':'review'}
    if frozen:t['frozen_output']={'path':'old'}
    with patch('arcagi_pipeline.revision_protocol.review_targets',return_value=['implementation']):
     if allowed:
      r._review_gate(t,{'output_path':'review.json'});r._review_gate(t,{'output_path':'review.json'})
      self.assertEqual(r._save.call_count,1)
     else:
      with self.assertRaises(WorkflowError):r._review_gate(t,{'output_path':'review.json'})
 def test_execution_cache_rechecks_inputs_and_production(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);(root/'production').mkdir();p=root/'production/production.json';p.write_text(json.dumps({'schema_version':'arcagi-production/v1','status':'pass','feedback':[],'tasks':[{'task_id':'one','status':'pass'}]}))
   report={'status':'pass','production_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'workflow_outputs':{'implementation':{'sha256':'a'}}}
   (root/'machine.json').write_text(json.dumps(report))
   with patch('arcagi_pipeline.controller.collect',return_value={'implementation':{'sha256':'a'}}):
    self.assertEqual(_checked_execution(root,root,{}),report)
    p.write_text('{"status":"return"}')
    with self.assertRaises(Fault):_checked_execution(root,root,{})
   with patch('arcagi_pipeline.controller.collect',return_value={'implementation':{'sha256':'b'}}):
    with self.assertRaises(Fault):_checked_execution(root,root,{})
 def test_passing_cache_requires_production_evidence(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);(root/'machine.json').write_text('{"status":"pass","workflow_outputs":{}}')
   with patch('arcagi_pipeline.controller.collect',return_value={}):
    with self.assertRaises(Fault):_checked_execution(root,root,{})
 def test_v2_search_engine_and_unit_failures_remain_visible(self):
  for stage,status in [('model_search','unknown'),('engine_replay','return'),('fulfillment','return')]:
   with self.subTest(stage=stage),tempfile.TemporaryDirectory() as td:
    root=Path(td);(root/'production').mkdir()
    p={'schema_version':'arcagi-production/v2','status':status,'feedback':[],'tasks':[{'task_id':'task','status':status,'checks':{stage:status}}],
       'units':[{'unit_id':'unit','status':status,'unit_execution':{'status':status}}]}
    (root/'production/production.json').write_text(json.dumps(p));(root/'machine.json').write_text('{}')
    f=_facts({'status':status,'feedback':[],'production_sha256':hashlib.sha256((root/'production/production.json').read_bytes()).hexdigest()},root)
    self.assertTrue(any(c['stage']==stage and c['status']==status for c in f['checks']))
    self.assertTrue(any(c['stage']=='unit_execution' and c['status']==status for c in f['checks']))
    self.assertEqual(f['next_route'],'ds_diagnosis')
