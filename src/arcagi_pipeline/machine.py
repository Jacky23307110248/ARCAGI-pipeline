"""Bridge hashed workflow outputs to explicit local code execution and revision inputs."""
from __future__ import annotations
import hashlib
import ast
import math
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from .production import FrozenInputs, run_production
from .diagnostics import traced, current, ACTIVE, Context, Fault, ArchiveWriteError, scrub, packets, file_ref

ROLES = ("evidence", "rules", "blueprint", "implementation")
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def write(path, data): path.write_text(json.dumps(data, ensure_ascii=True, indent=2)+"\n", encoding="utf-8")
def read_production(path, contract=None, expected_hash=None, *, raw=None):
    """Read one published result and optionally verify its confirmed identity."""
    code = "artifact_changed" if expected_hash else "output_protocol_invalid"
    try:
        if raw is None:
            raw = path.read_bytes()
        if expected_hash and hashlib.sha256(raw).hexdigest() != expected_hash:
            raise Fault(code, "confirmed production evidence changed")
        result = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Fault(code, "production result unavailable or invalid JSON") from exc
    versions = {"generated-game/v2": "arcagi-production/v2", "design-production/v1": "arcagi-production/v1"}
    allowed = {versions[contract]} if contract in versions else set(versions.values())
    if (not isinstance(result, dict) or not isinstance(result.get("schema_version"),str) or result["schema_version"] not in allowed
            or result.get("status") not in ("pass", "return", "unknown")
            or not isinstance(result.get("feedback"), list) or not isinstance(result.get("tasks"), list)
            or any(not isinstance(row, dict) or not isinstance(row.get("task_id"), str) for row in result["tasks"])):
        raise Fault(code, "production result violates its protocol")
    if any(not isinstance(item,dict) for item in result["feedback"]):
        raise Fault(code,"production feedback is malformed")
    units=result.get("units",[])
    if (not isinstance(units,list)
            or any(not isinstance(unit,dict) or not isinstance(unit.get("unit_id"),str) for unit in units)):
        raise Fault(code,"production unit records are malformed")
    for row in result["tasks"]:
        fields=("checks","search","replay","fulfillment","obligations","probes","level_smoke")
        if any(key in row and not isinstance(row[key],dict) for key in fields):
            raise Fault(code,"production check records are malformed")
        if row.get("search") and not isinstance(row["search"].get("status"),str):
            raise Fault(code,"production search status is malformed")
        if row.get("replay") and type(row["replay"].get("accepted")) is not bool:
            raise Fault(code,"production replay verdict is malformed")
    if result["status"] == "pass":
        if not result["tasks"] or result["feedback"] or any(row.get("status") != "pass" for row in result["tasks"]):
            raise Fault(code, "passing result contradicts its checks")
        if result["schema_version"] == "arcagi-production/v2":
            if result.get("completion_scope") != "generated_unit_verified/v2" or not units:
                raise Fault(code, "generated unit completion evidence required")
            if any(unit.get("status") != "pass" for unit in units):
                raise Fault(code, "generated unit completion contradicts unit results")
    return result


def confirmed_production(output, report):
    expected_hash = report.get("production_sha256")
    if not expected_hash:
        if report.get("status") == "pass":
            raise Fault("artifact_changed", "passing execution lacks production evidence")
        return None
    result = read_production(output / "production/production.json", expected_hash=expected_hash)
    if result["status"] != report.get("status"):
        raise Fault("artifact_changed", "production and machine statuses differ")
    return result


def safe(root, raw, code="output_protocol_invalid"):
    if not isinstance(raw, str) or not raw or "\\" in raw or ":" in raw: raise Fault(code,"invalid artifact path")
    p = (root/raw).resolve()
    if not p.is_relative_to(root.resolve()) or p == root.resolve(): raise Fault(code,"artifact path escapes root")
    return p

def collect(workflow_run: Path, roles: dict) -> dict:
    root = workflow_run.resolve()
    record = json.loads((root/"workflow_run.json").read_text(encoding="utf-8"))
    if record.get("schema_version") != "arcagi-workflow-run/v1" or record.get("status") != "complete":
        raise Fault("workflow_not_ready","completed workflow run required")
    if set(roles) != set(ROLES) or len(set(roles.values())) != len(ROLES):
        raise Fault("config_invalid","roles must name distinct evidence/rules/blueprint/implementation tasks")
    tasks = {r["id"]:r for r in record["tasks"]}
    result = {}
    for role, task_id in roles.items():
        row = tasks[task_id]
        path = safe(root, row["output_path"],"artifact_changed")
        if row["status"] != "complete" or sha(path) != row["output_sha256"]:
            raise Fault("artifact_changed","workflow artifact changed: " + role)
        result[role] = {"value":json.loads(path.read_text(encoding="utf-8")), "sha256":sha(path), "task_id":task_id}
    return result

def materialize(bundle: dict, directory: Path):
    if not isinstance(bundle,dict): raise Fault("output_protocol_invalid","implementation bundle must be an object")
    files = bundle.get("files")
    if not isinstance(files, list) or not files: raise Fault("output_protocol_invalid","implementation requires files")
    destinations = []
    for row in files:
        if not isinstance(row,dict) or "path" not in row: raise Fault("output_protocol_invalid","file records require path and content")
        target = safe(directory, row["path"])
        if target in destinations or not isinstance(row.get("content"), str): raise Fault("output_protocol_invalid","duplicate or invalid file")
        if target.suffix == ".py":
            try: ast.parse(row["content"],filename=row["path"])
            except (SyntaxError,ValueError): raise Fault("output_protocol_invalid","supplied Python file has invalid syntax") from None
        destinations.append(target)
    entry = safe(directory, bundle.get("entrypoint"))
    if entry not in destinations or entry.suffix != ".py": raise Fault("output_protocol_invalid","entrypoint must name a supplied Python file")
    if not isinstance(bundle.get("factory"),str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", bundle["factory"]): raise Fault("output_protocol_invalid","invalid factory")
    directory.mkdir()
    inventory = []
    for row, path in zip(files, destinations):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(row["content"], encoding="utf-8")
        inventory.append({"path":row["path"],"sha256":sha(path)})
    return inventory

@traced("handoff")
def execute_handoff(workflow_run: Path, roles: dict, output: Path, *, seed=0, cap=250_000, timeout=300, runtime_config=None, require_unit_checks=False, production_contract="generated-game/v2",diagnostic_requests=None):
    if isinstance(timeout,bool) or not isinstance(timeout,(int,float)) or not math.isfinite(timeout) or timeout <= 0: raise Fault("config_invalid","timeout must be positive")
    if isinstance(cap,bool) or not isinstance(cap,int) or cap < 1: raise Fault("config_invalid","cap must be a positive integer")
    if isinstance(seed,bool) or not isinstance(seed,int): raise Fault("config_invalid","seed must be an integer")
    if output.exists():
        exc=FileExistsError(output);exc.diagnostic_code="output_exists";raise exc
    output.mkdir(parents=True)
    production = None
    stage = "artifact_handoff"
    report = {"schema_version":"arcagi-machine/v1", "status":"return", "stage":stage}
    try:
        outputs = collect(workflow_run, roles)
        report["workflow_outputs"] = {role:{k:v for k,v in row.items() if k != "value"} for role,row in outputs.items()}
        stage = "runtime_preflight"
        if runtime_config is None: raise Fault("config_invalid","execution requires an explicit runtime_config")
        runtime_config = Path(runtime_config).resolve()
        from .runtime_control import runtime_command,preflight_runtime
        verified_runtime = preflight_runtime(runtime_config)
        python, env, runtime_facts = runtime_command(runtime_config)
        runtime_facts["verified"] = verified_runtime
        runtime_hash = sha(runtime_config)
        report["runtime"] = runtime_facts
        stage = "artifact_handoff"
        for role in ("evidence", "rules", "blueprint"): write(output/(role+".json"), outputs[role]["value"])
        from .blueprint import validate_design
        from .generated_game import target_issues
        validator = target_issues if production_contract == "generated-game/v2" else validate_design
        errors = validator(*(outputs[r]["value"] for r in ("evidence", "rules", "blueprint")))
        if errors:
            report["revision_roles"]=sorted({issue["artifact"] for issue in errors if isinstance(issue,dict)})
            failure=Fault("design_invalid","design protocol checks failed")
            failure.diagnostic_ref=current().record("design_invalid",stage,observed={"errors":errors},refs=[file_ref(output/(role+".json")) for role in ("evidence","rules","blueprint")])
            raise failure
        stage = "materialization"
        bundle = outputs["implementation"]["value"]
        inventory = materialize(bundle, output/"plugin")
        report["files"] = inventory
        from .handoff_control import operation_plan
        requests = diagnostic_requests
        if requests is None:
            requests = []
            for row in outputs.values():
                diagnosis = row["value"].get("diagnosis")
                check = diagnosis.get("next_check") if isinstance(diagnosis, dict) else None
                if isinstance(check, dict) and isinstance(check.get("operation"), str):
                    requests.append({"task_id":row["task_id"],"operation":check["operation"]})
        write(output/'operation-plan.json',{'requests':requests,'plan':operation_plan(requests,production_contract)})
        report['operation_plan_sha256']=sha(output/'operation-plan.json')
        if production_contract not in {"generated-game/v2", "design-production/v1"}: raise Fault("config_invalid","unknown production contract")
        execution = {"diagnostics":current().serial(),"entrypoint":bundle["entrypoint"],"factory":bundle["factory"],"files":inventory,"seed":seed,"cap":cap,"runtime_config":str(runtime_config),"runtime_config_sha256":runtime_hash,"require_unit_checks":require_unit_checks,"production_contract":production_contract}
        execution["operation_plan_sha256"]=report["operation_plan_sha256"]
        write(output/"execution.json", execution)
        stage = "execution"
        # Runtime roots are explicit; no ambient Python path or provider credentials.
        runtime_data = json.loads(runtime_config.read_text(encoding="utf-8"))
        roots = [str(Path(__file__).resolve().parents[1])] + [str((runtime_config.parent / root).resolve()) for root in runtime_data.get("import_roots",[])]
        bootstrap = "import sys,runpy;sys.path[:0]="+repr(roots)+";sys.argv=['arcagi_pipeline.machine','--worker',"+repr(str(output.resolve()))+"];runpy.run_module('arcagi_pipeline.machine',run_name='__main__')"
        cmd = [python,"-I","-c",bootstrap]
        # stdout/stderr are opaque, unused plugin logs. Decode only the declared
        # UTF-8 report file below, never arbitrary log bytes via host locale.
        try: proc = subprocess.run(cmd, env=env, cwd=output.resolve(), capture_output=True, timeout=timeout)
        except OSError as exc: raise Fault("process_start_failed","declared worker could not be started") from None
        if sha(runtime_config) != runtime_hash: raise Fault("artifact_changed","runtime configuration changed during execution")
        report["process_exit_code"] = proc.returncode
        # Plugin output may contain arbitrary text: no automatic credential/log capture.
        production_path = output/"production"/"production.json"
        if proc.returncode == 0 and production_path.is_file():
            raw = production_path.read_bytes()
            production = read_production(production_path, production_contract, raw=raw)
            report["status"] = production["status"]
            report["completion_scope"] = production.get("completion_scope","unverified")
            report["production_sha256"] = hashlib.sha256(raw).hexdigest()
            report["feedback"] = production["feedback"]
        else:
            report["error"] = "plugin worker failed; inspect worker_diagnostic for location"
            diagnostic = output/"worker_error.json"
            if diagnostic.is_file():
                report["worker_diagnostic"] = json.loads(diagnostic.read_text(encoding="utf-8"))
                ref=report["worker_diagnostic"].get("diagnostic_ref")
            else: ref=None
            if ref is None: ref=current().record("worker_failed",stage,observed={"exit_code":proc.returncode})
            report["feedback"]=[ref]
    except ArchiveWriteError: raise
    except Exception as exc:
        code="worker_timeout" if isinstance(exc,subprocess.TimeoutExpired) else getattr(exc,"diagnostic_code",None) or "exception"
        ref=getattr(exc,"diagnostic_ref",None) or current().record(code,stage,exc=exc)
        report["error"] = code
        report["feedback"]=[ref]
    if 'operation_plan_sha256' in report:
        from .handoff_control import operation_receipt
        plan_path=output/'operation-plan.json'
        if sha(plan_path)!=report['operation_plan_sha256']:raise Fault('artifact_changed','operation plan changed')
        requests=json.loads(plan_path.read_text(encoding='utf-8'))['requests']
        receipts=[{**operation_receipt(r['operation'],production or {},production_contract),'task_id':r['task_id'],'evidence_ref':{'path':str((output/'production/production.json').resolve()),'sha256':report['production_sha256']} if production is not None else None} for r in requests]
        write(output/'diagnostic-operations.json',{'schema_version':'arcagi-operation-receipts/v1','receipts':receipts})
        report['operation_receipts_sha256']=sha(output/'diagnostic-operations.json')
    report["stage"] = stage
    report["diagnostics"]=current().serial()
    from .budget import atomic_json
    atomic_json(output/"machine.json", scrub(report))
    route_packets=packets(current(),report.get("feedback",[]))
    for route,packet in route_packets.items(): write(output/(route+".json"),packet)
    revision = {"schema_version":"arcagi-revision/v2", "machine":scrub(report),
                "routing":route_packets,"instruction":"Follow the recorded route; undetermined failures require diagnosis before any repair."}
    path = output/"production"/"production.json"
    if production is not None: revision["production"] = scrub(production)
    elif path.is_file(): revision["invalid_production_ref"] = file_ref(path)
    write(output/"revision_packet.json", revision)
    for item in report.get("feedback",[]):
        current().archive.attach(item["event_id"],[file_ref(output/"machine.json"),file_ref(output/"revision_packet.json")])
    return report

@traced("worker")
def worker(root):
    execution = json.loads((root/"execution.json").read_text(encoding="utf-8"))
    requests=[]
    if execution.get('operation_plan_sha256'):
        plan=root/'operation-plan.json'
        if sha(plan)!=execution['operation_plan_sha256']:raise Fault('artifact_changed','operation plan changed')
        requests=json.loads(plan.read_text(encoding='utf-8'))['requests']
    plugin_root = root/"plugin"
    for row in execution["files"]:
        if sha(safe(plugin_root,row["path"])) != row["sha256"]: raise Fault("artifact_changed","plugin file changed")
    entry = safe(plugin_root, execution["entrypoint"])
    sys.path.insert(0,str(plugin_root))
    spec = importlib.util.spec_from_file_location("_generated_plugin",entry,submodule_search_locations=[str(entry.parent)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        from .runtime_control import RuntimeContext
        runtime_path = Path(execution["runtime_config"])
        if sha(runtime_path) != execution["runtime_config_sha256"]: raise Fault("artifact_changed","runtime configuration changed before worker load")
        runtime_context = RuntimeContext(runtime_path)
        factory = getattr(module,execution["factory"])
        import inspect
        try: inspect.signature(factory).bind(runtime_context)
        except TypeError:
            if execution.get("require_unit_checks") or execution.get("production_contract") == "generated-game/v2": raise Fault("output_protocol_invalid","controlled factory must accept runtime context")
            plugin = factory()
        else: plugin = factory(runtime_context)
    except (ImportError,AttributeError,SyntaxError) as exc:
        exc.diagnostic_code="module_load_failed"
        raise
    if execution.get("production_contract") == "generated-game/v2":
        from .generated_game import run_generated_production
        run_generated_production(plugin,FrozenInputs(root/"rules.json",root/"blueprint.json",root/"evidence.json"),
                                 seed=execution["seed"],cap=execution["cap"],output_dir=root/"production",runtime_context=runtime_context,
                                 operation_requests=requests,
                                 probe_factory={'runtime_config':str(runtime_path),'runtime_config_sha256':execution['runtime_config_sha256'],
                                                'module_path':str(entry),'factory_name':execution['factory'],
                                                'file_refs':[{'path':str(safe(plugin_root,r['path'])),'sha256':r['sha256']} for r in execution['files']]})
        return
    run_production(plugin,FrozenInputs(root/"rules.json",root/"blueprint.json",root/"evidence.json"),
                   seed=execution["seed"],cap=execution["cap"],output_dir=root/"production",require_design=True,runtime_context=runtime_context if execution.get("require_unit_checks") else None,require_unit_checks=execution.get("require_unit_checks",False))

@traced("machine_preflight","config_invalid")
def plan_machine(config_path: Path):
    from .agent_workflow import WorkflowRunner
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(config,dict): raise Fault("config_invalid","machine configuration must be an object")
    roles = config.get("roles", {})
    if not isinstance(roles,dict) or set(roles) != set(ROLES) or any(not isinstance(v,str) or not v for v in roles.values()) or len(set(roles.values())) != len(ROLES):
        raise Fault("config_invalid","roles must name distinct evidence/rules/blueprint/implementation tasks")
    for key, default in (("cap",250_000),("timeout_seconds",300)):
        value = config.get(key,default)
        if isinstance(value,bool) or not isinstance(value,int) or value <= 0:
            raise Fault("config_invalid",key + " must be a positive integer")
    spec = (config_path.parent/config["workflow_spec"]).resolve()
    runner = WorkflowRunner(spec,plan_only=True)
    tasks = {task["id"]:task for task in runner.spec["tasks"]}
    if any(t not in tasks for t in roles.values()): raise Fault("config_invalid","role refers to unknown workflow task")
    from .execution_contract import validate_execution_inputs
    try:
        validate_execution_inputs(tasks[roles["implementation"]], spec.parent, required=True)
        contract = config.get("production_contract", "generated-game/v2")
        if contract not in {"generated-game/v2", "design-production/v1"} or tasks[roles["implementation"]].get("execution_contract") != contract:
            raise ValueError("machine and implementation production contracts must match")
    except ValueError as exc:
        raise Fault("config_invalid", str(exc)) from None
    required = {"rules":["evidence"],"blueprint":["evidence","rules"],"implementation":["rules","blueprint"]}
    for role, upstream in required.items():
        supplied = {item.get("from_task") for item in tasks[roles[role]].get("inputs",[])}
        if not {roles[r] for r in upstream}.issubset(supplied):
            raise Fault("config_invalid",role + " task lacks required upstream artifact inputs")
    if not isinstance(config.get("runtime_config"),str): raise Fault("config_invalid","machine configuration requires runtime_config")
    runtime_path = (config_path.parent/config["runtime_config"]).resolve()
    from .runtime_control import preflight_runtime
    plan = runner.plan()
    plan["runtime"] = preflight_runtime(runtime_path)
    config["_runtime_config_path"] = str(runtime_path)
    return config, spec, plan

@traced("machine_run")
def run_machine(config_path: Path, workflow_run: Path, output: Path, *, env_file=None, provider=None, retry_failed=False):
    from .agent_workflow import WorkflowRunner
    config, spec, plan = plan_machine(config_path)
    runner = WorkflowRunner(spec,workflow_run,provider=provider,env_file=env_file)
    runner.run(retry_failed=retry_failed)
    result = execute_handoff(workflow_run,config["roles"],output,seed=config.get("seed",0),
                             cap=config.get("cap",250_000),timeout=config.get("timeout_seconds",300),runtime_config=Path(config["_runtime_config_path"]),require_unit_checks=config.get("require_unit_checks",False),production_contract=config.get("production_contract","generated-game/v2"))
    write(output/"workflow_plan.json",plan)
    return result

if __name__ == "__main__":
    root = Path(sys.argv[2]).resolve()
    context=Context.restore(json.loads((root/"execution.json").read_text(encoding="utf-8"))["diagnostics"])
    try: worker(root,_diagnostics=context)
    except Exception as exc:
        import traceback
        write(root/"worker_error.json", {"diagnostic_ref":getattr(exc,"diagnostic_ref",None),"type":type(exc).__name__,"frames":[{"file":Path(f.filename).name,"line":f.lineno,"function":f.name} for f in traceback.extract_tb(exc.__traceback__)]})
        raise SystemExit(1)
