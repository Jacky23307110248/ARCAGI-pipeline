import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from arcagi_pipeline.controller import _advance_revision, _facts, control_resume
from arcagi_pipeline.diagnostics import Fault
from arcagi_pipeline.handoff_control import compact_feedback_fragment, operation_receipt
from arcagi_pipeline.machine import execute_handoff
from examples.neutral_machine.controlled import prepare_control
from examples.neutral_machine.generated import Plugin, design
import test_handoff_chain


class ExecutionPriorityTests(unittest.TestCase):
    def test_real_search_order_and_reused_prerequisites(self):
        requests = [{'task_id':'implementation','operation':op} for op in
                    ('adapter_smoke','search','engine_replay','validate_unit','adapter_smoke')]
        output = test_handoff_chain.HandoffChainTests().execute_plugin(Plugin(3),design(3)[2],requests)
        self.assertEqual(output['status'],'pass',output['feedback'])
        order = output['execution_order']
        self.assertEqual(order[0]['stage'],'adapter_smoke')
        self.assertLess(max(row['sequence'] for row in order if row['stage']=='model_search'),
                        min(row['sequence'] for row in order if row['stage']=='engine_replay'))
        self.assertEqual(len({row['check_id'] for row in order}),len(order))
        for op in ('adapter_smoke','search','engine_replay','validate_unit'):
            receipt=operation_receipt(op,output,'generated-game/v2')
            self.assertEqual(receipt['status'],'executed')
            self.assertTrue(all(len(row['evidence_sha256'])==64 for row in receipt['results']))
        self.assertEqual(operation_receipt('adapter_smoke',output,'design-production/v1')['status'],'unsupported')

    def test_failed_search_blocks_replay_but_runs_smoke(self):
        requests=[{'task_id':'implementation','operation':'engine_replay'}]
        output=test_handoff_chain.HandoffChainTests().execute_plugin(Plugin(2,'unknown'),design(2)[2],requests)
        receipt=operation_receipt('engine_replay',output,'generated-game/v2')
        self.assertEqual(receipt['status'],'prerequisite_missing')
        self.assertEqual(len(receipt['blocked_checks']),2)
        self.assertEqual(output['units'][0]['adapter_smoke']['status'],'pass')

    def test_partial_and_unconfirmed_files_cannot_supply_feedback(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            path=root/'production/production.json'
            path.parent.mkdir()
            for raw in ('{"truncated":','{"schema_version":"arcagi-production/v1","status":"return","tasks":[],"feedback":[]}'):
                path.write_text(raw,encoding='utf-8')
                feedback=_facts({'status':'return','feedback':[{'code':'worker_timeout'}]},root)
                self.assertEqual(feedback['observations'],[])
                self.assertEqual(feedback['facts'][0]['code'],'worker_timeout')
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            path.write_text('{}')
            with self.assertRaises(Fault) as error:
                _facts({'status':'return','production_sha256':digest},root)
            self.assertEqual(error.exception.diagnostic_code,'artifact_changed')

    def test_invalid_v2_design_stops_before_materialization(self):
        evidence,rules,blueprint=design(1)
        blueprint['slots'][0].pop('game_function')
        outputs={role:{'value':value,'sha256':'test','task_id':role}
                 for role,value in [('evidence',evidence),('rules',rules),('blueprint',blueprint),('implementation',{})]}
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            runtime=root/'runtime.json';runtime.write_text('{}')
            with patch('arcagi_pipeline.machine.collect',return_value=outputs), \
                 patch('arcagi_pipeline.runtime_control.preflight_runtime'), \
                 patch('arcagi_pipeline.runtime_control.runtime_command',return_value=('',{},{})), \
                 patch('arcagi_pipeline.machine.materialize') as materialize:
                report=execute_handoff(root,{},root/'out',runtime_config=runtime)
            self.assertEqual(report['error'],'design_invalid')
            self.assertEqual(report['revision_roles'],['blueprint'])
            materialize.assert_not_called()

    def test_resume_preflight_does_not_publish_running(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            cfg=prepare_control(root/'spec')
            run=root/'run';run.mkdir()
            spec=root/'spec/workflow.json'
            state={'status':'paused','baseline':{},'workflow_spec':str(spec),
                   'spec_sha256':hashlib.sha256(spec.read_bytes()).hexdigest()}
            (run/'state.json').write_text(json.dumps(state))
            marker=run/'pause-request.json';marker.write_text('{}')
            with patch('arcagi_pipeline.controller._runtime_identity',side_effect=OSError('runtime unavailable')), \
                 patch('arcagi_pipeline.controller._confirm_infrastructure',return_value=(None,[])):
                result=control_resume(cfg,run,provider=object())
            self.assertEqual(result['status'],'paused')
            self.assertEqual(json.loads((run/'state.json').read_text())['status'],'paused')
            self.assertTrue(marker.exists())

    def test_gate_hash_is_verified_before_baseline_promotion(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            spec=root/'spec.json'
            spec.write_text(json.dumps({'tasks':[{'id':'blueprint','prompt':'prompt.md'}],'budget':{'aggregate_ledger':'ledger.json'}}))
            artifact=root/'out.json';artifact.write_text('{}')
            before=hashlib.sha256(artifact.read_bytes()).hexdigest()
            artifact.write_text('{"changed":true}')
            state={'current_workflow':str(root),'initial_spec':str(spec),'workflow_spec':str(spec),'baseline':{}}
            previous={'tasks':[{'id':'blueprint','gate_status':'pass','gate_artifact_sha256':before,
                                'output_sha256':before,'output_path':'out.json'}]}
            blocked=_advance_revision(root,state,{'roles':{'implementation':'blueprint'}},previous,{},'blueprint')
            self.assertEqual(blocked['status'],'waiting_supervisor')
            self.assertEqual(blocked['block_code'],'artifact_changed')
            self.assertNotIn('commitment_baselines',state)

    def test_compaction_preserves_failed_obligation(self):
        failed={'status':'return','actual':3,'expected':4,'missing_path':['grid','0']}
        payload={'observations':[{'task_id':'a','search':{'actions':['move']*2000},
                    'obligations':{'bad':failed,'good':{'status':'pass','detail':'x'*2000}}}],'facts':[]}
        compact=compact_feedback_fragment(payload)
        self.assertEqual(compact['observations'][0]['obligations']['bad'],failed)
        self.assertLess(len(json.dumps(compact)),400)


    def test_rendered_budget_compacts_once_and_rejects_minimum_before_provider(self):
        import test_agent_workflow
        from arcagi_pipeline.agent_workflow import Completion, WorkflowError, WorkflowRunner
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            spec,*_=test_agent_workflow.AgentWorkflowTests().make_bundle(root,budget_tokens=30000)
            data=json.loads(spec.read_text())
            task=data['tasks'][1]
            failed={'status':'return','actual':3,'expected':4,'missing_path':['grid']}
            fragment=root/'fragment.json'
            fragment.write_text(json.dumps({'observations':[{'task_id':'a','search':{'actions':['move']*4000},
                'obligations':{'bad':failed}}],'facts':[]}))
            task['depends_on']=[]
            task['inputs']=[{'id':'control_evidence','role':'execution_evidence_fragments',
                'path':'fragment.json','sha256':hashlib.sha256(fragment.read_bytes()).hexdigest()}]
            data['tasks']=[task]
            data['budget']['max_input_tokens_per_request']=6000
            spec.write_text(json.dumps(data))
            provider=test_agent_workflow.FakeProvider([Completion('{"status":"draft"}',total_tokens=7)])
            WorkflowRunner(spec,root/'good',provider=provider).run()
            messages=provider.calls[0]['messages']
            request=json.loads(messages[-1]['content'])
            payload=json.loads(request['input_artifacts'][0]['content'])
            self.assertEqual(payload['observations'][0]['obligations']['bad'],failed)
            self.assertEqual(payload['observations'][0]['search']['action_count'],4000)
            self.assertLessEqual(sum(len(m['content'].encode()) for m in messages)+256,6000)
            data['budget']['max_input_tokens_per_request']=400
            spec.write_text(json.dumps(data))
            provider=test_agent_workflow.FakeProvider([])
            with self.assertRaises(WorkflowError) as error:
                WorkflowRunner(spec,root/'limited',provider=provider).run()
            self.assertEqual(error.exception.diagnostic_code,'budget_limit')
            self.assertEqual(provider.calls,[])
