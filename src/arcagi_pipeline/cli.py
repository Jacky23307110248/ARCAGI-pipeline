from __future__ import annotations

import argparse
import json
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
    args = parser.parse_args(argv)
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
            print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False, indent=2))
            return 2
    if args.command == "workflow-run":
        from .agent_workflow import WorkflowRunner
        try:
            runner = WorkflowRunner(args.spec, args.run_dir, env_file=args.env_file)
            result = runner.run(retry_failed=args.retry_failed)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, ValueError) as exc:
            print(json.dumps({"completed": False, "error": str(exc)}, ensure_ascii=False, indent=2))
            return 1
    if args.command == "workflow-status":
        from .agent_workflow import workflow_status
        try:
            print(json.dumps(workflow_status(args.run_dir), ensure_ascii=False, indent=2))
            return 0
        except (OSError, ValueError) as exc:
            print(json.dumps({"found": False, "error": str(exc)}, ensure_ascii=False, indent=2))
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
            print(json.dumps({"prepared": False, "error": str(exc)}, ensure_ascii=False, indent=2))
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
            print(json.dumps({"executed": False, "error": str(exc)}, ensure_ascii=False, indent=2))
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "completed" else 1
    if args.command == "harness-status":
        from .harness_sessions import get_harness_session_status
        try:
            result = get_harness_session_status(run_id=args.run_id, runs_root=args.runs_root)
        except (OSError, ValueError) as exc:
            print(json.dumps({"found": False, "error": str(exc)}, ensure_ascii=False, indent=2))
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "trajectory-export":
        from .trajectory_handoff import export_trajectory_packet
        try:
            result = export_trajectory_packet(run_id=args.run_id, runs_root=args.runs_root)
        except (OSError, ValueError) as exc:
            print(json.dumps({"exported": False, "error": str(exc)}, ensure_ascii=False, indent=2))
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
            print(json.dumps({"imported": False, "error": str(exc)}, ensure_ascii=False, indent=2))
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
            print(json.dumps({"imported": False, "error": str(exc)}, ensure_ascii=False, indent=2))
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "legacy-trajectory-freeze":
        from .legacy_evidence import freeze_legacy_claims
        try:
            result = freeze_legacy_claims(packet_path=args.packet, claims_path=args.claims, output_path=args.output)
        except (OSError, ValueError) as exc:
            print(json.dumps({"frozen": False, "error": str(exc)}, ensure_ascii=False, indent=2))
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
                output_path=args.output,
            )
        except (OSError, ValueError) as exc:
            print(json.dumps({"bound": False, "error": str(exc)}, ensure_ascii=False, indent=2))
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
            print(json.dumps({"frozen": False, "error": str(exc)}, ensure_ascii=False, indent=2))
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "source-audit-export":
        from .source_audit import export_source_audit_task
        try:
            result = export_source_audit_task(run_id=args.run_id, runs_root=args.runs_root)
        except (OSError, ValueError) as exc:
            print(json.dumps({"exported": False, "error": str(exc)}, ensure_ascii=False, indent=2))
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
            print(json.dumps({"imported": False, "error": str(exc)}, ensure_ascii=False, indent=2))
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
