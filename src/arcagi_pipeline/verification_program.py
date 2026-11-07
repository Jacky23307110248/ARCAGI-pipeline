"""Closed validation programs over machine-owned named traces."""
from __future__ import annotations
import math
from .diagnostics import Fault

LEGACY_VERSION='arcagi-validation-program/v1'
VERSION='arcagi-validation-program/v2'
VERSIONS={LEGACY_VERSION,VERSION}
CAPABILITIES={
 'version':VERSION,
 'operations':['literal','read','count','eq','ne','lt','le','gt','ge','contains','subset','and','or','not','sequence','event_ref'],
 'executions':['candidate','behavior','reset_failure','counterfactual','unit','smoke','level_smoke'],
 'phases':['implementation','runtime','research'],
 'proof_scopes':['observed_witness','trace_comparison','declared_comparison']}
CAPABILITIES['executable_proof_scopes']=['observed_witness','trace_comparison','declared_comparison']
CAPABILITIES['non_executable_proof_scopes']=['finite_model_exhaustion','research_hypothesis']
CAPABILITIES['design_checks']=['reference_integrity','capability_compile','function_anchor_coverage','commitment_change_history']
ARITY={'count':1,'not':1,'eq':2,'ne':2,'lt':2,'le':2,'gt':2,'ge':2,'contains':2,'subset':2}

class UnsupportedProgram(Fault):
 def __init__(self,message):super().__init__('output_protocol_invalid',message)
class MissingEvidence(ValueError):pass

def validate(expression,trace_ids,depth=0):
 """Validate before execution; no arbitrary Python, methods or implicit reads."""
 if depth>32 or not isinstance(expression,dict):raise UnsupportedProgram('expression_depth_or_type')
 op=expression.get('op')
 if not isinstance(op,str) or op not in CAPABILITIES['operations']:raise UnsupportedProgram('unsupported_operation:'+str(op))
 if op=='literal':
  if set(expression)!={'op','value'}:raise UnsupportedProgram('literal_fields')
  import json
  try:json.dumps(expression['value'],allow_nan=False)
  except (ValueError,TypeError,RecursionError):raise UnsupportedProgram('finite_json_literal_required') from None
  return
 if op=='read':
  if set(expression)!={'op','trace','path'}:raise UnsupportedProgram('read_fields')
  if expression['trace'] not in trace_ids:raise UnsupportedProgram('undeclared_trace')
  path=expression['path']
  if not isinstance(path,list) or any(type(p) not in (str,int) for p in path):raise UnsupportedProgram('read_path')
  return
 if op=='event_ref':
  if set(expression)!={'op','trace','index'} or expression['trace'] not in trace_ids or type(expression['index']) is not int or expression['index']<0:raise UnsupportedProgram('event_reference_fields')
  return
 if op=='sequence':
  if set(expression)!={'op','items'} or not isinstance(expression['items'],list) or not expression['items']:raise UnsupportedProgram('sequence_fields')
  for row in expression['items']:validate(row,trace_ids,depth+1)
  return
 args=expression.get('args')
 if set(expression)!={'op','args'} or not isinstance(args,list) or len(args)>128:raise UnsupportedProgram('operator_fields')
 if op in ARITY and len(args)!=ARITY[op]:raise UnsupportedProgram('operator_arity')
 if op in ('and','or') and not args:raise UnsupportedProgram('boolean_arity')
 for arg in args:validate(arg,trace_ids,depth+1)
 for arg in args:
  if arg.get('op')=='literal':
   value=arg['value']
   if op in ('and','or','not') and type(value) is not bool:raise UnsupportedProgram('boolean_operand_type')
   if op in ('lt','le','gt','ge') and (type(value) not in (int,float) or not math.isfinite(value)):raise UnsupportedProgram('numeric_operand_type')
 if op=='count' and args[0].get('op')=='literal' and not isinstance(args[0]['value'],(list,dict,str)):raise UnsupportedProgram('count_operand_type')

def evaluate(expression,traces,version=LEGACY_VERSION):
 """Return a value using only supplied machine evidence; missing data is explicit."""
 op=expression['op']
 if op=='literal':return expression['value']
 if op=='read':
  try:
   value=traces[expression['trace']]
   for part in expression['path']:value=value[part]
   return value
  except (KeyError,IndexError,TypeError):raise MissingEvidence('declared_path_unavailable') from None
 if op=='event_ref':
  try:
   trace=traces[expression['trace']];event=trace['trajectory'][expression['index']]
   sid=trace['session_id'];index=event['event_index']
   if not isinstance(sid,str) or not sid or type(index) is not int or index<0:raise MissingEvidence('machine_event_identity_required')
   return (sid,index)
  except (KeyError,IndexError,TypeError):raise MissingEvidence('machine_event_unavailable') from None
 if op=='sequence':
  values=[evaluate(x,traces,version) for x in expression['items']]
  if version==VERSION:
   if any(x.get('op')!='event_ref' for x in expression['items']) or any(not isinstance(v,tuple) or len(v)!=2 for v in values):raise MissingEvidence('sequence_requires_machine_events')
   if len({v[0] for v in values})!=1:raise MissingEvidence('events_not_same_session')
   return all(a[1]<b[1] for a,b in zip(values,values[1:]))
  if any(type(x) is not int or x<0 for x in values):raise MissingEvidence('sequence_requires_event_indices')
  return all(a<b for a,b in zip(values,values[1:]))
 args=[evaluate(x,traces,version) for x in expression['args']]
 if op in ('and','or','not'):
  if any(type(x) is not bool for x in args):raise MissingEvidence('boolean_required')
  return all(args) if op=='and' else any(args) if op=='or' else not args[0]
 if op=='count':
  if not isinstance(args[0],(list,dict,str)):raise MissingEvidence('count_requires_collection')
  return len(args[0])
 if op in ('eq','ne'):return (args[0]==args[1]) if op=='eq' else (args[0]!=args[1])
 if op in ('lt','le','gt','ge'):
  if any(type(x) not in (int,float) or not math.isfinite(x) for x in args):raise MissingEvidence('finite_numbers_required')
  a,b=args
  return a<b if op=='lt' else a<=b if op=='le' else a>b if op=='gt' else a>=b
 if op=='contains':
  if not isinstance(args[0],(list,dict,str)):raise MissingEvidence('contains_requires_collection')
  return args[1] in args[0]
 if op=='subset':
  if any(not isinstance(x,list) for x in args):raise MissingEvidence('subset_requires_lists')
  return all(x in args[1] for x in args[0])
 raise UnsupportedProgram('unsupported_operation')

def check(expression,traces,declared_traces=None,scope='target',version=LEGACY_VERSION):
 reads=[]
 def summary(value,depth=0):
  if depth>3:return {'omitted':'depth'}
  if isinstance(value,list):return [summary(v,depth+1) for v in value[:8]]+([{'omitted_items':len(value)-8}] if len(value)>8 else [])
  if isinstance(value,dict):return {k:summary(v,depth+1) for k,v in list(value.items())[:16]}
  if isinstance(value,str) and len(value)>1024:return value[:1024]+'...'
  return value
 def collect(node,views):
  if node.get('op')=='read':
   try:value=evaluate(node,views);reads.append({'trace':node['trace'],'path':node['path'],'value':summary(value)})
   except MissingEvidence:reads.append({'trace':node['trace'],'path':node['path'],'missing':True})
  for child in node.get('args',node.get('items',[])):collect(child,views)
 try:
  traces={name:{**trace,'trajectory':[{**row,'observation':{k:v for k,v in row.get('observation',{}).items() if scope=='unit' or k!='projected' or row.get('engine_position')==trace.get('target_position')}} for row in trace.get('trajectory',[])]} for name,trace in traces.items() if declared_traces is None or name in declared_traces}
  validate(expression,set(traces) if declared_traces is None else set(declared_traces))
  collect(expression,traces)
  value=evaluate(expression,traces,version)
  if type(value) is not bool:raise MissingEvidence('check_requires_boolean')
  return {'status':'pass' if value else 'return','reason':'evaluated_machine_evidence','evaluated_reads':reads,'expression':expression,
          'trace_refs':{n:{k:t[k] for k in ('session_id','configuration_sha256','source_sha256','artifact_path','artifact_sha256') if k in t} for n,t in traces.items()}}
 except UnsupportedProgram as exc:return {'status':'unsupported','reason':str(exc)}
 except MissingEvidence as exc:return {'status':'unknown','reason':str(exc),'evaluated_reads':reads}
 except (TypeError,ValueError,OverflowError):return {'status':'unknown','reason':'evidence_type_mismatch'}

def result_type(node):
 op=node.get('op')
 if op=='read':return 'unknown'
 if op=='event_ref':return 'event'
 if op=='count':return 'number'
 if op=='literal':
  v=node.get('value')
  return 'boolean' if type(v) is bool else 'number' if type(v) in (int,float) else 'collection' if isinstance(v,(list,dict,str)) else 'value'
 return 'boolean'

def validate_result_types(node):
 op=node.get('op');args=node.get('args',[])
 expected='boolean' if op in ('and','or','not') else 'number' if op in ('lt','le','gt','ge') else 'collection' if op=='count' else None
 if expected and any(result_type(a) not in (expected,'unknown') for a in args):raise UnsupportedProgram('operator_result_type_mismatch')
 for child in args+node.get('items',[]):validate_result_types(child)

def validate_event_order(node):
 if node.get('op')=='sequence':
  items=node['items']
  if len(items)<2 or any(x.get('op')!='event_ref' for x in items):raise UnsupportedProgram('sequence_requires_machine_events')
  if len({x['trace'] for x in items})!=1:raise UnsupportedProgram('sequence_requires_single_timeline')
 for child in node.get('args',node.get('items',[])):validate_event_order(child)

def valid_prior(value):
 if not isinstance(value,dict) or not isinstance(value.get('slots'),list):return False
 collections=[value['slots'],value.get('function_anchors',[])]
 for slot in value['slots']:
  if not isinstance(slot,dict):return False
  collections.extend([slot.get('behavior_requirements',[]),slot.get('validation_obligations',[])])
 return all(isinstance(rows,list) and all(isinstance(r,dict) and isinstance(r.get('id'),str) and r['id'] for r in rows) for rows in collections)

def compile_obligations(blueprint,strict=False):
 """Compile optional v1 programs; legacy behavior requirements remain supported."""
 errors=[]
 if not isinstance(blueprint,dict) or not isinstance(blueprint.get('slots'),list):return [{'code':'blueprint_slots_required'}]
 version=blueprint.get('validation_program_version')
 if version is not None and (not isinstance(version,str) or version not in VERSIONS):return [{'code':'validation_program_version'}]
 strict=strict or version in VERSIONS
 anchors={}
 if strict:
  if blueprint.get('validation_program_version') not in VERSIONS:errors.append({'code':'validation_program_version'})
  if type(blueprint.get('commitment_version')) is not int or blueprint.get('commitment_version',0)<1:errors.append({'code':'commitment_version'})
  values=blueprint.get('function_anchors',[])
  if not isinstance(values,list) or not values:errors.append({'code':'original_function_anchors_required'});values=[]
  for value in values:
   if not isinstance(value,dict) or not isinstance(value.get('id'),str) or not value['id'] or value['id'] in anchors or not value.get('statement') or not isinstance(value.get('evidence_refs'),list) or not value['evidence_refs'] or any(not isinstance(e,str) for e in value['evidence_refs']):errors.append({'code':'function_anchor_invalid'});continue
   anchors[value['id']]=value
 selected_anchors=set()
 for slot in blueprint.get('slots',[]):
  if not isinstance(slot,dict):errors.append({'code':'slot_object_required'});continue
  lists={}
  for key in ('probe_plan','function_anchor_ids','behavior_requirements','validation_obligations'):
   value=slot.get(key,[])
   if not isinstance(value,list):errors.append({'slot':slot.get('id'),'code':key+'_must_be_list'});value=[]
   lists[key]=value
  check_ids=set()
  for row in lists['behavior_requirements']+lists['validation_obligations']:
   rid=row.get('id') if isinstance(row,dict) else None
   if not isinstance(rid,str) or not rid or rid in check_ids:errors.append({'slot':slot.get('id'),'code':'duplicate_or_invalid_check_id'})
   else:check_ids.add(rid)
  registry={name:{'id':name,'kind':name} for name in ('candidate','unit','smoke','level_smoke')}
  for plan in lists['probe_plan']:
   if not isinstance(plan,dict) or not isinstance(plan.get('id'),str) or not plan['id'] or plan['id'] in registry or plan.get('kind') not in ('behavior','reset_failure','counterfactual'):
    errors.append({'slot':slot.get('id'),'code':'probe_plan_invalid'});continue
   registry[plan['id']]=plan
  for plan in registry.values():
   if plan['kind']=='counterfactual' and (not isinstance(plan.get('baseline'),str) or plan.get('baseline') not in registry or registry[plan['baseline']]['kind'] not in ('behavior','reset_failure')):errors.append({'slot':slot.get('id'),'code':'counterfactual_baseline_plan_required'})
  if strict and (not isinstance(slot.get('function_anchor_ids'),list) or not slot['function_anchor_ids'] or any(not isinstance(a,str) or a not in anchors for a in slot['function_anchor_ids'])):errors.append({'slot':slot.get('id'),'code':'function_anchor_coverage'})
  if strict:
   selected_anchors.update(a for a in lists['function_anchor_ids'] if isinstance(a,str))
   evidence=slot.get('evidence_refs',[])
   if not isinstance(evidence,list):evidence=[]
   for aid in lists['function_anchor_ids']:
    if isinstance(aid,str) and aid in anchors and any(ref not in evidence for ref in anchors[aid]['evidence_refs']):errors.append({'slot':slot.get('id'),'code':'function_anchor_evidence_not_in_slot','anchor_id':aid})
   covered=set()
   for claim in lists['behavior_requirements']+[r for r in lists['validation_obligations'] if isinstance(r,dict) and r.get('required') and r.get('phase')!='research']:
    refs=claim.get('anchor_refs',[]) if isinstance(claim,dict) else []
    if not isinstance(refs,list) or any(not isinstance(a,str) or a not in lists['function_anchor_ids'] for a in refs):errors.append({'slot':slot.get('id'),'code':'claim_anchor_reference'})
    else:covered.update(refs)
   if any(a not in covered for a in lists['function_anchor_ids'] if isinstance(a,str)):errors.append({'slot':slot.get('id'),'code':'required_function_has_no_check'})
  for legacy in lists['behavior_requirements']:
   if not isinstance(legacy,dict) or legacy.get('kind')!='behavior' or legacy.get('predicate') not in ('equals','changes','contains') or not isinstance(legacy.get('observation_path'),list):
    errors.append({'slot':slot.get('id'),'code':'unsupported_legacy_behavior_requirement'})
   elif any(type(p) not in (str,int) for p in legacy['observation_path']) or legacy['predicate'] in ('equals','contains') and 'value' not in legacy:errors.append({'slot':slot.get('id'),'code':'legacy_behavior_arguments'})
  plans=lists['validation_obligations']
  if not isinstance(plans,list):errors.append({'slot':slot.get('id'),'code':'obligations_must_be_list'});continue
  seen=set()
  for row in plans:
   try:
    if not isinstance(row,dict) or not isinstance(row.get('id'),str) or not row['id'] or row['id'] in seen:raise UnsupportedProgram('obligation_id')
    seen.add(row['id'])
    if row.get('phase') not in CAPABILITIES['phases']:raise UnsupportedProgram('obligation_phase')
    if type(row.get('required')) is not bool:raise UnsupportedProgram('obligation_required_flag')
    if row.get('function_ref')!=slot.get('id') or not isinstance(row.get('evidence_refs'),list) or not row['evidence_refs'] or any(not isinstance(e,str) for e in row['evidence_refs']) or not set(row['evidence_refs'])<=set(slot.get('evidence_refs',[])):raise UnsupportedProgram('obligation_function_basis')
    if row.get('phase')=='research':
     if row['required']:raise UnsupportedProgram('research_not_production_proof')
     continue
    if row.get('proof_scope') not in CAPABILITIES['executable_proof_scopes']:raise UnsupportedProgram('proof_scope_not_executable')
    traces=row.get('traces')
    if not isinstance(traces,list) or not traces or any(not isinstance(t,str) for t in traces):raise UnsupportedProgram('declared_traces_required')
    if len(set(traces))!=len(traces) or not set(traces)<=set(registry):raise UnsupportedProgram('trace_plan_not_declared')
    if row.get('observation_scope','target') not in ('target','unit') or row.get('observation_scope')=='unit' and not set(traces)<={'unit','smoke'}:raise UnsupportedProgram('invalid_unit_observation_scope')
    if blueprint.get('validation_program_version')==VERSION and 'smoke' in traces and row.get('observation_scope')!='unit':raise UnsupportedProgram('unit_smoke_requires_unit_scope')
    validate(row['expression'],set(traces))
    validate_result_types(row['expression'])
    if result_type(row['expression']) not in ('boolean','unknown'):raise UnsupportedProgram('check_requires_boolean')
    if blueprint.get('validation_program_version')==VERSION:validate_event_order(row['expression'])
    def reads(node):return node.get('op') in ('read','event_ref') or any(reads(x) for x in node.get('args',node.get('items',[])))
    if not reads(row['expression']):raise UnsupportedProgram('machine_evidence_read_required')
    def trace_reads(node):return {node['trace']} if node.get('op') in ('read','event_ref') else set().union(*(trace_reads(c) for c in node.get('args',node.get('items',[]))))
    if trace_reads(row['expression'])!=set(traces):raise UnsupportedProgram('declared_traces_must_be_read')
    variants=[t for t in traces if registry[t]['kind']=='counterfactual']
    if row['proof_scope']=='declared_comparison':
     if len(traces)!=2 or len(variants)!=1 or registry[variants[0]].get('baseline') not in traces:raise UnsupportedProgram('configuration_contrast_pair_required')
    elif variants:raise UnsupportedProgram('variant_requires_declared_comparison_scope')
    elif row['proof_scope']=='trace_comparison' and len(traces)<2:raise UnsupportedProgram('trace_comparison_needs_two_sources')
   except (UnsupportedProgram,KeyError,TypeError,ValueError) as exc:errors.append({'slot':slot.get('id'),'obligation_id':row.get('id') if isinstance(row,dict) else None,'code':str(exc)})
 if strict:
  for aid in anchors:
   if aid not in selected_anchors:errors.append({'code':'original_function_anchor_unassigned','anchor_id':aid})
 return errors

def commitment_changes(previous,current):
 """Structural change accountability; semantic equivalence remains model review."""
 if not valid_prior(previous):return []
 old={s['id']:s for s in previous.get('slots',[])};new={s['id']:s for s in current.get('slots',[])}
 changes=[]
 old_anchors={a['id']:a for a in previous.get('function_anchors',[])}
 new_anchors={a['id']:a for a in current.get('function_anchors',[])}
 for aid,row in old_anchors.items():
  if new_anchors.get(aid)!=row:changes.append({'slot_id':None,'anchor_id':aid,'kind':'original_anchor_changed'})
 for sid,slot in old.items():
  replacement=new.get(sid)
  if replacement is None or slot.get('game_function')!=replacement.get('game_function'):
   changes.append({'slot_id':sid,'kind':'function_changed'})
  for name in ('behavior_requirements','validation_obligations'):
   prior={r['id']:r for r in slot.get(name,[])}
   after={r['id']:r for r in (replacement or {}).get(name,[])}
   for rid,row in prior.items():
    if rid not in after or after[rid]!=row:changes.append({'slot_id':sid,'obligation_id':rid,'kind':name+'_changed'})
 notes=current.get('commitment_changes',[])
 for change in changes:
  if not any(isinstance(n,dict) and n.get('slot_id')==change['slot_id'] and n.get('anchor_id')==change.get('anchor_id') and n.get('obligation_id')==change.get('obligation_id') and isinstance(n.get('reason'),str) and n['reason'].strip() for n in notes):
   change['unacknowledged']=True
 if changes and current.get('validation_program_version') in VERSIONS and current.get('commitment_version',0)<=previous.get('commitment_version',0):changes.append({'kind':'commitment_version_not_advanced','unacknowledged':True})
 return changes

def _run_probes_in_process(unit,index,fresh,compiled,expected,unit_hash,source_sha,runtime_id,cap,output_dir,compile_variant=None,session_id=None):
 """Every named probe owns a fresh instance and immutable trace file."""
 import copy,json,time,uuid
 from dataclasses import replace
 from .generated_game import NativeDriver,level_configuration,initialize,digest
 traces={}
 level=unit.levels[index]
 for probe in level.probes:
  name=probe['id'];sid=session_id or uuid.uuid4().hex;path=output_dir/('probe-'+sid+'.json')
  journal=output_dir/('probe-'+sid+'.jsonl');journal.open('x',encoding='utf-8').close();written=0
  record={'id':name,'session_id':sid,'instance_id':uuid.uuid4().hex,'source_sha256':source_sha,
          'runtime_identity':runtime_id,'unit_artifact_sha256':unit_hash,'target_position':index,'status':'running','trajectory':[]}
  def save(final=False):
   nonlocal written
   with journal.open('a',encoding='utf-8') as stream:
    for event in record['trajectory'][written:]:stream.write(json.dumps({'kind':'observation','event':event},ensure_ascii=False,allow_nan=False)+'\n')
    written=len(record['trajectory'])
    if final:stream.write(json.dumps({'kind':'outcome','status':record['status'],'reason':record.get('reason')})+'\n')
   if final:
    import hashlib
    record.update(journal_path=str(journal),journal_sha256=hashlib.sha256(journal.read_bytes()).hexdigest())
    with path.open('x',encoding='utf-8') as stream:json.dump(record,stream,ensure_ascii=False,allow_nan=False,indent=2)
  started=time.monotonic()
  try:
   variant=probe.get('variant');levels=list(unit.levels);target=level
   values=compiled;want=expected
   if variant:
    if set(variant)-{'configuration','initializations','observation_bindings'}:raise UnsupportedProgram('variant_fields')
    target=replace(level,configuration=copy.deepcopy(variant['configuration']),initializations=tuple(variant.get('initializations',level.initializations)),observation_bindings=copy.deepcopy(variant.get('observation_bindings',level.observation_bindings)),probes=())
    levels[index]=target;changed=replace(unit,levels=tuple(levels))
    if unit.profile=='raw-arcengine':
     values=compile_variant(copy.deepcopy(changed)) if compile_variant is not None else None
     # A compile callback is supplied by the runner, not invented source discovery.
     if values is None:raise UnsupportedProgram('variant_compile_callback_missing')
     want=[l.configuration['engine_level'] for l in levels]
     if [level_configuration(x) for x in values]!=want:raise UnsupportedProgram('variant_compilation_mismatch')
    else:values=[copy.deepcopy(l.configuration) for l in levels];want=copy.deepcopy(values)
    if digest(want)==digest(expected):raise UnsupportedProgram('counterfactual_configuration_unchanged')
   d=fresh();d.install(values);d.reset()
   if isinstance(d,NativeDriver):d.observation_bindings_by_position={i:copy.deepcopy(l.observation_bindings) for i,l in enumerate(levels)}
   elif any(l.observation_bindings for l in levels):raise UnsupportedProgram('observation_bindings_require_native_profile')
   if d.configuration()!=want:raise UnsupportedProgram('probe_configuration_mismatch')
   d.select(index);initialize(d,target.initializations)
   record.update(configuration_sha256=digest(want),variant_sha256=digest(target.configuration),goal_sha256=digest(target.goal),declared_probe=copy.deepcopy(probe))
   record['trajectory'].append({'op':'reset_select','event_index':0,'engine_position':d.position(),'observation':d.observe()});save()
   steps=probe['steps']
   limit=min(cap,probe.get('max_steps',cap));seconds=probe.get('max_elapsed_seconds',30)
   if len(steps)>limit:raise MissingEvidence('probe_step_limit')
   for n,step in enumerate(steps,1):
    if time.monotonic()-started>=seconds:raise MissingEvidence('probe_elapsed_limit')
    if step['op']=='reset':d.reset();d.select(index);initialize(d,target.initializations);observation=d.observe()
    else:observation=d.step(copy.deepcopy(step['action']))
    record['trajectory'].append({'op':step['op'],'event_index':n,'engine_position':d.position(),'observation':observation});save()
   if time.monotonic()-started>=seconds:raise MissingEvidence('probe_elapsed_limit')
   if d.configuration()!=want:raise UnsupportedProgram('probe_configuration_drift')
   record['goal_accepted']=d.goal(target.goal)
   record['status']='complete'
  except MissingEvidence as exc:record.update(status='unknown',reason=str(exc))
  except Exception as exc:
   from .diagnostics import exception_facts
   record.update(status='return',reason=str(exc) if isinstance(exc,Fault) else type(exc).__name__,exception=exception_facts(exc))
  if record['status']!='complete':
   from .diagnostics import current
   if current() is not None:record['diagnostic_ref']=current().record('probe_limited' if record['status']=='unknown' else 'probe_execution_failed','validation_probe',kind='limited_result' if record['status']=='unknown' else 'error',observed={'probe_id':name,'reason':record.get('reason')})
  save(final=True)
  import hashlib
  record.update(artifact_path=str(path),artifact_sha256=hashlib.sha256(path.read_bytes()).hexdigest());traces[name]=record
 return traces

def validate_probes(level):
 ids={'candidate','unit','smoke','level_smoke'}
 for p in level.probes:
  if not isinstance(p,dict) or not isinstance(p.get('id'),str) or not p['id'] or p['id'] in ids:raise UnsupportedProgram('probe_id')
  ids.add(p['id'])
  if p.get('kind') not in ('behavior','reset_failure','counterfactual'):raise UnsupportedProgram('probe_kind')
  if p.get('kind')=='counterfactual' and (not isinstance(p.get('variant'),dict) or 'configuration' not in p['variant']):raise UnsupportedProgram('counterfactual_variant_required')
  if p.get('kind')!='counterfactual' and p.get('variant') is not None:raise UnsupportedProgram('variant_requires_counterfactual_kind')
  if not isinstance(p.get('steps'),list):raise UnsupportedProgram('probe_steps')
  if type(p.get('max_steps',1)) is not int or p.get('max_steps',1)<1:raise UnsupportedProgram('probe_budget')
  seconds=p.get('max_elapsed_seconds',30)
  if type(seconds) not in (int,float) or not math.isfinite(seconds) or seconds<=0:raise UnsupportedProgram('probe_time_budget')
  for step in p['steps']:
   if not isinstance(step,dict) or step.get('op') not in ('reset','step'):raise UnsupportedProgram('probe_operation')
   if step['op']=='step' and 'action' not in step:raise UnsupportedProgram('probe_action')
   if set(step)!=({'op','action'} if step['op']=='step' else {'op'}):raise UnsupportedProgram('probe_step_fields')
 actual={p['id']:p for p in level.probes}
 for p in level.probes:
  if p['kind']=='counterfactual':
   baseline=actual.get(p.get('baseline'))
   if not baseline or baseline['kind']=='counterfactual' or baseline['steps']!=p['steps']:raise UnsupportedProgram('counterfactual_matched_baseline_required')
 return ids

def run_probes(unit,index,fresh,compiled,expected,unit_hash,source_sha,runtime_id,cap,output_dir,compile_variant=None,recipe=None):
 """A separate stdlib guardian enforces each probe's absolute deadline.

 Even if the enclosing worker is interrupted, the guardian kills its engine
 subprocess at the deadline. These are local processes, not security sandboxes.
 """
 import sys,os,json,time,uuid,subprocess,hashlib
 from dataclasses import asdict,replace
 from pathlib import Path
 from .runtime_control import _env
 from .diagnostics import current
 if recipe is None:raise UnsupportedProgram('isolated_probe_recipe_required')
 results={};core_root=str(Path(__file__).resolve().parents[1])
 for probe in unit.levels[index].probes:
  sid=uuid.uuid4().hex;request=output_dir/('probe-request-'+sid+'.json');response=output_dir/('probe-response-'+sid+'.json')
  levels=list(unit.levels);levels[index]=replace(levels[index],probes=(probe,));one=replace(unit,levels=tuple(levels))
  body={'unit':asdict(one),'index':index,'expected':expected,'unit_hash':unit_hash,'source_sha':source_sha,'runtime_id':runtime_id,
        'cap':cap,'output_dir':str(output_dir.resolve()),'recipe':recipe,'session_id':sid}
  request.write_text(json.dumps(body,ensure_ascii=False,allow_nan=False),encoding='utf-8')
  bootstrap="import sys,runpy;sys.path[:0]="+repr([core_root]+recipe.get('import_roots',[]))+";sys.argv=['probe',"+repr(str(request.resolve()))+","+repr(str(response.resolve()))+"];runpy.run_module('arcagi_pipeline._probe_worker',run_name='__main__')"
  seconds=probe.get('max_elapsed_seconds',30);guard_request=output_dir/('probe-guardian-'+sid+'.json');guard_result=output_dir/('probe-guardian-result-'+sid+'.json')
  guard_request.write_text(json.dumps({'command':[sys.executable,'-I','-c',bootstrap],'cwd':os.getcwd(),'deadline':time.monotonic()+seconds}),encoding='utf-8')
  guardian=Path(__file__).with_name('_probe_guardian.py');outcome={}
  try:
   subprocess.run([sys.executable,'-I',str(guardian),str(guard_request),str(guard_result)],env=_env(),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=seconds+3)
   if guard_result.is_file():outcome=json.loads(guard_result.read_text(encoding='utf-8'))
  except (subprocess.TimeoutExpired,OSError):pass
  record=None
  if outcome.get('status')=='exited' and outcome.get('returncode')==0 and response.is_file():
   record=json.loads(response.read_text(encoding='utf-8')).get(probe['id'])
  if record is None:
   limited=outcome.get('status')=='timeout' or not outcome
   record={'id':probe['id'],'status':'unknown' if limited else 'return','reason':'probe_elapsed_limit' if limited else 'probe_worker_failed',
           'session_id':sid,'instance_id':sid,'unit_artifact_sha256':unit_hash,'source_sha256':source_sha,'runtime_identity':runtime_id,'target_position':index,'trajectory':[],'partial_refs':[]}
   for partial in (output_dir/('probe-'+sid+'.jsonl'),output_dir/('probe-'+sid+'.json'),Path(str(response)+'.error.json')):
    if partial.is_file():record['partial_refs'].append({'path':str(partial),'sha256':hashlib.sha256(partial.read_bytes()).hexdigest()})
   error=Path(str(response)+'.error.json')
   if error.is_file():record['worker_error']=json.loads(error.read_text(encoding='utf-8'))
   journal=output_dir/('probe-outcome-'+sid+'.jsonl');journal.write_text(json.dumps({'kind':'outcome','status':record['status'],'reason':record['reason']})+'\n',encoding='utf-8')
   record.update(journal_path=str(journal),journal_sha256=hashlib.sha256(journal.read_bytes()).hexdigest())
   path=output_dir/('probe-outcome-'+sid+'.json');path.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
   record.update(artifact_path=str(path),artifact_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
  if record['status']!='complete' and current() is not None:
   record['diagnostic_ref']=current().record('probe_limited' if record['status']=='unknown' else 'probe_execution_failed','validation_probe',kind='limited_result' if record['status']=='unknown' else 'error',observed={'probe_id':probe['id'],'reason':record.get('reason')})
  results[probe['id']]=record
 return results

def verify_trace_files(production_path):
 import json,hashlib
 from pathlib import Path
 root=Path(production_path).resolve().parent
 value=json.loads(Path(production_path).read_text(encoding='utf-8'))
 for row in value.get('tasks',[]):
  for probe in row.get('probes',{}).values():
   for path_key,hash_key in (('artifact_path','artifact_sha256'),('journal_path','journal_sha256')):
    if not isinstance(probe.get(path_key),str) or not isinstance(probe.get(hash_key),str):raise Fault('artifact_changed','verification trace identity missing')
    path=Path(probe[path_key]).resolve()
    if not path.is_relative_to(root) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=probe[hash_key]:raise Fault('artifact_changed','verification trace file changed')
