from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from arcagi_pipeline.cli import main
from arcagi_pipeline.demo import GAME_ID, PROVENANCE, run_demo
from arcagi_pipeline.workflow import STAGE_ORDER


class OfflineDemoTests(unittest.TestCase):
    def test_demo_completes_stages_hashes_evidence_and_engine_replays(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "demo"
            summary = run_demo(root, seed=17)

            self.assertEqual(summary["game_id"], GAME_ID)
            self.assertEqual(summary["stage_order"], list(STAGE_ORDER))
            self.assertEqual(summary["completed_stages"], len(STAGE_ORDER))
            self.assertEqual(summary["model_calls"], 0)
            self.assertFalse(summary["harness_execution"])

            record = json.loads((root / "pipeline_run.json").read_text(encoding="utf-8"))
            self.assertEqual([stage["name"] for stage in record["stages"]], list(STAGE_ORDER))
            for stage in record["stages"]:
                self.assertEqual(stage["status"], "complete")
                for artifact in stage["outputs"]:
                    content = (root / artifact["path"]).read_bytes()
                    self.assertEqual(hashlib.sha256(content).hexdigest(), artifact["sha256"])

            trajectory = json.loads((root / "trajectory_analysis.json").read_text(encoding="utf-8"))
            packet_wire = json.dumps(trajectory["packet"], ensure_ascii=False)
            self.assertFalse(trajectory["source_context_provided"])
            self.assertNotIn("PRIVATE_", packet_wire)
            self.assertNotIn("HIDDEN_FIXTURE_SENTINEL", packet_wire)

            ledger_wrapper = json.loads((root / "evidence_ledger.json").read_text(encoding="utf-8"))
            ledger = ledger_wrapper["ledger"]
            self.assertEqual(ledger_wrapper["provenance"], PROVENANCE)
            self.assertTrue(ledger["trajectory_frozen"])
            self.assertEqual(ledger["trajectory_digest"], trajectory["frozen_trajectory_digest"])
            self.assertEqual(
                ledger["trajectory_digest"],
                json.loads((root / "source_audit.json").read_text(encoding="utf-8"))["trajectory_digest_before_audit"],
            )

            validation = json.loads((root / "validation_results.json").read_text(encoding="utf-8"))
            self.assertTrue(validation["all_levels_solved_and_replayed"])
            self.assertEqual([row["status"] for row in validation["results"]], ["solved", "solved"])
            self.assertTrue(all(row["replay_accepted"] is True for row in validation["results"]))
            self.assertEqual(
                [action["kind"] for action in validation["results"][0]["actions"]],
                ["append", "append", "commit"],
            )
            self.assertEqual(
                [action["kind"] for action in validation["results"][1]["actions"]],
                ["append", "append", "append", "commit"],
            )

            session = json.loads((root / "harness_session.json").read_text(encoding="utf-8"))
            preview = json.loads((root / "harness_command_preview.json").read_text(encoding="utf-8"))
            self.assertEqual(session["game_id"], GAME_ID)
            self.assertFalse(preview["execution_started"])
            self.assertTrue(preview["preview_only"])
            self.assertFalse(preview["runnable"])
            self.assertEqual(preview["provenance"], PROVENANCE)

    def test_fixed_seed_produces_identical_files_in_fresh_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first"
            second = Path(tmp) / "second"
            run_demo(first, seed=41)
            run_demo(second, seed=41)

            first_files = {path.relative_to(first).as_posix(): path.read_bytes() for path in first.rglob("*") if path.is_file()}
            second_files = {path.relative_to(second).as_posix(): path.read_bytes() for path in second.rglob("*") if path.is_file()}
            self.assertEqual(first_files, second_files)

    def test_demo_is_offline_and_does_not_serialize_environment_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "demo"
            secret_marker = "synthetic-secret-marker-do-not-read"
            with (
                patch.dict(os.environ, {"DEEPSEEK_API_KEY": secret_marker, "OPENAI_API_KEY": secret_marker}),
                patch.object(subprocess, "run", side_effect=AssertionError("subprocess must not run")),
                patch.object(socket, "socket", side_effect=AssertionError("network must not be used")),
            ):
                run_demo(root)

            all_text = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*") if path.is_file())
            self.assertNotIn(secret_marker, all_text)
            self.assertNotIn("DEEPSEEK_API_KEY", all_text)
            self.assertNotIn("OPENAI_API_KEY", all_text)

    def test_nonempty_output_directory_is_preserved_and_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "existing"
            root.mkdir()
            marker = root / "keep.txt"
            marker.write_text("keep", encoding="utf-8")
            with self.assertRaises(ValueError):
                run_demo(root)
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
            self.assertEqual(sorted(path.name for path in root.iterdir()), ["keep.txt"])

    def test_cli_runs_demo_and_prints_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "cli-demo"
            output = StringIO()
            with redirect_stdout(output):
                result = main(["demo", "--output-dir", str(root), "--seed", "9"])
            self.assertEqual(result, 0)
            self.assertEqual(json.loads(output.getvalue())["completed_stages"], len(STAGE_ORDER))
            self.assertTrue((root / "harness_command_preview.json").is_file())


if __name__ == "__main__":
    unittest.main()
