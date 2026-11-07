from __future__ import annotations

import argparse
import json
from .diagnostics import Archive, ArchiveWriteError, traced, current, scrub
import re
from pathlib import Path

from .harness import HarnessRunSpec
from .harness_adapters.duck import DuckAdapter
from .harness_adapters.tycho import TychoAdapter


def validate_manifest(path: Path) -> list[str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [str(exc)]
    errors: list[str] = []
    if raw.get("schema_version") != "arcagi-pipeline-manifest/v2":
        errors.append("schema_version must be arcagi-pipeline-manifest/v2")
    if not isinstance(raw.get("pipeline_id"), str) or not raw["pipeline_id"]:
        errors.append("pipeline_id is required")
    game = raw.get("game")
    if not isinstance(game, dict):
        errors.append("game must be an object")
    else:
        for key in ("id", "version", "plugin_id", "plugin_version"):
            if not isinstance(game.get(key), str) or not game[key]:
                errors.append(f"game.{key} is required")
        if not isinstance(game.get("extension"), dict):
            errors.append("game.extension must be an object")
    materials = raw.get("materials")
    if not isinstance(materials, list):
        errors.append("materials must be a list")
    else:
        for index, material in enumerate(materials):
            if not isinstance(material, dict):
                errors.append(f"materials[{index}] must be an object")
                continue
            if not re.fullmatch(r"[0-9a-f]{64}", str(material.get("sha256", ""))):
                errors.append(f"materials[{index}].sha256 must be lowercase SHA-256")
            if material.get("role") not in {"source", "level_data", "play_trace", "saved_note", "engine", "other"}:
                errors.append(f"materials[{index}].role is invalid")
    if not isinstance(raw.get("workflow"), dict):
        errors.append("workflow must be an object")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="arcagi-pipeline")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("validate-manifest")
    check.add_argument("path", type=Path)
    workflow_plan = sub.add_parser("workflow-plan", help="Validate and preview a game-neutral agent workflow without API access")
    workflow_plan.add_argument("--spec", type=Path, required=True)
    workflow_run = sub.add_parser("workflow-run", help="Execute or resume a budgeted agent workflow")
    workflow_run.add_argument("--spec", type=Path, required=True)
    workflow_run.add_argument("--run-dir", type=Path, required=True)
    workflow_run.add_argument("--env-file", type=Path, default=None, help="Optional .env file; only provider variables are read and never stored")
    workflow_run.add_argument("--retry-failed", action="store_true", help="Spend another budgeted request for previously failed tasks")
    workflow_status = sub.add_parser("workflow-status", help="Inspect workflow progress, budget use, and artifact hashes")
    workflow_status.add_argument("--run-dir", type=Path, required=True)
    demo = sub.add_parser("demo", help="Run the synthetic offline vertical slice")
    demo.add_argument("--output-dir", type=Path, required=True)
    demo.add_argument("--seed", type=int, default=17)
    prepare = sub.add_parser("harness-prepare", help="Prepare a secret-free original-game harness run")
    prepare.add_argument("--backend", choices=("tycho", "duck"), default="tycho")
    prepare.add_argument("--repo", type=Path, required=True, help="Tycho or Duck source root")
    prepare.add_argument("--game", required=True, help="Original game ID")
    prepare.add_argument("--config", type=Path, default=None, help="Non-secret Tycho YAML/JSON config")
    prepare.add_argument("--game-source-dir", type=Path, default=None, help="Optional exact Tycho game environment directory")
    prepare.add_argument("--environments-dir", type=Path, default=None, help="Explicit local Duck game environment root")
    prepare.add_argument("--model", default="", help="Duck model name; credentials remain in the process environment")
    prepare.add_argument("--max-actions", type=int, default=None, help="Positive Duck action cap; optional Tycho action cap for bounded harness smoke")
    prepare.add_argument("--max-runtime-minutes", type=float, default=10.0, help="Positive per-game Duck runtime bound")
    prepare.add_argument("--run-id", required=True)
    prepare.add_argument("--runs-root", type=Path, default=None)
    prepare.add_argument("--python", default=None)
    prepare.add_argument("--seed", type=int, default=0)
    prepare.add_argument("--operation-mode", choices=("normal", "offline", "online"), default="offline")
    execute = sub.add_parser("harness-execute", help="Explicitly execute a prepared Tycho or Duck run")
    execute.add_argument("--run-id", required=True)
    execute.add_argument("--runs-root", type=Path, default=None)
    execute.add_argument("--resume", action="store_true", help="Resume from Tycho's native checkpoint")
    status = sub.add_parser("harness-status", help="Inspect a prepared or completed Tycho or Duck run")
    status.add_argument("--run-id", required=True)
    status.add_argument("--runs-root", type=Path, default=None)
    trajectory_export = sub.add_parser("trajectory-export", help="Export a source-blind packet from a saved original-game run")
    trajectory_export.add_argument("--run-id", required=True)
    trajectory_export.add_argument("--runs-root", type=Path, default=None)
    trajectory_import = sub.add_parser("trajectory-import", help="Validate and freeze offline trajectory claims")
    trajectory_import.add_argument("--run-id", required=True)
    trajectory_import.add_argument("--claims-file", type=Path, required=True)
    trajectory_import.add_argument("--runs-root", type=Path, default=None)
    legacy_import = sub.add_parser("legacy-trajectory-import", help="Import visible fields from a historic Tycho or Duck run")
    legacy_import.add_argument("--backend", choices=("tycho", "duck"), required=True)
    legacy_import.add_argument("--run-id", required=True)
    legacy_import.add_argument("--game-id", required=True)
    legacy_import.add_argument("--artifact", type=Path, nargs="+", required=True)
    legacy_import.add_argument("--saved-note", type=Path, nargs="*", default=[])
    legacy_import.add_argument("--output", type=Path, required=True)
    legacy_freeze = sub.add_parser("legacy-trajectory-freeze", help="Freeze claims against an imported historic trajectory packet")
    legacy_freeze.add_argument("--packet", type=Path, required=True)
    legacy_freeze.add_argument("--claims", type=Path, required=True)
    legacy_freeze.add_argument("--output", type=Path, required=True)
    game_bind = sub.add_parser("game-bind", help="Fingerprint a Tycho game source tree and runtime")
    game_bind.add_argument("--game-id", required=True)
    game_bind.add_argument("--runtime-profile", choices=("tycho","duck","raw-arcengine"), default="tycho")
    game_bind.add_argument("--tycho-root", type=Path, required=True)
    game_bind.add_argument("--source-dir", type=Path, required=True)
    game_bind.add_argument("--python", required=True)
    game_bind.add_argument("--output", type=Path, required=True)
    contract_freeze = sub.add_parser("rule-contract-freeze", help="Validate and freeze a version-bound rule contract")
    contract_freeze.add_argument("--contract", type=Path, required=True)
    contract_freeze.add_argument("--source-binding", type=Path, required=True)
    contract_freeze.add_argument("--trajectory-ledger", type=Path, action="append", default=[])
    contract_freeze.add_argument("--source-findings", type=Path)
    contract_freeze.add_argument("--probe-suite", type=Path)
    contract_freeze.add_argument("--supporting", action="append", default=[], metavar="ROLE=PATH")
    contract_freeze.add_argument("--output", type=Path, required=True)
    source_export = sub.add_parser("source-audit-export", help="Create a version-bound offline source audit task")
    source_export.add_argument("--run-id", required=True)
    source_export.add_argument("--runs-root", type=Path, default=None)
    source_import = sub.add_parser("source-audit-import", help="Validate and save source audit findings separately")
    source_import.add_argument("--run-id", required=True)
    source_import.add_argument("--findings-file", type=Path, required=True)
    source_import.add_argument("--runs-root", type=Path, default=None)
    preview = sub.add_parser("harness-command")
    preview.add_argument("--backend", choices=("tycho", "duck"), required=True)
    preview.add_argument("--repo", type=Path, required=True)
    preview.add_argument("--game", required=True)
    preview.add_argument("--output-dir", type=Path, required=True)
    preview.add_argument("--python", default=None)
    preview.add_argument("--model", default="")
    preview.add_argument("--approach", default="tycho")
    preview.add_argument("--environments-dir", default="")
    preview.add_argument("--max-actions", type=int, default=None)
    preview.add_argument("--max-runtime-minutes", type=float, default=10.0)
    preview.add_argument("--preflight", action="store_true")
    production = sub.add_parser("production-run", help="Run an explicit frozen-rule game plugin")
    production.add_argument("--runtime-config",type=Path,required=True)
    production.add_argument("--plugin", required=True, help="Importable module:factory")
    production.add_argument("--rule-contract", type=Path, required=True)
    production.add_argument("--blueprint", type=Path, required=True)
    production.add_argument("--seed", type=int, default=21)
    production.add_argument("--cap", type=int, default=250_000)
    production.add_argument("--output-dir", type=Path, required=True)
    production.add_argument("--evidence", type=Path)
    production.add_argument("--require-design", action="store_true")
    production.add_argument("--require-unit-checks",action="store_true")
    production.add_argument("--production-contract", choices=["generated-game/v2", "design-production/v1"], default="generated-game/v2")
    machine_plan = sub.add_parser("machine-plan", help="Inspect a machine workflow without model calls or plugin execution")
    machine_plan.add_argument("--config", type=Path, required=True)
    machine = sub.add_parser("machine-run", help="Run workflow, materialize implementation, execute and export revision feedback")
    machine.add_argument("--config", type=Path, required=True)
    machine.add_argument("--workflow-run", type=Path, required=True)
    machine.add_argument("--output-dir", type=Path, required=True)
    machine.add_argument("--env-file", type=Path)
    machine.add_argument("--retry-failed", action="store_true")
    handoff = sub.add_parser("machine-handoff", help="Execute completed workflow outputs without another model call")
    handoff.add_argument("--config", type=Path, required=True)
    handoff.add_argument("--workflow-run", type=Path, required=True)
    handoff.add_argument("--output-dir", type=Path, required=True)
    fork = sub.add_parser("control-fork",help="Offline verified continuation after an approved core repair")
    fork.add_argument("--config",type=Path,required=True)
    fork.add_argument("--source-run",type=Path,required=True)
    fork.add_argument("--run-dir",type=Path,required=True)
    fork.add_argument("--previous-core",type=Path,required=True)
    recover = sub.add_parser('control-recover',help='Preserve a compatible completed workflow after protocol repair')
    for name in ('source-run','checkpoint-workflow','run-dir','previous-core','history-manifest'):
        recover.add_argument('--'+name,type=Path,required=True)
    recover.add_argument('--additional-revisions',type=int,default=100)
    for operation in ("plan","run","status","resume","pause"):
        control = sub.add_parser("control-"+operation, help="Persistent generic production controller")
        control.add_argument("--run-dir",type=Path,required=operation!="plan")
        if operation not in {"status","pause"}: control.add_argument("--config",type=Path,required=True)
        if operation in {"run","resume"}: control.add_argument("--env-file",type=Path)
        if operation=="resume": control.add_argument("--resolution",type=Path)
    show = sub.add_parser("diagnostic-show", help="Read diagnostic facts, attribution history and continuation links")
    show.add_argument("--log-dir",type=Path,required=True)
    show.add_argument("--event-id")
    attribute = sub.add_parser("diagnostic-attribute",help="Append an evidence-backed attribution without changing facts")
    attribute.add_argument("--log-dir",type=Path,required=True)
    attribute.add_argument("--event-id",required=True)
    attribute.add_argument("--category",choices=("artifact","runtime","undetermined"),required=True)
    attribute.add_argument("--basis",type=Path,required=True,help="JSON list of structured evidence records")
    attribute.add_argument("--source",choices=("operator","independent_review"),default="operator")
    attribute.add_argument("--supersedes")
    attribute.add_argument("--confidence",choices=("provisional","confirmed"),default="provisional")
    resolve = sub.add_parser("diagnostic-resolve",help="Append an event-bound successor check as resolution evidence")
    resolve.add_argument("--log-dir",type=Path,required=True)
    resolve.add_argument("--event-id",required=True)
    resolve.add_argument("--successor-run",required=True)
    resolve.add_argument("--check-file",type=Path,action="append",required=True)
    for command in (machine_plan,machine,handoff,production,workflow_plan,workflow_run):
        command.add_argument("--log-dir",type=Path)
        command.add_argument("--previous-run")
        command.add_argument("--pending-event",action="append",default=[])
    args = parser.parse_args(argv)
    if args.command.startswith("diagnostic-"):
        try:
            archive=Archive(args.log_dir)
            if args.command=="diagnostic-show": result=archive.view(args.event_id)
            elif args.command=="diagnostic-attribute":
                result={"attribution_id":archive.attribute(args.event_id,args.category,basis=json.loads(args.basis.read_text(encoding="utf-8")),source=args.source,confidence=args.confidence,supersedes=args.supersedes)}
            else: result={"resolution_id":archive.resolve(args.event_id,run_id=args.successor_run,check_refs=args.check_file)}
            print(json.dumps(result,ensure_ascii=False,indent=2));return 0
        except (ValueError,OSError,KeyError) as exc:
            print(json.dumps({"status":"return","error_type":type(exc).__name__}));return 1
    path=next((getattr(args,key) for key in ("output_dir","run_dir","workflow_run","config","spec") if getattr(args,key,None) is not None),Path.cwd()/"run")
    root=getattr(args,"log_dir",None) or Path(path).resolve().parent/"error-archive"
    try:
        try: context=Archive(root).start(args.command,previous_run=getattr(args,"previous_run",None),pending_events=getattr(args,"pending_event",()))
        except ArchiveWriteError: raise
        except Exception as exc:
            context=Archive(root).start(args.command)
            ref=context.record("config_invalid","continuation_links",exc=exc)
            print(json.dumps({"status":"return","diagnostic_ref":ref}));return 1
        return _dispatch(args,_diagnostics=context)
    except ArchiveWriteError:
        print(json.dumps({"status":"return","error":"diagnostic_archive_write_failed","facts_saved":"unknown","archive_complete":False}));return 2


def _cli_error(exc):
    if isinstance(exc,ArchiveWriteError): raise exc
    return getattr(exc,"diagnostic_ref",None) or current().record(getattr(exc,"diagnostic_code",None) or "exception","cli",exc=exc)


@traced("cli")
def _dispatch(args):
    if args.command.startswith("control-"):
        from .controller import control_plan,control_run,control_status,control_resume,control_pause
        try:
            if args.command=='control-recover':
                from .resume_checkpoint import recover
                result=recover(args.source_run,args.checkpoint_workflow,args.run_dir,args.previous_core,args.history_manifest,args.additional_revisions)
            elif args.command=="control-fork":
                from .control_checkpoint import control_fork
                result=control_fork(args.config,args.source_run,args.run_dir,args.previous_core)
            elif args.command=="control-pause": result=control_pause(args.run_dir)
            elif args.command=="control-plan": result=control_plan(args.config)
            elif args.command=="control-status": result=control_status(args.run_dir)
            elif args.command=="control-run": result=control_run(args.config,args.run_dir,env_file=args.env_file)
            else: result=control_resume(args.config,args.run_dir,env_file=args.env_file,resolution=args.resolution)
            print(json.dumps(result,ensure_ascii=False,indent=2))
            if args.command in {"control-status","control-pause","control-fork","control-recover"}:return 0
            return 0 if result.get("status") in {"pass","complete","ready"} or result.get("valid") is True else 1
        except (ValueError,OSError,KeyError) as exc:
            print(json.dumps({"status":"return","error":_cli_error(exc)}));return 1
    if args.command == "machine-plan":
        from .machine import plan_machine
        try:
            config, _, plan = plan_machine(args.config)
            print(json.dumps({"workflow":plan,"roles":config["roles"],"execution_started":False},ensure_ascii=False,indent=2))
            return 0
        except (ValueError,OSError,KeyError) as exc:
            print(json.dumps({"valid":False,"error":_cli_error(exc)}))
            return 1
    if args.command in {"machine-run", "machine-handoff"}:
        from .machine import run_machine, execute_handoff
        try:
            if args.command == "machine-run":
                result = run_machine(args.config, args.workflow_run, args.output_dir,
                                     env_file=args.env_file, retry_failed=args.retry_failed)
            else:
                config = json.loads(args.config.read_text(encoding="utf-8"))
                if not isinstance(config.get("runtime_config"),str):
                    from .diagnostics import Fault
                    raise Fault("config_invalid","machine-handoff requires runtime_config")
                result = execute_handoff(args.workflow_run,config["roles"],args.output_dir,
                                         seed=config.get("seed",0),cap=config.get("cap",250_000),
                                         timeout=config.get("timeout_seconds",300),runtime_config=(args.config.parent/config["runtime_config"]).resolve(),require_unit_checks=config.get("require_unit_checks",False),production_contract=config.get("production_contract","generated-game/v2"))
            print(json.dumps({"status":result["status"],"stage":result["stage"],"completion_scope":result.get("completion_scope")}))
            return 0 if result["status"] == "pass" else 1
        except (ValueError, OSError, KeyError, ImportError) as exc:
            print(json.dumps({"status":"return","error":_cli_error(exc)}))
            return 1
    if args.command == "production-run":
        import importlib
        from .production import FrozenInputs, run_production
        try:
            from .runtime_control import runtime_command
            import sys,os,subprocess
            python,env,facts=runtime_command(args.runtime_config)
            runtime_digest=__import__("hashlib").sha256(args.runtime_config.read_bytes()).hexdigest()
            if not os.path.samefile(sys.executable,python) or os.environ.get("ARCAGI_RUNTIME_CLI_CHILD")!=runtime_digest:
                runtime_data=json.loads(args.runtime_config.read_text(encoding="utf-8"))
                roots=[str(Path(__file__).resolve().parents[1])]+[str((args.runtime_config.parent/root).resolve()) for root in runtime_data.get("import_roots",[])]
                # Invoke same CLI in the declared interpreter, not the caller interpreter.
                import argparse
                argv=["production-run","--runtime-config",str(args.runtime_config.resolve()),"--plugin",args.plugin,"--rule-contract",str(args.rule_contract.resolve()),"--blueprint",str(args.blueprint.resolve()),"--seed",str(args.seed),"--cap",str(args.cap),"--output-dir",str(args.output_dir.resolve())]
                if args.evidence: argv += ["--evidence",str(args.evidence.resolve())]
                if args.require_design: argv += ["--require-design"]
                if args.require_unit_checks: argv += ["--require-unit-checks"]
                argv += ["--production-contract", args.production_contract]
                env["ARCAGI_RUNTIME_CLI_CHILD"]=runtime_digest
                bootstrap="import sys;sys.path[:0]="+repr(roots)+";from arcagi_pipeline.cli import main;raise SystemExit(main("+repr(argv)+"))"
                return subprocess.run([python,"-I","-c",bootstrap],env=env).returncode
            module, factory = args.plugin.split(":", 1)
            from .runtime_control import RuntimeContext
            context=RuntimeContext(args.runtime_config)
            factory_fn=getattr(importlib.import_module(module),factory)
            import inspect
            try:inspect.signature(factory_fn).bind(context)
            except TypeError:
                if args.require_unit_checks or args.production_contract == "generated-game/v2":raise ValueError("controlled factory must accept runtime context")
                plugin=factory_fn()
            else:plugin=factory_fn(context)
            if args.production_contract == "generated-game/v2":
                from .generated_game import run_generated_production
                result = run_generated_production(plugin, FrozenInputs(args.rule_contract,args.blueprint,args.evidence),seed=args.seed,cap=args.cap,output_dir=args.output_dir,runtime_context=context,
                    probe_factory={'runtime_config':str(args.runtime_config.resolve()),'runtime_config_sha256':runtime_digest,
                                   'module_path':str(Path(importlib.import_module(module).__file__).resolve()),'factory_name':factory,
                                   'file_refs':[{'path':str(Path(importlib.import_module(module).__file__).resolve()),'sha256':__import__('hashlib').sha256(Path(importlib.import_module(module).__file__).read_bytes()).hexdigest()}]})
            else: result = run_production(plugin, FrozenInputs(args.rule_contract, args.blueprint, args.evidence),
                                    seed=args.seed, cap=args.cap, output_dir=args.output_dir, require_design=args.require_design or args.require_unit_checks,runtime_context=context if args.require_unit_checks else None,require_unit_checks=args.require_unit_checks)
            print(json.dumps({"status": result["status"], "tasks": len(result["tasks"])}))
            return 0 if result["status"] == "pass" else 1
        except (ValueError, OSError, ImportError, AttributeError) as exc:
            print(json.dumps({"status": "return", "error": _cli_error(exc)}))
            return 1
    if args.command == "validate-manifest":
        errors = validate_manifest(args.path)
        if errors:
            print(json.dumps({"valid": False, "errors": errors}, indent=2))
            return 1
        print(json.dumps({"valid": True, "manifest": str(args.path)}, indent=2))
        return 0
    if args.command == "workflow-plan":
        from .agent_workflow import WorkflowRunner
        try:
            runner = WorkflowRunner(args.spec, plan_only=True)
            print(json.dumps(runner.plan(), ensure_ascii=False, indent=2))
            return 0
        except (OSError, ValueError) as exc:
            print(json.dumps({"valid": False, "error": _cli_error(exc)}, ensure_ascii=False, indent=2))
            return 2
    if args.command == "workflow-run":
        from .agent_workflow import WorkflowRunner
        try:
            runner = WorkflowRunner(args.spec, args.run_dir, env_file=args.env_file)
            result = runner.run(retry_failed=args.retry_failed)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, ValueError) as exc:
            print(json.dumps({"completed": False, "error": _cli_error(exc)}, ensure_ascii=False, indent=2))
            return 1
    if args.command == "workflow-status":
        from .agent_workflow import workflow_status
        try:
            print(json.dumps(workflow_status(args.run_dir), ensure_ascii=False, indent=2))
            return 0
        except (OSError, ValueError) as exc:
            print(json.dumps({"found": False, "error": _cli_error(exc)}, ensure_ascii=False, indent=2))
            return 2
    if args.command == "demo":
        from .demo import run_demo
        print(json.dumps(run_demo(args.output_dir, seed=args.seed), ensure_ascii=False, indent=2))
        return 0
    if args.command == "harness-prepare":
        try:
            if args.backend == "tycho":
                from .harness_sessions import prepare_tycho_session
                if args.config is None:
                    raise ValueError("Tycho backend requires --config")
                result = prepare_tycho_session(
                    run_id=args.run_id,
                    tycho_root=args.repo,
                    game_id=args.game,
                    config_path=args.config,
                    python=args.python,
                    seed=args.seed,
                    operation_mode=args.operation_mode,
                    runs_root=args.runs_root,
                    game_source_dir=args.game_source_dir,
                    environments_dir=args.environments_dir,
                    max_actions=args.max_actions,
                )
            else:
                from .duck_sessions import prepare_duck_session
                if args.environments_dir is None:
                    raise ValueError("Duck backend requires --environments-dir")
                if args.max_actions is None:
                    raise ValueError("Duck backend requires --max-actions")
                result = prepare_duck_session(
                    run_id=args.run_id,
                    duck_root=args.repo,
                    game_id=args.game,
                    environments_dir=args.environments_dir,
                    model=args.model,
                    max_actions=args.max_actions,
                    max_runtime_minutes=args.max_runtime_minutes,
                    python=args.python,
                    runs_root=args.runs_root,
                )
        except (OSError, ValueError) as exc:
            print(json.dumps({"prepared": False, "error": _cli_error(exc)}, ensure_ascii=False, indent=2))
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "harness-execute":
        from .harness_sessions import execute_harness_session
        try:
            result = execute_harness_session(
                run_id=args.run_id,
                resume=args.resume,
                runs_root=args.runs_root,
            )
        except (OSError, ValueError) as exc:
            print(json.dumps({"executed": False, "error": _cli_error(exc)}, ensure_ascii=False, indent=2))
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "completed" else 1
    if args.command == "harness-status":
        from .harness_sessions import get_harness_session_status
        try:
            result = get_harness_session_status(run_id=args.run_id, runs_root=args.runs_root)
        except (OSError, ValueError) as exc:
            print(json.dumps({"found": False, "error": _cli_error(exc)}, ensure_ascii=False, indent=2))
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "trajectory-export":
        from .trajectory_handoff import export_trajectory_packet
        try:
            result = export_trajectory_packet(run_id=args.run_id, runs_root=args.runs_root)
        except (OSError, ValueError) as exc:
            print(json.dumps({"exported": False, "error": _cli_error(exc)}, ensure_ascii=False, indent=2))
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "trajectory-import":
        from .trajectory_handoff import import_trajectory_claims
        try:
            result = import_trajectory_claims(
                run_id=args.run_id,
                claims_path=args.claims_file,
                runs_root=args.runs_root,
            )
        except (OSError, ValueError) as exc:
            print(json.dumps({"imported": False, "error": _cli_error(exc)}, ensure_ascii=False, indent=2))
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "legacy-trajectory-import":
        from .legacy_evidence import import_legacy_run
        try:
            result = import_legacy_run(
                backend=args.backend,
                run_id=args.run_id,
                expected_game_id=args.game_id,
                artifact_paths=args.artifact,
                saved_note_paths=args.saved_note,
                output_path=args.output,
            )
        except (OSError, ValueError) as exc:
            print(json.dumps({"imported": False, "error": _cli_error(exc)}, ensure_ascii=False, indent=2))
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "legacy-trajectory-freeze":
        from .legacy_evidence import freeze_legacy_claims
        try:
            result = freeze_legacy_claims(packet_path=args.packet, claims_path=args.claims, output_path=args.output)
        except (OSError, ValueError) as exc:
            print(json.dumps({"frozen": False, "error": _cli_error(exc)}, ensure_ascii=False, indent=2))
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "game-bind":
        from .legacy_evidence import create_tycho_binding_manifest
        try:
            result = create_tycho_binding_manifest(
                game_id=args.game_id,
                tycho_root=args.tycho_root,
                source_dir=args.source_dir,
                python=args.python,
                runtime_profile=args.runtime_profile, output_path=args.output,
            )
        except (OSError, ValueError) as exc:
            print(json.dumps({"bound": False, "error": _cli_error(exc)}, ensure_ascii=False, indent=2))
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "rule-contract-freeze":
        from .rule_contract import freeze_rule_contract
        try:
            supporting = []
            for item in args.supporting:
                if "=" not in item:
                    raise ValueError("--supporting must use ROLE=PATH")
                role, path = item.split("=", 1)
                supporting.append((role, Path(path)))
            result = freeze_rule_contract(
                contract_path=args.contract,
                source_binding_path=args.source_binding,
                trajectory_ledger_path=args.trajectory_ledger,
                source_findings_path=args.source_findings,
                probe_suite_path=args.probe_suite,
                supporting_material_paths=supporting,
                output_path=args.output,
            )
        except (OSError, ValueError) as exc:
            print(json.dumps({"frozen": False, "error": _cli_error(exc)}, ensure_ascii=False, indent=2))
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "source-audit-export":
        from .source_audit import export_source_audit_task
        try:
            result = export_source_audit_task(run_id=args.run_id, runs_root=args.runs_root)
        except (OSError, ValueError) as exc:
            print(json.dumps({"exported": False, "error": _cli_error(exc)}, ensure_ascii=False, indent=2))
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "source-audit-import":
        from .source_audit import import_source_audit_findings
        try:
            result = import_source_audit_findings(
                run_id=args.run_id,
                findings_path=args.findings_file,
                runs_root=args.runs_root,
            )
        except (OSError, ValueError) as exc:
            print(json.dumps({"imported": False, "error": _cli_error(exc)}, ensure_ascii=False, indent=2))
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    cfg = {"approach": args.approach, "model": args.model}
    if args.environments_dir:
        cfg["environments_dir"] = args.environments_dir
    if args.max_actions is not None:
        cfg["max_actions"] = args.max_actions
    if args.backend == "duck":
        cfg["max_runtime_minutes"] = args.max_runtime_minutes
    spec = HarnessRunSpec(args.game, args.output_dir, cfg)
    adapter = TychoAdapter(args.repo, python=args.python) if args.backend == "tycho" else DuckAdapter(args.repo, python=args.python)
    command = adapter.preflight_command(spec) if args.preflight else adapter.run_command(spec)
    print(json.dumps({
        "backend": args.backend,
        "working_directory": str(command.working_directory),
        "command": list(command.command),
        "environment_overrides": command.environment_overrides,
        "execution_started": False
    }, ensure_ascii=False, indent=2))
    return 0
