from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from arcagi_pipeline.evidence import EvidenceLedger, SourceFinding, TrajectoryEntry, trajectory_analysis_packet
from arcagi_pipeline.workflow import PipelineRun


class EvidenceTests(unittest.TestCase):
    def test_trajectory_packet_whitelists_player_visible_fields(self):
        packet = trajectory_analysis_packet({
            "game_id": "g", "run_id": "r", "source_code": "SECRET",
            "trace": [{"observation": "a", "action": "left", "engine_internal": "SECRET"}],
            "outcome": "won",
        }, [{"text": "I think the stored sequence preserves entry order", "source_path": "SECRET"}])
        wire = json.dumps(packet)
        self.assertNotIn("SECRET", wire)
        self.assertIn("left", wire)
        self.assertIn("I think the stored sequence preserves entry order", wire)

    def test_source_audit_requires_frozen_trajectory_and_keeps_it_unchanged(self):
        ledger = EvidenceLedger("game-x")
        ledger.add_trajectory(TrajectoryEntry("t1", "direct_observation", "the sequence was saved", ("step-2",)))
        with self.assertRaises(ValueError):
            ledger.add_source_finding(SourceFinding("s1", "save requires an exact sequence", "engine.py:20", "abc"))
        before = ledger.freeze_trajectory()
        ledger.add_source_finding(SourceFinding("s1", "save requires an exact sequence", "engine.py:20", "abc"))
        self.assertEqual(ledger.trajectory_digest(), before)
        with self.assertRaises(ValueError):
            ledger.add_trajectory(TrajectoryEntry("t2", "agent_inference", "late", ("note",)))

    def test_pipeline_stage_artifact_hashes_and_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = PipelineRun(root, "p", {"id": "g", "version": "1"})
            artifact = root / "materials.json"
            artifact.write_text("{}", encoding="utf-8")
            run.record_stage("materials", [artifact])
            with self.assertRaises(ValueError):
                run.record_stage("source_audit", [artifact])
            run.record_stage("trajectory_analysis", [artifact])
            run.record_stage("source_audit", [artifact])
            record = json.loads((root / "pipeline_run.json").read_text(encoding="utf-8"))
            self.assertEqual(record["stages"][0]["outputs"][0]["sha256"], "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a")


if __name__ == "__main__":
    unittest.main()
