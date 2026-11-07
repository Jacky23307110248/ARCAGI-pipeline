from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from arcagi_pipeline import generated_level_replay as replay
from arcagi_pipeline.generated_level_replay import ReplayProvenance, run_generated_level_replay
from arcagi_pipeline.engine_probe import ProbeBinding, VersionBoundEngineProbe


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class OpaqueProvider:
    def __init__(self, binding_path: Path, contract_path: Path, *, envelope_change=None, goal_at=2):
        binding = json.loads(binding_path.read_text())
        self.binding_path, self.contract_path = binding_path, contract_path
        self.goal_at = goal_at
        source, runtime = binding["source"], binding["runtime"]
        data = dict(game_id="opaque-game", game_version="v7",
                    level_id="opaque-17", level_sha256="a" * 64,
                    source_binding_sha256=sha(binding_path), source_tree_sha256=source["source_tree_sha256"],
                    runtime_sha256=runtime["dependency_inventory_sha256"], rule_contract_sha256=sha(contract_path))
        if envelope_change:
            data.update(envelope_change)
        self.envelope = ReplayProvenance(**data)
        self.last_adapter = None

    def replay_provenance(self, level): return self.envelope
    def level_identity(self, level): return level["id"]
    def level_sha256(self, level): return level["sha"]
    def make_engine_adapter(self, level):
        self.last_adapter = FakeAdapter()
        return self.last_adapter
    def is_goal_observation(self, level, obs): return obs["node"] == self.goal_at


class FakeAdapter:
    backend = "opaque-backend"
    game_id = "opaque-game"

    def __init__(self, refusal=None): self.i, self.refusal, self.actions, self.reset_calls = 0, refusal, [], 0
    def reset(self): self.reset_calls += 1; self.i = 0; return {"node": 0, "opaque": {"v": 2}}
    def step(self, action):
        self.actions.append(action)
        if self.refusal == "raise": raise RuntimeError("engine failed")
        if self.refusal == "reject": return {"node": -1, "state": "LOSE"}
        self.i += 1
        return {"node": self.i, "opaque": {"action": action}}
    @staticmethod
    def summarize(observation): return observation


class FakeBinder:
    def __init__(self, *, drift=None, refusal=None): self.drift, self.refusal = drift, refusal; self.last_probe = None
    def bind(self, adapter, *, source_binding_path):
        raw = json.loads(source_binding_path.read_text())
        source = {"status":"verified", "backend":adapter.backend, "game_id":raw["game_id"],
                  "environment":{"source_tree_sha256":raw["source"]["source_tree_sha256"]}}
        runtime = raw["runtime"]
        binding = ProbeBinding(source, runtime)
        current_source = dict(source)
        current_runtime = dict(runtime)
        if self.drift == "source":
            current_source = {**source, "environment":{"source_tree_sha256":"9"*64}}
        elif self.drift == "runtime":
            current_runtime = {**runtime, "dependency_inventory_sha256":"8"*64}
        current = ProbeBinding(current_source, current_runtime)
        adapter.refusal = self.refusal
        self.last_probe = VersionBoundEngineProbe(adapter, binding, lambda: current)
        return self.last_probe


class GeneratedReplayTests(unittest.TestCase):
    def setup_inputs(self, root):
        binding_path = root / "binding.json"
        binding = {"game_id":"opaque-game", "game_version":"v7", "status":"verified",
                   "source":{"status":"verified", "source_tree_sha256":"1"*64},
                   "runtime":{"status":"verified", "dependency_inventory_sha256":"2"*64}}
        binding_path.write_text(json.dumps(binding))
        contract_path = root / "contract.json"
        contract_input = root / "contract-input.json"
        contract_input.write_text("{}")
        contract = {"schema_version":"arcagi-rule-contract-result/v1", "frozen":True,
                    "game_id":"opaque-game", "game_version":"v7", "source_sha256":"1"*64,
                    "runtime_binding_sha256":"2"*64,
                    "source_binding":{"sha256":sha(binding_path)},
                    "inputs":[{"role":"source_binding", "path":str(binding_path), "sha256":sha(binding_path)},
                              {"role":"contract_input", "path":str(contract_input), "sha256":sha(contract_input)}]}
        contract_path.write_text(json.dumps(contract))
        return binding_path, contract_path, {"id":"opaque-17", "sha":"a"*64}

    def test_non_boolean_goal_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            b,c,level=self.setup_inputs(Path(d));provider=OpaqueProvider(b,c)
            provider.is_goal_observation=lambda level,obs: "false"
            result=run_generated_level_replay(provider,level,[],source_binding_path=b,rule_contract_path=c,binder=FakeBinder())
            self.assertFalse(result.accepted);self.assertIn("TypeError",result.reason)

    def test_custom_terminal_predicate_stops_before_actions(self):
        with tempfile.TemporaryDirectory() as d:
            b,c,level=self.setup_inputs(Path(d));provider=OpaqueProvider(b,c)
            provider.is_terminal_observation=lambda level,obs: obs["node"]==0
            result=run_generated_level_replay(provider,level,["arbitrary"],source_binding_path=b,rule_contract_path=c,binder=FakeBinder())
            self.assertFalse(result.accepted);self.assertEqual(len(result.trace),1)

    def test_opaque_actions_and_states_replay_to_goal_with_trace(self):
        with tempfile.TemporaryDirectory() as d:
            b, c, level = self.setup_inputs(Path(d)); provider = OpaqueProvider(b,c)
            result = run_generated_level_replay(provider, level, ["blink", {"phase": 9}], source_binding_path=b, rule_contract_path=c, binder=FakeBinder())
            self.assertTrue(result.accepted); self.assertEqual(len(result.trace), 3)
            self.assertEqual(provider.last_adapter.actions, ["blink", {"phase": 9}])

    def test_identity_and_trusted_binding_claim_mismatches_reject(self):
        for change, level_change in [({"source_binding_sha256":"0"*64}, None), ({"source_tree_sha256":"0"*64}, None), ({"runtime_sha256":"0"*64}, None), ({"rule_contract_sha256":"0"*64}, None), ({}, {"id":"changed"}), ({"level_sha256":"0"*64}, None)]:
            with self.subTest(change=change, level_change=level_change), tempfile.TemporaryDirectory() as d:
                b,c,level=self.setup_inputs(Path(d)); provider=OpaqueProvider(b,c,envelope_change=change)
                if level_change: level.update(level_change)
                result=run_generated_level_replay(provider,level,["blink"],source_binding_path=b,rule_contract_path=c,binder=FakeBinder())
                self.assertFalse(result.accepted); self.assertIsNone(provider.last_adapter)

    def test_goal_not_reached_engine_rejection_and_adapter_exception_reject(self):
        with tempfile.TemporaryDirectory() as d:
            b,c,level=self.setup_inputs(Path(d)); provider=OpaqueProvider(b,c,goal_at=99)
            for mode in (None,"reject","raise"):
                binder=FakeBinder(refusal=mode)
                result=run_generated_level_replay(provider,level,["blink","blink"],source_binding_path=b,rule_contract_path=c,binder=binder)
                self.assertFalse(result.accepted)

    def test_any_frozen_contract_input_mutation_rejected_before_engine_open(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); b,c,level=self.setup_inputs(root); provider=OpaqueProvider(b,c)
            extra=root/"contract-input.json"; extra.write_text("changed")
            result=run_generated_level_replay(provider,level,["blink"],source_binding_path=b,rule_contract_path=c,binder=FakeBinder())
            self.assertFalse(result.accepted); self.assertIsNone(provider.last_adapter)


    def test_version_bound_probe_rejects_source_or_runtime_drift_on_reset(self):
        for drift in ("source", "runtime"):
            with self.subTest(drift=drift), tempfile.TemporaryDirectory() as d:
                b,c,level=self.setup_inputs(Path(d)); provider=OpaqueProvider(b,c,goal_at=2)
                binder=FakeBinder(drift=drift)
                result=run_generated_level_replay(provider,level,["blink"],source_binding_path=b,rule_contract_path=c,binder=binder)
                self.assertFalse(result.accepted)
                self.assertEqual(binder.last_probe.adapter.reset_calls, 0)

    def test_runner_rejects_bare_fake_probe_without_session_guard(self):
        class UnguardedBinder:
            def bind(self, adapter, *, source_binding_path): return adapter
        with tempfile.TemporaryDirectory() as d:
            b,c,level=self.setup_inputs(Path(d)); provider=OpaqueProvider(b,c)
            result=run_generated_level_replay(provider,level,["blink"],source_binding_path=b,rule_contract_path=c,binder=UnguardedBinder())
            self.assertFalse(result.accepted)
            self.assertEqual(result.reason,"replay_mismatch:TypeError")
            self.assertEqual(provider.last_adapter.reset_calls,0)


if __name__ == "__main__": unittest.main()
