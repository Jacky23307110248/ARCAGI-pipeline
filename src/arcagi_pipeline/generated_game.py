"""Generated-game/v2: machine-owned loading, play and completion evidence.

Plugins produce JSON configurations, compile native Levels and search. They do
not return replay/pass verdicts. Native ARCEngine observations are taken here.
The source-protocol profile uses methods in the hash-bound original source.
This is an execution contract in an ordinary local process, not a sandbox or
an automatic proof of game-design intent.
"""
from __future__ import annotations

import copy
import hashlib
import inspect
import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .production import FrozenInputs, ModelSearch
from .diagnostics import traced, current, file_ref, Fault, ArchiveWriteError

CONTRACT = "generated-game/v2"
SCOPE = "generated_unit_verified/v2"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def require(condition, code):
    if not condition:
        raise Fault("output_protocol_invalid", code)


def plain(value):
    """Finite JSON of runtime data; unknown objects never become repr strings."""
    from enum import Enum
    if isinstance(value, Enum): return plain(value.value)
    if hasattr(value, "tolist"): return plain(value.tolist())
    if isinstance(value, dict): return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)): return [plain(v) for v in value]
    canonical(value)
    return value


def level_configuration(level):
    """Native executable Level content, excluding names, UUIDs and annotations."""
    return plain({"grid_size": level._grid_size, "data": level._data,
                  "sprites": [{key: getattr(s, key) for key in
                    ("pixels", "_name", "_x", "_y", "_layer", "rotation", "_mirror_ud",
                     "_mirror_lr", "_scale", "_blocking", "_interaction", "_tags")}
                    for s in level.get_sprites()]})


@dataclass(frozen=True)
class GeneratedLevel:
    task_id: str
    slot_id: str
    configuration: Any
    goal: Any
    construction: dict
    # Native engine per-level initialization, data only. No live-engine callback.
    initializations: tuple[dict, ...] = ()
    # Machine-observed changes relative to original content, replayed identically.
    novelty_actions: tuple[Any, ...] = ()
    # Read-only game-owned state projections; implementation binds planned
    # blueprint observables here. Values are collected by the machine.
    observation_bindings: dict[str, list] = field(default_factory=dict)
    probes: tuple[dict, ...] = ()


@dataclass(frozen=True)
class GeneratedUnit:
    unit_id: str
    source_id: str
    engine_class: str
    profile: str  # raw-arcengine or source-protocol
    levels: tuple[GeneratedLevel, ...]
    transition_policy: str = "continuous"  # or reset_each
    constructor: dict = field(default_factory=dict)


def validate_target(evidence, rules, blueprint):
    """V2 adds game functions and evidence commitments to the existing graph."""
    from .blueprint import validate_design
    errors = validate_design(evidence, rules, blueprint)
    from .verification_program import compile_obligations
    errors.extend(str(error) for error in compile_obligations(blueprint))
    if not isinstance(blueprint,dict):return errors
    target = blueprint.get("production_target")
    if not isinstance(target,dict):target={}
    if target.get("contract") != CONTRACT or target.get("object") != "playable_generated_unit":
        errors.append("production_target must request generated-game/v2 playable_generated_unit")
    slots=blueprint.get("slots")
    if not isinstance(slots,list):return errors
    for slot in slots:
        if not isinstance(slot,dict):continue
        if not isinstance(slot.get("game_function"), str) or not slot["game_function"].strip():
            errors.append("slot requires game_function")
        if not slot.get("evidence_refs"): errors.append("slot requires original-material evidence")
        obligations=slot.get('validation_obligations')
        if not isinstance(obligations,list):obligations=[]
        if not isinstance(slot.get("behavior_requirements",[]), list) or not (slot.get('behavior_requirements') or any(r.get('required') and r.get('phase')!='research' for r in obligations if isinstance(r,dict))):
            errors.append("slot requires executable behavior_requirements")
        if not isinstance(slot.get("teaching_hypotheses", []), list):
            errors.append("teaching_hypotheses must be separately listed hypotheses")
    return errors


def target_issues(evidence, rules, blueprint, *, strict=False):
    """Return the same machine-readable target issues at both v2 entry points."""
    from .verification_program import compile_obligations
    inputs={"evidence":evidence,"rules":rules,"blueprint":blueprint}
    malformed=[{"artifact":role,"stage":"design_preflight","code":"object_required"}
               for role,value in inputs.items() if not isinstance(value,dict)]
    if malformed:
        return malformed
    issues=[]
    for message in validate_target(evidence,rules,blueprint):
        role="evidence" if message.startswith("evidence ") else "rules" if message.startswith(("rule ","rules ")) else "blueprint"
        issues.append({"artifact":role,"stage":"design_preflight","code":"target_invalid","detail":message})
    if strict and blueprint.get("validation_program_version") is None:
        issues.extend({**item,"artifact":"blueprint","stage":"design_preflight"}
                      for item in compile_obligations(blueprint,strict=True))
    return issues


class NativeDriver:
    def __init__(self, engine):
        from arcengine import ARCBaseGame
        require(isinstance(engine, ARCBaseGame), "native_engine_required")
        self.engine = engine

    def configuration(self):
        return [level_configuration(level) for level in self.engine._clean_levels]

    def install(self, levels):
        from arcengine import Level
        require(all(type(level) is Level for level in levels), "compile_levels_requires_native_Level")
        self.engine._clean_levels = [x.clone() for x in levels]
        self.engine._levels = [x.clone() for x in levels]
        self.engine._win_score = len(levels)

    def reset(self):
        from arcengine import ARCBaseGame
        # Full reset is deliberate and independent of ambient ONLY_RESET_LEVELS.
        ARCBaseGame.full_reset(self.engine)
        return self.observe()

    def select(self, index):
        from arcengine import ARCBaseGame
        ARCBaseGame.set_level(self.engine, index)
        self.engine._score = index

    def step(self, action):
        from arcengine import ARCBaseGame, ActionInput, GameAction
        data = action if isinstance(action, dict) else {"id": action}
        frame = ARCBaseGame.perform_action(self.engine, ActionInput(**{**data, "id": GameAction.from_id(data["id"])}), raw=True)
        return self.observe()

    def observe(self):
        e = self.engine
        result = plain({"level_index": e.level_index, "completed": e._score,
                      "state": e._state.name,
                      "frame": e.camera.render(e.current_level.get_sprites())})
        projected={}
        by_position=getattr(self,'observation_bindings_by_position',None)
        bindings=by_position.get(e.level_index,{}) if by_position is not None else getattr(self,'observation_bindings',{})
        for name,path in bindings.items():
            require(isinstance(name,str) and name and isinstance(path,list) and path, 'observation_binding_protocol')
            value=e
            for key in path:
                require(type(key) is int or isinstance(key,str) and not key.startswith('__'), 'observation_binding_path')
                try:value=value[key] if isinstance(value,(dict,list,tuple)) else getattr(value,key)
                except (AttributeError,KeyError,IndexError,TypeError):
                    raise Fault('output_protocol_invalid','observation_binding_unavailable:'+name) from None
                require(not callable(value),'observation_binding_must_be_data')
            projected[name]=plain(value)
        if projected:result['projected']=projected
        return result

    def novelty_observe(self): return {"frame": self.observe()["frame"]}
    def position(self): return self.engine.level_index

    def goal(self, goal):
        require(isinstance(goal, dict) and set(goal) == {"completed"} and
                type(goal["completed"]) is int and goal["completed"] >= 0,
                "native_goal_requires_completed_count")
        return self.engine._score >= goal["completed"]

    def complete(self):
        from arcengine import GameState
        return self.engine._state == GameState.WIN and self.engine._score == len(self.engine._clean_levels)


class SourceDriver:
    """Alternate backends implement this protocol in the ORIGINAL bound source.

    Methods: configuration(), install_configuration(list), reset(), select(index),
    step(action), observe(), goal(goal_json), complete(). No plugin-supplied
    implementation of these methods is accepted as source evidence.
    """
    def __init__(self, engine, source):
        self.engine = engine
        for name in ("configuration", "install_configuration", "reset", "select", "step", "observe", "goal", "complete", "novelty_observe", "position"):
            fn = getattr(type(engine), name, None)
            require(inspect.isfunction(fn) and fn.__module__ == source.__name__, "source_protocol_method_origin:" + name)
        self.methods = {name: getattr(type(engine), name) for name in
                        ("configuration", "install_configuration", "reset", "select", "step", "observe", "goal", "complete", "novelty_observe", "position")}

    def call(self, name, *args): return self.methods[name](self.engine, *args)
    def configuration(self): return plain(self.call("configuration"))
    def install(self, values): self.call("install_configuration", copy.deepcopy(values))
    def reset(self): self.call("reset"); return self.observe()
    def select(self, index): self.call("select", index)
    def step(self, action): self.call("step", copy.deepcopy(action)); return self.observe()
    def observe(self): return plain(self.call("observe"))
    def novelty_observe(self): return plain(self.call("novelty_observe"))
    def position(self): return self.call("position")
    def goal(self, value):
        answer = self.call("goal", copy.deepcopy(value)); require(type(answer) is bool, "goal_boolean"); return answer
    def complete(self):
        answer = self.call("complete"); require(type(answer) is bool, "complete_boolean"); return answer


def initialize(driver, declarations):
    """Apply declared source-owned initial state; never replace engine methods.

    Paths begin at a game-owned field, excluding base runtime bookkeeping. Their
    values and actual reads are part of the semantic configuration identity.
    """
    from arcagi_pipeline.diagnostics import Fault
    protected = set()
    if isinstance(driver, NativeDriver):
        from arcengine import ARCBaseGame
        protected = set(ARCBaseGame.__annotations__) | {"_state", "_score", "_win_score", "_levels", "_clean_levels", "_current_level_index"}
    actual = []
    for row in declarations:
        require(isinstance(row, dict) and set(row) == {"path", "value"}, "initialization_protocol")
        path = row["path"]
        require(isinstance(path, list) and path and isinstance(path[0], str) and
                path[0] not in protected and all(not isinstance(p, str) or not p.startswith("__") for p in path), "protected_initialization")
        obj = driver.engine
        for key in path[:-1]: obj = obj[key] if isinstance(obj, (list, dict)) else getattr(obj, key)
        key = path[-1]
        old = obj[key] if isinstance(obj, (list, dict)) else getattr(obj, key)
        require(not callable(old), "method_initialization_forbidden")
        if isinstance(obj, (list, dict)): obj[key] = copy.deepcopy(row["value"])
        else: setattr(obj, key, copy.deepcopy(row["value"]))
        new = obj[key] if isinstance(obj, (list, dict)) else getattr(obj, key)
        actual.append({"path": path, "value": plain(new)})
    return actual


def at(value, path):
    for part in path: value = value[part]
    return value


def behavior_check(requirement, trajectory, target_position=None):
    """Evaluate a blueprint commitment against machine observations, not echoes.

    Supported predicates: equals, changes, contains. Necessity/learning claims
    need a separately declared experimental design and remain unknown here.
    """
    if requirement.get("kind") != "behavior":
        return {"status": "unknown", "reason": "requires_separate_evidence"}
    try:
        path = requirement["observation_path"]
        values=[];indices=[];missing=[];excluded=[]
        for i,row in enumerate(trajectory):
            if path and path[0]=='projected' and target_position is not None and row.get('engine_position')!=target_position:
                excluded.append(i);continue
            try:value=at(row['observation'],path)
            except (KeyError,TypeError,IndexError):missing.append(i);continue
            values.append(value);indices.append(i)
        op = requirement["predicate"]
        if op == "changes": passed = len({canonical(v) for v in values}) > 1
        elif op == "equals": passed = any(v == requirement["value"] for v in values)
        elif op == "contains": passed = any(requirement["value"] in v for v in values)
        else: return {"status": "unknown", "reason": "unsupported_behavior_predicate"}
        return {"status": "pass" if passed else ("unknown" if missing or excluded or not values else "return"),
                "trace_indices": indices, "missing_indices":missing,"excluded_other_target_indices":excluded,"observation_path": path}
    except (KeyError, TypeError, IndexError):
        return {"status": "unknown", "reason": "observation_not_available"}


@traced("production")
def run_generated_production(plugin, inputs: FrozenInputs, *, seed, output_dir: Path,
                             runtime_context, cap=250000,probe_factory=None,operation_requests=()):
    """No plugin replay/pass hooks: the machine installs and plays each unit."""
    require(not output_dir.exists(), "output_exists")
    output_dir.mkdir(parents=True)
    evidence, rules, blueprint = [json.loads(p.read_text(encoding="utf-8")) for p in
                                  (inputs.evidence, inputs.rule_contract, inputs.blueprint)]
    errors = validate_target(evidence, rules, blueprint)
    rows, units, feedback = [], [], []
    artifacts = inputs.fingerprints()
    def failure(stage, reason, task_id=None, unknown=False,obligation_id=None,exc=None):
        ref = current().record("search_limited" if unknown else "output_protocol_invalid", stage,
                               task_id=task_id, exc=exc, kind="limited_result" if unknown else "error",
                               observed={"reason": reason}, refs=[file_ref(inputs.blueprint)])
        feedback.append({"stage": stage, "reason": reason, "task_id": task_id,"obligation_id":obligation_id,"root_cause_status":"unconfirmed","suggested_role":"implementation", **ref})
    try:
        require(not errors, "; ".join(errors))
        require(getattr(plugin, "production_contract", None) == CONTRACT, "generated_game_plugin_required")
        generated = list(plugin.generate_units(inputs, seed))
        require(generated and all(isinstance(u, GeneratedUnit) and u.levels for u in generated), "playable_units_required")
        require(len({u.unit_id for u in generated}) == len(generated), "duplicate_unit_id")
        all_ids = [l.task_id for u in generated for l in u.levels]
        require(len(set(all_ids)) == len(all_ids), "duplicate_task_id")
    except ArchiveWriteError:
        raise
    except Exception as exc:
        failure("generation", str(exc)); generated = []
    from .verification_program import check,run_probes,validate_probes
    from .handoff_control import operation_plan
    priority_plan = operation_plan(operation_requests)
    execution_order = []

    def record(identifier, stage, status, evidence, executed=True):
        execution_order.append({"sequence":len(execution_order)+1,
                                "check_id":identifier+":"+stage,"stage":stage,
                                "status":status,"executed":executed,
                                "evidence_sha256":digest(evidence)})

    slots = {s["id"]: s for s in blueprint.get("slots", [])}
    def unit_checks(unit):
        ur = {"unit_id": unit.unit_id, "status": "return", "levels": [], "sessions": []}
        units.append(ur)
        try:
            require(unit.transition_policy in {"continuous", "reset_each"}, "transition_policy")
            require({l.slot_id for l in unit.levels} == set(slots), "unit_slot_coverage")
            require(all(isinstance(l, GeneratedLevel) and l.configuration is not None for l in unit.levels), "generated_level_configuration_required")
            # Freeze plugin data before invoking compilation/search.
            artifact = json.loads(canonical(asdict(unit)))
            unit_hash = digest(artifact)
            (output_dir / ("unit-" + unit_hash + ".json")).write_text(json.dumps(artifact, indent=2), encoding="utf-8")
            module = runtime_context.load_source(unit.source_id)
            source_before = hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
            cls = getattr(module, unit.engine_class)
            require(isinstance(cls, type) and cls.__module__ == module.__name__, "original_source_class_required")
            original_methods = {k: v for k, v in vars(cls).items() if callable(v)}
            def fresh():
                require(all(getattr(cls, k) is v for k, v in original_methods.items()), "source_method_changed")
                engine = cls(**copy.deepcopy(unit.constructor))
                if unit.profile == "raw-arcengine": return NativeDriver(engine)
                require(unit.profile == "source-protocol", "unknown_engine_profile")
                return SourceDriver(engine, module)
            baseline = fresh()
            baseline_config = baseline.configuration()
            if unit.profile == "raw-arcengine":
                compiled = plugin.compile_levels(copy.deepcopy(unit), module)
                require(len(compiled) == len(unit.levels), "compiled_level_count")
                expected = [l.configuration["engine_level"] for l in unit.levels]
                require([level_configuration(x) for x in compiled] == expected, "compiled_generated_configuration_mismatch")
            else:
                compiled = [copy.deepcopy(l.configuration) for l in unit.levels]
                expected = copy.deepcopy(compiled)
            require(all(getattr(cls, k) is v for k, v in original_methods.items()), "source_method_changed")
            semantic = [{"configuration": c, "initializations": list(l.initializations), "goal": l.goal} for c, l in zip(expected, unit.levels)]
            require(len({digest(x) for x in semantic}) == len(semantic), "duplicate_semantic_configuration")
            def loaded():
                d = fresh(); d.install(compiled); d.reset()
                if isinstance(d,NativeDriver):
                    d.observation_bindings_by_position={i:copy.deepcopy(l.observation_bindings) for i,l in enumerate(unit.levels)}
                else:require(not any(l.observation_bindings for l in unit.levels),'observation_bindings_require_native_profile')
                require(d.configuration() == expected, "generated_loaded_configuration_mismatch")
                return d
            candidates = [None] * len(unit.levels)
            validation_contexts={}
            observed_content = set()
            def level_checks(index,level):
                row = {"task_id": level.task_id, "unit_id": unit.unit_id, "slot_id": level.slot_id,
                       "status": "return", "unit_artifact_sha256": unit_hash,
                       "configuration_sha256": digest(level.configuration), "semantic_sha256": digest(semantic[index]),
                       "goal_sha256": digest(level.goal), "checks": {}}
                rows.append(row); ur["levels"].append(level.task_id)
                stage="structure"
                try:
                    from .production import GeneratedTask
                    from .blueprint import validate_construction
                    check_construction = {**level.construction, "config": {"value": level.configuration}}
                    require(not validate_construction(GeneratedTask(level.task_id, level.configuration, check_construction), rules, blueprint), "construction_blueprint_mismatch")
                    require(level.construction.get("slot_id") == level.slot_id and level.construction.get("config") == level.configuration, "construction_configuration_mismatch")
                    trace_ids=validate_probes(level)
                    actual_probes={p['id']:p for p in level.probes}
                    require(all(p['id'] in actual_probes and all(actual_probes[p['id']].get(k)==p.get(k) for k in ('kind','baseline')) for p in slots[level.slot_id].get('probe_plan',[])),'probe_plan_implementation_mismatch')
                    obligations=slots[level.slot_id].get('validation_obligations',[])
                    require(all(set(r.get('traces',[]))<=trace_ids for r in obligations if r.get('phase')!='research'),'obligation_probe_not_implemented')
                    required={r['id'] for r in obligations if r.get('required') and r.get('phase')!='research'}
                    require({c["id"] for c in level.construction["claims"]} == {r["id"] for r in slots[level.slot_id].get("behavior_requirements",[])}|required, "construction_claim_evidence_coverage")
                    row['checks']['structure']='pass'
                    record(level.task_id,stage,'pass',row['checks'])
                    yield stage
                    stage='model_search'
                    probes=run_probes(unit,index,fresh,compiled,expected,unit_hash,source_before,runtime_context.facts['identity'],cap,output_dir,
                                      compile_variant=lambda variant:plugin.compile_levels(variant,module),recipe={'source_path':str(module.__file__),'factory':probe_factory,
                                      'import_roots':[str((runtime_context.base/Path(p)).resolve()) for p in runtime_context.data.get('import_roots',[])] if hasattr(runtime_context,'data') else []})
                    row['probes']=probes;validation_contexts[level.task_id]=dict(probes)
                    search = plugin.search_generated(copy.deepcopy(level), cap)
                    require(isinstance(search, ModelSearch), "ModelSearch_required")
                    require(search.status in {'candidate','unknown','unsolvable'} and isinstance(search.actions,(tuple,list)) and type(search.complete_model) is bool and type(search.exhausted) is bool and isinstance(search.evidence,dict),'model_search_protocol')
                    require(search.evidence.get("configuration_sha256") == row["configuration_sha256"] and
                            search.evidence.get("goal_sha256") == row["goal_sha256"] and
                            isinstance(search.evidence.get("model_version"), str) and bool(search.evidence["model_version"]), "search_configuration_goal_binding")
                    canonical(asdict(search)); row["search"] = asdict(search)
                    require(digest(asdict(unit)) == unit_hash, "generated_artifact_mutated")
                    record(level.task_id,stage,search.status,row["search"])
                    yield stage
                    if search.status != "candidate":
                        negative=search.status=='unsolvable' and search.complete_model and search.exhausted
                        row["status"] = "return" if negative else "unknown"
                        row['model_conclusion']='model_exhaustion_claim' if negative else 'unresolved'
                        failure("model_search", "model_result_" + search.status, level.task_id, unknown=not negative)
                        record(level.task_id,"engine_replay","blocked",{"reason":"candidate_missing"},executed=False)
                        return
                    stage="engine_replay"
                    # Configuration novelty is runtime-observed, not a changed ID/seed.
                    d = loaded(); d.select(index); init = initialize(d, level.initializations)
                    actual_config = d.configuration()
                    require(actual_config == expected, "initialization_changed_level_configuration")
                    observed = [d.novelty_observe()]
                    for action in level.novelty_actions: d.step(action); observed.append(d.novelty_observe())
                    comparisons = []
                    for bi in range(len(baseline_config)):
                        b = fresh(); b.reset(); b.select(bi)
                        bo = [b.novelty_observe()]
                        try:
                            for action in level.novelty_actions: b.step(action); bo.append(b.novelty_observe())
                        except ArchiveWriteError:
                            raise
                        except Exception:
                            # An invalid baseline action is not novelty evidence.
                            comparisons.append({"index": bi, "different": False, "unknown": True}); continue
                        comparisons.append({"index": bi, "different": digest(observed) != digest(bo)})
                    require(all(c["different"] for c in comparisons), "generated_content_not_distinguished_from_original")
                    content_identity = digest({"observations": observed, "probe_actions": level.novelty_actions})
                    require(content_identity not in observed_content, "unit_content_needs_distinguishing_runtime_evidence")
                    observed_content.add(content_identity)
                    row["novelty"] = {"baseline_sha256": digest(baseline_config), "comparisons": comparisons,
                                      "probe_actions": list(level.novelty_actions), "observations_sha256": digest(observed)}
                    # Start a fresh generated instance for replay, independent of probes.
                    d = loaded(); d.select(index); init = initialize(d, level.initializations)
                    d.observation_bindings=copy.deepcopy(level.observation_bindings)
                    require(d.position() == index, "selected_target_mismatch")
                    session = {"session_id": uuid.uuid4().hex, "purpose": "generated_level_replay", "task_id": level.task_id,
                               "source_sha256": source_before, "runtime_identity": runtime_context.facts["identity"],
                               "unit_artifact_sha256": unit_hash, "loaded_configuration_sha256": digest(d.configuration()),
                               "semantic_sha256": row["semantic_sha256"], "initializations": init,
                               "instance_id": uuid.uuid4().hex, "trajectory": [{"op": "reset_select", "observation": d.observe(),"engine_position":d.position()}]}
                    ur["sessions"].append(session)
                    for action in search.actions:
                        session["trajectory"].append({"op": "step", "action": copy.deepcopy(action), "observation": d.step(action),"engine_position":d.position()})
                    accepted = d.goal(level.goal)
                    for n,event in enumerate(session['trajectory']):event['event_index']=n
                    named=validation_contexts[level.task_id]
                    named['candidate']={**session,'status':'complete','target_position':index}
                    row["replay"] = {"accepted": accepted, "session_id": session["session_id"], "goal_sha256": row["goal_sha256"],
                                     "loaded_configuration_sha256": session["loaded_configuration_sha256"]}
                    row["fulfillment"] = {r["id"]: behavior_check(r, session["trajectory"],index) for r in slots[level.slot_id].get("behavior_requirements",[])}
                    require(bool(row["fulfillment"]) or bool(required), "behavior_requirements_empty")
                    passed = accepted and all(c["status"] == "pass" for c in row["fulfillment"].values())
                    row["status"] = "pass" if passed else ("unknown" if accepted and any(c["status"] == "unknown" for c in row["fulfillment"].values()) else "return")
                    row["verified_solution"] = accepted
                    if not passed: failure("generated_level_validation", "goal_or_behavior_not_met", level.task_id, unknown=row["status"] == "unknown")
                    candidates[index]=search if accepted else None
                    record(level.task_id,stage,"pass" if accepted else "return",row["replay"])
                    yield stage
                except ArchiveWriteError:
                    raise
                except Exception as exc:
                    row['status']='return'
                    from .handoff_control import safe_exception
                    row['execution_error']=safe_exception(exc)
                    if isinstance(exc,Fault) and str(exc) in ('source_method_changed','source_file_changed','generated_loaded_configuration_mismatch'):raise
                    failure('generated_level_validation',str(exc) if isinstance(exc,Fault) else type(exc).__name__,level.task_id,exc=exc)
                    record(level.task_id,stage,'return',row['execution_error'])
            session=None
            smoke_trace=None

            def execute_unit():
                nonlocal session
                if 'unit_execution' in ur:
                    return
                if all(c is not None for c in candidates):
                    d = loaded()
                    session = {"session_id": uuid.uuid4().hex, "purpose": "complete_generated_unit",
                               "unit_artifact_sha256": unit_hash, "source_sha256": source_before,
                               "loaded_configuration_sha256": digest(d.configuration()), "instance_id": uuid.uuid4().hex,
                               "transition_policy": unit.transition_policy, "trajectory": []}
                    ur["sessions"].append(session)
                    try:
                        for i, (level, candidate) in enumerate(zip(unit.levels, candidates)):
                            d.observation_bindings=copy.deepcopy(level.observation_bindings)
                            if unit.transition_policy == "reset_each": d.reset(); d.select(i)
                            require(d.position() == i, "unit_target_order_mismatch")
                            init = initialize(d, level.initializations)
                            session["trajectory"].append({"op": "target_start", "task_id": level.task_id, "initializations": init, "observation": d.observe(),"engine_position":d.position()})
                            for action in candidate.actions:
                                session["trajectory"].append({"op": "step", "task_id": level.task_id, "action": action, "observation": d.step(action),"engine_position":d.position()})
                            require(d.goal(level.goal), "unit_transition_or_goal_failed")
                        require(d.complete(), "complete_generated_unit_goal_failed")
                        require(d.configuration() == expected, "unit_configuration_drift")
                        ur["unit_execution"] = {"status": "pass", "session_id": session["session_id"]}
                        session['status']='complete';ur['status']='pass'
                    except ArchiveWriteError:
                        raise
                    except Exception as exc:
                        session['status']='return';ur['unit_execution']={'status':'return','reason':str(exc) if isinstance(exc,Fault) else type(exc).__name__}
                        ur['status']='return';failure('unit_execution',ur['unit_execution']['reason'],unit.unit_id)
                else:
                    ur["status"] = "unknown" if any(r["status"] == "unknown" for r in rows if r["unit_id"] == unit.unit_id) else "return"
                    ur['unit_execution']={'status':'unknown','reason':'verified_candidates_missing'}
                if ur.get('unit_execution',{}).get('status') in ('pass','return'):
                    for n,event in enumerate(session['trajectory']):event['event_index']=n
                result=ur['unit_execution']
                ran=result.get('reason')!='verified_candidates_missing'
                record(unit.unit_id,'unit_execution',result['status'] if ran else 'blocked',result,executed=ran)

            def execute_smoke():
                nonlocal smoke_trace
                if smoke_trace is not None:
                    return
                smoke_trace={'status':'running','session_id':uuid.uuid4().hex,'instance_id':uuid.uuid4().hex,'source_sha256':source_before,'runtime_identity':runtime_context.facts['identity'],'unit_artifact_sha256':unit_hash,'trajectory':[]}
                try:
                    smoke=loaded();smoke_action=plugin.smoke_action(copy.deepcopy(unit));before=smoke.observe();before_position=smoke.position()
                    smoke_trace['trajectory'].append({'op':'reset','event_index':0,'observation':before,'engine_position':before_position})
                    after=smoke.step(smoke_action);smoke_trace['trajectory'].append({'op':'step','event_index':1,'action':smoke_action,'observation':after,'engine_position':smoke.position()});smoke_trace['status']='complete'
                    ur['adapter_smoke']={'status':'pass','action':smoke_action,'reset':before,'step':after,'session_id':smoke_trace['session_id']}
                except ArchiveWriteError:
                    raise
                except Exception as exc:
                    smoke_trace.update(status='return',reason=str(exc) if isinstance(exc,Fault) else type(exc).__name__)
                    ur['adapter_smoke']={'status':'return','reason':smoke_trace['reason']};ur['status']='return';failure('adapter_smoke',smoke_trace['reason'],unit.unit_id)
                ur['sessions'].append(smoke_trace)
                result=ur['adapter_smoke']
                record(unit.unit_id,'adapter_smoke',result['status'],result)

            level_smokes={}

            def execute_level_smoke(i,level):
                if i in level_smokes:
                    return level_smokes[i]
                if not any('level_smoke' in r.get('traces',[]) for r in slots[level.slot_id].get('validation_obligations',[])):
                    return None
                local={'source_sha256':source_before,'runtime_identity':runtime_context.facts['identity'],'unit_artifact_sha256':unit_hash,'configuration_sha256':digest(level.configuration),'goal_sha256':digest(level.goal),'status':'running','session_id':uuid.uuid4().hex,'instance_id':uuid.uuid4().hex,'target_position':i,'scope':'level','trajectory':[]}
                try:
                    sd=loaded();sd.select(i);initialize(sd,level.initializations)
                    require(sd.position()==i,'smoke_target_mismatch')
                    local['trajectory'].append({'op':'reset_select','event_index':0,'engine_position':sd.position(),'observation':sd.observe()})
                    action=plugin.level_smoke_action(copy.deepcopy(unit),copy.deepcopy(level)) if hasattr(plugin,'level_smoke_action') else plugin.smoke_action(copy.deepcopy(unit))
                    local['trajectory'].append({'op':'step','event_index':1,'engine_position':sd.position(),'action':action,'observation':sd.step(action)})
                    local['trajectory'][-1]['engine_position']=sd.position();local['status']='complete'
                except ArchiveWriteError:raise
                except Exception as exc:
                    from .diagnostics import exception_facts
                    local.update(status='return',exception=exception_facts(exc));failure('level_smoke',type(exc).__name__,level.task_id,exc=exc)
                record(level.task_id,'level_smoke',local['status'],local)
                level_smokes[i]=local
                return local

            checks=[level_checks(i,level) for i,level in enumerate(unit.levels)]
            reached=[set() for _ in checks]

            def advance(index,stage):
                if stage not in reached[index]:
                    for completed in checks[index]:
                        reached[index].add(completed)
                        if completed==stage:
                            break

            for request in priority_plan:
                operation=request['operation']
                if operation in ('rerun_declared_checks','revise_and_rerun'):
                    break
                if operation=='adapter_smoke':
                    execute_smoke()
                    for i,level in enumerate(unit.levels):
                        execute_level_smoke(i,level)
                elif operation in ('validate_structure','search','engine_replay','validate_unit'):
                    goal={'validate_structure':'structure','search':'model_search',
                          'engine_replay':'engine_replay','validate_unit':'engine_replay'}[operation]
                    for index in range(len(checks)):
                        advance(index,goal)
                    if operation=='validate_unit':
                        execute_unit()
                yield
            for index in range(len(checks)):
                advance(index,'engine_replay')
            execute_unit()
            execute_smoke()
            for i,level in enumerate(unit.levels):
                row=next(r for r in rows if r['task_id']==level.task_id)
                named=validation_contexts.get(level.task_id,{})
                if ur.get('unit_execution',{}).get('status')=='pass':named['unit']={**session,'target_position':i}
                named['smoke']={**smoke_trace,'scope':'unit'}
                local=execute_level_smoke(i,level)
                if local is not None:
                    named['level_smoke']=local
                    row['level_smoke']=local
                obligations=slots[level.slot_id].get('validation_obligations',[])
                row['obligations']={r['id']:({'status':'hypothesis'} if r.get('phase')=='research' else check(r['expression'],{n:v for n,v in named.items() if v['status']=='complete'},r['traces'],r.get('observation_scope','target'),version=blueprint.get('validation_program_version','arcagi-validation-program/v1'))) for r in obligations}
                for r in obligations:
                    if r.get('required') and r.get('phase')!='research':
                        result=row['obligations'][r['id']];row.setdefault('fulfillment',{})[r['id']]=result
                        if result['status']!='pass':
                            row['status']='unknown' if result['status']=='unknown' and row['status']!='return' else 'return'
                            failure('validation_obligation',result.get('reason','unmet'),level.task_id,unknown=result['status']=='unknown',obligation_id=r['id'])
                if row['status']!='pass' and ur['status']=='pass':ur['status']=row['status']
            unit_rows=[r for r in rows if r['unit_id']==unit.unit_id]
            if ur['adapter_smoke']['status']!='pass' or any(r['status']=='return' for r in unit_rows):ur['status']='return'
            require(hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest() == source_before, "source_file_changed")
            require(all(getattr(cls, k) is v for k, v in original_methods.items()), "source_method_changed")
        except ArchiveWriteError:
            raise
        except Exception as exc:
            ur["status"] = "return"; failure("generated_unit_validation", str(exc) if isinstance(exc, Fault) else type(exc).__name__, unit.unit_id,exc=exc)
            ur["blocked_checks"]={"reason":"shared_prerequisite_failed","unexecuted_tasks":[l.task_id for l in unit.levels if not any(r["task_id"]==l.task_id for r in rows)]}
    # Advance each unit through the requested stage before considering the next
    # request. A unit with an invalid shared prerequisite simply exhausts early.
    pending_units=[unit_checks(unit) for unit in generated]
    for _ in priority_plan:
        for pending in pending_units:
            next(pending,None)
    for pending in pending_units:
        for _ in pending:
            pass
    status = "pass" if units and all(u["status"] == "pass" for u in units) and not feedback else (
             "unknown" if units and all(u["status"] in {"pass", "unknown"} for u in units) else "return")
    result = {"schema_version": "arcagi-production/v2", "production_contract": CONTRACT, "status": status,
              "completion_scope": SCOPE if status == "pass" else "generated_unit_incomplete/v2",
              "inputs": artifacts, "tasks": rows, "units": units, "feedback": feedback,
              "execution_order": execution_order,
              "runtime": runtime_context.receipt(),
              "validation_program_version": blueprint.get("validation_program_version","arcagi-validation-program/v1"),
              "proof_limits": {"teaching_benefit": "hypothesis", "plugin_semantics": "model_self_review",
                               "source_protocol": "original_source_methods", "process_isolation": "ordinary_local_process"}}
    from .budget import atomic_json
    atomic_json(output_dir / "production.json", result)
    return result
