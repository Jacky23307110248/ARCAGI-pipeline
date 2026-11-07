"""Bounded workflow repair of diagnosis metadata, preserving game artifact bytes."""
from pathlib import Path
import copy,time
from .agent_workflow import WorkflowRunner,WorkflowError
from .budget import atomic_json
from .diagnosis_protocol import (VERSION,INSTRUCTION,feedback_for,describe,validate,
                                read,sha,pinned,digest,effective_bundle,schema)
from .diagnostics import Fault

def _write(path,value):
    if path.exists():
        if read(path)!=value:raise Fault('artifact_changed')
    else:atomic_json(path,value)

def process(root,state,cfg,provider,env_file,pause_check):
    from .controller import _save,_event,_verified_ref
    pending=state['pending_protocol'];artifact=pinned(pending['artifact_ref']);contract=pending['contract']
    feedback=feedback_for(contract);original=read(artifact)
    directory=root/'protocol-corrections'/(pending['task_id']+'-'+sha(artifact)[:16]+'-'+digest(contract)[:16])
    directory.mkdir(parents=True,exist_ok=True);receipt_path=directory/'receipt.json'
    while True:
        pause_check()
        if receipt_path.exists():
            ref=_verified_ref(receipt_path);effective_bundle(artifact,contract,ref)
            state.setdefault('diagnosis_receipts',{})[pending['task_id']]=ref
            state.pop('pending_protocol',None);_save(root,state);return
        if time.time()-state['started']>=cfg['max_elapsed_seconds'] or (state['revision']+state.get('protocol_attempts_used',0)>=cfg['max_revisions'] and not pending.get('active_spec')):
            state.update(status='limited',reason='diagnosis_correction_resource_limit');_save(root,state);return
        if not pending.get('active_spec'):
            number=pending.get('attempt',0)+1
            attempt=directory/f'attempt-{number:04d}';attempt.mkdir(exist_ok=True)
            prior=pending.get('last_diagnosis',original.get('diagnosis'))
            _write(attempt/'prior-diagnosis.json',{'diagnosis':prior})
            _write(attempt/'constraints.json',{'contract':describe({**contract,'allow_stage_revision':False}),'issues':pending.get('issues',validate(original,feedback))})
            prompt=attempt/'prompt.md'
            text=('Correct the supplied diagnosis metadata using the exact machine contract. Return {"diagnosis": ...}. '
                  'Copy completed check IDs, status values and evidence objects from control_feedback. '
                  'Choose next_check.operation from the supplied enum. Retain supported conclusions and uncertainty. '
                  'This task updates diagnosis metadata. '+INSTRUCTION)
            if prompt.exists() and prompt.read_text(encoding='utf-8')!=text:raise Fault('artifact_changed')
            if not prompt.exists():prompt.write_text(text,encoding='utf-8')
            main=read(state['workflow_spec']);repair_contract={**contract,'allow_stage_revision':False}
            task={'id':'diagnosis-correction','stage':'protocol_correction','prompt':str(prompt),
                'inputs':[{'id':'prior_diagnosis','role':'prior_artifact','path':str(attempt/'prior-diagnosis.json'),'sha256':sha(attempt/'prior-diagnosis.json')},
                  {'id':'control_feedback','role':'revision_feedback',**contract['feedback_ref']},
                  {'id':'protocol_constraints','role':'protocol_contract','path':str(attempt/'constraints.json'),'sha256':sha(attempt/'constraints.json')}],
                'diagnosis_contract':repair_contract,'max_output_tokens':min(cfg.get('diagnosis_max_output_tokens',8192),main['budget'].get('max_output_tokens_per_request',8192)),
                'output_schema':{'type':'object','required':['diagnosis'],'additionalProperties':False,'properties':{'diagnosis':schema(False)}}}
            spec={'schema_version':'arcagi-agent-workflow/v1','workflow_id':'diagnosis-correction-'+directory.name+'-'+str(number),
                  'unit_id':main['unit_id'],'game':copy.deepcopy(main['game']),'provider':copy.deepcopy(main['provider']),
                  'budget':copy.deepcopy(main['budget']),'tasks':[task]}
            spec_path=attempt/'workflow.json';_write(spec_path,spec)
            pending.update(attempt=number,active_spec={'path':str(spec_path),'sha256':sha(spec_path)},active_workflow=str(attempt/'run'))
            state['protocol_attempts_used']=state.get('protocol_attempts_used',0)+1
            _event(root,'protocol_correction_planned',task_id=pending['task_id'],attempt=number,artifact=pending['artifact_ref'],spec=pending['active_spec'])
            _save(root,state)
        spec_path=pinned(pending['active_spec']);run=Path(pending['active_workflow'])
        try:
            runner=WorkflowRunner(spec_path,run,provider=provider,env_file=env_file,
                deadline=state['started']+cfg['max_elapsed_seconds'],pause_check=pause_check)
            runner.run(retry_failed=pending.pop('retry_failed',False))
            row=runner.data['tasks'][0];output=run/row['output_path']
            correction=read(output);issues=validate(correction,feedback,False)
            if issues:raise Fault('output_protocol_invalid','correction validation diverged')
            record=run/'workflow_run.json'
            receipt={'schema_version':'arcagi-diagnosis-correction/v1','artifact':pending['artifact_ref'],
                'contract_sha256':digest(contract),'correction_artifact':_verified_ref(output),
                'workflow_origin':{'record':_verified_ref(record),'spec':_verified_ref(spec_path),'task_id':'diagnosis-correction'}}
            _write(receipt_path,receipt)
            _event(root,'diagnosis_correction_accepted',task_id=pending['task_id'],receipt=_verified_ref(receipt_path),artifact_bytes_preserved=True)
        except WorkflowError as exc:
            if getattr(exc,'diagnostic_code',None)!='output_protocol_invalid':
                pending['retry_failed']=True;_save(root,state);raise
            repair=getattr(exc,'diagnosis_repair',None)
            if repair:pending['last_diagnosis']=read(pinned(repair['artifact_ref'])).get('diagnosis')
            pending['issues']=getattr(exc,'protocol_facts',{'check':'output_schema','reason':'invalid_correction_output'})
            _event(root,'diagnosis_correction_returned',task_id=pending['task_id'],attempt=pending['attempt'],issues=pending['issues'])
            pending.pop('active_spec',None);pending.pop('active_workflow',None);_save(root,state)
