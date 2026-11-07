"""Executable frozen-input production contracts, separate from text workflows.

Plugins own rules, generation, search and engine bindings. The runner owns gates
and feedback; a model candidate is never reported as an engine-verified solution.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, Sequence
from .diagnostics import traced, current, scrub, exception_facts, Fault, ArchiveWriteError, file_ref


@dataclass(frozen=True)
class FrozenInputs:
    rule_contract: Path
    blueprint: Path
    evidence: Path | None = None

    def fingerprints(self) -> dict[str, str]:
        return {name: hashlib.sha256(path.read_bytes()).hexdigest()
                for name, path in (("rule_contract", self.rule_contract), ("blueprint", self.blueprint), ("evidence", self.evidence)) if path is not None}


@dataclass(frozen=True)
class GeneratedTask:
    task_id: str
    payload: Any
    construction: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelSearch:
    status: Literal["candidate", "unsolvable", "unknown"]
    actions: tuple[Any, ...] = ()
    reason: str = ""
    complete_model: bool = False
    exhausted: bool = False
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EngineVerdict:
    accepted: bool
    reason: str = ""
    trace: tuple[Any, ...] = ()
    failed_action_index: int | None = None
    binding: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FulfillmentVerdict:
    checks: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


@dataclass(frozen=True)
class UnitVerdict:
    status: Literal["pass", "return", "unknown"]
    checks: dict[str, Any]
    reason: str = ""


class ProductionPlugin(Protocol):
    plugin_id: str

    def generate(self, inputs: FrozenInputs, seed: int) -> Sequence[GeneratedTask]: ...
    def validate(self, task: GeneratedTask) -> Sequence[str]: ...
    def search(self, task: GeneratedTask, cap: int) -> ModelSearch: ...
    def replay(self, task: GeneratedTask, candidate: ModelSearch) -> EngineVerdict: ...
    def validate_unit(self, tasks: Sequence[GeneratedTask], task_records: Sequence[dict[str, Any]], context: Any) -> UnitVerdict: ...
    def adapter_smoke(self, context: Any) -> UnitVerdict: ...


class DesignProductionPlugin(ProductionPlugin, Protocol):
    def assess(self, task: GeneratedTask, candidate: ModelSearch, verdict: EngineVerdict) -> FulfillmentVerdict: ...


def protocol(condition, message):
    if not condition: raise Fault("output_protocol_invalid",message)

def json_value(value):
    try: json.dumps(value,allow_nan=False)
    except (TypeError,ValueError,OverflowError):
        raise Fault("output_protocol_invalid","plugin values must be finite JSON-compatible data") from None


@traced("production")
def run_production(plugin: ProductionPlugin, inputs: FrozenInputs, *, seed: int,
                   output_dir: Path, cap: int = 250_000, require_design: bool = False,
                   runtime_context: Any = None, require_unit_checks: bool = False) -> dict[str, Any]:
    """Generate, search, replay and persist reviewable pass/return/unknown results.

    A revision is a new invocation/output directory; autonomous repair is outside
    this interface. Frozen inputs must be reviewed before they reach this runner.
    """
    if isinstance(cap,bool) or not isinstance(cap,int) or cap < 1:
        raise Fault("config_invalid", "cap must be positive")
    if output_dir.exists():
        exc=FileExistsError(output_dir);exc.diagnostic_code="output_exists";raise exc
    fingerprints = inputs.fingerprints()
    design = None
    if require_design:
        from .blueprint import validate_design
        if inputs.evidence is None: raise ValueError("evidence required for design validation")
        evidence = json.loads(inputs.evidence.read_text(encoding="utf-8"))
        rules = json.loads(inputs.rule_contract.read_text(encoding="utf-8"))
        blueprint = json.loads(inputs.blueprint.read_text(encoding="utf-8"))
        errors = validate_design(evidence, rules, blueprint)
        if errors: raise Fault("design_invalid", "invalid design: " + "; ".join(errors))
        design = (rules, blueprint)
    output_dir.mkdir(parents=True)
    rows: list[dict[str, Any]] = []
    feedback: list[dict[str, Any]] = []
    unit_checks: dict[str, Any] = {"status": "unknown", "checks": {}, "reason": "not_run"}
    smoke: dict[str, Any] = {"status": "unknown", "checks": {}, "reason": "not_run"}

    refs=[file_ref(path) for path in (inputs.rule_contract,inputs.blueprint,inputs.evidence) if path is not None]
    def returned(task_id: str | None, stage: str, reason: str, actions=(), index=None, *, code="exception", exc=None, limited=False):
        ref=current().record(code,stage,task_id=task_id,kind="limited_result" if limited else "error",
                             observed={"reason":reason,"failed_action_index":index},refs=refs,exc=exc)
        feedback.append(scrub({"task_id": task_id, "stage": stage, "reason": reason,
                         "actions": list(actions), "failed_action_index": index,**ref}))

    if require_unit_checks and runtime_context is None:
        returned(None,"runtime_context","strict unit checks require machine runtime context",code="config_invalid")
    try:
        tasks = list(plugin.generate(inputs, seed))
        protocol(all(isinstance(task,GeneratedTask) and isinstance(task.task_id,str) and task.task_id and isinstance(task.construction,dict) for task in tasks),"generate must return GeneratedTask objects with string IDs and construction dictionaries")
        ids = [task.task_id for task in tasks]
        protocol(bool(tasks) and len(ids)==len(set(ids)),"generation must return nonempty tasks with unique IDs")
        for task in tasks: json_value(asdict(task))
    except ArchiveWriteError: raise
    except Exception as exc:
        tasks = []
        returned(None, "generation", type(exc).__name__,code=getattr(exc,"diagnostic_code","exception"),exc=exc)
    for task in tasks:
        row: dict[str, Any] = {"task_id": task.task_id, "payload": task.payload, "construction": task.construction, "status": "return"}
        rows.append(row)
        row["checks"] = {"structure": "pending", "model_search": "pending", "engine_replay": "pending", "fulfillment": "pending" if require_design else "not_checked"}
        stage, actions = "structure", ()
        try:
            validation = plugin.validate(task)
            protocol(not isinstance(validation,(str,bytes,dict)),"validate must return a sequence of error strings")
            errors = list(validation)
            protocol(all(isinstance(error,str) for error in errors),"validate must return string errors")
            if design:
                from .blueprint import validate_construction
                errors.extend(validate_construction(task, *design))
            row["checks"]["structure"] = "return" if errors else "pass"
            if errors:
                returned(task.task_id, stage, "; ".join(errors),code="structure_invalid")
                continue
            stage = "model_search"
            search = plugin.search(task, cap)
            protocol(isinstance(search,ModelSearch),"search must return ModelSearch")
            protocol(isinstance(search.status,str) and isinstance(search.actions,(tuple,list)) and isinstance(search.complete_model,bool) and isinstance(search.exhausted,bool) and isinstance(search.reason,str) and isinstance(search.evidence,dict),"invalid ModelSearch field types")
            json_value(asdict(search))
            row["search"] = asdict(search)
            row["checks"]["model_search"] = search.status
            actions = search.actions
            if search.status == "unknown":
                row["status"] = "unknown"
                returned(task.task_id, stage, search.reason, actions,code="search_limited",limited=True)
                continue
            if search.status == "unsolvable":
                if not (search.complete_model and search.exhausted):
                    row["status"] = "unknown"
                    returned(task.task_id, stage, "unsolvability_not_proven", actions,code="search_limited",limited=True)
                else:
                    returned(task.task_id, stage, search.reason or "model_exhausted", actions,code="model_exhausted")
                continue
            if search.status != "candidate":
                raise Fault("output_protocol_invalid", "invalid model search status")
            stage = "engine_replay"
            verdict = plugin.replay(task, search)
            protocol(isinstance(verdict,EngineVerdict),"replay must return EngineVerdict")
            protocol(isinstance(verdict.accepted,bool) and isinstance(verdict.trace,(tuple,list)) and isinstance(verdict.binding,dict) and isinstance(verdict.reason,str),"invalid EngineVerdict field types")
            protocol(verdict.failed_action_index is None or (isinstance(verdict.failed_action_index,int) and not isinstance(verdict.failed_action_index,bool) and 0 <= verdict.failed_action_index),"invalid failed action index")
            json_value(asdict(verdict))
            row["replay"] = asdict(verdict)
            row["checks"]["engine_replay"] = "pass" if verdict.accepted is True else "return"
            if verdict.accepted is True and require_design and (not verdict.trace or not verdict.binding):
                row["checks"]["engine_replay"] = "return"
                returned(task.task_id, stage, "accepted replay requires trace and environment binding", actions,code="output_protocol_invalid")
                continue
            if verdict.accepted is True:
                if require_design:
                    stage = "fulfillment"
                    from .blueprint import validate_fulfillment
                    assessment = plugin.assess(task, search, verdict)
                    protocol(isinstance(assessment,FulfillmentVerdict) and isinstance(assessment.checks,dict) and isinstance(assessment.reason,str),"assess must return FulfillmentVerdict with claim-keyed checks")
                    json_value(asdict(assessment))
                    row["fulfillment"] = asdict(assessment)
                    errors = validate_fulfillment(task, assessment)
                    statuses = [v.get("status") for v in assessment.checks.values() if isinstance(v, dict)]
                    outcome = "return" if errors or "return" in statuses else "unknown" if "unknown" in statuses else "pass"
                    row["checks"]["fulfillment"] = outcome
                    row["status"] = outcome
                    if outcome != "pass": returned(task.task_id, stage, "; ".join(errors) or assessment.reason or "claim_not_demonstrated", actions,code="claim_invalid" if errors else "claim_inconclusive",limited=outcome=="unknown")
                else:
                    row["status"] = "pass"
            else:
                returned(task.task_id, stage, verdict.reason or "engine_rejected", actions,
                         verdict.failed_action_index,code="engine_mismatch")
        except ArchiveWriteError: raise
        except Exception as exc:
            if stage in row["checks"]: row["checks"][stage] = "return"
            returned(task.task_id, stage, type(exc).__name__, actions,code=getattr(exc,"diagnostic_code","exception"),exc=exc)
    if design:
        covered = {task.construction.get("slot_id") for task in tasks if isinstance(task.construction.get("slot_id"),str)}
        if covered != {slot["id"] for slot in design[1]["slots"]}:
            returned(None, "blueprint_coverage", "generated tasks do not cover the declared slots",code="structure_invalid")
    # These whole-unit gates run after every per-task replay, including on partial
    # runs. Hook presence and evidence are part of the plugin output protocol.
    def run_unit_hook(name, args, stage):
        hook = getattr(plugin, name, None)
        if not callable(hook):
            if not require_unit_checks:
                return {"status": "unknown", "checks": {}, "reason": "artifact_revision"}
            raise Fault("output_protocol_invalid", f"plugin missing required {name} hook")
        try:
            verdict = hook(*args)
        except ArchiveWriteError:
            raise
        except Exception as exc:
            returned(None, stage, type(exc).__name__, code=getattr(exc, "diagnostic_code", "exception"), exc=exc, limited=True)
            return {"status": "unknown", "checks": {}, "reason": type(exc).__name__}
        protocol(isinstance(verdict, UnitVerdict), f"{name} must return UnitVerdict")
        protocol(verdict.status in ("pass", "return", "unknown") and isinstance(verdict.checks, dict) and isinstance(verdict.reason, str), f"invalid {name} verdict")
        protocol(bool(verdict.checks), f"{name} requires evidence checks")
        for key, check in verdict.checks.items():
            protocol(isinstance(key, str) and key and isinstance(check, dict) and check.get("status") in ("pass", "return", "unknown"), f"{name} checks require named statuses")
            ev = check.get("evidence")
            protocol(isinstance(ev, dict) and isinstance(ev.get("kind"), str) and ev["kind"] and ev.get("data") is not None, f"{name} checks require typed evidence")
            protocol(not (check.get("status") == "pass" and ev.get("kind", "").lower() in {"text", "string"} and isinstance(ev.get("data"), str) and ev["data"].strip().lower() in {"pass", "passed", "ok"}), f"{name} cannot use a pass word as sole evidence")
        json_value(asdict(verdict))
        statuses=[row["status"] for row in verdict.checks.values()]
        if verdict.status=="pass":
            protocol(all(status=="pass" for status in statuses),"unit pass contradicts individual checks")
        if verdict.status!="pass": returned(None,stage,verdict.reason or "unit_check_not_passed",code="claim_inconclusive",limited=verdict.status=="unknown")
        return asdict(verdict)
    try:
        unit_checks = run_unit_hook("validate_unit", (tasks, rows, runtime_context), "unit_validation")
    except ArchiveWriteError: raise
    except Exception as exc:
        unit_checks = {"status": "return", "checks": {}, "reason": "output_protocol_invalid"}
        returned(None, "unit_validation", str(exc), code=getattr(exc, "diagnostic_code", "output_protocol_invalid"), exc=exc)
    try:
        smoke = run_unit_hook("adapter_smoke", (runtime_context,), "adapter_smoke")
    except ArchiveWriteError: raise
    except Exception as exc:
        smoke = {"status": "return", "checks": {}, "reason": "output_protocol_invalid"}
        returned(None, "adapter_smoke", str(exc), code=getattr(exc, "diagnostic_code", "output_protocol_invalid"), exc=exc)
    if require_unit_checks and runtime_context is not None:
        # Bind accepted per-task replays to a machine-owned session in the context
        # receipt. The runtime owns receipt details; do not infer from prose.
        receipt = runtime_context.receipt()
        sessions = receipt.get("sessions", []) if isinstance(receipt, dict) else []
        loaded = receipt.get("loaded_sources", []) if isinstance(receipt, dict) else []
        for row in rows:
            replay = row.get("replay")
            if row.get("status") != "pass" or not isinstance(replay, dict): continue
            binding = replay.get("binding", {})
            sid = binding.get("session_id") if isinstance(binding, dict) else None
            session = next((s for s in sessions if isinstance(s, dict) and s.get("session_id") == sid and s.get("machine_owned") is True), None)
            trace = replay.get("trace", [])
            trajectory = session.get("trajectory", []) if session else []
            source = next((s for s in loaded if isinstance(s, dict) and s.get("id") == session.get("source_id")), None) if session else None
            valid = bool(session and source and source.get("path") and source.get("sha256") and isinstance(trajectory, list)
                         and any(isinstance(x, dict) and x.get("op") == "reset" for x in trajectory)
                         and bool(trace) and list(trace) == [x.get("result") for x in trajectory if isinstance(x, dict) and x.get("op") in {"reset", "step"}]
                         and all("result" in x and "exception_type" not in x for x in trajectory)
                         and sum(x.get("op")=="step" for x in trajectory)==len(row.get("search",{}).get("actions",[])))
            if not valid:
                row["status"] = "return"
                row["checks"]["engine_replay"] = "return"
                returned(row["task_id"], "engine_replay", "accepted replay lacks matching machine session evidence", row.get("search", {}).get("actions", ()), code="output_protocol_invalid")
        if smoke.get("status")=="pass":
            smoke_refs=[check.get("evidence",{}).get("data",{}).get("session_id") for check in smoke.get("checks",{}).values() if isinstance(check.get("evidence",{}).get("data"),dict)]
            if not any(s.get("session_id") in smoke_refs and s.get("machine_owned") is True and {x.get("op") for x in s.get("trajectory",[])} >= {"reset","step"} for s in sessions):
                smoke["status"]="return"
                returned(None,"adapter_smoke","smoke needs a machine session with reset and step",code="output_protocol_invalid")
    # Inputs changing during execution invalidate all results.
    try:
        if inputs.fingerprints() != fingerprints:
            returned(None, "inputs", "frozen_inputs_changed",code="input_drift")
    except OSError as exc:
        returned(None, "inputs", "frozen_inputs_unavailable",code="input_drift",exc=exc)
    if any(row["status"] == "return" for row in rows) or any(f["task_id"] is None and f.get("stage") not in {"unit_validation","adapter_smoke"} for f in feedback):
        status = "return"
    elif require_unit_checks and (unit_checks.get("status") != "pass" or smoke.get("status") != "pass"):
        if unit_checks.get("status") == "unknown" or smoke.get("status") == "unknown": status = "unknown"
        else: status = "return"
    elif any(row["status"] == "return" for row in rows):
        status = "return"
    elif any(row["status"] == "unknown" for row in rows):
        status = "unknown"
    else:
        status = "pass"
    plugin_id = getattr(plugin,"plugin_id",None)
    if not isinstance(plugin_id,str): plugin_id = type(plugin).__name__
    result = {"schema_version": "arcagi-production/v1", "plugin_id": plugin_id,
              "seed": seed, "cap": cap, "frozen_inputs": fingerprints,
              "status": status, "tasks": rows, "feedback": feedback,
              "completion_scope": "strict_unit" if require_unit_checks else ("design_and_engine" if require_design else "engine_only"),
              "unit_checks": unit_checks, "adapter_smoke": smoke,
              "runtime_receipt": runtime_context.receipt() if runtime_context is not None else None,
              "fulfillment_evidence_origin": "plugin supplied checks; independent review remains separate",
              "diagnostics":current().serial()}
    result = scrub(json.loads(json.dumps(result, ensure_ascii=False)))
    from .budget import atomic_json
    atomic_json(output_dir / "production.json", result)
    for item in feedback:
        current().archive.attach(item["event_id"],[file_ref(output_dir/"production.json")],selector={"task_id":item["task_id"],"stage":item["stage"]})
    return result
