from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from arcagi_pipeline.cli import main
from arcagi_pipeline.duck_sessions import (
    execute_duck_session,
    get_duck_session_status,
    prepare_duck_session,
)
from arcagi_pipeline.harness import HarnessCommand, HarnessExecution
from arcagi_pipeline.harness_adapters.duck import DuckAdapter


class DuckSessionTests(unittest.TestCase):
    GAME_ID = "syntheticgame-v3"

    def setUp(self):
        if shutil.which("git") is None:
            self.skipTest("Git is required to verify Duck source provenance")
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.duck = self.root / "duck"
        runner = self.duck / "ARC3-Inference" / "inference" / "framework" / "run.py"
        runner.parent.mkdir(parents=True)
        runner.write_text("# fake Duck entrypoint for offline tests\n", encoding="utf-8")
        self.git("init", "--quiet")
        self.git("config", "user.name", "ARCAGI pipeline tests")
        self.git("config", "user.email", "pipeline-tests@example.invalid")
        self.git("add", "ARC3-Inference/inference/framework/run.py")
        self.git("commit", "--quiet", "-m", "initial Duck fixture")

        self.environments = self.root / "environment_files"
        self.game_source = self.environments / "syntheticgame" / "v3"
        self.game_source.mkdir(parents=True)
        (self.game_source / "metadata.json").write_text(json.dumps({
            "game_id": self.GAME_ID,
            "title": "Synthetic game",
            "class_name": "SyntheticGame",
        }), encoding="utf-8")
        (self.game_source / "syntheticgame.py").write_text("class SyntheticGame: pass\n", encoding="utf-8")
        self.runs = self.root / "runs"
        self.runtime = {
            "status": "verified", "reason": None, "python_executable": "fixture-python",
            "python_version": "3.12.12", "python_implementation": "CPython",
            "python_cache_tag": "cpython-312", "python_soabi": "fixture-abi",
            "required_packages": ["tufa-arc-agi-framework", "arc-agi", "arcengine"],
            "dependency_inventory": [
                {"name": "arc-agi", "version": "0.9.9"},
                {"name": "arcengine", "version": "0.9.3"},
                {"name": "tufa-arc-agi-framework", "version": "0.1.0"},
            ],
            "dependency_inventory_sha256": "d" * 64,
            "packages": {
                "arc-agi": {"version": "0.9.9", "installed_tree_sha256": "a" * 64,
                            "installed_file_count": 1, "source_kind": "installed_distribution"},
                "arcengine": {"version": "0.9.3", "installed_tree_sha256": "b" * 64,
                              "installed_file_count": 1, "source_kind": "installed_distribution"},
                "tufa-arc-agi-framework": {"version": "0.1.0", "installed_tree_sha256": "c" * 64,
                                            "installed_file_count": 1, "source_kind": "editable_local",
                                            "editable_source": "fixture-taaf", "source_tree_sha256": "e" * 64},
            },
        }
        for module_name in (
            "arcagi_pipeline.duck_sessions.inspect_runtime_packages",
            "arcagi_pipeline.harness_sessions.inspect_runtime_packages",
        ):
            runtime_patch = patch(module_name, return_value=self.runtime)
            runtime_patch.start()
            self.addCleanup(runtime_patch.stop)

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.duck), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def prepare(self, run_id="duck-session", *, preflight=None, game_id="syntheticgame"):
        return prepare_duck_session(
            run_id=run_id,
            duck_root=self.duck,
            game_id=game_id,
            environments_dir=self.environments,
            model="test-provider:test-model",
            max_actions=25,
            python="duck-python",
            runs_root=self.runs,
            preflight_executor=preflight or (lambda _command: HarnessExecution(0, self.GAME_ID + "\n", "")),
        )

    @staticmethod
    def output_dir(command: HarnessCommand) -> Path:
        args = list(command.command)
        return Path(args[args.index("--experiment-dir") + 1])

    def write_native_artifacts(self, command: HarnessCommand):
        output = self.output_dir(command)
        (output / "artifacts").mkdir(parents=True, exist_ok=True)
        (output / "transcripts").mkdir(parents=True, exist_ok=True)
        (output / "solver_analysis").mkdir(parents=True, exist_ok=True)
        (output / "run_config.json").write_text(json.dumps({
            "games": [self.GAME_ID],
            "max_actions": 25,
            "max_runtime_minutes": 10.0,
            "deployment": {"target": "inline"},
        }), encoding="utf-8")
        (output / "artifacts" / "syntheticgame_p0_viewer_data.json").write_text(json.dumps({
            "game_id": self.GAME_ID,
            "status": "gave_up",
            "levels_completed": 0,
            "total_levels": 3,
            "final_score": 0,
            "eventCount": 2,
            "lastEvent": {"state": "PLAYING"},
        }), encoding="utf-8")
        (output / "artifacts" / "syntheticgame_p0_events.jsonl").write_text(
            '{"type":"initial","state":"PLAYING"}\n{"type":"action","action_name":"MOVE_A"}\n',
            encoding="utf-8",
        )
        (output / "transcripts" / "syntheticgame_p0.txt").write_text(
            "Saved native analyzer transcript.\n", encoding="utf-8"
        )
        (output / "solver_analysis" / "syntheticgame_p0.html").write_text(
            "<html>saved analysis</html>\n", encoding="utf-8"
        )

    def test_prepare_checks_official_id_without_starting_game_or_model(self):
        commands = []

        def preflight(command):
            commands.append(command)
            return HarnessExecution(0, self.GAME_ID + "\n", "")

        session = self.prepare(preflight=preflight)
        self.assertEqual(len(commands), 1)
        self.assertIn("--list-games", commands[0].command)
        self.assertIn("--game", commands[0].command)
        self.assertNotIn("--model", commands[0].command)
        self.assertEqual(session["backend"], "duck")
        self.assertEqual(session["game"]["id"], self.GAME_ID)
        self.assertEqual(session["game"]["requested_id"], "syntheticgame")
        self.assertEqual(session["duck"]["source_provenance"]["status"], "verified")
        self.assertEqual(session["duck"]["environment_provenance"]["status"], "verified")
        self.assertEqual(session["status"], "prepared")
        self.assertFalse(session["command_preview"]["execution_started"])
        self.assertFalse(session["preflight"]["model_or_game_execution_started"])
        self.assertEqual(session["run_config"]["max_runtime_minutes"], 10.0)
        self.assertIn("--max-runtime-minutes", session["command_preview"]["argv"])
        self.assertTrue((self.runs / "duck-session" / "duck").is_dir())

    def test_prepare_rejects_unknown_official_id_and_mismatched_environment(self):
        with self.assertRaisesRegex(ValueError, "exactly one metadata.json for official game"):
            self.prepare(preflight=lambda _command: HarnessExecution(0, "not-a-game\n", ""))

        metadata = self.game_source / "metadata.json"
        original = metadata.read_text(encoding="utf-8")
        metadata.write_text(json.dumps({"game_id": "different-game", "class_name": "SyntheticGame"}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "metadata.json for official game"):
            self.prepare(run_id="bad-environment")
        metadata.write_text(original, encoding="utf-8")

    def test_success_indexes_native_artifacts_without_rewriting_transcript(self):
        secret = "duck-session-secret-marker"
        self.prepare()
        transcript = self.runs / "duck-session" / "duck" / "transcripts" / "syntheticgame_p0.txt"

        def fake_run(command):
            self.write_native_artifacts(command)
            return HarnessExecution(0, "private stdout", "private stderr")

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": secret}):
            result = execute_duck_session(
                run_id="duck-session", runs_root=self.runs, executor=fake_run
            )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["trajectory_status"], "complete")
        self.assertTrue(result["artifact_hashes_valid"])
        self.assertEqual(result["game_summaries"][0]["status"], "gave_up")
        self.assertNotIn("solved", result["game_summaries"][0])

        run_dir = self.runs / "duck-session"
        index = json.loads((run_dir / "trajectory_index.json").read_text(encoding="utf-8"))
        self.assertFalse(index["analysis_performed"])
        kinds = {artifact["kind"] for artifact in index["artifacts"]}
        self.assertTrue({"run_config", "viewer_data", "raw_event_sidecar", "saved_transcript", "analysis_html"}.issubset(kinds))
        transcript_artifact = next(item for item in index["artifacts"] if item["kind"] == "saved_transcript")
        self.assertEqual(transcript_artifact["sha256"], __import__("hashlib").sha256(transcript.read_bytes()).hexdigest())
        serialized = "\n".join(path.read_text(encoding="utf-8") for path in run_dir.rglob("*") if path.is_file())
        self.assertNotIn(secret, serialized)
        self.assertNotIn("private stdout", serialized)
        self.assertNotIn("private stderr", serialized)
        self.assertEqual(transcript.read_text(encoding="utf-8"), "Saved native analyzer transcript.\n")

    def test_failure_and_interruption_preserve_partial_artifacts_and_resume_is_rejected(self):
        self.prepare()
        failed = execute_duck_session(
            run_id="duck-session",
            runs_root=self.runs,
            executor=lambda command: (
                self.write_native_artifacts(command) or HarnessExecution(9, "", "")
            ),
        )
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["trajectory_status"], "complete")
        invoked = []
        with self.assertRaisesRegex(ValueError, "does not support native resume"):
            execute_duck_session(
                run_id="duck-session", resume=True, runs_root=self.runs,
                executor=lambda command: invoked.append(command),
            )
        self.assertEqual(invoked, [])

        self.prepare(run_id="interrupted-session")
        interrupted = execute_duck_session(
            run_id="interrupted-session",
            runs_root=self.runs,
            executor=lambda command: (
                self.write_native_artifacts(command) or (_ for _ in ()).throw(TimeoutError())
            ),
        )
        self.assertEqual(interrupted["status"], "interrupted")
        self.assertEqual(interrupted["trajectory_status"], "complete")

    def test_changed_duck_source_or_environment_blocks_execution(self):
        self.prepare()
        runner = self.duck / "ARC3-Inference" / "inference" / "framework" / "run.py"
        runner.write_text("# changed after prepare\n", encoding="utf-8")
        called = []
        with self.assertRaisesRegex(ValueError, "Duck source changed"):
            execute_duck_session(
                run_id="duck-session", runs_root=self.runs,
                executor=lambda command: called.append(command),
            )
        self.assertEqual(called, [])

        self.prepare(run_id="environment-session")
        (self.game_source / "syntheticgame.py").write_text("class SyntheticGame: changed = True\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "game environment changed"):
            execute_duck_session(
                run_id="environment-session", runs_root=self.runs,
                executor=lambda command: called.append(command),
            )
        self.assertEqual(called, [])
        status = get_duck_session_status(run_id="environment-session", runs_root=self.runs)
        self.assertEqual(status["source_provenance"]["environment"]["status"], "mismatch")

    def test_changed_runtime_dependency_inventory_blocks_execution(self):
        self.prepare()
        changed_runtime = dict(self.runtime)
        changed_runtime["dependency_inventory_sha256"] = "f" * 64
        with patch("arcagi_pipeline.harness_sessions.inspect_runtime_packages", return_value=changed_runtime):
            with self.assertRaisesRegex(ValueError, "runtime packages changed"):
                execute_duck_session(
                    run_id="duck-session", runs_root=self.runs, executor=lambda command: self.fail()
                )

    def test_cli_routes_duck_sessions_and_preserves_tycho_default(self):
        with patch.object(DuckAdapter, "execute", autospec=True) as execute:
            execute.side_effect = lambda adapter, command, timeout=None: HarnessExecution(
                0, self.GAME_ID + "\n", ""
            )
            output = StringIO()
            with redirect_stdout(output):
                result = main([
                    "harness-prepare", "--backend", "duck", "--repo", str(self.duck),
                    "--game", "syntheticgame", "--environments-dir", str(self.environments),
                    "--model", "test-model", "--max-actions", "25", "--run-id", "cli-duck",
                    "--python", "duck-python", "--runs-root", str(self.runs),
                ])
            self.assertEqual(result, 0)
            self.assertEqual(json.loads(output.getvalue())["backend"], "duck")

        def fake_execute(adapter, command, timeout=None):
            self.write_native_artifacts(command)
            return HarnessExecution(0, "", "")

        with patch.object(DuckAdapter, "execute", autospec=True, side_effect=fake_execute):
            output = StringIO()
            with redirect_stdout(output):
                result = main(["harness-execute", "--run-id", "cli-duck", "--runs-root", str(self.runs)])
            self.assertEqual(result, 0)
            self.assertEqual(json.loads(output.getvalue())["backend"], "duck")
            output = StringIO()
            with redirect_stdout(output):
                result = main(["harness-status", "--run-id", "cli-duck", "--runs-root", str(self.runs)])
            self.assertEqual(result, 0)
            self.assertEqual(json.loads(output.getvalue())["trajectory_status"], "complete")


if __name__ == "__main__":
    unittest.main()
