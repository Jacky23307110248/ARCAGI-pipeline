"""Compatible completed-workflow recovery after a machine protocol repair."""
from pathlib import Path
import copy
from .controller import (_read,_hash,_absolute_spec,_baseline,_save,_event,_verified_ref,_check_baseline,_load_config)
from .budget import file_lock,atomic_json,AggregateBudget
from .agent_workflow import WorkflowRunner,_schema_errors,_private_reasoning_paths
from .diagnosis_protocol import VERSION,LEGACY_INSTRUCTION,INSTRUCTION,schema,payload_schema,payload_value
from .revision_protocol import review_targets
from .diagnostics import Fault

CONTROL_IDS={'control_feedback','control_previous','control_self_review','control_blind_previous','control_evidence','commitment_baseline'}

def _domain(task,base):
    value=copy.deepcopy(task)
    for key in ('frozen_output','frozen_origin','diagnosis_contract'):value.pop(key,None)
    value['inputs']=[i for i in value.get('inputs',[]) if i['id'] not in CONTROL_IDS]
    value['output_schema'].setdefault('properties',{}).pop('diagnosis',None)
    value['output_schema']['required']=[x for x in value['output_schema'].get('required',[]) if x!='diagnosis']
    text=Path(value.pop('prompt')).read_text(encoding='utf-8')
    for suffix in (LEGACY_INSTRUCTION,'\n'+INSTRUCTION+'\n'):
        if text.endswith(suffix):text=text[:-len(suffix)]
    value['prompt_content']=text
    return value

def recover(source_run,checkpoint_workflow,run_root,previous_core,history_manifest,additional_revisions=100):
    source=Path(source_run).resolve();workflow=Path(checkpoint_workflow).resolve();root=Path(run_root).resolve()
    archive=Path(previous_core).resolve();inventory_path=Path(history_manifest).resolve()
    if root.exists() or not workflow.is_relative_to(source) or type(additional_revisions) is not int or additional_revisions<1:raise Fault('config_invalid')
    with file_lock(source/'.controller.lock'):
        old=_read(source/'state.json')
        if old['status']!='paused':raise Fault('config_invalid','recovery requires a paused predecessor')
        inventory=_read(inventory_path)
        if Path(inventory['run']).resolve()!=source:raise Fault('artifact_changed')
        indexed={str((source/r['path']).resolve()):r['sha256'] for r in inventory['files']}
        for raw,digest in indexed.items():
            if not Path(raw).is_file() or _hash(raw)!=digest:raise Fault('artifact_changed','archived history changed')
        pinned_cores={Path(p).parent for p in old['baseline'] if Path(p).name=='controller.py' and Path(p).parent.name=='arcagi_pipeline'}
        if len(pinned_cores)!=1:raise Fault('artifact_changed')
        core=next(iter(pinned_cores))
        for raw,digest in old['baseline'].items():
            p=Path(raw);p=archive/p.name if p.parent==core else p
            if not p.is_file() or _hash(p)!=digest:raise Fault('artifact_changed','predecessor baseline changed')
        _check_baseline(old.get('revision_files',{}))
        if _hash(old['workflow_spec'])!=old['spec_sha256']:raise Fault('artifact_changed')
        configs=[]
        for raw in old['baseline']:
            if Path(raw).suffix=='.json':
                try:
                    if _read(raw).get('schema_version')=='arcagi-control/v1':configs.append(Path(raw))
                except (ValueError,AttributeError):continue
        if len(configs)!=1:raise Fault('config_invalid','ambiguous predecessor configuration')
        cfg,initial,runtime=_load_config(configs[0]);base=_absolute_spec(initial)
        from .runtime_control import preflight_runtime
        runtime_facts=preflight_runtime(runtime)
        if runtime_facts['fingerprint']!=old.get('runtime_fingerprint'):raise Fault('artifact_changed')
        ledger=Path(base['budget']['aggregate_ledger']);budget=AggregateBudget(ledger).snapshot()
        if budget.get('tokens_inflight_reserved',0) or any(r['status']=='reserved' for r in budget.get('runtime_requests',{}).values()):raise Fault('config_invalid','settled requests required')
        record_path=workflow/'workflow_run.json';record=_read(record_path)
        if str(record_path) not in indexed or record.get('status')!='complete' or any(a.get('status') in ('running','output_staged') for a in record.get('attempt_log',[])):raise Fault('workflow_not_ready')
        specs=[Path(old['initial_spec'])]+list(source.glob('revision-*/workflow.json'))
        matches=[p for p in specs if (str(p.resolve()) in indexed or str(p.resolve()) in old['baseline']) and _hash(p)==record['spec_sha256']]
        if not matches:raise Fault('artifact_changed','selected workflow spec not archived')
        selected_spec=matches[0];selected=_absolute_spec(selected_spec)
        if any(selected.get(k)!=base.get(k) for k in ('game','provider','budget')):raise Fault('config_invalid','selected workflow identity changed')
        if [t['id'] for t in selected['tasks']]!=[t['id'] for t in base['tasks']]:raise Fault('config_invalid','task graph changed')
        runner=WorkflowRunner(selected_spec,workflow,plan_only=True);rows={r['id']:r for r in record['tasks']};original_tasks={t['id']:t for t in runner.spec['tasks']}
        reused=[];diagnoses=[]
        for template,prior in zip(base['tasks'],selected['tasks']):
            tid=template['id'];row=rows[tid]
            if _domain(template,initial.parent)!=_domain(prior,selected_spec.parent):raise Fault('config_invalid','incompatible task template: '+tid)
            path=(workflow/row.get('output_path','')).resolve()
            if row['status']!='complete' or not path.is_relative_to(workflow) or str(path) not in indexed or _hash(path)!=row.get('output_sha256'):raise Fault('artifact_changed')
            if runner._fingerprint(original_tasks[tid],rows)!=row.get('fingerprint'):raise Fault('artifact_changed','selected task input fingerprint changed')
            value=_read(path)
            if _private_reasoning_paths(value) or _schema_errors(value,prior['output_schema']):raise Fault('output_protocol_invalid')
            if prior.get('gate_review') and (value.get('verdict')!='pass' or review_targets(value,prior,selected['tasks'])):raise Fault('workflow_not_ready','passing review required')
            template['frozen_output']={'path':str(path),'sha256':row['output_sha256']}
            if template.get('evidence_lane')=='trajectory_blind':template['frozen_origin']={'record_path':str(record_path),'record_sha256':_hash(record_path),'spec_path':str(selected_spec),'spec_sha256':_hash(selected_spec),'task_id':tid}
            feedback_input=next((i for i in prior.get('inputs',[]) if i['id']=='control_feedback'),None)
            if 'diagnosis' in prior['output_schema'].get('required',[]):
                if not feedback_input or template.get('evidence_lane')=='trajectory_blind':raise Fault('config_invalid','diagnosis feedback provenance required')
                ref={'path':feedback_input['path'],'sha256':feedback_input['sha256']}
                ref_path=str(Path(ref['path']).resolve())
                if indexed.get(ref_path,old['baseline'].get(ref_path))!=ref['sha256']:raise Fault('artifact_changed')
                template['diagnosis_contract']={'schema_version':VERSION,'feedback_ref':ref,'allow_stage_revision':True}
                template['output_schema'].setdefault('properties',{})['diagnosis']=schema()
                template['output_schema']['required']=list(dict.fromkeys(template['output_schema'].get('required',[])+['diagnosis']))
                diagnoses.append(tid)
            reused.append({'task_id':tid,'artifact':_verified_ref(path),'fingerprint':row['fingerprint']})
        root.mkdir(parents=True);checkpoint=root/'checkpoint';checkpoint.mkdir()
        atomic_json(checkpoint/'predecessor-state.json',old)
        spec=checkpoint/'workflow.json';atomic_json(spec,base)
        cfg.update(workflow_spec=str(spec),runtime_config=str(runtime),max_revisions=old['revision']+old.get('protocol_attempts_used',0)+additional_revisions)
        config=checkpoint/'control.json';atomic_json(config,cfg)
        WorkflowRunner(spec,plan_only=True)
        receipt={'operation':'compatible_completed_workflow_recovery','source_run':str(source),'selected_record':_verified_ref(record_path),
          'selected_spec':_verified_ref(selected_spec),'predecessor_state':_verified_ref(checkpoint/'predecessor-state.json'),
          'history_manifest':_verified_ref(inventory_path),'previous_core':str(archive),'reused':reused,'diagnosis_tasks':diagnoses,
          'budget':_verified_ref(ledger),'requests_preserved':budget['requests_used'],'tokens_preserved':budget['total_tokens_used'],
          'additional_revision_units':additional_revisions,'model_requests':0,'completion_scope':'candidate payloads, pending execution verification'}
        atomic_json(checkpoint/'migration.json',receipt)
        state={'schema_version':'arcagi-controller/v1','status':'paused','revision':old['revision'],'protocol_attempts_used':old.get('protocol_attempts_used',0),
          'started':old['started'],'initial_spec':str(spec),'workflow_spec':str(spec),'spec_sha256':_hash(spec),'baseline':_baseline(config,spec,runtime),
          'history':[],'phase':'workflow','current_workflow':None,'runtime_fingerprint':runtime_facts['fingerprint'],'predecessor':_verified_ref(checkpoint/'migration.json')}
        _save(root,state);_event(root,'compatible_recovery_prepared',**receipt)
        old.update(status='retired',successor=str(root));_event(source,'retired_for_compatible_recovery',migration=_verified_ref(checkpoint/'migration.json'));_save(source,old)
        return {'status':'paused','run_dir':str(root),'config':str(config),'reused_tasks':[x['task_id'] for x in reused],'diagnosis_tasks':diagnoses,'model_requests':0}
