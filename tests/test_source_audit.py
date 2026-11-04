from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from arcagi_pipeline.cli import main
from arcagi_pipeline.duck_sessions import inspect_duck_environment
from arcagi_pipeline.game_provenance import inspect_tycho_environment
from arcagi_pipeline.harness_sessions import _git_source_provenance, sha256_file
from arcagi_pipeline.source_audit import (
    FINDINGS_SCHEMA,
    export_source_audit_task,
    import_source_audit_findings,
)
from arcagi_pipeline.trajectory_handoff import _canonical_digest


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _file_ref(path: Path, run_dir: Path) -> dict[str, str]:
    return {
        "path": path.relative_to(run_dir).as_posix(),
        "sha256": sha256_file(path),
    }


class SourceAuditHandoffTests(unittest.TestCase):
    def setUp(self):
        if shutil.which("git") is None:
            self.skipTest("Git is required for source provenance checks")
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runs = self.root / "runs"

    def tearDown(self):
        self.temp.cleanup()

    def _git_init(self, repo: Path) -> None:
        repo.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "-C", str(repo), "init", "--quiet"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Pipeline tests"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
        if not any(repo.iterdir()):
            pass

    def _make_run(self, backend="tycho", run_id="audit-run"):
        game_id = "gridgame" if backend == "tycho" else "gridgame-abcdef12"
        env_root = self.root / f"environment-{run_id}"
        env_root.mkdir(parents=True)
        _write_json(env_root / "metadata.json", {
            "game_id": "gridgame-abcdef12",
            "version": "synthetic-v1",
            "class_name": "GridGame",
        })
        (env_root / "gridgame.py").write_text(
            "def transition(state, action):\n    return state\n", encoding="utf-8"
        )
        (env_root / "levels.json").write_text('{"levels": []}\n', encoding="utf-8")

        harness = self.root / f"{backend}-repo-{run_id}"
        if backend == "tycho":
            entry = harness / "tycho" / "harness" / "run_parallel.py"
        else:
            entry = harness / "ARC3-Inference" / "inference" / "framework" / "run.py"
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text("# test harness entrypoint\n", encoding="utf-8")
        self._git_init(harness)
        subprocess.run(["git", "-C", str(harness), "add", "."], check=True)
        subprocess.run(["git", "-C", str(harness), "commit", "--quiet", "-m", "fixture"], check=True)

        if backend == "tycho":
            env = inspect_tycho_environment(harness, game_id, source_dir=env_root)
            game_version = env["version"]
            python = {"arc-agi": "0.9.9", "arcengine": "0.9.3", "tufa-arc-agi-framework": None}
            runtime_required = ["arc-agi", "arcengine"]
        else:
            envs = self.root / "duck-envs" / game_id
            envs.mkdir(parents=True)
            for path in env_root.iterdir():
                shutil.copy2(path, envs / path.name)
            env_root = envs
            env = inspect_duck_environment(self.root, game_id) if False else None
            # Duck environment inspection searches the supplied root recursively.
            env = inspect_duck_environment(self.root / "duck-envs", game_id)
            game_version = game_id
            python = {"arc-agi": None, "arcengine": "0.9.3", "tufa-arc-agi-framework": "0.1.0"}
            runtime_required = ["tufa-arc-agi-framework", "arcengine"]

        run_dir = self.runs / run_id
        analysis = run_dir / "analysis"
        analysis.mkdir(parents=True)
        native_path = run_dir / "native.json"
        native_path.write_text('{"native": true}\n', encoding="utf-8")
        native_ref = _file_ref(native_path, run_dir)
        index_path = run_dir / "trajectory_index.json"
        if backend == "tycho":
            artifacts = {"game_record": native_ref}
            saved_notes = []
        else:
            artifacts = [{"kind": "viewer_data", **native_ref}]
            saved_notes = None
        index_doc = {
            "schema_version": "arcagi-trajectory-index/v1",
            "provenance": f"{backend}_original_game_run",
            "game_id": game_id,
            "artifacts": artifacts,
        }
        if backend == "tycho":
            index_doc["saved_notes"] = saved_notes
        _write_json(index_path, index_doc)
        index_ref = _file_ref(index_path, run_dir)
        verified_artifacts = (
            [{"kind": "game_record", **native_ref}]
            if backend == "tycho"
            else [{"kind": "viewer_data", **native_ref}]
        )
        packet = {
            "schema_version": "arcagi-trajectory-packet/v1",
            "run_id": run_id,
            "backend": backend,
            "game": {"id": game_id, "version": game_version},
            "run_status": "completed",
            "trajectory_index": index_ref,
            "source_artifacts": verified_artifacts,
        }
        packet_path = analysis / "trajectory_packet.json"
        _write_json(packet_path, packet)
        packet_sha = sha256_file(packet_path)
        claims = [{
            "claim_id": "claim-sequence",
            "statement": "The saved play note suggests that appending a token preserves prior entries.",
            "kind": "gameplay_recorded_inference",
            "evidence_refs": ["note-0001"],
            "alternatives": [],
            "prediction": None,
            "confidence": None,
        }]
        claims_input_sha = "a" * 64
        ledger = {
            "schema_version": "arcagi-trajectory-ledger/v1",
            "run_id": run_id,
            "game_id": game_id,
            "trajectory_frozen": True,
            "packet": {"path": "trajectory_packet.json", "sha256": packet_sha},
            "claims_input": {"filename": "claims.json", "sha256": claims_input_sha},
            "trajectory_digest": _canonical_digest({
                "packet_sha256": packet_sha,
                "claims_sha256": claims_input_sha,
                "claims": claims,
            }),
            "claims": claims,
            "source_audit": [],
            "probes": [],
        }
        _write_json(analysis / "trajectory_ledger.json", ledger)
        source_provenance = _git_source_provenance(harness, backend=backend)
        session = {
            "schema_version": "arcagi-harness-session/v2",
            "run_id": run_id,
            "backend": backend,
            "game": {"id": game_id, "kind": "original_game", "version": game_version},
            backend: {
                "root": str(harness),
                "python": "python",
                "source_provenance": source_provenance,
                "environment_provenance": env,
                "runtime_provenance": {
                    "status": "verified",
                    "required_packages": runtime_required,
                    "packages": python,
                    "python_executable": "fixture-python",
                },
                **({"environments_dir": str(self.root / "duck-envs")} if backend == "duck" else {}),
            },
            "status": "completed",
            "trajectory_index": index_ref,
        }
        _write_json(run_dir / "session.json", session)
        return run_dir, session

    @staticmethod
    def _runtime_for_session(session):
        backend = session["backend"]
        return lambda _python, _backend: session[backend]["runtime_provenance"]

    def _findings_document(self, task_path: Path, task: dict, **overrides):
        source = next(item for item in task["materials"] if item["kind"] == "game_source")
        document = {
            "schema_version": FINDINGS_SCHEMA,
            "run_id": task["run_id"],
            "game_id": task["game"]["id"],
            "game_version": task["game"]["version"],
            "task_sha256": sha256_file(task_path),
            "trajectory_digest": task["trajectory"]["digest"],
            "findings": [{
                "finding_id": "src-sequence",
                "statement": "The source appends the supplied token to the stored sequence.",
                "source_version": task["game"]["version"],
                "source_refs": [source["material_id"]],
                "location": {
                    "path": source["path"],
                    "start_line": 1,
                    "end_line": 2,
                    "symbol": "transition",
                },
                "condition": "An append action supplies a valid token.",
                "state_changes": ["sequence length increases by one"],
                "boundary_cases": [],
                "reachable_interactions": ["A later action reads the sequence in order."],
                "uncovered_by_play": False,
                "trajectory_relation": "extends",
                "trajectory_claim_refs": ["claim-sequence"],
            }],
        }
        document.update(overrides)
        return document

    def test_tycho_task_and_findings_round_trip_without_mutating_trajectory(self):
        run_dir, session = self._make_run()
        ledger_path = run_dir / "analysis" / "trajectory_ledger.json"
        ledger_before = ledger_path.read_bytes()
        with patch("arcagi_pipeline.source_audit.inspect_runtime_packages", side_effect=self._runtime_for_session(session)):
            exported = export_source_audit_task(run_id="audit-run", runs_root=self.runs)
            task_path = run_dir / exported["task_path"]
            task = json.loads(task_path.read_text(encoding="utf-8"))
            self.assertEqual(task["game"]["version"], "synthetic-v1")
            self.assertEqual(task["trajectory"]["digest"], session["run_id"] and json.loads(ledger_before)["trajectory_digest"])
            self.assertGreaterEqual(exported["material_count"], 3)
            findings_path = self.root / "findings.json"
            findings_path.write_text(
                json.dumps(self._findings_document(task_path, task), ensure_ascii=False),
                encoding="utf-8",
            )
            imported = import_source_audit_findings(
                run_id="audit-run", findings_path=findings_path, runs_root=self.runs
            )
        saved = json.loads((run_dir / imported["findings_path"]).read_text(encoding="utf-8"))
        self.assertEqual(saved["findings"][0]["trajectory_relation"], "extends")
        self.assertEqual(ledger_path.read_bytes(), ledger_before)
        self.assertFalse(saved["trajectory_ledger_modified"])

    def test_duck_task_uses_its_bound_game_environment(self):
        run_dir, session = self._make_run("duck", "duck-audit")
        with patch("arcagi_pipeline.source_audit.inspect_runtime_packages", side_effect=self._runtime_for_session(session)):
            result = export_source_audit_task(run_id="duck-audit", runs_root=self.runs)
        task = json.loads((run_dir / result["task_path"]).read_text(encoding="utf-8"))
        self.assertEqual(task["backend"], "duck")
        self.assertEqual(task["game"]["id"], "gridgame-abcdef12")
        self.assertTrue(any(item["path"] == "metadata.json" for item in task["materials"]))

    def test_unfrozen_or_unbound_session_is_rejected(self):
        run_dir, session = self._make_run("tycho", "unbound")
        ledger_path = run_dir / "analysis" / "trajectory_ledger.json"
        ledger_path.unlink()
        with self.assertRaisesRegex(FileNotFoundError, "freeze the trajectory"):
            export_source_audit_task(run_id="unbound", runs_root=self.runs)

        run_dir, session = self._make_run("tycho", "no-env")
        session["tycho"]["environment_provenance"] = {"status": "unverified"}
        _write_json(run_dir / "session.json", session)
        with patch("arcagi_pipeline.source_audit.inspect_runtime_packages", side_effect=self._runtime_for_session(session)):
            with self.assertRaisesRegex(ValueError, "game source version is unbound"):
                export_source_audit_task(run_id="no-env", runs_root=self.runs)

    def test_source_mutation_invalid_citation_and_overwrite_are_rejected(self):
        run_dir, session = self._make_run()
        with patch("arcagi_pipeline.source_audit.inspect_runtime_packages", side_effect=self._runtime_for_session(session)):
            exported = export_source_audit_task(run_id="audit-run", runs_root=self.runs)
            task_path = run_dir / exported["task_path"]
            task = json.loads(task_path.read_text(encoding="utf-8"))
            findings_path = self.root / "bad-findings.json"
            bad = self._findings_document(task_path, task)
            bad["findings"][0]["source_version"] = "different-version"
            findings_path.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source_version"):
                import_source_audit_findings(run_id="audit-run", findings_path=findings_path, runs_root=self.runs)

            bad = self._findings_document(task_path, task)
            bad["findings"][0]["location"]["end_line"] = 999
            findings_path.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "line count"):
                import_source_audit_findings(run_id="audit-run", findings_path=findings_path, runs_root=self.runs)

            game_source = Path(task["source_root"]) / next(
                item["path"] for item in task["materials"] if item["kind"] == "game_source"
            )
            game_source.write_text("changed after task export\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "changed since harness-prepare"):
                import_source_audit_findings(run_id="audit-run", findings_path=findings_path, runs_root=self.runs)

        run_dir, session = self._make_run("tycho", "overwrite")
        with patch("arcagi_pipeline.source_audit.inspect_runtime_packages", side_effect=self._runtime_for_session(session)):
            export_source_audit_task(run_id="overwrite", runs_root=self.runs)
            task_path = run_dir / "analysis" / "source_audit_task.json"
            task = json.loads(task_path.read_text(encoding="utf-8"))
            findings_path = self.root / "good-findings.json"
            findings_path.write_text(
                json.dumps(self._findings_document(task_path, task)),
                encoding="utf-8",
            )
            import_source_audit_findings(run_id="overwrite", findings_path=findings_path, runs_root=self.runs)
            with self.assertRaises(FileExistsError):
                import_source_audit_findings(run_id="overwrite", findings_path=findings_path, runs_root=self.runs)
            with self.assertRaises(FileExistsError):
                export_source_audit_task(run_id="overwrite", runs_root=self.runs)

    def test_runtime_dependency_change_between_export_and_import_is_rejected(self):
        run_dir, session = self._make_run("duck", "runtime-change")
        original = session["duck"]["runtime_provenance"]

        def original_runtime(_python, _backend):
            return original

        with patch("arcagi_pipeline.source_audit.inspect_runtime_packages", side_effect=original_runtime):
            exported = export_source_audit_task(run_id="runtime-change", runs_root=self.runs)
        changed = json.loads(json.dumps(original))
        changed["packages"]["tufa-arc-agi-framework"] = {
            "source_tree_sha256": "f" * 64,
            "version": "0.1.0",
        }
        findings_path = self.root / "runtime-findings.json"
        with patch("arcagi_pipeline.source_audit.inspect_runtime_packages", return_value=changed):
            with self.assertRaisesRegex(ValueError, "runtime packages changed"):
                import_source_audit_findings(
                    run_id="runtime-change", findings_path=findings_path, runs_root=self.runs
                )

    def test_unverified_runtime_and_tampered_task_are_rejected(self):
        run_dir, session = self._make_run("tycho", "runtime-unbound")
        session["tycho"]["runtime_provenance"]["status"] = "unverified"
        _write_json(run_dir / "session.json", session)
        with patch("arcagi_pipeline.source_audit.inspect_runtime_packages", side_effect=self._runtime_for_session(session)):
            with self.assertRaisesRegex(ValueError, "engine/runtime package versions are unbound"):
                export_source_audit_task(run_id="runtime-unbound", runs_root=self.runs)

        run_dir, session = self._make_run("tycho", "tampered-task")
        with patch("arcagi_pipeline.source_audit.inspect_runtime_packages", side_effect=self._runtime_for_session(session)):
            exported = export_source_audit_task(run_id="tampered-task", runs_root=self.runs)
            task_path = run_dir / exported["task_path"]
            task = json.loads(task_path.read_text(encoding="utf-8"))
            task["materials"][0]["sha256"] = "0" * 64
            _write_json(task_path, task)
            with self.assertRaisesRegex(ValueError, "task was altered"):
                import_source_audit_findings(
                    run_id="tampered-task", findings_path=self.root / "unused.json", runs_root=self.runs
                )

    def test_cli_commands_export_and_import_offline(self):
        run_dir, session = self._make_run("tycho", "cli-audit")
        output = StringIO()
        with patch("arcagi_pipeline.source_audit.inspect_runtime_packages", side_effect=self._runtime_for_session(session)):
            with redirect_stdout(output):
                code = main(["source-audit-export", "--run-id", "cli-audit", "--runs-root", str(self.runs)])
            self.assertEqual(code, 0)
            result = json.loads(output.getvalue())
            task_path = run_dir / result["task_path"]
            task = json.loads(task_path.read_text(encoding="utf-8"))
            findings_path = self.root / "cli-findings.json"
            findings_path.write_text(
                json.dumps(self._findings_document(task_path, task)),
                encoding="utf-8",
            )
            output = StringIO()
            with redirect_stdout(output):
                code = main([
                    "source-audit-import", "--run-id", "cli-audit",
                    "--findings-file", str(findings_path), "--runs-root", str(self.runs),
                ])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())["finding_count"], 1)


if __name__ == "__main__":
    unittest.main()
