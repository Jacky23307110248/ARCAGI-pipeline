from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from arcagi_pipeline.agent_workflow import Completion, DeepSeekProvider, WorkflowError, WorkflowRunner, workflow_status
from arcagi_pipeline.cli import main
from arcagi_pipeline.trajectory_handoff import export_trajectory_packet


class FakeProvider:
    provider_id = "test-provider/v1"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, messages, *, model, max_tokens):
        self.calls.append({"messages": messages, "model": model, "max_tokens": max_tokens})
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class AgentWorkflowTests(unittest.TestCase):
    def make_bundle(self, root: Path, *, budget_tokens: int = 12000, blind_role: str = "trajectory_packet") -> tuple[Path, Path, Path]:
        prompt = root / "prompt.md"
        prompt.write_text("Analyze only the supplied evidence and return a structured artifact.", encoding="utf-8")
        source_record = root / "game_synthetic.json"
        record = {"game_id": "synthetic-grid", "trace": [{"turn": 1, "frame": "a", "action": "press"}]}
        source_record.write_text(json.dumps(record), encoding="utf-8")
        record_hash = hashlib.sha256(source_record.read_bytes()).hexdigest()
        record_ref = source_record.resolve().relative_to(root.resolve().parent).as_posix()
        trace = root / "trajectory_packet.json"
        packet = {
            "schema_version": "arcagi-trajectory-packet/v1", "run_id": "historic-synthetic", "backend": "tycho",
            "game": {"id": "synthetic-grid", "kind": "original", "version": "fixture-1"}, "run_status": "legacy_recorded",
            "source_binding": {"status": "unbound"}, "source_provenance": {"code_version_status": "unbound"},
            "trajectory_index": None, "observation_encoding": "plain-json/v1",
            "trace": [{
                "evidence_id": "ev-000001", "step": 1, "observation": "a", "action": {"name": "press"},
                "feedback": None, "terminal_result": None,
                "source_ref": {"path": record_ref, "sha256": record_hash, "record_index": 0},
            }],
            "saved_notes": [], "run_outcome": {}, "outcome_evidence": [], "coverage": {"exported_trace_steps": 1},
            "interpretation_limits": {"source_code_included": False, "engine_internal_fields_imported": False, "raw_model_reasoning_imported": False},
            "excluded_material": {}, "source_artifacts": [{"role": "game_record", "path": record_ref, "sha256": record_hash}],
        }
        trace.write_text(json.dumps(packet, ensure_ascii=False), encoding="utf-8")
        spec = {
            "schema_version": "arcagi-agent-workflow/v1",
            "workflow_id": "synthetic-evidence-to-contract",
            "unit_id": "synthetic-unit-a",
            "game": {"id": "synthetic-grid", "version": "fixture-1", "extension": {"shape": "non-grid"}},
            "provider": {"id": "deepseek-chat-completions/v1", "model": "deepseek-chat"},
            "budget": {"max_attempts": 4, "max_tokens": budget_tokens},
            "tasks": [
                {
                    "id": "trajectory-claims", "stage": "trajectory_analysis", "evidence_lane": "trajectory_blind",
                    "depends_on": [], "prompt": "prompt.md", "max_output_tokens": 900,
                    "inputs": [{"id": "saved-run", "role": blind_role, "path": "trajectory_packet.json"}],
                    "output_schema": {
                        "type": "object", "required": ["claims"], "additionalProperties": False,
                        "properties": {"claims": {"type": "array", "items": {"type": "object", "required": ["claim"], "properties": {"claim": {"type": "string"}}}}},
                    },
                },
                {
                    "id": "rule-contract-review", "stage": "rule_contract", "evidence_lane": "general",
                    "depends_on": ["trajectory-claims"], "prompt": "prompt.md", "max_output_tokens": 900,
                    "inputs": [{"id": "claims", "role": "trajectory_claims", "from_task": "trajectory-claims"}],
                    "output_schema": {
                        "type": "object", "required": ["status"], "additionalProperties": False,
                        "properties": {"status": {"type": "string", "enum": ["draft", "conflict", "unknown"]}},
                    },
                },
            ],
        }
        spec_path = root / "workflow.json"
        spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
        return spec_path, prompt, trace

    def test_blind_cache_provenance_preserves_blind_dependents(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);spec,_,_=self.make_bundle(root)
            data=json.loads(spec.read_text());data["tasks"][1]["evidence_lane"]="trajectory_blind";spec.write_text(json.dumps(data))
            p=FakeProvider([Completion('{"claims":[{"claim":"visible fixture"}]}',total_tokens=7),Completion('{"status":"draft"}',total_tokens=7)])
            WorkflowRunner(spec,root/"run0",provider=p).run();record=json.loads((root/"run0/workflow_run.json").read_text());row=record["tasks"][0]
            import copy
            updated=copy.deepcopy(data);updated["workflow_id"]="blind-cache-revision";task=updated["tasks"][0]
            task["frozen_output"]={"path":str(root/"run0"/row["output_path"]),"sha256":row["output_sha256"]}
            task["frozen_origin"]={"record_path":str(root/"run0/workflow_run.json"),"record_sha256":hashlib.sha256((root/"run0/workflow_run.json").read_bytes()).hexdigest(),"spec_path":str(spec),"spec_sha256":hashlib.sha256(spec.read_bytes()).hexdigest(),"task_id":task["id"]}
            revised=root/"revised.json";revised.write_text(json.dumps(updated));p2=FakeProvider([Completion('{"status":"unknown"}',total_tokens=7)])
            result=WorkflowRunner(revised,root/"run1",provider=p2).run()
            self.assertEqual(result["status"],"complete");self.assertEqual(len(p2.calls),1)
            updated["tasks"][0]["frozen_origin"]["record_sha256"]="0"*64;revised.write_text(json.dumps(updated))
            with self.assertRaises(WorkflowError):WorkflowRunner(revised,plan_only=True)

    def test_evidence_union_cannot_rewrite_immutable_input(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);spec,_,_=self.make_bundle(root);data=json.loads(spec.read_text())
            for task in data["tasks"]:task["output_schema"]={"type":"object","required":["evidence"]}
            data["tasks"][1]["preserve_evidence_inputs"]=["claims"];spec.write_text(json.dumps(data))
            provider=FakeProvider([Completion('{"evidence":[{"id":"e","content":"original"}]}',total_tokens=7),Completion('{"evidence":[{"id":"e","content":"rewritten"}]}',total_tokens=7)])
            with self.assertRaises(WorkflowError) as caught:WorkflowRunner(spec,root/"run",provider=provider).run()
            self.assertEqual(caught.exception.diagnostic_ref["route"],"artifact_revision")

    def test_plan_is_offline_and_does_not_create_a_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec, _, _ = self.make_bundle(root)
            run_dir = root / "never-created"
            runner = WorkflowRunner(spec, run_dir, plan_only=True)
            with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "secret-plan-marker"}):
                summary = runner.plan()
            self.assertFalse(summary["execution_started"])
            self.assertEqual([item["id"] for item in summary["tasks"]], ["trajectory-claims", "rule-contract-review"])
            self.assertEqual(summary["tasks"][0]["inputs"][0]["sha256"], hashlib.sha256((root / "trajectory_packet.json").read_bytes()).hexdigest())
            self.assertFalse(run_dir.exists())

    def test_dag_execution_persists_hashes_and_resume_does_not_repeat_completed_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec, _, _ = self.make_bundle(root)
            run_dir = root / "run"
            provider = FakeProvider([
                Completion('{"claims":[{"claim":"press changes a visible marker"}]}', total_tokens=30, prompt_tokens=20, completion_tokens=10),
                Completion('{"status":"draft"}', total_tokens=25, prompt_tokens=18, completion_tokens=7),
            ])
            runner = WorkflowRunner(spec, run_dir, provider=provider)
            result = runner.run()
            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["attempts_used"], 2)
            self.assertEqual(len(provider.calls), 2)
            state = json.loads((run_dir / "workflow_run.json").read_text(encoding="utf-8"))
            self.assertEqual([row["status"] for row in state["tasks"]], ["complete", "complete"])
            self.assertTrue(all(len(row["output_sha256"]) == 64 for row in state["tasks"]))
            self.assertEqual(state["budget"]["tokens_used"], 55)
            self.assertNotIn("secret", json.dumps(state).lower())
            resumed = FakeProvider([])
            result2 = WorkflowRunner(spec, run_dir, provider=resumed).run()
            self.assertEqual(result2["status"], "complete")
            self.assertEqual(resumed.calls, [])
            status = workflow_status(run_dir)
            self.assertEqual(status["status"], "complete")
            self.assertEqual(status["budget"]["attempts_used"], 2)
            self.assertEqual([item["status"] for item in status["tasks"]], ["complete", "complete"])
            (run_dir / "artifacts" / "trajectory-claims.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(WorkflowError, "missing or changed"):
                workflow_status(run_dir)

    def test_resume_rejects_changed_input_even_if_output_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec, _, trace = self.make_bundle(root)
            provider = FakeProvider([
                Completion('{"claims":[{"claim":"observed"}]}', total_tokens=12),
                Completion('{"status":"draft"}', total_tokens=12),
            ])
            WorkflowRunner(spec, root / "run", provider=provider).run()
            packet = json.loads(trace.read_text(encoding="utf-8"))
            packet["run_outcome"] = {"changed": True}
            trace.write_text(json.dumps(packet), encoding="utf-8")
            with self.assertRaisesRegex(WorkflowError, "input hash mismatch|inputs or prompt changed"):
                WorkflowRunner(spec, root / "run", provider=FakeProvider([])).run()

    def test_trajectory_blind_task_rejects_source_input_before_provider_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec, _, _ = self.make_bundle(root, blind_role="source_code")
            provider = FakeProvider([])
            with self.assertRaisesRegex(WorkflowError, "trajectory_blind"):
                WorkflowRunner(spec, root / "run", provider=provider)
            self.assertEqual(provider.calls, [])

    def test_trajectory_blind_rejects_raw_source_even_when_role_claims_packet(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec, _, _ = self.make_bundle(root)
            source = root / "source.py"
            source.write_text("def hidden_rule(): return 'secret source'", encoding="utf-8")
            raw = json.loads(spec.read_text(encoding="utf-8"))
            raw["tasks"][0]["inputs"][0]["path"] = "source.py"
            spec.write_text(json.dumps(raw), encoding="utf-8")
            provider = FakeProvider([])
            with self.assertRaisesRegex(WorkflowError, "trajectory-blind input|historic game record"):
                WorkflowRunner(spec, root / "run", provider=provider)
            self.assertEqual(provider.calls, [])

    def test_trajectory_blind_rejects_raw_game_record_even_when_role_claims_packet(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec, _, _ = self.make_bundle(root)
            raw = json.loads(spec.read_text(encoding="utf-8"))
            raw["tasks"][0]["inputs"][0]["path"] = "game_synthetic.json"
            spec.write_text(json.dumps(raw), encoding="utf-8")
            provider = FakeProvider([])
            with self.assertRaisesRegex(WorkflowError, "must be a pipeline trajectory packet"):
                WorkflowRunner(spec, root / "run", provider=provider)
            self.assertEqual(provider.calls, [])

    def test_sourceblind_accepts_only_canonical_packet_bound_to_harness_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs = root / "runs"
            run_id = "native-synthetic"
            run_dir = runs / run_id
            record_path = run_dir / "tycho" / "game_synthetic.json"
            record_path.parent.mkdir(parents=True)
            record_path.write_text(json.dumps({
                "game_id": "synthetic-grid", "trace": [{"turn": 1, "frame": "visible", "action": "press"}],
            }), encoding="utf-8")
            record_ref = {
                "path": record_path.relative_to(run_dir).as_posix(),
                "sha256": hashlib.sha256(record_path.read_bytes()).hexdigest(),
            }
            index = {
                "schema_version": "arcagi-trajectory-index/v1", "provenance": "tycho_original_game_run",
                "game_id": "synthetic-grid", "artifacts": {"game_record": record_ref}, "saved_notes": [],
            }
            index_path = run_dir / "trajectory_index.json"
            index_path.write_text(json.dumps(index), encoding="utf-8")
            index_ref = {"path": "trajectory_index.json", "sha256": hashlib.sha256(index_path.read_bytes()).hexdigest()}
            session = {
                "schema_version": "arcagi-harness-session/v2", "run_id": run_id, "backend": "tycho",
                "game": {"id": "synthetic-grid", "kind": "original_game", "version": "fixture-1"},
                "status": "interrupted", "trajectory_index": index_ref, "tycho": {},
            }
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "session.json").write_text(json.dumps(session), encoding="utf-8")
            packet_info = export_trajectory_packet(run_id=run_id, runs_root=runs)
            spec, _, _ = self.make_bundle(root)
            raw = json.loads(spec.read_text(encoding="utf-8"))
            raw["tasks"][0]["inputs"][0]["path"] = str((run_dir / packet_info["packet_path"]).relative_to(root).as_posix())
            spec.write_text(json.dumps(raw), encoding="utf-8")
            preview = WorkflowRunner(spec, plan_only=True).plan()
            self.assertTrue(preview["valid"])
            self.assertEqual(preview["tasks"][0]["inputs"][0]["role"], "trajectory_packet")

    def test_failure_is_counted_and_retry_requires_explicit_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec, _, _ = self.make_bundle(root, budget_tokens=12000)
            run_dir = root / "run"
            provider = FakeProvider([OSError("synthetic failure with SECRET_MARKER")])
            with self.assertRaisesRegex(WorkflowError, "task trajectory-claims failed"):
                WorkflowRunner(spec, run_dir, provider=provider).run()
            state = json.loads((run_dir / "workflow_run.json").read_text(encoding="utf-8"))
            self.assertEqual(state["budget"]["attempts_used"], 1)
            self.assertGreater(state["budget"]["tokens_used"], 0)
            self.assertEqual(state["attempt_log"][0]["error_code"], "OSError")
            self.assertNotIn("SECRET_MARKER", json.dumps(state))
            with self.assertRaisesRegex(WorkflowError, "--retry-failed"):
                WorkflowRunner(spec, run_dir, provider=FakeProvider([])).run()
            retry = FakeProvider([
                Completion('{"claims":[{"claim":"observed"}]}', total_tokens=10),
                Completion('{"status":"draft"}', total_tokens=10),
            ])
            result = WorkflowRunner(spec, run_dir, provider=retry).run(retry_failed=True)
            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["attempts_used"], 3)

    def test_schema_failure_spends_budget_and_does_not_write_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec, _, _ = self.make_bundle(root)
            run_dir = root / "run"
            provider = FakeProvider([Completion('{"wrong":true}', total_tokens=14)])
            with self.assertRaisesRegex(WorkflowError, "schema validation"):
                WorkflowRunner(spec, run_dir, provider=provider).run()
            state = json.loads((run_dir / "workflow_run.json").read_text(encoding="utf-8"))
            self.assertEqual(state["budget"]["attempts_used"], 1)
            self.assertEqual(state["tasks"][0]["status"], "failed")
            self.assertFalse((run_dir / "artifacts" / "trajectory-claims.json").exists())

    def test_private_reasoning_fields_are_rejected_and_never_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec, _, _ = self.make_bundle(root)
            run_dir = root / "run"
            provider = FakeProvider([Completion('{"claims":[],"reasoning_content":"PRIVATE_MARKER"}', total_tokens=20)])
            with self.assertRaisesRegex(WorkflowError, "private-reasoning"):
                WorkflowRunner(spec, run_dir, provider=provider).run()
            persisted = "\n".join(path.read_text(encoding="utf-8") for path in run_dir.rglob("*") if path.is_file())
            self.assertNotIn("PRIVATE_MARKER", persisted)
            self.assertFalse((run_dir / "artifacts" / "trajectory-claims.json").exists())

    def test_interrupted_request_reserves_tokens_and_requires_explicit_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec, _, _ = self.make_bundle(root, budget_tokens=12000)
            run_dir = root / "run"
            with self.assertRaises(KeyboardInterrupt):
                WorkflowRunner(spec, run_dir, provider=FakeProvider([KeyboardInterrupt()])).run()
            state = json.loads((run_dir / "workflow_run.json").read_text(encoding="utf-8"))
            self.assertEqual(state["budget"]["attempts_used"], 1)
            self.assertGreater(state["budget"]["tokens_reserved"], 0)
            runner = WorkflowRunner(spec, run_dir, provider=FakeProvider([]))
            with self.assertRaisesRegex(WorkflowError, "--retry-failed"):
                runner.run()
            state = json.loads((run_dir / "workflow_run.json").read_text(encoding="utf-8"))
            self.assertEqual(state["budget"]["tokens_reserved"], 0)
            self.assertGreater(state["budget"]["tokens_used"], 0)
            self.assertEqual(state["tasks"][0]["status"], "interrupted")
            retry = FakeProvider([
                Completion('{"claims":[{"claim":"observed"}]}', total_tokens=10),
                Completion('{"status":"draft"}', total_tokens=10),
            ])
            self.assertEqual(WorkflowRunner(spec, run_dir, provider=retry).run(retry_failed=True)["status"], "complete")

    def test_recovers_after_final_artifact_was_written_before_completion_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec, _, _ = self.make_bundle(root, budget_tokens=12000)
            run_dir = root / "run"
            first = WorkflowRunner(spec, run_dir, provider=FakeProvider([
                Completion('{"claims":[{"claim":"press changes a marker"}]}', total_tokens=24),
            ]))
            actual_save = first._save
            save_count = 0

            def fail_after_artifact_commit():
                nonlocal save_count
                save_count += 1
                if save_count == 5:
                    raise KeyboardInterrupt()
                actual_save()

            first._save = fail_after_artifact_commit
            with self.assertRaises(KeyboardInterrupt):
                first.run()
            artifact = run_dir / "artifacts" / "trajectory-claims.json"
            self.assertTrue(artifact.is_file())
            disk_state = json.loads((run_dir / "workflow_run.json").read_text(encoding="utf-8"))
            self.assertEqual(disk_state["tasks"][0]["status"], "running")
            self.assertEqual(disk_state["attempt_log"][0]["status"], "output_staged")

            resumed_provider = FakeProvider([Completion('{"status":"draft"}', total_tokens=10)])
            result = WorkflowRunner(spec, run_dir, provider=resumed_provider).run()
            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["attempts_used"], 2)
            self.assertEqual(len(resumed_provider.calls), 1)
            self.assertEqual(result["tasks"][0]["status"], "complete")
            final_state = json.loads((run_dir / "workflow_run.json").read_text(encoding="utf-8"))
            self.assertEqual(final_state["budget"]["tokens_reserved"], 0)
            self.assertEqual(final_state["budget"]["tokens_used"], 34)

    def test_explicit_large_budget_is_supported_and_nonpositive_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec, _, _ = self.make_bundle(root)
            raw = json.loads(spec.read_text(encoding="utf-8"))
            raw["budget"]["max_attempts"] = 51
            spec.write_text(json.dumps(raw), encoding="utf-8")
            raw["budget"]["max_tokens"]=2_000_000
            spec.write_text(json.dumps(raw),encoding="utf-8")
            self.assertTrue(WorkflowRunner(spec,plan_only=True).plan()["valid"])
            raw["budget"]["max_attempts"]=0
            spec.write_text(json.dumps(raw),encoding="utf-8")
            with self.assertRaisesRegex(WorkflowError,"max_attempts"):
                WorkflowRunner(spec,root/"run",provider=FakeProvider([]))

    def test_malformed_nested_schema_and_dependency_cycle_are_rejected_preflight(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec, _, _ = self.make_bundle(root)
            raw = json.loads(spec.read_text(encoding="utf-8"))
            raw["tasks"][0]["output_schema"]["properties"]["claims"]["items"] = {"type": "object", "unknownKeyword": True}
            spec.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(WorkflowError, "unsupported schema keys"):
                WorkflowRunner(spec, root / "schema-run", provider=FakeProvider([]))
            raw = json.loads(spec.read_text(encoding="utf-8"))
            raw["tasks"][0]["output_schema"]["properties"]["claims"]["items"] = {"type": "object", "required": ["claim"], "properties": {"claim": {"type": "string"}}}
            raw["tasks"][0]["evidence_lane"] = "general"
            raw["tasks"][0]["depends_on"] = ["rule-contract-review"]
            spec.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(WorkflowError, "cycle"):
                WorkflowRunner(spec, root / "cycle-run", provider=FakeProvider([]))

    def test_cli_plan_prints_hashes_without_execution_or_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec, _, _ = self.make_bundle(root)
            output = []
            from contextlib import redirect_stdout
            from io import StringIO
            stream = StringIO()
            with redirect_stdout(stream), patch.dict(os.environ, {"DEEPSEEK_API_KEY": "do-not-print"}):
                code = main(["workflow-plan", "--spec", str(spec)])
            output.append(stream.getvalue())
            self.assertEqual(code, 0)
            self.assertIn('"execution_started": false', output[0])
            self.assertNotIn("do-not-print", output[0])

    def test_env_file_loader_reads_only_provider_keys_and_provider_hides_reasoning(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text("DEEPSEEK_API_KEY=test-placeholder-key\nDEEPSEEK_MODEL=deepseek-flash\nUNRELATED_SECRET=never-read\n", encoding="utf-8")
            provider, model = DeepSeekProvider.from_environment(env_file, environ={})
            self.assertEqual(model, "deepseek-flash")
            response = {
                "choices": [{"message": {"content": "{}", "reasoning_content": "PRIVATE_THOUGHT"}}],
                "usage": {"total_tokens": 9, "prompt_tokens": 5, "completion_tokens": 4},
            }
            class FakeResponse:
                def __enter__(self): return self
                def __exit__(self, *args): return False
                def read(self): return json.dumps(response).encode()
            with patch("urllib.request.urlopen", return_value=FakeResponse()):
                completion = provider.complete([{"role": "user", "content": "fixture"}], model=model, max_tokens=8)
            self.assertEqual(completion.content, "{}")
            self.assertNotIn("PRIVATE_THOUGHT", completion.content)
            self.assertEqual(completion.total_tokens, 9)


if __name__ == "__main__":
    unittest.main()
