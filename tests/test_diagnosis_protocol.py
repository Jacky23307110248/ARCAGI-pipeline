import copy,json,tempfile,unittest,shutil
from pathlib import Path
from unittest.mock import patch
from dataclasses import replace
from arcagi_pipeline.diagnosis_protocol import VERSION,OPERATIONS,schema,validate,sha,read
from arcagi_pipeline.controller import control_run,control_resume,control_status,_save
from arcagi_pipeline.resume_checkpoint import recover
from arcagi_pipeline.diagnostics import Fault
from examples.neutral_machine.controlled import prepare_control,ScriptedControlProvider

def feedback_at(root):
    evidence=root/'evidence.json';evidence.write_text('{"check":"neutral machine check","status":"return"}')
    ref={'path':str(evidence.resolve()),'sha256':sha(evidence)}
    value={'checks':[{'check_id':'actual-check','stage':'structure','status':'return','evidence_ref':ref}],
           'evidence_refs':[ref],'revision_targets':['rules','blueprint','implementation']}
    path=root/'feedback.json';path.write_text(json.dumps(value));return path,value

def valid(feedback):
    check=feedback['checks'][0]
    return {'stage':check['stage'],'root_cause_status':'unconfirmed','candidate_causes':['neutral cause'],
      'completed_checks':[{'check_id':check['check_id'],'evidence_ref':check['evidence_ref'],'observed_status':check['status']}],
      'excluded_causes':[],'missing_evidence':[],'next_check':{'operation':'rerun_declared_checks'},'fact_refs':feedback['evidence_refs']}

def bad(feedback):
    value=valid(feedback);value['next_check']={'status':'none_declared'}
    value['completed_checks'][0]['observed_status']='a sentence instead of the exact status'
    value['fact_refs']=['control_feedback.status=return'];return value

class Diagnosing(ScriptedControlProvider):
    def __init__(self,feedback,invalid_corrections=0):super().__init__(failures=0);self.feedback=feedback;self.invalid_corrections=invalid_corrections;self.seen=[];self.requests=[]
    def complete(self,messages,**kwargs):
        request=json.loads(messages[-1]['content']);tid=request['task']['id'];self.seen.append(tid);self.requests.append(request)
        result=super().complete(messages,**kwargs);value=json.loads(result.content)
        if tid=='blueprint':value['diagnosis']=bad(self.feedback)
        if tid=='diagnosis-correction' and self.invalid_corrections>0:
            self.invalid_corrections-=1;value={'diagnosis':bad(self.feedback)}
        return replace(result,content=json.dumps(value))

def prepare(root,legacy=False):
    cfg=prepare_control(root/'spec',max_revisions=8);path,feedback=feedback_at(root/'spec')
    spec=root/'spec/workflow.json';data=read(spec);task=next(t for t in data['tasks'] if t['id']=='blueprint')
    task['output_schema'].setdefault('required',[]).append('diagnosis')
    task['output_schema'].setdefault('properties',{})['diagnosis']={'type':'object'} if legacy else schema()
    if not legacy:task['diagnosis_contract']={'schema_version':VERSION,'feedback_ref':{'path':str(path),'sha256':sha(path)},'allow_stage_revision':True}
    else:task['inputs'].append({'id':'control_feedback','role':'revision_feedback','path':str(path),'sha256':sha(path)})
    spec.write_text(json.dumps(data));return cfg,feedback

class DiagnosisProtocolTests(unittest.TestCase):
    def test_all_protocol_errors_reported_together(self):
        with tempfile.TemporaryDirectory() as td:
            _,feedback=feedback_at(Path(td));issues=validate({'diagnosis':bad(feedback)},feedback)
            text=json.dumps(issues)
            self.assertIn('next_check.operation',text);self.assertIn('observed_status',text);self.assertIn('fact_refs',text)
            self.assertEqual(validate({'diagnosis':valid(feedback)},feedback),[])

    def test_early_gate_corrects_metadata_and_then_calls_downstream_once(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);cfg,feedback=prepare(root);p=Diagnosing(feedback)
            state=control_run(cfg,root/'run',provider=p)
            self.assertEqual(state['status'],'complete')
            self.assertEqual(p.seen,['evidence','rules','blueprint','diagnosis-correction','implementation'])
            blueprint=root/'run/workflow-0000/artifacts/blueprint.json';before=sha(blueprint)
            self.assertEqual(read(blueprint)['diagnosis'],bad(feedback))
            receipt=read(state['diagnosis_receipts']['blueprint']['path']);self.assertEqual(receipt['artifact']['sha256'],before)
            self.assertEqual(read(root/'run/execution-0000/machine.json')['workflow_outputs']['blueprint']['sha256'],before)
            request=p.requests[2];contract=request['diagnosis_contract']
            self.assertEqual(contract['allowed_operations'],list(OPERATIONS));self.assertEqual(contract['checks'],feedback['checks'])
            self.assertEqual(request['required_output_schema']['properties']['diagnosis'],schema())
            self.assertEqual(contract['example']['completed_checks'][0]['observed_status'],'return')

    def test_multiple_corrections_preserve_game_artifact_and_no_downstream_repeat(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);cfg,feedback=prepare(root);p=Diagnosing(feedback,invalid_corrections=1)
            state=control_run(cfg,root/'run',provider=p)
            self.assertEqual(state['status'],'complete');self.assertEqual(p.seen.count('blueprint'),1)
            self.assertEqual(p.seen.count('implementation'),1);self.assertEqual(p.seen.count('diagnosis-correction'),2)
            self.assertEqual(state['protocol_attempts_used'],2)

    def test_receipt_commit_interruption_recovers_without_repeating_correction(self):
        import arcagi_pipeline.protocol_repair as repairs
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);cfg,feedback=prepare(root);p=Diagnosing(feedback);write=repairs._write
            def stop(path,value):
                write(path,value)
                if path.name=='receipt.json':raise KeyboardInterrupt()
            with patch.object(repairs,'_write',side_effect=stop):
                with self.assertRaises(KeyboardInterrupt):control_run(cfg,root/'run',provider=p)
            after=Diagnosing(feedback);state=control_resume(cfg,root/'run',provider=after)
            self.assertEqual(state['status'],'complete');self.assertEqual(after.seen,['implementation'])

    def test_correction_limit_is_resource_result(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);cfg,feedback=prepare(root);data=read(cfg);data['max_revisions']=2;cfg.write_text(json.dumps(data))
            p=Diagnosing(feedback,invalid_corrections=100);state=control_run(cfg,root/'run',provider=p)
            self.assertEqual(state['status'],'limited');self.assertNotIn('implementation',p.seen)
            self.assertEqual(p.seen.count('diagnosis-correction'),2)

    def test_completed_receipt_tampering_is_detected(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);cfg,feedback=prepare(root);state=control_run(cfg,root/'run',provider=Diagnosing(feedback))
            receipt=read(state['diagnosis_receipts']['blueprint']['path']);output=Path(receipt['correction_artifact']['path'])
            output.write_text('{"diagnosis":{}}')
            with self.assertRaises(Fault):control_status(root/'run')
            p=Diagnosing(feedback);resumed=control_resume(cfg,root/'run',provider=p)
            self.assertEqual(resumed['status'],'waiting_supervisor');self.assertEqual(p.calls,0)

    def test_empty_contract_context_rejected_before_requests(self):
        from arcagi_pipeline.agent_workflow import WorkflowRunner
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);cfg,feedback=prepare(root);path=root/'spec/feedback.json';path.write_text('{"checks":[],"evidence_refs":[]}')
            spec=root/'spec/workflow.json';data=read(spec);data['tasks'][2]['diagnosis_contract']['feedback_ref']['sha256']=sha(path);spec.write_text(json.dumps(data))
            p=Diagnosing(feedback)
            with self.assertRaises(Fault):WorkflowRunner(spec,root/'run',provider=p)
            self.assertEqual(p.calls,0)

    def test_compatible_recovery_preserves_implementation_and_repairs_one_sidecar(self):
        import arcagi_pipeline.controller as controller
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);cfg,feedback=prepare(root,legacy=True);p=Diagnosing(feedback)
            with patch.object(controller,'execute_handoff',side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):control_run(cfg,root/'old',provider=p)
            state=read(root/'old/state.json');state['status']='paused';_save(root/'old',state)
            record=read(root/'old/workflow-0000/workflow_run.json');original=next(t for t in record['tasks'] if t['id']=='implementation')['output_sha256']
            core=Path(controller.__file__).parent;shutil.copytree(core,root/'archive-core',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
            inventory={'run':str(root/'old'),'files':[{'path':str(f.relative_to(root/'old')),'sha256':sha(f)} for f in (root/'old').rglob('*') if f.is_file() and not f.name.endswith('.lock')]}
            manifest=root/'inventory.json';manifest.write_text(json.dumps(inventory));budget_before=(root/'spec/ledger.json').read_bytes()
            result=recover(root/'old',root/'old/workflow-0000',root/'new',root/'archive-core',manifest,100)
            self.assertEqual(result['reused_tasks'],['evidence','rules','blueprint','implementation'])
            self.assertEqual((root/'spec/ledger.json').read_bytes(),budget_before)
            new_provider=Diagnosing(feedback);done=control_resume(Path(result['config']),root/'new',provider=new_provider)
            self.assertEqual(done['status'],'complete');self.assertEqual(new_provider.seen,['diagnosis-correction'])
            report=read(Path(done['result']));self.assertEqual(report['workflow_outputs']['implementation']['sha256'],original)

if __name__=='__main__':unittest.main()
