"""Bounded production controller: model revisions, machine facts, explicit infra exits."""
from __future__ import annotations
import copy,hashlib,json,time,uuid,math
from pathlib import Path
from .agent_workflow import WorkflowRunner,WorkflowError,workflow_status
from .machine import execute_handoff,plan_machine,collect,confirmed_production
from .runtime_control import preflight_runtime
from .budget import file_lock,atomic_json
from .diagnostics import Fault,ArchiveWriteError,scrub,Archive
from .revision_protocol import PauseRequested

INFRA={"provider_transport","provider_http","budget_limit","declared_dependency_missing","config_invalid","artifact_changed","input_drift","workflow_not_ready","diagnostic_archive_write_failed","process_start_failed"}
ROLES={"evidence","rules","blueprint","implementation"}

def _hash(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def _read(p):return json.loads(Path(p).read_text(encoding="utf-8"))
def _event(root,kind,**data):
    directory=root/"events";directory.mkdir(exist_ok=True)
    row=scrub({"event_id":uuid.uuid4().hex,"time":time.time(),"kind":kind,**data})
    try:
        with (directory/(str(time.time_ns())+"-"+row["event_id"]+".json")).open("x",encoding="utf-8") as f:json.dump(row,f,ensure_ascii=False,allow_nan=False)
    except OSError:raise ArchiveWriteError("controller event archive unavailable") from None
    return row

def _load_config(path):
    path=Path(path).resolve();cfg=_read(path)
    if not isinstance(cfg,dict) or cfg.get("schema_version")!="arcagi-control/v1":raise Fault("config_invalid")
    cfg.setdefault("max_revisions",100);cfg.setdefault("unknown_policy","revise");cfg.setdefault("max_elapsed_seconds",2592000)
    if not isinstance(cfg.get("roles"),dict) or set(cfg["roles"])!=ROLES:raise Fault("config_invalid")
    for key in ("max_revisions","max_elapsed_seconds"):
        if type(cfg[key]) is not int or cfg[key]<1:raise Fault("config_invalid")
    if type(cfg.get('diagnosis_max_output_tokens',8192)) is not int or cfg.get('diagnosis_max_output_tokens',8192)<1:raise Fault('config_invalid')
    if cfg["unknown_policy"] not in ("revise","stop"):raise Fault("config_invalid")
    if type(cfg.get('capability_revision_limit',3)) is not int or cfg.get('capability_revision_limit',3)<1:raise Fault('config_invalid','invalid capability revision limit')
    schedule=cfg.get('search_cap_schedule',[cfg.get('cap',250000)])
    if not isinstance(schedule,list) or not schedule or schedule[0]!=cfg.get('cap',250000) or any(type(c) is not int or c<1 for c in schedule) or any(a>=b for a,b in zip(schedule,schedule[1:])):raise Fault('config_invalid','invalid bounded search cap schedule')
    try:spec=(path.parent/cfg["workflow_spec"]).resolve();runtime=(path.parent/cfg["runtime_config"]).resolve()
    except (KeyError,TypeError):raise Fault("config_invalid") from None
    data=_read(spec);ledger=data.get("budget",{}).get("aggregate_ledger")
    if not isinstance(ledger,str) or not (spec.parent/ledger).resolve().is_file():raise Fault("config_invalid","workflow requires existing aggregate ledger")
    ids={t["id"] for t in data.get("tasks",[])}
    roles=list(cfg["roles"].values())
    if any(not isinstance(x,str) or x not in ids for x in roles) or len(set(roles))!=len(roles):raise Fault("config_invalid","roles must name distinct existing tasks")
    targets=cfg.get("revision_targets",roles)
    if not isinstance(targets,list) or not targets or any(not isinstance(x,str) or x not in ids for x in targets) or len(set(targets))!=len(targets):raise Fault("config_invalid","revision_targets must name distinct existing tasks")
    for key,default,minimum in (("seed",0,None),("cap",250000,1)):
        value=cfg.get(key,default)
        if type(value) is not int or (minimum is not None and value<minimum):raise Fault("config_invalid",key+" invalid")
    timeout=cfg.get("timeout_seconds",300)
    if isinstance(timeout,bool) or not isinstance(timeout,(int,float)) or not math.isfinite(timeout) or timeout<=0:raise Fault("config_invalid","timeout_seconds invalid")
    return cfg,spec,runtime

def control_plan(config):
    cfg,spec,runtime=_load_config(config)
    _,_,plan=plan_machine(Path(config))
    return {"valid":True,"status":"ready","workflow":plan,"runtime_config":str(runtime),"max_revisions":cfg["max_revisions"],"supervisor_codes":sorted(INFRA),"model_claims_can_escalate":False}

def _absolute_spec(spec):
    data=copy.deepcopy(_read(spec));base=Path(spec).parent
    for task in data["tasks"]:
        task["prompt"]=str((base/task["prompt"]).resolve())
        for item in task.get("inputs",[]):
            if "path" in item:item["path"]=str((base/item["path"]).resolve())
        if task.get("frozen_output"):task["frozen_output"]["path"]=str((base/task["frozen_output"]["path"]).resolve())
    data["budget"]["aggregate_ledger"]=str((base/data["budget"]["aggregate_ledger"]).resolve())
    return data

def _baseline(config,spec,runtime):
    files={str(Path(config).resolve()):_hash(config),str(spec):_hash(spec),str(runtime):_hash(runtime)}
    for task in _read(spec)["tasks"]:
        for raw in [task["prompt"]]+[i["path"] for i in task.get("inputs",[]) if "path" in i]+([task["frozen_output"]["path"]] if task.get("frozen_output") else []):
            path=(spec.parent/raw).resolve();files[str(path)]=_hash(path)
        if task.get('diagnosis_contract'):
            ref=task['diagnosis_contract']['feedback_ref'];files[ref['path']]=ref['sha256']
    core=Path(__file__).parent
    for path in core.glob("*.py"):files[str(path)]=_hash(path)
    return files

def _check_baseline(files):
    for path,digest in files.items():
        if not Path(path).is_file() or _hash(path)!=digest:raise Fault("artifact_changed")

def _check_current_spec(state):
    if _hash(state["workflow_spec"])!=state["spec_sha256"]:raise Fault("artifact_changed")

def _check_corrections(state):
    from .diagnosis_protocol import pinned,effective_bundle
    tasks={t['id']:t for t in _read(state['workflow_spec'])['tasks']}
    for tid,ref in state.get('diagnosis_receipts',{}).items():
        receipt=_read(pinned(ref));contract=tasks.get(tid,{}).get('diagnosis_contract')
        if not contract:raise Fault('artifact_changed')
        effective_bundle(receipt['artifact']['path'],contract,ref)

def _save(root,state):
    try:atomic_json(root/"state.json",scrub(state))
    except OSError:raise ArchiveWriteError("controller checkpoint archive unavailable") from None
def control_status(run_root):
    root=Path(run_root).resolve();state=_read(root/"state.json")
    _check_baseline(state.get("baseline",{}));_check_baseline(state.get("revision_files",{}))
    from .diagnosis_protocol import pinned
    for ref in state.get('diagnosis_receipts',{}).values():pinned(ref)
    _check_corrections(state)
    if state.get('pending_protocol'):
        pinned(state['pending_protocol']['artifact_ref'])
        from .diagnosis_protocol import feedback_for
        feedback_for(state['pending_protocol']['contract'])
    if _hash(state["workflow_spec"])!=state["spec_sha256"]:raise Fault("artifact_changed")
    if state.get("status")=="complete":
        if _hash(state["result"])!=state["result_sha256"]:raise Fault("artifact_changed")
    return state

def _verified_ref(path):return {"path":str(Path(path).resolve()),"sha256":_hash(path)}

def _bounded(value,depth=0):
    if depth>8:return {"omitted":"depth_limit","type":type(value).__name__}
    if isinstance(value,str) and len(value)>2048:return {"prefix":value[:2048],"length":len(value),"truncated":True}
    if isinstance(value,list):
        if len(value)>16:return {"items":[_bounded(x,depth+1) for x in value[:8]],"tail":[_bounded(x,depth+1) for x in value[-8:]],"length":len(value),"truncated":True}
        return [_bounded(x,depth+1) for x in value]
    if isinstance(value,dict):return {k:_bounded(v,depth+1) for k,v in list(value.items())[:80]}
    return value

def _facts(report,out):
    refs=[];checks=[];observations=[]
    if out and (out/"machine.json").is_file():refs.append(_verified_ref(out/"machine.json"))
    production=confirmed_production(out,report) if out is not None else None
    if production is not None:
        path=out/"production/production.json";refs.append({"path":str(path.resolve()),"sha256":report["production_sha256"]})
        for row in production.get("tasks",[]):
            detailed_stages=set()
            for stage,value in (('model_search',row.get('search')),('engine_replay',row.get('replay')),('level_smoke',row.get('level_smoke'))):
                if value:
                    detailed_stages.add(stage)
                    status=('pass' if value['accepted'] else 'return') if stage=='engine_replay' else value['status']
                    checks.append({'check_id':row['task_id']+':'+stage,'stage':stage,'status':status,'executed':True,'evidence_ref':refs[-1]})
            for oid,result in row.get('obligations',{}).items():
                checks.append({'check_id':row['task_id']+':obligation:'+oid,'stage':'validation_obligation','obligation_id':oid,'status':result['status'],'evidence_ref':refs[-1]})
            for stage,status in row.get("checks",{}).items():
                if stage in detailed_stages or status in {"pending","not_checked"}:continue
                checks.append({"check_id":row["task_id"]+":"+stage,"stage":stage,"status":status,"evidence_ref":refs[-1]})
            if production.get("schema_version") == "arcagi-production/v2":
                checks.append({"check_id":row["task_id"]+":generated_validation","stage":"generated_level_validation","status":row["status"],"evidence_ref":refs[-1]})
        from .handoff_control import feedback_rows
        observations=feedback_rows(production.get('tasks',[]))
        for unit in production.get('units',[]):
            for tid in unit.get('blocked_checks',{}).get('unexecuted_tasks',[]):
                checks.append({'check_id':tid+':shared_prerequisite','stage':'shared_prerequisite','status':'blocked','executed':False,'reason':unit['blocked_checks']['reason'],'evidence_ref':refs[-1]})
            for stage in ('unit_execution','adapter_smoke'):
                if stage in unit:
                    checks.append({'check_id':unit['unit_id']+':'+stage,'stage':stage,'status':'blocked' if unit[stage].get('reason')=='verified_candidates_missing' else unit[stage]['status'],'executed':unit[stage].get('reason')!='verified_candidates_missing','evidence_ref':refs[-1]})

        for stage in ("unit_checks","adapter_smoke"):
            if stage in production:checks.append({"check_id":stage,"stage":stage,"status":production[stage]["status"],"evidence_ref":refs[-1]})
        seen={check["check_id"] for check in checks}
        for step in production.get("execution_order",[]):
            if step["check_id"] not in seen:
                checks.append({**step,"evidence_ref":refs[-1]})
                seen.add(step["check_id"])
    expanded=[]
    for item in report.get("feedback",[]):
        if item.get("archive") and item.get("event_id"):
            try:
                event=Archive(item["archive"]).read("events",item["event_id"])
                expanded.append({"event_id":item["event_id"],**{k:event.get(k) for k in ("code","summary","stage","expected","observed","exception","artifact_refs")}})
            except (OSError,ValueError,KeyError):raise Fault("artifact_changed") from None
        else:expanded.append(item)
    if not checks:
        checks=[{"check_id":(f.get("event_id") or "check-"+str(i)),"stage":f.get("stage",report.get("stage","workflow")),"status":report.get("status","return"),"evidence_ref":refs[0] if refs else None} for i,f in enumerate(report.get("feedback",[]))]
    return {"schema_version":"arcagi-check-feedback/v1","observations":observations,"status":report.get("status","return"),"facts":_bounded(expanded),"checks":checks,"evidence_refs":refs,"attribution":"unconfirmed","next_route":"ds_diagnosis","internal_audit_forwarded":False}

def _code(item):
    if item.get("archive") and item.get("event_id"):
        try:return Archive(item["archive"]).read("events",item["event_id"])["code"]
        except (OSError,ValueError,KeyError):raise Fault("artifact_changed") from None
    return item.get("code")

def _runtime_identity(state,runtime):
    facts=preflight_runtime(runtime)
    if state.get("runtime_fingerprint") and facts["fingerprint"]!=state["runtime_fingerprint"]:raise Fault("artifact_changed","runtime identity changed during run")
    return facts

def _check_execution_evidence(state):
    """Independently verify pinned active execution evidence before any routing."""
    if not state.get('execution_hash'):return
    if not state.get('execution_intent'):raise Fault('artifact_changed','execution identity missing')
    out=Path(state.get('execution_output',state['execution_intent']))
    report_path=out/'machine.json'
    if not report_path.is_file() or _hash(report_path)!=state['execution_hash']:
        raise Fault('artifact_changed','pinned execution report changed')
    report=_read(report_path)
    for key,name in (('operation_plan_sha256','operation-plan.json'),('operation_receipts_sha256','diagnostic-operations.json')):
        if report.get(key) and (not (out/name).is_file() or _hash(out/name)!=report[key]):raise Fault('artifact_changed','operation evidence changed')
    from .machine import safe
    inventory=report.get('files',[])
    if not isinstance(inventory,list):raise Fault('artifact_changed','materialized inventory malformed')
    for ref in inventory:
        if not isinstance(ref,dict) or not isinstance(ref.get('path'),str) or not isinstance(ref.get('sha256'),str):raise Fault('artifact_changed','materialized inventory malformed')
        path=safe(out/'plugin',ref['path'],'artifact_changed')
        if not path.is_file() or _hash(path)!=ref['sha256']:raise Fault('artifact_changed','materialized implementation changed')
    workflow=state.get('current_workflow')
    outputs=report.get('workflow_outputs',{})
    if not workflow or not isinstance(outputs,dict) or set(outputs)!=ROLES or any(not isinstance(v,dict) or not isinstance(v.get('task_id'),str) for v in outputs.values()):
        raise Fault('artifact_changed','cached execution workflow identity missing')
    expected=collect(Path(workflow),{role:value['task_id'] for role,value in outputs.items()})
    if any(outputs[role].get('sha256')!=value['sha256'] for role,value in expected.items()):
        raise Fault('artifact_changed','cached execution workflow inputs changed')
    if confirmed_production(out,report) is not None:
        from .verification_program import verify_trace_files
        verify_trace_files(out/'production/production.json')



def _confirm_infrastructure(state,runtime,workflow=None,ref=None,exc=None):
    # Independently repeat core prerequisites; never escalate from a name alone.
    checks=[]
    if state.get("provider_config_required") and (not state.get("provider_config_ref") or not Path(state["provider_config_ref"]).is_file()):
        return "config_invalid",[{"check":"provider_configuration_presence","status":"fail","code":"config_invalid","producer":"machine_recheck","expected":"explicit CLI provider config file available","observed":"missing"}]
    for name,operation in (("baseline_integrity",lambda:_check_baseline(state["baseline"])),("execution_evidence_integrity",lambda:_check_execution_evidence(state)),("revision_integrity",lambda:_check_baseline(state.get("revision_files",{}))),("current_spec_integrity",lambda:_check_current_spec(state)),('diagnosis_receipt_integrity',lambda:_check_corrections(state)),("runtime_preflight",lambda:_runtime_identity(state,runtime)),("workflow_preflight",lambda:WorkflowRunner(Path(state["workflow_spec"]),plan_only=True))):
        try:operation()
        except (Fault,ValueError,OSError) as failure:
            code=getattr(failure,"diagnostic_code",None) or "config_invalid"
            if code in INFRA:
                checks.append({"check":name,"status":"fail","code":code,"producer":"machine_recheck","expected":"declared prerequisite intact","observed_exception_type":type(failure).__name__,"runtime_config":_verified_ref(runtime) if Path(runtime).is_file() else {"path":str(runtime),"missing":True}})
                return code,checks
    if workflow and (Path(workflow)/"workflow_run.json").is_file():
        try:workflow_status(Path(workflow))
        except (Fault,ValueError,OSError) as failure:return "artifact_changed",[{"check":"workflow_output_integrity","status":"fail","code":"artifact_changed","producer":"machine_recheck","observed_exception_type":type(failure).__name__}]
    # Transport/launch/archive failures cannot be reproduced by issuing another
    # paid request. Verify their machine-owned event/implementation trace instead.
    fact=None
    if ref and ref.get("archive") and ref.get("event_id"):
        try:fact=Archive(ref["archive"]).read("events",ref["event_id"])
        except (OSError,ValueError,KeyError):return "artifact_changed",[{"check":"diagnostic_fact_integrity","status":"fail","code":"artifact_changed","producer":"machine_recheck"}]
    if fact:
        code=fact.get("code");locations=fact.get("exception",{}).get("locations",[])
        expected={"provider_transport":("agent_workflow.py","complete"),"provider_http":("agent_workflow.py","complete"),"process_start_failed":("machine.py","execute_handoff")}.get(code)
        if expected and any(row.get("file")==expected[0] and row.get("function")==expected[1] and row.get("source_sha256")==_hash(Path(__file__).with_name(expected[0])) for row in locations):
            return code,[{"check":"core_failure_provenance","status":"fail","code":code,"producer":"machine_recheck","event_ref":ref,"expected":"core transport/launch successful","observed":fact.get("observed"),"exception_type":fact.get("exception",{}).get("type")}]
    if isinstance(exc,ArchiveWriteError):
        import traceback
        if any(((Path(f.filename).name=="diagnostics.py" and f.name=="exclusive_json") or (Path(f.filename).name=="controller.py" and f.name in {"_event","_save"})) for f in traceback.extract_tb(exc.__traceback__)):
            return "diagnostic_archive_write_failed",[{"check":"archive_writer_failure","status":"fail","code":"diagnostic_archive_write_failed","producer":"machine_recheck","observed_exception_type":"ArchiveWriteError"}]
    return None,checks


def _block(root,state,code,stage,observed=None):
    if not observed or not observed.get("confirmed_checks"):raise Fault("output_protocol_invalid","supervisor transition requires machine confirmation")
    event=_event(root,"infrastructure_block",code=code,stage=stage,expected="declared runtime/protocol/integrity prerequisites",observed=observed or {},config_refs=state.get("baseline",{}),checks_performed=state.get("last_checks",[]),recovery_requirements=["matching event and hash-verified resolution evidence","successful repeated runtime and integrity preflight"])
    state.update(status="waiting_supervisor",block_event_id=event["event_id"],block_code=code)
    _save(root,state);return state

def _checked_execution(out,workflow,roles):
    """Revalidate both the execution inputs and its result on every cache hit."""
    report=_read(out/"machine.json")
    expected=collect(workflow,roles)
    if any(report.get('workflow_outputs',{}).get(role,{}).get('sha256')!=row['sha256'] for role,row in expected.items()):
        raise Fault('artifact_changed','execution belongs to different workflow artifacts')
    if confirmed_production(out,report) is not None:
        from .verification_program import verify_trace_files
        verify_trace_files(out/'production/production.json')
    return report


def _diagnosis(bundle,feedback):
    from .diagnosis_protocol import validate
    return validate(bundle,feedback)


def _revision(root,state,cfg,previous,feedback,target=None):
    feedback=copy.deepcopy(feedback)
    data=_absolute_spec(Path(state["workflow_spec"]));tasks={t["id"]:t for t in data["tasks"]}
    targets=[cfg["roles"]["implementation"]] if target is None else ([target] if isinstance(target,str) else target)
    if not isinstance(targets,list) or not targets or any(not isinstance(t,str) or t not in tasks for t in targets) or len(set(targets))!=len(targets):raise Fault("config_invalid","invalid revision address; no fallback")
    affected=set(targets)
    while True:
        more={tid for tid,t in tasks.items() if set(t.get("depends_on",[]))&affected}
        if more<=affected:break
        affected|=more
    # Rebuild invalidated tasks from their declared templates, not prior revision glue.
    templates={t["id"]:t for t in _absolute_spec(Path(state["initial_spec"]))["tasks"]}
    for tid in affected:tasks[tid]=copy.deepcopy(templates[tid])
    for tid in affected:state.get('diagnosis_receipts',{}).pop(tid,None)
    data["tasks"]=[tasks[t["id"]] for t in data["tasks"]]
    records={r["id"]:r for r in previous.get("tasks",[])}
    for tid,row in records.items():
        if row.get('gate_status')=='pass' and row.get('gate_artifact_sha256')==row.get('output_sha256') and row.get('output_path'):
            workflow_root=Path(state['current_workflow']).resolve()
            artifact=(workflow_root/row['output_path']).resolve()
            if not artifact.is_relative_to(workflow_root) or not artifact.is_file() or _hash(artifact)!=row['gate_artifact_sha256']:
                raise Fault("artifact_changed","commitment baseline no longer matches its passing gate")
            state.setdefault('commitment_baselines',{})[tid]={"path":str(artifact),"sha256":row['gate_artifact_sha256']}
    revision=state["revision"]+1;directory=root/("revision-"+str(revision).zfill(4));directory.mkdir(exist_ok=True)
    feedback["revision_targets"]=[tid for tid in cfg.get("revision_targets",list(cfg["roles"].values())) if tasks[tid].get("evidence_lane")!="trajectory_blind"]
    fragment_content={"schema_version":"arcagi-feedback-fragments/v1",
                      "observations":feedback.pop("observations",[]),"facts":feedback.pop("facts",[])}
    feedback_path=directory/"feedback.json"
    if feedback_path.exists():
        if _read(feedback_path)!=feedback:raise Fault("artifact_changed")
    else:atomic_json(feedback_path,feedback)
    for tid,task in tasks.items():
        row=records.get(tid,{})
        if tid not in affected and row.get("status")=="complete":
            artifact=Path(state["current_workflow"])/row["output_path"]
            if not artifact.resolve().is_relative_to(Path(state["current_workflow"]).resolve()) or _hash(artifact)!=row["output_sha256"]:raise Fault("artifact_changed")
            task["frozen_output"]={"path":str(artifact.resolve()),"sha256":row["output_sha256"]}
            if task.get("evidence_lane")=="trajectory_blind":
                task.setdefault("frozen_origin",{"record_path":str(Path(state["current_workflow"])/"workflow_run.json"),"record_sha256":_hash(Path(state["current_workflow"])/"workflow_run.json"),"spec_path":state["workflow_spec"],"spec_sha256":_hash(state["workflow_spec"]),"task_id":tid})
        elif tid in affected:task.pop("frozen_output",None);task.pop("frozen_origin",None)
    revision_files={str(feedback_path):_hash(feedback_path)};diagnosis_tasks=[]
    for target_id in targets:
        task=tasks[target_id]
        if task.get("frozen_origin_lane")=="trajectory_blind":
            task["evidence_lane"]="trajectory_blind";task.pop("frozen_origin_lane",None)
        task["inputs"]=[i for i in task.get("inputs",[]) if i["id"] not in {"control_feedback","control_previous","control_self_review","control_blind_previous"}]
        if task.get("evidence_lane")=="trajectory_blind":
            review=feedback.get("self_review")
            if review:
                origin=review["origin"]
                task["inputs"].append({"id":"control_self_review","role":"trajectory_review_feedback","path":review["path"],"sha256":review["sha256"],"blind_revision_origin":origin})
                row=records.get(target_id,{})
                if row.get("status")=="complete":
                    old=(Path(state["current_workflow"])/row["output_path"]).resolve()
                    prior_origin={**origin,"task_id":target_id}
                    task["inputs"].append({"id":"control_blind_previous","role":"prior_trajectory_claims","path":str(old),"sha256":row["output_sha256"],"blind_revision_origin":prior_origin})
            # Only protocol facts enter a prompt, never source-derived audit/semantic feedback.
            if feedback.get("protocol"):
                prompt=directory/(target_id+"-protocol-prompt.md")
                content=Path(task["prompt"]).read_text(encoding="utf-8")+"\nMachine protocol check (not game evidence): "+json.dumps(feedback["protocol"],ensure_ascii=False)+"\n"
                if prompt.exists() and prompt.read_text(encoding="utf-8")!=content:raise Fault("artifact_changed")
                if not prompt.exists():prompt.write_text(content,encoding="utf-8")
                task["prompt"]=str(prompt);revision_files[str(prompt)]=_hash(prompt)
            continue
        task['inputs']=[i for i in task['inputs'] if i['id'] not in ('control_evidence','commitment_baseline')]
        fragment=directory/'evidence-fragments.json'
        content=fragment_content
        if fragment.exists():
            if _read(fragment)!=content:raise Fault('artifact_changed')
        else:atomic_json(fragment,content)
        revision_files[str(fragment)]=_hash(fragment)
        task['inputs'].append({'id':'control_evidence','role':'execution_evidence_fragments','content_encoding':'json_compact','path':str(fragment),'sha256':_hash(fragment)})
        task["inputs"].append({"id":"control_feedback","role":"revision_feedback","content_encoding":"json_compact","path":str(feedback_path),"sha256":_hash(feedback_path)})
        if feedback.get("self_review"):
            review=feedback["self_review"]
            task["inputs"].append({"id":"control_self_review","role":"model_self_review","path":review["path"],"sha256":review["sha256"]})
        row=records.get(target_id,{})
        if row.get("status")=="complete":
            old=Path(state["current_workflow"])/row["output_path"]
            if _hash(old)!=row["output_sha256"]:raise Fault("artifact_changed")
            task["inputs"].append({"id":"control_previous","role":"prior_artifact","path":str(old.resolve()),"sha256":_hash(old)})
        baseline=state.get('commitment_baselines',{}).get(target_id)
        if baseline:
            if _hash(baseline['path'])!=baseline['sha256']:raise Fault('artifact_changed')
            task['inputs'].append({'id':'commitment_baseline','role':'commitment_baseline',**baseline})
        prompt=directory/(target_id+"-revision-prompt.md")
        from .diagnosis_protocol import INSTRUCTION,VERSION,schema
        prompt_text=Path(task["prompt"]).read_text(encoding="utf-8")+'\n'+INSTRUCTION+'\n'
        original_prompt=Path(task["prompt"]).read_text(encoding="utf-8")
        if INSTRUCTION in original_prompt:prompt_text=original_prompt
        if not prompt.exists():prompt.write_text(prompt_text,encoding="utf-8")
        elif prompt.read_text(encoding="utf-8")!=prompt_text:raise Fault("artifact_changed")
        task["prompt"]=str(prompt)
        revision_files[str(prompt)]=_hash(prompt)
        diagnosis_tasks.append(target_id)
        task["output_schema"].setdefault("required",[])
        if "diagnosis" not in task["output_schema"]["required"]:task["output_schema"]["required"].append("diagnosis")
        task['output_schema'].setdefault('properties',{})['diagnosis']=schema()
        task['diagnosis_contract']={'schema_version':VERSION,'feedback_ref':_verified_ref(feedback_path),'allow_stage_revision':True}
    # A non-imported blind revision must remain blind even if proposed by source review.
    for tid,t in tasks.items():
        if tid in affected and t.get("frozen_origin_lane")=="trajectory_blind":t["evidence_lane"]="trajectory_blind";t.pop("frozen_origin_lane",None)
    data["workflow_id"]=_read(Path(state["initial_spec"]))["workflow_id"]+"-r"+str(revision)
    dest=directory/"workflow.json"
    if dest.exists():
        if _read(dest)!=data:raise Fault("artifact_changed")
    else:atomic_json(dest,data)
    # Validate before committing the next control state, no model call here.
    WorkflowRunner(dest,plan_only=True)
    state["revision_files"]=revision_files
    state["history"].append({"revision":state["revision"],"feedback":_verified_ref(feedback_path),"targets":targets})
    state.pop("pending_revision",None)
    state.pop("diagnosis_task",None)
    state.update(revision=revision,workflow_spec=str(dest),spec_sha256=_hash(dest),current_workflow=None,phase="workflow",pending_feedback=str(feedback_path),diagnosis_tasks=diagnosis_tasks,status="running")
    _event(root,"revision_prepared",revision=revision,targets=targets,affected=sorted(affected),feedback=_verified_ref(feedback_path));_save(root,state)


def _advance_revision(root,state,cfg,previous,feedback,target=None):
    try:
        _revision(root,state,cfg,previous,feedback,target)
    except Fault as exc:
        if exc.diagnostic_code!="artifact_changed":
            raise
        checks=[{"check":"revision_input_integrity","status":"fail","code":"artifact_changed",
                 "producer":"machine_recheck","reason":str(exc)}]
        return _block(root,state,"artifact_changed","revision",{"confirmed_checks":checks})
    return None


def control_pause(run_root):
    root=Path(run_root).resolve()
    if not (root/"state.json").is_file():raise Fault("config_invalid","existing run required")
    request=root/"pause-request.json"
    if not request.exists():atomic_json(request,{"request_id":uuid.uuid4().hex,"time":time.time(),"operation":"pause_at_checkpoint"})
    return {"status":"pause_requested","request":_verified_ref(request),"note":"An active provider call completes before pausing; no forced cancellation."}

def _run(config,run_root,env_file=None,provider=None,resolution=None,resume_requested=False):
    config=Path(config).resolve();root=Path(run_root).resolve();root.mkdir(parents=True,exist_ok=True)
    with file_lock(root/".controller.lock"):
        cfg,initial,runtime=_load_config(config)
        if not (root/"state.json").exists():
            state={"schema_version":"arcagi-controller/v1","status":"running","revision":0,"started":time.time(),"initial_spec":str(initial),"workflow_spec":str(initial),"spec_sha256":_hash(initial),"baseline":_baseline(config,initial,runtime),"history":[],"phase":"workflow","current_workflow":None}
            _save(root,state);_event(root,"control_started",baseline=state["baseline"])
        else:state=_read(root/"state.json")
        if state.get("status")=="retired":return state
        resuming_paused=state.get("status")=="paused"
        if resuming_paused and not resume_requested:return state
        def pause_check():
            if (root/"pause-request.json").is_file():raise PauseRequested()
        state["provider_config_required"]=provider is None
        state["provider_config_ref"]=str(Path(env_file).resolve()) if env_file is not None else None
        try:
            if provider is None and (env_file is None or not Path(env_file).is_file()):raise Fault("config_invalid","explicit provider config file required")
            _check_baseline(state["baseline"]);_check_baseline(state.get("revision_files",{}))
            _check_corrections(state)
            if _hash(state["workflow_spec"])!=state["spec_sha256"]:raise Fault("artifact_changed")
            facts=_runtime_identity(state,runtime)
            if not state.get("runtime_fingerprint"):
                state["runtime_fingerprint"]=facts["fingerprint"];_save(root,state)
        except (Fault,OSError,ValueError) as exc:
            confirmed,checks=_confirm_infrastructure(state,runtime)
            if confirmed:return _block(root,state,confirmed,"preflight",{"exception_type":type(exc).__name__,"confirmed_checks":checks})
            if resuming_paused:
                _event(root,"resume_preflight_failed",exception_type=type(exc).__name__,code=getattr(exc,"diagnostic_code",None))
                return state
            raise
        if resuming_paused:
            request=root/"pause-request.json"
            _event(root,"pause_resumed",request=_verified_ref(request) if request.exists() else None)
            request.unlink(missing_ok=True)
            state["status"]="running"
            _save(root,state)
        if state["status"]=="complete" or state["status"]=="limited":return state
        if state["status"]=="waiting_supervisor":
            if resolution is None:return state
            record=_read(resolution)
            if record.get("block_event_id")!=state["block_event_id"] or not record.get("evidence_refs"):raise Fault("config_invalid","resolution evidence required")
            for ref in record["evidence_refs"]:
                if _hash(ref["path"])!=ref["sha256"]:raise Fault("artifact_changed")
                if Path(ref["path"]).suffix==".json" and _read(ref["path"]).get("status")!="pass":raise Fault("config_invalid","resolution check must pass")
            _event(root,"supervisor_resolution",resolution=_verified_ref(resolution));state["status"]="running";_save(root,state)
            state.pop("block_event_id",None);state.pop("block_code",None);_save(root,state)
        elif resolution is not None:raise Fault("config_invalid","not waiting for supervisor")
        if state.get("pending_revision"):
            pending=state["pending_revision"]
            blocked=_advance_revision(root,state,cfg,pending["previous"],pending["feedback"],pending.get("target"))
            if blocked is not None:return blocked
        while True:
            if time.time()-state["started"]>=cfg["max_elapsed_seconds"]:
                state.update(status="limited",reason="elapsed_limit");_save(root,state);return state
            revision=state["revision"];workflow=root/("workflow-"+str(revision).zfill(4));out=Path(state.get("execution_output",str(root/("execution-"+str(revision).zfill(4)))))
            state["current_workflow"]=str(workflow);_save(root,state)
            report=None;previous={};target=None
            try:
                pause_check()
                _check_baseline(state["baseline"]);_check_baseline(state.get("revision_files",{}))
                if state.get('pending_protocol'):
                    from .protocol_repair import process
                    process(root,state,cfg,provider,env_file,pause_check)
                    if state['status']=='limited':return state
                runner=WorkflowRunner(Path(state["workflow_spec"]),workflow,provider=provider,env_file=env_file,deadline=state["started"]+cfg["max_elapsed_seconds"],pause_check=pause_check,diagnosis_receipts=state.get('diagnosis_receipts'),diagnosis_observer=lambda tid,artifact,errors:_event(root,'diagnosis_checked',revision=revision,task_id=tid,artifact=artifact,errors=errors))
                summary=runner.run(retry_failed=state.pop("retry_same_workflow",False));previous=runner.data
                _event(root,"workflow_finished",revision=revision,summary=summary)
                if time.time()-state["started"]>=cfg["max_elapsed_seconds"]:
                    state.update(status="limited",reason="elapsed_limit");_save(root,state);return state
                from .handoff_control import bind_search_resources,producer_identity
                identities={k:v['sha256'] for k,v in collect(workflow,cfg['roles']).items()}
                bind_search_resources(state,producer_identity(identities,_hash(runtime),cfg.get('search_cap_schedule',[cfg.get('cap',250000)])))
                if report is None:
                    if out.exists() and not (out/"machine.json").exists():
                        if state.get("execution_intent")!=str(out):raise Fault("artifact_changed")
                        old_out=out;number=state.get("execution_attempt",0)+1
                        out=root/("execution-"+str(revision).zfill(4)+"-retry-"+str(number))
                        state.update(execution_output=str(out),execution_attempt=number)
                        _event(root,"interrupted_execution_preserved",path=str(old_out),successor=str(out));_save(root,state)
                    if (out/"machine.json").exists():
                        if not state.get("execution_hash"):
                            if state.get("execution_intent")!=str(out):raise Fault("artifact_changed")
                            recovered=_read(out/"machine.json");expected=collect(workflow,cfg["roles"])
                            if any(recovered.get("workflow_outputs",{}).get(role,{}).get("sha256")!=value["sha256"] for role,value in expected.items()):raise Fault("artifact_changed")
                            state["execution_hash"]=_hash(out/"machine.json");_save(root,state)
                            _event(root,"execution_recovered",report=_verified_ref(out/"machine.json"))
                        if _hash(out/"machine.json")!=state["execution_hash"]:raise Fault("artifact_changed")
                        report=_checked_execution(out,workflow,cfg['roles'])
                    else:
                        preflight_runtime(runtime)
                        state["execution_intent"]=str(out);_save(root,state)
                        from .diagnosis_protocol import effective_bundle
                        task_specs={t['id']:t for t in _read(state['workflow_spec'])['tasks']}
                        operation_requests=[]
                        for row in previous['tasks']:
                            ts=task_specs[row['id']]
                            if ts.get('diagnosis_contract'):
                                bundle=effective_bundle(workflow/row['output_path'],ts['diagnosis_contract'],state.get('diagnosis_receipts',{}).get(row['id']))
                                operation_requests.append({'task_id':row['id'],'operation':bundle['diagnosis']['next_check']['operation']})
                        report=execute_handoff(workflow,cfg["roles"],out,seed=cfg.get("seed",0),cap=cfg.get('search_cap_schedule',[cfg.get('cap',250000)])[state.get('search_cap_index',0)],timeout=min(cfg.get("timeout_seconds",300),max(.001,state["started"]+cfg["max_elapsed_seconds"]-time.time())),runtime_config=runtime,require_unit_checks=True,production_contract=cfg.get("production_contract","generated-game/v2"),diagnostic_requests=operation_requests)
                        state["execution_hash"]=_hash(out/"machine.json");_save(root,state)
                    _event(root,"execution_finished",revision=revision,report=_verified_ref(out/"machine.json"),status=report["status"])
                _check_execution_evidence(state)
                _check_baseline(state["baseline"]);_runtime_identity(state,runtime)
                for item in report.get("feedback",[]):
                    code=_code(item)
                    if code in INFRA:
                        confirmed,checks=_confirm_infrastructure(state,runtime,workflow,item)
                        _event(root,"infrastructure_claim_checked",claimed_code=code,confirmed=bool(confirmed),checks=checks,revision=revision)
                        if confirmed:return _block(root,state,confirmed,report.get("stage","execution"),{"failure":item,"confirmed_checks":checks})
                if report.get("error")=="design_invalid":
                    target=[cfg["roles"][role] for role in report.get("revision_roles",[]) if role in cfg["roles"]] or cfg["roles"]["blueprint"]
                if report["status"]=="pass":
                    state.update(status="complete",result=str(out/"machine.json"),result_sha256=_hash(out/"machine.json"));_event(root,"completed",result=_verified_ref(out/"machine.json"));_save(root,state);return state
            except PauseRequested:
                state.update(status="paused");_event(root,"paused",revision=revision,request=_verified_ref(root/"pause-request.json"));_save(root,state);return state
            except ArchiveWriteError as exc:
                confirmed,checks=_confirm_infrastructure(state,runtime,workflow,exc=exc)
                if confirmed:return _block(root,state,confirmed,"archive",{"confirmed_checks":checks})
                report={"status":"return","stage":"plugin_archive_claim","feedback":[{"code":"exception","exception_type":"ArchiveWriteError"}]}
                if (workflow/"workflow_run.json").is_file():previous=_read(workflow/"workflow_run.json")
            except Exception as exc:
                if getattr(exc,'diagnosis_repair',None):
                    state['pending_protocol']={**exc.diagnosis_repair,'issues':getattr(exc,'protocol_facts',{}).get('issues',[])}
                    _event(root,'diagnosis_correction_requested',revision=revision,task_id=exc.diagnosis_repair['task_id'],artifact=exc.diagnosis_repair['artifact_ref'],errors=state['pending_protocol']['issues'])
                    _save(root,state);continue
                code=getattr(exc,"diagnostic_code",None) or "exception"
                if code=="budget_limit":
                    state.update(status="limited",reason="request_or_token_budget_limit");_event(root,"limited",code=code,revision=revision,diagnostic_ref=getattr(exc,"diagnostic_ref",None));_save(root,state);return state
                if code in INFRA:
                    confirmed,checks=_confirm_infrastructure(state,runtime,workflow,getattr(exc,"diagnostic_ref",None),exc)
                    _event(root,"workflow_infrastructure_claim_checked",claimed_code=code,confirmed=bool(confirmed),checks=checks)
                    if confirmed:
                        state["retry_same_workflow"]=True
                        return _block(root,state,confirmed,"workflow",{"exception_type":type(exc).__name__,"diagnostic_ref":getattr(exc,"diagnostic_ref",None),"confirmed_checks":checks})
                if workflow.joinpath("workflow_run.json").exists():previous=_read(workflow/"workflow_run.json")
                # Schema/provider protocol failures belong to the producing model task.
                target=getattr(exc,"task_id",None)
                target=getattr(exc,"revision_targets",None) or target
                ref=getattr(exc,"diagnostic_ref",None) or {}
                report={"status":"return","stage":"workflow","feedback":[{"code":code,**ref,"exception_type":type(exc).__name__}]}
                if getattr(exc,"protocol_facts",None):report["protocol"]=exc.protocol_facts
                if getattr(exc,"review_task_id",None):
                    review_id=exc.review_task_id
                    review_row=next(r for r in previous["tasks"] if r["id"]==review_id)
                    record=workflow/"workflow_run.json"
                    report["self_review"]={"path":str((workflow/review_row["output_path"]).resolve()),"sha256":review_row["output_sha256"],"origin":{"record_path":str(record),"record_sha256":_hash(record),"spec_path":state["workflow_spec"],"spec_sha256":_hash(state["workflow_spec"]),"task_id":review_id}}
            try:
                feedback=_facts(report,out if out.exists() else None)
                production=confirmed_production(out,report) if report['status']=='unknown' and report.get('production_sha256') else None
            except Fault as exc:
                if exc.diagnostic_code!="artifact_changed":
                    raise
                checks=[{"check":"execution_feedback_integrity","status":"fail","code":"artifact_changed",
                         "producer":"machine_recheck","reason":str(exc)}]
                return _block(root,state,"artifact_changed","feedback",{"confirmed_checks":checks})
            if production is not None:
                capped=any(r.get('search',{}).get('status')=='unknown' and r.get('search',{}).get('evidence',{}).get('limit_kind')=='state_cap' for r in production.get('tasks',[]))
                schedule=cfg.get('search_cap_schedule',[cfg.get('cap',250000)]);idx=state.get('search_cap_index',0)
                if capped and idx+1<len(schedule):
                    _event(root,'bounded_search_resource_retry',revision=revision,previous=_verified_ref(out/'machine.json'),old_cap=schedule[idx],new_cap=schedule[idx+1],model_requests=0)
                    state.update(search_cap_index=idx+1,execution_output=str(root/('execution-'+str(revision).zfill(4)+'-cap-'+str(idx+1))))
                    state.pop('execution_hash',None);state.pop('execution_intent',None);_save(root,state);continue
            if report.get('stage')=='workflow':
                from .handoff_control import operation_receipt
                requests=[]
                for row in previous.get('tasks',[]):
                    if row.get('gate_status')=='pass' and row.get('output_path'):
                        value=_read(workflow/row['output_path'])
                        operation=value.get('diagnosis',{}).get('next_check',{}).get('operation')
                        if operation:requests.append({'task_id':row['id'],**operation_receipt(operation,{})})
                feedback['operation_receipts']=requests
            if out.joinpath('diagnostic-operations.json').is_file():
                receipt=_read(out/'diagnostic-operations.json')
                feedback['operation_receipts']=receipt.get('receipts',[])
                feedback['evidence_refs'].append(_verified_ref(out/'diagnostic-operations.json'))
            for key in ("protocol","self_review"):
                if key in report:feedback[key]=report[key]
            if report.get('protocol',{}).get('check')=='validation_capabilities':
                signature=json.dumps(report['protocol'].get('issues',[]),sort_keys=True)
                state['capability_failure_count']=state.get('capability_failure_count',0)+1 if signature==state.get('capability_failure_signature') else 1
                state['capability_failure_signature']=signature
                if state['capability_failure_count']>=cfg.get('capability_revision_limit',3):
                    state.update(status='limited',reason='capability_revision_limit');_event(root,'capability_repair_limited',revision=revision,protocol=report['protocol']);_save(root,state);return state
            else:
                state.pop('capability_failure_count',None);state.pop('capability_failure_signature',None)
            if state.get("pending_feedback") and report.get("stage")=="diagnosis":
                old=_read(state["pending_feedback"]);feedback["checks"]=old["checks"];feedback["evidence_refs"]=old["evidence_refs"]
            if not feedback["evidence_refs"]:
                factfile=root/("workflow-facts-"+str(revision)+"-"+uuid.uuid4().hex+".json");atomic_json(factfile,{"report":report,"workflow_record":_verified_ref(workflow/"workflow_run.json") if (workflow/"workflow_run.json").exists() else None})
                feedback["evidence_refs"]=[_verified_ref(factfile)]
                for check in feedback["checks"]:check["evidence_ref"]=feedback["evidence_refs"][0]
            state["last_checks"]=feedback["checks"]
            signature=sorted([x.get("stage"),x.get("status")] for x in feedback["checks"])
            if signature==state.get("last_failure_signature"):
                _check_baseline(state["baseline"]);WorkflowRunner(Path(state["workflow_spec"]),plan_only=True);preflight_runtime(runtime)
                _event(root,"repeated_failure_input_check",result="pass",checks=feedback["checks"],revision=revision)
            state["last_failure_signature"]=signature
            if revision+state.get('protocol_attempts_used',0)>=cfg["max_revisions"] or (report["status"]=="unknown" and cfg["unknown_policy"]=="stop"):
                state.update(status="limited",reason="revision_or_unknown_limit");_event(root,"limited",revision=revision);_save(root,state);return state
            state.pop("execution_hash",None);state.pop("execution_intent",None);state.pop("execution_output",None);state.pop("execution_attempt",None)
            state["pending_revision"]={"previous":previous,"feedback":feedback,"target":target};_save(root,state)
            blocked=_advance_revision(root,state,cfg,previous,feedback,target)
            if blocked is not None:return blocked

def control_run(config,run_root,env_file=None,provider=None):return _run(config,run_root,env_file,provider)
def control_resume(config,run_root,env_file=None,provider=None,resolution=None):return _run(config,run_root,env_file,provider,resolution,resume_requested=True)
