"""Game-neutral, closed revision addressing. Never interpret prose as task IDs."""
from __future__ import annotations
import copy

class ReviewProtocolError(ValueError):
    def __init__(self, reason, allowed):
        self.facts = {"check": "review_protocol", "reason": reason,
                      "allowed_targets": list(allowed),
                      "expected": {"verdict": ["pass", "return", "unknown"],
                                   "revision_targets": "array of exact allowed IDs; empty only for pass"}}
        super().__init__(reason)

def review_scope(task, tasks):
    by_id = {t["id"]: t for t in tasks}
    return [tid for tid in task.get("depends_on", [])
            if by_id[tid].get("evidence_lane", "general") != "trajectory_blind"
            or task.get("evidence_lane", "general") == "trajectory_blind"]

def review_schema(task, tasks):
    schema = copy.deepcopy(task["output_schema"])
    schema.setdefault("properties", {}).update(
        verdict={"type": "string", "enum": ["pass", "return", "unknown"]},
        revision_targets={"type": "array", "items": {"type": "string", "enum": review_scope(task, tasks)}})
    if not review_scope(task,tasks):
        schema["properties"]["revision_targets"]={"type":"array","maxItems":0,"items":{"type":"string"}}
    schema["required"] = list(dict.fromkeys(schema.get("required", []) + ["verdict", "revision_targets"]))
    # The old field is input compatibility only, never advertised to new calls.
    schema["properties"].pop("revision_target", None)
    schema["required"] = [x for x in schema["required"] if x != "revision_target"]
    if task.get('review_contract')=='phase-review/v1':
        schema['properties']['verdict']={'type':'string','enum':['pass','return']}
        schema['properties']['findings']={'type':'array','items':{'type':'object','required':['id','category','target','summary'],
            'properties':{'id':{'type':'string','minLength':1},'category':{'type':'string','enum':['design_contradiction','implementation_obligation','runtime_obligation','research_hypothesis']},
                          'target':{'type':'string','enum':[t['id'] for t in tasks if t.get('evidence_lane')!='trajectory_blind']},'summary':{'type':'string','minLength':1},
                          'obligation_ref':{'type':'object','required':['slot_id','id'],'properties':{'slot_id':{'type':'string'},'id':{'type':'string'}}}}}}
    return schema

def review_targets(value, task, tasks):
    allowed = review_scope(task, tasks)
    def fail(reason): raise ReviewProtocolError(reason, allowed)
    if task.get('review_contract')=='phase-review/v1':
        findings=value.get('findings') if isinstance(value,dict) else None
        if not isinstance(findings,list):fail('phase_findings_required')
        blockers=[]
        for row in findings:
            if not isinstance(row,dict) or row.get('category') not in {'design_contradiction','implementation_obligation','runtime_obligation','research_hypothesis'} or row.get('target') not in [t['id'] for t in tasks if t.get('evidence_lane')!='trajectory_blind']:fail('phase_finding_invalid')
            if row['category']=='design_contradiction':
                if row['target'] not in allowed:fail('blocker_outside_review_scope')
                blockers.append(row['target'])
            elif row['category'] in {'implementation_obligation','runtime_obligation'}:
                ref=row.get('obligation_ref')
                if not isinstance(ref,dict) or any(not isinstance(ref.get(k),str) or not ref[k] for k in ('slot_id','id')):fail('deferred_obligation_reference_required')
        expected=list(dict.fromkeys(blockers))
        if value.get('verdict')!=('return' if expected else 'pass') or value.get('revision_targets')!=expected:fail('phase_verdict_disagrees_with_blockers')
    if not isinstance(value, dict) or value.get("verdict") not in ("pass", "return", "unknown"):
        fail("invalid_verdict")
    if "revision_targets" in value:
        targets = value["revision_targets"]
        if "revision_target" in value: fail("conflicting_target_fields")
        if not isinstance(targets, list) or any(not isinstance(x, str) for x in targets): fail("targets_must_be_array")
        if len(set(targets)) != len(targets): fail("duplicate_targets")
        if any(x not in allowed for x in targets): fail("target_outside_review_scope")
        if value["verdict"] == "pass" and targets: fail("pass_has_revision_targets")
    else:
        # Legacy exact singleton only. Never split delimiters or extract IDs from prose.
        target = value.get("revision_target")
        if value["verdict"] == "pass":
            if target is not None and target not in allowed: fail("invalid_legacy_target")
            targets = []
        else:
            if not isinstance(target, str) or target not in allowed: fail("invalid_legacy_target")
            targets = [target]
    if value["verdict"] != "pass" and not targets: fail("revision_targets_required")
    return targets

class PauseRequested(Exception):
    """Local checkpoint request; never a model/artifact error."""


def review_instructions(task):
    if task.get('review_contract')=='phase-review/v1':
        return ('Use verdict pass or return. Only design_contradiction blocks implementation. '
                'revision_targets contains exact responsible dependency IDs for contradictions; otherwise pass with []. '
                'Deferred implementation/runtime findings reference existing mandatory obligations; their target may be implementation. '
                'Do not demand runtime evidence before implementation. Put explanations in findings.')
    return 'Machine gate contract overrides legacy singular revision_target wording. Use revision_targets array of exact allowed dependency IDs. pass requires []; return/unknown requires targets. Put explanations in findings.'
