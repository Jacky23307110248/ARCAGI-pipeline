"""Shared diagnosis wire contract, validation and immutable correction receipts."""
from __future__ import annotations
import copy, hashlib, json
from pathlib import Path
from .diagnostics import Fault

VERSION = 'arcagi-diagnosis/v1'
OPERATIONS = ('rerun_declared_checks','revise_and_rerun','search','engine_replay',
              'validate_unit','adapter_smoke','validate_structure')
INSTRUCTION = ('Return a complete new artifact. diagnosis follows the supplied diagnosis_contract. '
               'next_check.operation requests a declared operation, not a pending check ID. Only machine operation receipts and check records prove execution. '
               'Copy completed_checks IDs, evidence_ref objects and observed_status values from the supplied checks. '
               'fact_refs contains complete {path,sha256} objects from evidence_refs. '
               'Keep uncertain causes unconfirmed. The returned artifact is the current revision, not a request to revise itself again. '
               'Omit revise_task_id when this artifact already addresses the feedback; downstream checks run next. '
               'Use revise_task_id only to request a different allowed stage. Planned interface bindings are implementation obligations, not executed evidence.')
LEGACY_INSTRUCTION = '\nReturn a complete new version using only declared inputs. Include diagnosis with stage, root_cause_status (confirmed/unconfirmed), candidate_causes, completed_checks [{check_id,evidence_ref,observed_status}], excluded_causes, missing_evidence, next_check object, fact_refs. Checks claimed completed must cite supplied machine check IDs and exact evidence refs. Uncertainty does not request a supervisor. If another declared stage needs revision, diagnosis.revise_task_id may explicitly request that NEXT stage from feedback.revision_targets; omit it otherwise. No invented observations or evidence.\n'

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def pinned(ref):
    if not isinstance(ref,dict) or set(ref)!={'path','sha256'} or not isinstance(ref['path'],str) or not isinstance(ref['sha256'],str) or sha(ref['path'])!=ref['sha256']:
        raise Fault('artifact_changed','diagnosis evidence changed')
    return Path(ref['path']).resolve()

def schema(allow_revision=True):
    text={'type':'string','minLength':1}
    strings={'type':'array','items':text}
    ref={'type':'object','required':['path','sha256'],'additionalProperties':False,
         'properties':{'path':text,'sha256':{'type':'string','minLength':64,'maxLength':64}}}
    properties={'stage':text,'root_cause_status':{'type':'string','enum':['confirmed','unconfirmed']},
      'candidate_causes':{**strings,'minItems':1},'excluded_causes':strings,'missing_evidence':strings,
      'completed_checks':{'type':'array','minItems':1,'items':{'type':'object','additionalProperties':False,
        'required':['check_id','evidence_ref','observed_status'],'properties':{'check_id':text,'evidence_ref':ref,'observed_status':text}}},
      'fact_refs':{'type':'array','minItems':1,'items':ref},
      'next_check':{'type':'object','required':['operation'],'additionalProperties':False,
        'properties':{'operation':{'type':'string','enum':list(OPERATIONS)},'reason':text}}}
    required=list(properties)
    if allow_revision:properties['revise_task_id']=text
    return {'type':'object','required':required,'properties':properties,'additionalProperties':False}

def feedback_for(contract):
    if not isinstance(contract,dict) or contract.get('schema_version')!=VERSION or type(contract.get('allow_stage_revision',True)) is not bool:
        raise Fault('config_invalid','invalid diagnosis contract')
    feedback=read(pinned(contract['feedback_ref']))
    if not isinstance(feedback,dict) or not isinstance(feedback.get('checks'),list) or not feedback['checks'] or not isinstance(feedback.get('evidence_refs'),list) or not feedback['evidence_refs']:raise Fault('config_invalid','diagnosis requires executed checks and evidence')
    for ref in feedback['evidence_refs']:pinned(ref)
    ids=[]
    for check in feedback['checks']:
        if not isinstance(check,dict) or not isinstance(check.get('check_id'),str) or not check['check_id'] or not isinstance(check.get('status'),str) or not check['status'] or check.get('evidence_ref') not in feedback['evidence_refs']:raise Fault('config_invalid','invalid diagnosis check context')
        ids.append(check['check_id'])
    if len(set(ids))!=len(ids):raise Fault('config_invalid','duplicate diagnosis check IDs')
    return feedback

def describe(contract):
    feedback=feedback_for(contract)
    example_ref=copy.deepcopy(feedback.get('evidence_refs',[{'path':'evidence.json','sha256':'0'*64}])[0])
    check=feedback.get('checks',[{'check_id':'check-1','status':'return','evidence_ref':example_ref}])[0]
    return {'schema_version':VERSION,'schema':schema(contract.get('allow_stage_revision',True)),
      'instructions':INSTRUCTION,'operation_meaning':'operation is an operation name; completed_checks refer to already executed checks',
      'allowed_operations':list(OPERATIONS),'checks':feedback.get('checks',[]),'evidence_refs':feedback.get('evidence_refs',[]),
      'revision_targets':feedback.get('revision_targets',[]) if contract.get('allow_stage_revision',True) else [],
      'example':{'stage':check.get('stage','workflow'),'root_cause_status':'unconfirmed',
        'candidate_causes':['A cause to investigate'],'completed_checks':[{'check_id':check['check_id'],
        'evidence_ref':check['evidence_ref'],'observed_status':check['status']}],
        'excluded_causes':[],'missing_evidence':[],'next_check':{'operation':'rerun_declared_checks'},'fact_refs':[example_ref]}}

def validate(bundle,feedback,allow_revision=True):
    from .agent_workflow import _schema_errors
    row=bundle.get('diagnosis') if isinstance(bundle,dict) else None
    issues=[{'path':e.split(':',1)[0],'code':'diagnosis_schema_invalid','detail':e,'expected':'diagnosis_contract.schema'}
            for e in _schema_errors(row,schema(allow_revision),'diagnosis')]
    if not isinstance(row,dict):return issues
    checks={c['check_id']:c for c in feedback.get('checks',[])}
    for i,c in enumerate(row.get('completed_checks',[]) if isinstance(row.get('completed_checks'),list) else []):
        if not isinstance(c,dict):continue
        actual=checks.get(c.get('check_id')) if isinstance(c.get('check_id'),str) else None
        if actual is not None and actual.get('executed') is False:
            issues.append({'path':f'diagnosis.completed_checks[{i}].check_id','code':'diagnosis_check_not_executed','expected':'actual execution, not a blocked prerequisite'})
        if actual is None:issues.append({'path':f'diagnosis.completed_checks[{i}].check_id','code':'diagnosis_check_not_executed','expected':list(checks)})
        else:
            for field,expected in (('evidence_ref',actual.get('evidence_ref')),('observed_status',actual.get('status'))):
                if c.get(field)!=expected:issues.append({'path':f'diagnosis.completed_checks[{i}].{field}','code':'diagnosis_check_not_executed','expected':expected})
    refs=row.get('fact_refs',[])
    if isinstance(refs,list):
        for i,ref in enumerate(refs):
            if ref not in feedback.get('evidence_refs',[]):issues.append({'path':f'diagnosis.fact_refs[{i}]','code':'diagnosis_fact_ref_invalid','expected':feedback.get('evidence_refs',[])})
            else:pinned(ref)
    proposed=row.get('revise_task_id')
    if proposed is not None and (not allow_revision or not isinstance(proposed,str) or proposed not in feedback.get('revision_targets',[])):
        issues.append({'path':'diagnosis.revise_task_id','code':'invalid_revision_target','expected':feedback.get('revision_targets',[]) if allow_revision else []})
    return issues

def payload_schema(task):
    value=copy.deepcopy(task['output_schema'])
    if task.get('diagnosis_contract'):
        value['required']=[x for x in value.get('required',[]) if x!='diagnosis']
        value.setdefault('properties',{}).pop('diagnosis',None)
    return value

def payload_value(value,task):
    return {k:v for k,v in value.items() if k!='diagnosis'} if task.get('diagnosis_contract') and isinstance(value,dict) else value

def output_schema(task):
    value=copy.deepcopy(task['output_schema'])
    if task.get('diagnosis_contract'):
        value.setdefault('properties',{})['diagnosis']=schema(task['diagnosis_contract'].get('allow_stage_revision',True))
        value['required']=list(dict.fromkeys(value.get('required',[])+['diagnosis']))
    return value

def effective_bundle(artifact,contract,receipt_ref=None):
    path=Path(artifact).resolve();value=read(path)
    if not receipt_ref:return value
    receipt=read(pinned(receipt_ref))
    if receipt.get('schema_version')!='arcagi-diagnosis-correction/v1' or receipt.get('artifact',{}).get('sha256')!=sha(path) or receipt.get('contract_sha256')!=digest(contract):raise Fault('artifact_changed')
    pinned(receipt['artifact'])
    output=pinned(receipt['correction_artifact']);origin=receipt['workflow_origin']
    record=read(pinned(origin['record']));spec=read(pinned(origin['spec']))
    if record.get('status')!='complete' or record.get('spec_sha256')!=origin['spec']['sha256']:raise Fault('artifact_changed')
    row=next((r for r in record['tasks'] if r['id']==origin['task_id']),None)
    if not row or row['status']!='complete' or (Path(origin['record']['path']).parent/row['output_path']).resolve()!=output or row['output_sha256']!=sha(output):raise Fault('artifact_changed')
    correction=read(output)
    if validate(correction,feedback_for(contract),False):raise Fault('artifact_changed','invalid diagnosis correction receipt')
    return {**value,'diagnosis':correction['diagnosis']}
