"""Offline, evidenced continuation across an explicitly approved core repair."""
from __future__ import annotations
import copy
from pathlib import Path
from .budget import file_lock,atomic_json
from .diagnostics import Fault
from .controller import (_read,_hash,_baseline,_absolute_spec,_load_config,
                         _check_baseline,_verified_ref,_save,_event)
from .agent_workflow import WorkflowRunner,_schema_errors
from .revision_protocol import review_schema

def control_fork(config,source_run,run_root,previous_core):
    """No provider construction, no requests, no counter resets, no target guessing.

    previous_core is an archived arcagi_pipeline package directory. Its bytes must
    match every old pinned core file. Non-core baselines must still match in place.
    Every review and its descendants rerun under the repaired protocol.
    """
    source=Path(source_run).resolve();root=Path(run_root).resolve()
    prior_core=Path(previous_core).resolve();config=Path(config).resolve()
    if root.exists() or source==root:raise Fault("config_invalid","fresh fork directory required")
    with file_lock(source/".controller.lock"):
        old=_read(source/"state.json");cfg,spec,runtime=_load_config(config)
        pinned_cores={Path(raw).parent for raw in old["baseline"] if Path(raw).name=="controller.py" and Path(raw).parent.name=="arcagi_pipeline"}
        if len(pinned_cores)!=1:raise Fault("artifact_changed","predecessor core identity missing")
        pinned_core=next(iter(pinned_cores))
        # Archived package identity is checked against pinned filenames, never trusted by label.
        for raw,digest in old["baseline"].items():
            path=Path(raw)
            if path.parent==pinned_core and path.suffix==".py":
                path=prior_core/path.name
            if not path.is_file() or _hash(path)!=digest:raise Fault("artifact_changed","predecessor baseline mismatch")
        _check_baseline(old.get("revision_files",{}))
        if _hash(old["workflow_spec"])!=old["spec_sha256"]:raise Fault("artifact_changed")
        original=_absolute_spec(Path(old["initial_spec"]));new=_absolute_spec(spec)
        if original.get("game")!=new.get("game") or original.get("provider")!=new.get("provider") or original["budget"]!=new["budget"]:raise Fault("config_invalid","fork cannot change game, provider or budget")
        if [t["id"] for t in original["tasks"]]!=[t["id"] for t in new["tasks"]]:raise Fault("config_invalid","fork task graph changed")
        for a,b in zip(original["tasks"],new["tasks"]):
            left=copy.deepcopy(a);right=copy.deepcopy(b)
            if a.get("gate_review") is True:
                if b["output_schema"] not in (a["output_schema"],review_schema(a,original["tasks"])):raise Fault("config_invalid","fork changed review meaning")
                left.pop("output_schema",None);right.pop("output_schema",None)
            for task in (left,right):
                for item in task.get("inputs",[]):
                    if item.get("role") in {"production_api","design_validation_api","runtime_api"}:
                        item.pop("sha256",None)
            if left!=right:raise Fault("config_invalid","fork changed semantic task inputs")
        # A repaired core must not inherit an unverified environment identity.
        from .runtime_control import preflight_runtime
        facts=preflight_runtime(runtime)
        if old.get("runtime_fingerprint")!=facts["fingerprint"]:raise Fault("artifact_changed","runtime changed at fork")
        workflow=Path(old["current_workflow"]);record=workflow/"workflow_run.json"
        previous=_read(record)
        if previous["spec_sha256"]!=old["spec_sha256"]:raise Fault("artifact_changed")
        rows={r["id"]:r for r in previous["tasks"]}
        for row in rows.values():
            if row.get("status")=="complete":
                path=(workflow/row["output_path"]).resolve()
                if not path.is_relative_to(workflow.resolve()) or not path.is_file() or _hash(path)!=row["output_sha256"]:raise Fault("artifact_changed")
        if any(a.get("status") in {"running","output_staged"} for a in previous.get("attempt_log",[])):raise Fault("config_invalid","reconcile interrupted requests before fork")
        from .budget import AggregateBudget
        ledger=Path(new["budget"]["aggregate_ledger"])
        snapshot=AggregateBudget(ledger).snapshot()
        if any(r["status"]=="reserved" for r in snapshot.get("runtime_requests",{}).values()):raise Fault("config_invalid","unsettled aggregate request at fork")
        invalid={t["id"] for t in new["tasks"] if t.get("gate_review")}
        while True:
            more={t["id"] for t in new["tasks"] if set(t.get("depends_on",[]))&invalid}
            if more<=invalid:break
            invalid|=more
        imported=[]
        for task in new["tasks"]:
            tid=task["id"];row=rows[tid]
            if task.get("gate_review"):task["output_schema"]=review_schema(task,new["tasks"])
            if tid in invalid or row["status"]!="complete":continue
            path=(workflow/row["output_path"]).resolve()
            if not path.is_relative_to(workflow.resolve()) or _hash(path)!=row["output_sha256"]:raise Fault("artifact_changed")
            if _schema_errors(_read(path),task["output_schema"]):raise Fault("output_protocol_invalid")
            task["frozen_output"]={"path":str(path),"sha256":row["output_sha256"]}
            if task.get("evidence_lane")=="trajectory_blind":
                task["frozen_origin"]={"record_path":str(record),"record_sha256":_hash(record),"spec_path":old["workflow_spec"],"spec_sha256":old["spec_sha256"],"task_id":tid}
            imported.append(tid)
        root.mkdir(parents=True)
        checkpoint=root/"checkpoint";checkpoint.mkdir()
        atomic_json(checkpoint/"predecessor-state.json",old)
        new_spec=checkpoint/"workflow.json";atomic_json(new_spec,new)
        new_cfg={**cfg,"workflow_spec":str(new_spec),"runtime_config":str(runtime)}
        new_config=checkpoint/"control.json";atomic_json(new_config,new_cfg)
        WorkflowRunner(new_spec,plan_only=True)
        receipt={"operation":"explicit_core_repair_fork","source_run":str(source),"predecessor_state":_verified_ref(checkpoint/"predecessor-state.json"),"predecessor_workflow":_verified_ref(record),"previous_core":str(prior_core),"imported_tasks":imported,"recheck_tasks":sorted(invalid),"ledger":_verified_ref(ledger),"budget_reset":False,"model_requests":0,"new_config":_verified_ref(new_config)}
        atomic_json(checkpoint/"migration.json",receipt)
        state={"schema_version":"arcagi-controller/v1","status":"paused","revision":old["revision"],"started":old["started"],"initial_spec":str(new_spec),"workflow_spec":str(new_spec),"spec_sha256":_hash(new_spec),"baseline":_baseline(new_config,new_spec,runtime),"history":[],"phase":"workflow","current_workflow":None,"runtime_fingerprint":facts["fingerprint"],"predecessor":_verified_ref(checkpoint/"migration.json")}
        _save(root,state);_event(root,"repair_checkpoint_prepared",**receipt)
        old.update(status="retired",successor=str(root));_event(source,"retired_for_core_repair",successor=str(root),migration=_verified_ref(checkpoint/"migration.json"));_save(source,old)
        return {"status":"paused","run_dir":str(root),"config":str(new_config),"migration":_verified_ref(checkpoint/"migration.json"),"model_requests":0}
