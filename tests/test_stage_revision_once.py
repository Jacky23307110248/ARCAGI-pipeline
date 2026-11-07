import tempfile,unittest,json
from pathlib import Path
from unittest.mock import patch,Mock
from arcagi_pipeline.agent_workflow import WorkflowRunner,WorkflowError

class StageRevisionTests(unittest.TestCase):
 def gate(self,frozen=True,target_frozen=False,issues=None):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'a.json';p.write_text('{}')
   r=object.__new__(WorkflowRunner);r.run_dir=Path(td);r.data={};r._save=Mock();r.diagnosis_receipts={};r.diagnosis_observer=None
   target={'id':'downstream'}
   if target_frozen:target['frozen_output']={'path':'old','sha256':'old'}
   r.spec={'tasks':[target]};task={'id':'upstream','diagnosis_contract':{}}
   task['diagnosis_contract']={'allow_stage_revision':True}
   if frozen:task['frozen_output']={'path':str(p)}
   with patch('arcagi_pipeline.diagnosis_protocol.effective_bundle',return_value={'diagnosis':{'revise_task_id':'downstream'}}),patch('arcagi_pipeline.diagnosis_protocol.feedback_for',return_value={}),patch('arcagi_pipeline.diagnosis_protocol.validate',return_value=issues or []):
    r._diagnosis_gate(task,{'output_path':'a.json'})
    r._diagnosis_gate(task,{'output_path':'a.json'})
   return r.data,r._save.call_count
 def test_frozen_request_allows_scheduled_target_and_is_idempotent(self):
  data,saves=self.gate();self.assertEqual(len(data['applied_stage_revision_requests']),1);self.assertEqual(saves,1)
 def test_new_request_still_dispatches(self):
  with self.assertRaises(WorkflowError):self.gate(frozen=False)
 def test_frozen_target_still_dispatches(self):
  with self.assertRaises(WorkflowError):self.gate(target_frozen=True)
 def test_frozen_diagnosis_is_still_validated(self):
  with self.assertRaises(WorkflowError):self.gate(issues=[{'code':'invalid'}])
