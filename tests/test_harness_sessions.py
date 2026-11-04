from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from arcagi_pipeline.cli import main
from arcagi_pipeline.harness import HarnessExecution, HarnessRunSpec
from arcagi_pipeline.engine_probe import bind_probe_to_harness_session
from arcagi_pipeline.harness_adapters.tycho import TychoAdapter
from arcagi_pipeline.harness_sessions import (
    execute_tycho_session,
    get_tycho_session_status,
    prepare_tycho_session,
)


class TychoSessionTests(unittest.TestCase):
    def setUp(self):
        if shutil.which("git") is None:
            self.skipTest("Git is required to verify Tycho source provenance")
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.tycho = self.root / "tycho"
        runner = self.tycho / "tycho" / "harness" / "run_parallel.py"
        runner.parent.mkdir(parents=True)
        runner.write_text("# fake Tycho entrypoint for offline tests\n", encoding="utf-8")
        self.git("init", "--quiet")
        self.git("config", "user.name", "ARCAGI pipeline tests")
        self.git("config", "user.email", "pipeline-tests@example.invalid")
        self.git("add", "tycho/harness/run_parallel.py")
        self.git("commit", "--quiet", "-m", "initial Tycho fixture")
        self.config = self.root / "run.yaml"
        self.config.write_text("model: synthetic-test-model\n", encoding="utf-8")
        self.runs = self.root / "runs"
        self.game_environment = self.root / "game-environment" / "synthetic-v1"
        self.game_environment.mkdir(parents=True)
        (self.game_environment / "metadata.json").write_text(json.dumps({
            "game_id": "gridgame-synthetic-v1", "version": "synthetic-v1", "class_name": "GridGame"
        }), encoding="utf-8")
        (self.game_environment / "gridgame.py").write_text("def step(state): return state\n", encoding="utf-8")
        (self.game_environment / "levels.json").write_text('{"levels": []}\n', encoding="utf-8")
        self.runtime = {
            "status": "verified", "reason": None, "python_executable": "fixture-python",
            "python_version": "3.12.12", "python_implementation": "CPython",
            "python_cache_tag": "cpython-312", "python_soabi": "fixture-abi",
            "required_packages": ["arc-agi", "arcengine"],
            "dependency_inventory": [
                {"name": "arc-agi", "version": "0.9.9"},
                {"name": "arcengine", "version": "0.9.3"},
            ],
            "dependency_inventory_sha256": "d" * 64,
            "packages": {
                "arc-agi": {"version": "0.9.9", "installed_tree_sha256": "a" * 64,
                            "installed_file_count": 1, "source_kind": "installed_distribution"},
                "arcengine": {"version": "0.9.3", "installed_tree_sha256": "b" * 64,
                              "installed_file_count": 1, "source_kind": "installed_distribution"},
                "tufa-arc-agi-framework": None,
            },
        }
        runtime_patch = patch("arcagi_pipeline.harness_sessions.inspect_runtime_packages", return_value=self.runtime)
        runtime_patch.start()
        self.addCleanup(runtime_patch.stop)

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.tycho), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def prepare(self, run_id="session-one"):
        return prepare_tycho_session(
            run_id=run_id,
            tycho_root=self.tycho,
            game_id="gridgame",
            config_path=self.config,
            python="python",
            runs_root=self.runs,
            game_source_dir=self.game_environment,
        )

    @staticmethod
    def command_output_dir(command):
        args = list(command.command)
        return Path(args[args.index("--out-dir") + 1])

    def write_native_artifacts(self, command, *, partial=False, error=None):
        output = self.command_output_dir(command)
        (output / "status" / "gridgame").mkdir(parents=True, exist_ok=True)
        (output / "ws" / "gridgame" / "notes").mkdir(parents=True, exist_ok=True)
        (output / "manifest.json").write_text(
            json.dumps({"games": ["gridgame"], "mode": "offline"}), encoding="utf-8"
        )
        (output / "game_gridgame.json").write_text(json.dumps({
            "game_id": "gridgame",
            "n_levels": 4,
            "levels_completed": 2,
            "total_actions": 5,
            "final_state": "IN_PROGRESS",
            "partial": partial,
            "error": error,
            "trace": [
                {"turn": 1, "action": "UP", "frame": [[0]], "state": "PLAYING"},
                {"turn": 2, "action": "RIGHT", "frame": [[1]], "state": "PLAYING"},
            ],
        }), encoding="utf-8")
        (output / "status" / "gridgame" / "status.json").write_text(
            json.dumps({"state": "finished"}), encoding="utf-8"
        )
        (output / "ws" / "gridgame" / "notes" / "level_0_insights.md").write_text(
            "Synthetic saved note for offline test.", encoding="utf-8"
        )

    def test_prepare_is_preview_only_and_secret_free(self):
        secret = "session-test-secret-must-not-persist"
        with (
            patch.dict(os.environ, {"LLM_API_KEY": secret}),
            patch.object(TychoAdapter, "execute", side_effect=AssertionError("prepare must not launch Tycho")),
        ):
            session = self.prepare()

        run_dir = self.runs / "session-one"
        self.assertEqual(session["status"], "prepared")
        self.assertEqual(session["game"]["kind"], "original_game")
        self.assertEqual(session["game"]["version"], "synthetic-v1")
        self.assertEqual(session["tycho"]["python"], "python")
        self.assertEqual(session["schema_version"], "arcagi-harness-session/v2")
        self.assertEqual(session["tycho"]["source_provenance"]["status"], "verified")
        self.assertEqual(session["tycho"]["environment_provenance"]["status"], "verified")
        self.assertEqual(session["tycho"]["runtime_provenance"]["dependency_inventory_sha256"], "d" * 64)
        self.assertFalse(session["command_preview"]["execution_started"])
        self.assertIn("--config", session["command_preview"]["argv"])
        self.assertIn("--no-auto-resume", session["command_preview"]["argv"])
        saved = (run_dir / "session.json").read_text(encoding="utf-8")
        self.assertNotIn(secret, saved)
        self.assertNotIn("LLM_API_KEY", saved)

    def test_prepare_binds_unique_environment_and_execution_rechecks_it(self):
        environment = self.game_environment
        session = prepare_tycho_session(
            run_id="bound-session",
            tycho_root=self.tycho,
            game_id="gridgame",
            config_path=self.config,
            python="python",
            runs_root=self.runs,
            game_source_dir=environment,
        )
        self.assertEqual(session["game"]["version"], "synthetic-v1")
        self.assertEqual(session["tycho"]["environment_provenance"]["status"], "verified")
        self.assertEqual(session["tycho"]["runtime_provenance"], self.runtime)
        (environment / "levels.json").write_text('{"levels": ["changed"]}\n', encoding="utf-8")
        invoked = []
        with self.assertRaisesRegex(ValueError, "game environment changed since harness-prepare"):
            execute_tycho_session(
                run_id="bound-session",
                runs_root=self.runs,
                executor=lambda command: invoked.append(command),
            )
        self.assertEqual(invoked, [])

    def test_ambiguous_tycho_versions_are_unbound(self):
        for version in ("v1", "v2"):
            environment = self.tycho / "environment_files" / "gridgame" / version
            environment.mkdir(parents=True)
            (environment / "metadata.json").write_text(
                json.dumps({"game_id": f"gridgame-{version}", "version": version, "class_name": "GridGame"}),
                encoding="utf-8",
            )
            (environment / "gridgame.py").write_text("def step(state): return state\n", encoding="utf-8")
        session = prepare_tycho_session(
            run_id="ambiguous-session", tycho_root=self.tycho, game_id="gridgame",
            config_path=self.config, python="python", runs_root=self.runs,
        )
        self.assertEqual(session["tycho"]["environment_provenance"]["status"], "unverified")
        self.assertEqual(
            session["tycho"]["environment_provenance"]["reason"],
            "multiple_matching_tycho_game_environments",
        )

    def test_prepare_rejects_invalid_ids_and_nonempty_run_directory(self):
        with self.assertRaises(ValueError):
            prepare_tycho_session(
                run_id="../outside", tycho_root=self.tycho, game_id="gridgame",
                config_path=self.config, runs_root=self.runs,
            )
        occupied = self.runs / "occupied"
        occupied.mkdir(parents=True)
        (occupied / "keep.txt").write_text("keep", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            self.prepare("occupied")
        self.assertEqual((occupied / "keep.txt").read_text(encoding="utf-8"), "keep")

    def test_execute_rejects_modified_tycho_worktree(self):
        self.prepare()
        runner = self.tycho / "tycho" / "harness" / "run_parallel.py"
        runner.write_text("# changed after prepare\n", encoding="utf-8")
        status = get_tycho_session_status(run_id="session-one", runs_root=self.runs)
        self.assertEqual(status["source_provenance"]["status"], "mismatch")
        invoked = []
        with self.assertRaisesRegex(ValueError, "Tycho source changed since harness-prepare"):
            execute_tycho_session(run_id="session-one", runs_root=self.runs, executor=lambda c: invoked.append(c))
        self.assertEqual(invoked, [])

    def test_execute_rejects_new_commit(self):
        self.prepare()
        (self.tycho / "tycho" / "harness" / "run_parallel.py").write_text("# changed\n", encoding="utf-8")
        self.git("add", "tycho/harness/run_parallel.py")
        self.git("commit", "--quiet", "-m", "changed Tycho fixture")
        with self.assertRaisesRegex(ValueError, "Tycho source changed since harness-prepare"):
            execute_tycho_session(run_id="session-one", runs_root=self.runs, executor=lambda c: self.fail())

    def test_dirty_checkout_is_fingerprinted_and_runs_if_unchanged(self):
        (self.tycho / "tycho" / "harness" / "run_parallel.py").write_text("# local patch\n", encoding="utf-8")
        session = self.prepare()
        self.assertEqual(session["tycho"]["source_provenance"]["working_tree_state"], "dirty")
        result = execute_tycho_session(
            run_id="session-one", runs_root=self.runs,
            executor=lambda command: self.write_native_artifacts(command) or HarnessExecution(0, "", ""),
        )
        self.assertEqual(result["status"], "completed")

    def test_untracked_change_blocks_execution(self):
        self.prepare()
        (self.tycho / "local_patch.py").write_text("# untracked\n", encoding="utf-8")
        invoked = []
        with self.assertRaisesRegex(ValueError, "Tycho source changed since harness-prepare"):
            execute_tycho_session(run_id="session-one", runs_root=self.runs, executor=lambda c: invoked.append(c))
        self.assertEqual(invoked, [])

    def test_non_git_source_is_unverified(self):
        non_git = self.root / "unversioned-tycho"
        runner = non_git / "tycho" / "harness" / "run_parallel.py"
        runner.parent.mkdir(parents=True)
        runner.write_text("# no Git metadata\n", encoding="utf-8")
        with patch("arcagi_pipeline.harness_sessions.inspect_runtime_packages", return_value={"status": "unverified"}):
            session = prepare_tycho_session(
                run_id="unverified-session", tycho_root=non_git, game_id="gridgame",
                config_path=self.config, python="python", runs_root=self.runs,
            )
        self.assertIsNone(session["tycho"]["source_commit"])
        self.assertEqual(session["tycho"]["source_provenance"]["status"], "unverified")
        with self.assertRaisesRegex(ValueError, "could not be verified at prepare"):
            execute_tycho_session(run_id="unverified-session", runs_root=self.runs, executor=lambda c: self.fail())

    def test_execution_rejects_unbound_game_source_and_changed_dependency_inventory(self):
        unbound = prepare_tycho_session(
            run_id="unbound-environment", tycho_root=self.tycho, game_id="gridgame",
            config_path=self.config, python="python", runs_root=self.runs,
        )
        self.assertEqual(unbound["tycho"]["environment_provenance"]["status"], "unverified")
        with self.assertRaisesRegex(ValueError, "game source/version was not verified"):
            execute_tycho_session(
                run_id="unbound-environment", runs_root=self.runs, executor=lambda command: self.fail()
            )

        self.prepare("changed-runtime")
        changed_runtime = dict(self.runtime)
        changed_runtime["dependency_inventory_sha256"] = "e" * 64
        with patch("arcagi_pipeline.harness_sessions.inspect_runtime_packages", return_value=changed_runtime):
            with self.assertRaisesRegex(ValueError, "runtime packages changed"):
                execute_tycho_session(
                    run_id="changed-runtime", runs_root=self.runs, executor=lambda command: self.fail()
                )

    def test_engine_probe_binds_prepared_sources_and_requires_matching_python(self):
        session = prepare_tycho_session(
            run_id="probe-bound", tycho_root=self.tycho, game_id="gridgame",
            config_path=self.config, python=sys.executable, runs_root=self.runs,
            game_source_dir=self.game_environment,
        )

        class ProbeAdapter:
            backend = "tycho"
            game_id = "gridgame"

            def reset(self):
                return SimpleNamespace(
                    game_id="gridgame-synthetic-v1", state="NOT_FINISHED", levels_completed=0,
                    win_levels=1, available_actions=[1], frame=[[[0]]],
                )

            def step(self, action):
                return self.reset()

        probe = bind_probe_to_harness_session(
            ProbeAdapter(), run_id="probe-bound", runs_root=self.runs
        )
        self.assertEqual(probe.binding.runtime, session["tycho"]["runtime_provenance"])
        initial = probe.reset()
        self.assertEqual(initial["game_id"], "gridgame-synthetic-v1")
        self.assertEqual(probe.step("ACTION1")["state"], "NOT_FINISHED")
        (self.game_environment / "levels.json").write_text('{"levels": ["changed"]}\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "game environment changed"):
            probe.reset()

        class WrongBackend(ProbeAdapter):
            backend = "duck"

        with self.assertRaisesRegex(ValueError, "adapter backend does not match"):
            bind_probe_to_harness_session(
                WrongBackend(), run_id="probe-bound", runs_root=self.runs
            )

    def test_legacy_session_is_unverified_and_cannot_resume(self):
        self.prepare()
        session_path = self.runs / "session-one" / "session.json"
        session = json.loads(session_path.read_text(encoding="utf-8"))
        session["schema_version"] = "arcagi-harness-session/v1"
        session["tycho"].pop("source_provenance")
        session_path.write_text(json.dumps(session), encoding="utf-8")
        status = get_tycho_session_status(run_id="session-one", runs_root=self.runs)
        self.assertEqual(status["source_provenance"]["status"], "legacy_unverified")
        with self.assertRaisesRegex(ValueError, "legacy session source version is unverified"):
            execute_tycho_session(run_id="session-one", resume=True, runs_root=self.runs, executor=lambda c: self.fail())

    def test_success_indexes_trace_notes_and_hashes(self):
        self.prepare()
        result = execute_tycho_session(
            run_id="session-one", runs_root=self.runs,
            executor=lambda command: self.write_native_artifacts(command) or HarnessExecution(0, "secret-output", ""),
        )
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["artifact_hashes_valid"])
        self.assertEqual(result["game_summary"]["trace_steps"], 2)
        self.assertEqual(result["saved_note_count"], 1)
        index = json.loads((self.runs / "session-one" / "trajectory_index.json").read_text(encoding="utf-8"))
        self.assertTrue(index["raw_trace_preserved"])
        self.assertFalse(index["analysis_performed"])
        for ref in [*index["artifacts"].values(), *index["saved_notes"]]:
            path = self.runs / "session-one" / ref["path"]
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), ref["sha256"])
        all_text = "\n".join(path.read_text(encoding="utf-8") for path in (self.runs / "session-one").rglob("*") if path.is_file())
        self.assertNotIn("secret-output", all_text)

    def test_failed_run_can_resume_with_native_flags(self):
        self.prepare()
        first = execute_tycho_session(
            run_id="session-one", runs_root=self.runs,
            executor=lambda command: self.write_native_artifacts(command) or HarnessExecution(7, "", "failed"),
        )
        self.assertEqual(first["status"], "failed")
        captured = {}
        second = execute_tycho_session(
            run_id="session-one", resume=True, runs_root=self.runs,
            executor=lambda command: captured.update(argv=list(command.command)) or HarnessExecution(0, "", ""),
        )
        self.assertEqual(second["status"], "completed")
        self.assertIn("--resume", captured["argv"])
        self.assertEqual(captured["argv"][captured["argv"].index("--resume-games") + 1], "gridgame")

    def test_resume_rechecks_tycho_source_after_failed_run(self):
        self.prepare()
        failed = execute_tycho_session(
            run_id="session-one", runs_root=self.runs,
            executor=lambda command: self.write_native_artifacts(command) or HarnessExecution(7, "", "failed"),
        )
        self.assertEqual(failed["status"], "failed")
        (self.tycho / "tycho" / "harness" / "run_parallel.py").write_text("# changed before resume\\n", encoding="utf-8")
        invoked = []
        with self.assertRaisesRegex(ValueError, "Tycho source changed since harness-prepare"):
            execute_tycho_session(
                run_id="session-one", resume=True, runs_root=self.runs,
                executor=lambda command: invoked.append(command),
            )
        self.assertEqual(invoked, [])

    def test_partial_record_can_resume_but_clean_completed_run_cannot(self):
        self.prepare()
        first = execute_tycho_session(
            run_id="session-one", runs_root=self.runs,
            executor=lambda command: self.write_native_artifacts(command, partial=True) or HarnessExecution(0, "", ""),
        )
        self.assertEqual(first["status"], "completed")
        def finish_partial_record(command):
            record_path = self.command_output_dir(command) / "game_gridgame.json"
            record = json.loads(record_path.read_text(encoding="utf-8"))
            record["partial"] = False
            record_path.write_text(json.dumps(record), encoding="utf-8")
            return HarnessExecution(0, "", "")

        resumed = execute_tycho_session(
            run_id="session-one", resume=True, runs_root=self.runs,
            executor=finish_partial_record,
        )
        self.assertEqual(resumed["status"], "completed")
        with self.assertRaisesRegex(ValueError, "clean completed game"):
            execute_tycho_session(run_id="session-one", resume=True, runs_root=self.runs, executor=lambda c: self.fail())

    def test_timeout_and_changed_config_are_recorded_and_block_resume(self):
        self.prepare()
        result = execute_tycho_session(
            run_id="session-one", runs_root=self.runs,
            executor=lambda command: (_ for _ in ()).throw(TimeoutError("private detail")),
        )
        self.assertEqual(result["status"], "interrupted")
        session_wire = (self.runs / "session-one" / "session.json").read_text(encoding="utf-8")
        self.assertNotIn("private detail", session_wire)
        self.config.write_text("model: changed\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "config is missing or changed"):
            execute_tycho_session(run_id="session-one", resume=True, runs_root=self.runs, executor=lambda c: self.fail())

    def test_adapter_builds_configured_resume_command(self):
        spec = HarnessRunSpec("gridgame", self.root / "output", {"seed": 4, "operation_mode": "offline"})
        adapter = TychoAdapter(self.tycho, python="python")
        command = adapter.run_command(spec, config_path=self.config, resume=True)
        self.assertIn("--config", command.command)
        self.assertIn(str(self.config.resolve()), command.command)
        self.assertIn("--resume", command.command)
        self.assertNotIn("LLM_API_KEY", str(command.command))

    def test_cli_prepare_execute_status_are_explicit_and_route_saved_run(self):
        output = StringIO()
        with patch("arcagi_pipeline.harness_sessions.inspect_runtime_packages", return_value={"status": "unverified"}):
            with redirect_stdout(output):
                code = main([
                    "harness-prepare", "--repo", str(self.tycho), "--game", "gridgame",
                    "--config", str(self.config), "--run-id", "cli-session",
                    "--runs-root", str(self.runs), "--python", "python",
                ])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "prepared")

        execution = {"run_id": "cli-session", "status": "completed"}
        with patch("arcagi_pipeline.harness_sessions.execute_harness_session", return_value=execution) as fake_execute:
            output = StringIO()
            with redirect_stdout(output):
                code = main(["harness-execute", "--run-id", "cli-session", "--runs-root", str(self.runs)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue()), execution)
        fake_execute.assert_called_once()

        status = {"run_id": "cli-session", "status": "completed", "source_provenance": {"status": "verified"}}
        with patch("arcagi_pipeline.harness_sessions.get_harness_session_status", return_value=status) as fake_status:
            output = StringIO()
            with redirect_stdout(output):
                code = main(["harness-status", "--run-id", "cli-session", "--runs-root", str(self.runs)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue()), status)
        fake_status.assert_called_once()

    def test_cli_prepare_accepts_game_source_dir_and_stays_preview_only(self):
        environment = self.tycho / "environment_files" / "gridgame" / "synthetic-v1"
        environment.mkdir(parents=True)
        (environment / "metadata.json").write_text(
            json.dumps({"game_id": "gridgame-synthetic-v1", "version": "synthetic-v1", "class_name": "GridGame"}),
            encoding="utf-8",
        )
        (environment / "gridgame.py").write_text("def step(state): return state\n", encoding="utf-8")
        output = StringIO()
        with patch("arcagi_pipeline.harness_sessions.inspect_runtime_packages", return_value={"status": "unverified"}):
            with redirect_stdout(output):
                code = main([
                    "harness-prepare", "--repo", str(self.tycho), "--game", "gridgame",
                    "--config", str(self.config), "--game-source-dir", str(environment),
                    "--run-id", "cli-bound", "--runs-root", str(self.runs), "--python", "python",
                ])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["game"]["version"], "synthetic-v1")


if __name__ == "__main__":
    unittest.main()
