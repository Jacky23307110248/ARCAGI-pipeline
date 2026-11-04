from __future__ import annotations

import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from arcagi_pipeline.cli import main
from arcagi_pipeline.trajectory_handoff import export_trajectory_packet, import_trajectory_claims


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value, run_dir: Path) -> dict[str, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return {"path": path.relative_to(run_dir).as_posix(), "sha256": _sha(path)}


class TrajectoryHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runs = self.root / "runs"

    def tearDown(self):
        self.temp.cleanup()

    def _session(self, run_id: str, backend: str, *, status: str, artifacts: list[dict], notes: list[dict] | None = None) -> Path:
        run_dir = self.runs / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        index_path = run_dir / "trajectory_index.json"
        game_id = "gridgame-synthetic-v1"
        if backend == "tycho":
            indexed_artifacts = {item["kind"]: {"path": item["path"], "sha256": item["sha256"]} for item in artifacts}
            index = {
                "schema_version": "arcagi-trajectory-index/v1",
                "provenance": "tycho_original_game_run",
                "game_id": game_id,
                "raw_trace_preserved": "game_record" in indexed_artifacts,
                "analysis_performed": False,
                "game_summary": {},
                "artifacts": indexed_artifacts,
                "saved_notes": notes or [],
            }
        else:
            index = {
                "schema_version": "arcagi-trajectory-index/v1",
                "provenance": "duck_original_game_run",
                "game_id": game_id,
                "raw_events_preserved": any(item["kind"] == "raw_event_sidecar" for item in artifacts),
                "trajectory_status": "complete",
                "analysis_performed": False,
                "game_summaries": [],
                "artifacts": artifacts,
            }
        index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
        session = {
            "schema_version": "arcagi-harness-session/v2",
            "run_id": run_id,
            "backend": backend,
            "game": {"id": game_id, "kind": "original_game", "version": "fixture"},
            backend: {
                "source_commit": "fixture-commit",
                "environment_provenance": {
                    "game_id": game_id,
                    "metadata_path": "gridgame/synthetic-v1/metadata.json",
                    "metadata_sha256": "a" * 64,
                    "game_source_path": "gridgame.py",
                    "source_tree_sha256": "b" * 64,
                    "source_dir": "PRIVATE_SOURCE_DIR",
                    "environments_dir": "PRIVATE_ENVIRONMENTS_DIR",
                } if backend == "duck" else None,
            },
            "status": status,
            "attempts": [{"exit_code": 1 if status == "failed" else 0}],
            "trajectory_index": {"path": "trajectory_index.json", "sha256": _sha(index_path)},
        }
        (run_dir / "session.json").write_text(json.dumps(session, ensure_ascii=False), encoding="utf-8")
        return run_dir

    def _make_tycho_run(self, run_id: str = "tycho-run", *, status: str = "interrupted") -> Path:
        run_dir = self.runs / run_id
        record = {
            "game_id": "gridgame-synthetic-v1",
            "final_state": "GAME_OVER",
            "levels_completed": 2,
            "partial": True,
            "trace": [{
                "turn": 1,
                "action": "ACTION1",
                "x": 2,
                "y": 3,
                "frame": [[0, 1], [2, 3]],
                "state": "ENGINE_INTERNAL_STATE",
                "reasoning": "PRIVATE_TYCHO_REASONING",
                "levels_completed": 1,
                "frame_changed": True,
            }],
        }
        ref = _write_json(run_dir / "tycho" / "game_gridgame.json", record, run_dir)
        note_path = run_dir / "tycho" / "workspace" / "notes" / "actor_beliefs.md"
        note_path.parent.mkdir(parents=True)
        note_path.write_text("The sequence may update its stored phase.\n", encoding="utf-8")
        note_ref = {"path": note_path.relative_to(run_dir).as_posix(), "sha256": _sha(note_path)}
        return self._session(run_id, "tycho", status=status, artifacts=[{"kind": "game_record", **ref}], notes=[note_ref])

    def _make_duck_run(self, run_id: str = "duck-run", *, status: str = "failed", compressed_events: bool = False) -> Path:
        run_dir = self.runs / run_id
        artifacts_dir = run_dir / "duck" / "artifacts"
        run_config = _write_json(artifacts_dir / "run_config.json", {"model": "fixture-model"}, run_dir)
        viewer = _write_json(artifacts_dir / "gridgame_p0_viewer_data.json", {
            "game_id": "gridgame-synthetic-v1", "status": "gave_up", "levels_completed": 1,
            "total_levels": 4, "final_score": 10,
            "lastEvent": {"transcript": "SHOULD_NOT_BE_READ"},
        }, run_dir)
        sidecar_path = artifacts_dir / ("gridgame_p0_events.json.gz" if compressed_events else "gridgame_p0_events.jsonl")
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        events = [
            json.dumps({"type": "initial", "board": [[0, 1]], "state": "ENGINE_INTERNAL_STATE"}),
            json.dumps({"type": "action", "board": [[0, 2]], "action_name": "ACTION1", "action_display": "UP", "reward": 1, "board_changed": True, "source_code": "MUST_NOT_LEAK"}),
            json.dumps({"type": "analysis", "transcript": "PRIVATE_DUCK_TRANSCRIPT"}),
            json.dumps({"type": "future_unknown_event", "payload": "OMITTED"}),
        ]
        if compressed_events:
            sidecar_path.write_bytes(gzip.compress(json.dumps([json.loads(item) for item in events]).encode("utf-8")))
        else:
            sidecar_path.write_text("\n".join(events) + "\n", encoding="utf-8")
        event_ref = {"path": sidecar_path.relative_to(run_dir).as_posix(), "sha256": _sha(sidecar_path)}
        transcript = run_dir / "duck" / "transcripts" / "gridgame_p0.txt"
        transcript.parent.mkdir(parents=True)
        transcript.write_text("PRIVATE_DUCK_TRANSCRIPT", encoding="utf-8")
        transcript_ref = {"kind": "saved_transcript", "path": transcript.relative_to(run_dir).as_posix(), "sha256": _sha(transcript)}
        refs = [
            {"kind": "run_config", **run_config},
            {"kind": "viewer_data", **viewer},
            {"kind": "raw_event_sidecar", **event_ref},
            transcript_ref,
        ]
        return self._session(run_id, "duck", status=status, artifacts=refs)

    def test_tycho_export_whitelists_frames_actions_and_saved_notes(self):
        run_dir = self._make_tycho_run()
        result = export_trajectory_packet(run_id="tycho-run", runs_root=self.runs)
        packet_path = run_dir / result["packet_path"]
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        wire = packet_path.read_text(encoding="utf-8")
        self.assertEqual(packet["run_status"], "interrupted")
        self.assertEqual(packet["trace"][0]["observation"], [[0, 1], [2, 3]])
        self.assertEqual(packet["trace"][0]["action"]["name"], "ACTION1")
        self.assertEqual(packet["saved_notes"][0]["evidence_id"], "note-0001")
        self.assertEqual(packet["outcome_evidence"][0]["result"]["levels_completed"], 2)
        self.assertEqual(packet["outcome_evidence"][0]["source_ref"]["sha256"], packet["source_artifacts"][0]["sha256"])
        self.assertIn("The sequence may update", wire)
        self.assertNotIn("PRIVATE_TYCHO_REASONING", wire)
        self.assertNotIn("ENGINE_INTERNAL_STATE", wire)
        self.assertNotIn("MUST_NOT_LEAK", wire)
        self.assertNotIn("source_dir", wire)

    def test_duck_export_excludes_transcripts_and_engine_fields(self):
        run_dir = self._make_duck_run()
        result = export_trajectory_packet(run_id="duck-run", runs_root=self.runs)
        packet_path = run_dir / result["packet_path"]
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        wire = packet_path.read_text(encoding="utf-8")
        self.assertEqual(packet["run_status"], "failed")
        self.assertEqual(len(packet["trace"]), 2)
        self.assertEqual(packet["trace"][1]["action"], "UP")
        self.assertEqual(packet["trace"][1]["observation"], [[0, 2]])
        self.assertEqual(packet["coverage"]["analysis_events_excluded"], 1)
        self.assertFalse(packet["interpretation_limits"]["duck_transcript_text_imported"])
        self.assertNotIn("PRIVATE_DUCK_TRANSCRIPT", wire)
        self.assertNotIn("SHOULD_NOT_BE_READ", wire)
        self.assertNotIn("ENGINE_INTERNAL_STATE", wire)
        self.assertNotIn("PRIVATE_SOURCE_DIR", wire)
        self.assertNotIn("PRIVATE_ENVIRONMENTS_DIR", wire)
        self.assertTrue(all(len(item["sha256"]) == 64 for item in packet["source_artifacts"]))

    def test_export_rejects_missing_index_hash_mismatch_and_overwrite(self):
        missing_run = self._make_tycho_run("missing-index")
        (missing_run / "trajectory_index.json").unlink()
        with self.assertRaises(FileNotFoundError):
            export_trajectory_packet(run_id="missing-index", runs_root=self.runs)

        run_dir = self._make_tycho_run()
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            (run_dir / "tycho" / "game_gridgame.json").write_text("{}", encoding="utf-8")
            export_trajectory_packet(run_id="tycho-run", runs_root=self.runs)

        run_dir = self._make_tycho_run("tycho-run-2")
        export_trajectory_packet(run_id="tycho-run-2", runs_root=self.runs)
        with self.assertRaises(FileExistsError):
            export_trajectory_packet(run_id="tycho-run-2", runs_root=self.runs)

        run_dir = self._make_tycho_run("prepared-run", status="prepared")
        with self.assertRaisesRegex(ValueError, "requires a completed"):
            export_trajectory_packet(run_id="prepared-run", runs_root=self.runs)

    def test_import_freezes_claims_bound_to_packet_and_rejects_unrecorded_inference(self):
        run_dir = self._make_tycho_run()
        result = export_trajectory_packet(run_id="tycho-run", runs_root=self.runs)
        packet = json.loads((run_dir / result["packet_path"]).read_text(encoding="utf-8"))
        claims_path = self.root / "claims.json"
        claims_doc = {
            "schema_version": "arcagi-trajectory-claims/v1",
            "run_id": "tycho-run",
            "game_id": "gridgame-synthetic-v1",
            "packet_sha256": result["packet_sha256"],
            "claims": [{
                "claim_id": "hyp-1",
                "statement": "Appending a token may preserve its position in the sequence.",
                "kind": "post_run_hypothesis",
                "evidence_refs": [packet["trace"][0]["evidence_id"]],
                "alternatives": ["The latest token may replace the previous one."],
                "prediction": "Appending from the same state should increase sequence length by one.",
                "confidence": 0.6,
            }, {
                "claim_id": "outcome-1",
                "statement": "The run record reports two completed levels.",
                "kind": "direct_observation",
                "evidence_refs": [packet["outcome_evidence"][0]["evidence_id"]],
            }],
        }
        claims_path.write_text(json.dumps(claims_doc), encoding="utf-8")
        imported = import_trajectory_claims(run_id="tycho-run", claims_path=claims_path, runs_root=self.runs)
        ledger_path = run_dir / imported["ledger_path"]
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        self.assertTrue(ledger["trajectory_frozen"])
        self.assertEqual(len(ledger["claims"]), 2)
        self.assertEqual(ledger["claims"][0]["kind"], "post_run_hypothesis")
        self.assertEqual(ledger["source_audit"], [])
        self.assertEqual(ledger["probes"], [])
        with self.assertRaises(FileExistsError):
            import_trajectory_claims(run_id="tycho-run", claims_path=claims_path, runs_root=self.runs)

        duck_dir = self._make_duck_run("duck-claims")
        duck_result = export_trajectory_packet(run_id="duck-claims", runs_root=self.runs)
        duck_packet = json.loads((duck_dir / duck_result["packet_path"]).read_text(encoding="utf-8"))
        claims_doc.update({
            "run_id": "duck-claims",
            "packet_sha256": duck_result["packet_sha256"],
            "claims": [{
                "claim_id": "recorded-1",
                "statement": "The original agent inferred that the board changed after ACTION1.",
                "kind": "gameplay_recorded_inference",
                "evidence_refs": [duck_packet["trace"][1]["evidence_id"]],
            }],
        })
        claims_path.write_text(json.dumps(claims_doc), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "must cite an original saved note"):
            import_trajectory_claims(run_id="duck-claims", claims_path=claims_path, runs_root=self.runs)

    def test_import_rejects_invalid_refs_and_changed_source_artifacts(self):
        run_dir = self._make_duck_run()
        result = export_trajectory_packet(run_id="duck-run", runs_root=self.runs)
        packet = json.loads((run_dir / result["packet_path"]).read_text(encoding="utf-8"))
        claims_path = self.root / "invalid-claims.json"
        claims_doc = {
            "schema_version": "arcagi-trajectory-claims/v1",
            "run_id": "duck-run",
            "game_id": "gridgame-synthetic-v1",
            "packet_sha256": result["packet_sha256"],
            "claims": [{
                "claim_id": "bad-ref",
                "statement": "Unsupported inference.",
                "kind": "post_run_hypothesis",
                "evidence_refs": ["does-not-exist"],
                "alternatives": ["Other explanation."],
                "prediction": "A discriminating action.",
            }],
        }
        claims_path.write_text(json.dumps(claims_doc), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "reference evidence IDs"):
            import_trajectory_claims(run_id="duck-run", claims_path=claims_path, runs_root=self.runs)

        artifact_path = run_dir / packet["source_artifacts"][0]["path"]
        artifact_path.write_text("changed after export", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            import_trajectory_claims(run_id="duck-run", claims_path=claims_path, runs_root=self.runs)

        index_run = self._make_tycho_run("changed-index")
        index_result = export_trajectory_packet(run_id="changed-index", runs_root=self.runs)
        index_claims = {
            "schema_version": "arcagi-trajectory-claims/v1",
            "run_id": "changed-index",
            "game_id": "gridgame-synthetic-v1",
            "packet_sha256": index_result["packet_sha256"],
            "claims": [{
                "claim_id": "obs-1",
                "statement": "A frame was recorded.",
                "kind": "direct_observation",
                "evidence_refs": ["ev-000001"],
            }],
        }
        claims_path.write_text(json.dumps(index_claims), encoding="utf-8")
        (index_run / "trajectory_index.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            import_trajectory_claims(run_id="changed-index", claims_path=claims_path, runs_root=self.runs)

    def test_duck_compressed_sidecar_is_supported_and_reports_skipped_event_types(self):
        run_dir = self._make_duck_run("duck-gzip", compressed_events=True)
        packet = export_trajectory_packet(run_id="duck-gzip", runs_root=self.runs)
        document = json.loads((run_dir / packet["packet_path"]).read_text(encoding="utf-8"))
        self.assertEqual(len(document["trace"]), 2)
        self.assertEqual(document["coverage"]["analysis_events_excluded"], 1)
        self.assertEqual(document["coverage"]["unsupported_event_types_excluded"], {"future_unknown_event": 1})

    def test_cli_export_and_import_are_offline_commands(self):
        self._make_duck_run()
        from io import StringIO
        from contextlib import redirect_stdout

        output = StringIO()
        with redirect_stdout(output):
            code = main(["trajectory-export", "--run-id", "duck-run", "--runs-root", str(self.runs)])
        self.assertEqual(code, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["run_id"], "duck-run")
        packet_path = self.runs / "duck-run" / result["packet_path"]
        self.assertEqual(result["packet_sha256"], _sha(packet_path))
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        claims_path = self.root / "cli-claims.json"
        claims_path.write_text(json.dumps({
            "schema_version": "arcagi-trajectory-claims/v1",
            "run_id": "duck-run",
            "game_id": "gridgame-synthetic-v1",
            "packet_sha256": result["packet_sha256"],
            "claims": [{
                "claim_id": "cli-hypothesis",
                "statement": "The action may change the board.",
                "kind": "post_run_hypothesis",
                "evidence_refs": [packet["trace"][1]["evidence_id"]],
                "alternatives": ["The display may animate without a state change."],
                "prediction": "Repeat the action from the same state and compare the board.",
            }],
        }), encoding="utf-8")
        output = StringIO()
        with redirect_stdout(output):
            code = main([
                "trajectory-import", "--run-id", "duck-run", "--claims-file", str(claims_path),
                "--runs-root", str(self.runs),
            ])
        self.assertEqual(code, 0)
        imported = json.loads(output.getvalue())
        self.assertTrue(imported["trajectory_frozen"])


if __name__ == "__main__":
    unittest.main()
