"""Deterministic evidence packing and bounded diagnostic-operation scheduling."""
import copy,hashlib,json

OPERATIONS=('rerun_declared_checks','revise_and_rerun','search','engine_replay','validate_unit','adapter_smoke','validate_structure')

def bind_search_resources(state,producer):
    if state.get('search_resource_producer')!=producer:
        state['search_resource_producer']=producer;state['search_cap_index']=0

def compact(value,limit=256):
    if isinstance(value,str) and len(value)>limit:return {'preview':value[:limit],'length':len(value),'truncated':True}
    if isinstance(value,list):return [compact(x,limit) for x in value]
    if isinstance(value,dict):return {k:compact(v,limit) for k,v in value.items()}
    return value

def feedback_rows(rows):
    """Keep every failure identity/reason/path; compress successful detail first."""
    out=[]
    for row in rows:
        item={'task_id':row['task_id'],'status':row.get('status')}
        for key in ('checks','search','replay','execution_error','blocked_by','level_smoke'):
            if key in row:item[key]=compact(row[key])
        for key in ('fulfillment','obligations'):
            if key in row:
                item[key]={}
                for oid,result in row[key].items():
                    if isinstance(result,dict) and result.get('status')=='pass':
                        item[key][oid]={'status':'pass'}
                    else:
                        item[key][oid]=compact(result)
        if row.get('probes'):
            item['probe_outcomes']={name:compact({k:v for k,v in value.items() if k in ('status','reason','artifact_path','artifact_sha256','exception','worker_error','partial_refs')}) for name,value in row['probes'].items()}
        out.append(item)
    return out

def operation_receipt(operation,production,contract=None):
    """Select actual machine outcomes only, never a model's claim of execution."""
    if operation not in OPERATIONS or contract=='design-production/v1':return {'operation':operation,'status':'unsupported','check_ids':[]}
    outcomes=[]
    for row in production.get('tasks',[]):
        tid=row['task_id']
        if operation in ('search','rerun_declared_checks','revise_and_rerun') and row.get('search'):
            outcomes.append({'check_id':tid+':model_search','status':row['search']['status'],'limit_kind':row['search'].get('evidence',{}).get('limit_kind')})
        if operation in ('engine_replay','rerun_declared_checks','revise_and_rerun') and row.get('replay'):
            outcomes.append({'check_id':tid+':engine_replay','status':'pass' if row['replay']['accepted'] else 'return'})
        if operation in ('validate_structure','rerun_declared_checks','revise_and_rerun') and row.get('checks',{}).get('structure'):
            outcomes.append({'check_id':tid+':structure','status':row['checks']['structure']})
        if operation=='adapter_smoke' and row.get('level_smoke'):
            outcomes.append({'check_id':tid+':level_smoke','status':row['level_smoke']['status']})
    for unit in production.get('units',[]):
        for stage,op in (('unit_execution','validate_unit'),('adapter_smoke','adapter_smoke')):
            result=unit.get(stage,{})
            if operation in (op,'rerun_declared_checks','revise_and_rerun') and result and result.get('reason')!='verified_candidates_missing':outcomes.append({'check_id':unit['unit_id']+':'+stage,'status':result['status']})
    targets={
        'validate_structure':{'structure'},'search':{'model_search'},
        'engine_replay':{'engine_replay'},'validate_unit':{'unit_execution'},
        'adapter_smoke':{'adapter_smoke','level_smoke'},
    }.get(operation)
    ordered=production.get('execution_order',[])
    if 'execution_order' in production:
        details={x['check_id']:x for x in outcomes}
        outcomes=[{**details.get(x['check_id'],{}),**x} for x in ordered
                  if x['executed'] and (targets is None or x['stage'] in targets)]
    plan=operation_plan([{'task_id':'','operation':operation}])[0]
    blocked=[x for x in ordered if not x['executed'] and (targets is None or x['stage'] in plan['stages'])]
    return {'blocked_checks':blocked,'operation':operation,'status':'executed'  if outcomes else 'prerequisite_missing','check_ids':[x['check_id'] for x in outcomes],'results':outcomes,
            'outcome':'resource_limited' if any(x.get('limit_kind') for x in outcomes) else 'unresolved' if any(x['status']=='unknown' for x in outcomes) else 'completed' if outcomes else 'not_executed',
            'reason':None if outcomes else 'No corresponding machine execution evidence; satisfy declared prerequisites first.'}

def operation_plan(requests,contract='generated-game/v2'):
    stages={'validate_structure':['structure'],'search':['structure','model_search'],'engine_replay':['structure','model_search','engine_replay'],
            'validate_unit':['structure','model_search','engine_replay','unit_execution'],'adapter_smoke':['adapter_smoke','level_smoke'],
            'rerun_declared_checks':['required_pipeline'],'revise_and_rerun':['required_pipeline']}
    return [{'task_id':r['task_id'],'operation':r['operation'],'status':'scheduled' if contract=='generated-game/v2' and r['operation'] in stages else 'unsupported',
             'stages':stages.get(r['operation'],[]),'mandatory_pipeline_retained':True} for r in requests]

def producer_identity(workflow_outputs,runtime_hash,schedule):
    return hashlib.sha256(json.dumps({'outputs':workflow_outputs,'runtime':runtime_hash,'schedule':schedule},sort_keys=True).encode()).hexdigest()


def safe_exception(exc):
    from .diagnostics import exception_facts
    facts=exception_facts(exc)
    fields={'id','slot_id','behavior_requirements','production_target','game_function','configuration','goal','evidence_refs','function_anchors'}
    if isinstance(exc,KeyError) and len(exc.args)==1 and isinstance(exc.args[0],str) and exc.args[0] in fields:facts['missing_protocol_field']=exc.args[0]
    if getattr(exc,'diagnostic_code',None):facts['code']=exc.diagnostic_code
    return facts



def compact_feedback_fragment(fragment):
    """Drop optional successful detail before dropping any failure identity."""
    result=copy.deepcopy(fragment)
    for row in result.get('observations',[]):
        search=row.get('search',{})
        if 'actions' in search:
            actions=search.pop('actions')
            search['action_count']=len(actions)
        if row.get('replay',{}).get('accepted') is True:
            row['replay']={'accepted':True}
        for key in ('obligations','fulfillment'):
            for check in row.get(key,{}).values():
                if isinstance(check,dict) and check.get('status')=='pass':
                    for detail in tuple(check):
                        if detail!='status':
                            check.pop(detail)
    return result
