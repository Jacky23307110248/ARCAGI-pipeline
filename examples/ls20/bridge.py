"""LS20 implementation of the generic production contract (CP2).

This adapter reuses CP1 algorithms. CP2 alignment is not a second engine run.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from arcagi_pipeline.production import EngineVerdict, FrozenInputs, GeneratedTask, ModelSearch
from .run import ROOT, sha, verify_delivery
from .implementation import generate_m2b as generator


class LS20ProductionPlugin:
    plugin_id = "ls20-m2b-v5-m3"

    def generate(self, inputs: FrozenInputs, seed: int):
        verify_delivery()
        expected = ROOT / "examples/ls20/evidence"
        if inputs.fingerprints() != {"rule_contract": sha(expected/"rule-contract.md"),
                                     "blueprint": sha(expected/"blueprint.md")}:
            raise ValueError("this reference plugin requires its frozen M1/M0 inputs")
        tasks = []
        profiles, previous_out = generator.PROFILES, generator.OUT
        try:
            generator.PROFILES = [(a,b,seed+i,c,d+seed%3)
                                  for i,(a,b,_,c,d) in enumerate(profiles)]
            with tempfile.TemporaryDirectory() as temporary:
                generator.OUT = Path(temporary)/"packages"
                rows = generator.generate()
                generator.validate(rows)
                for unit, _, _, package, manifest in rows:
                    for slot, (level, declaration) in enumerate(zip(package["levels"], manifest["level_declarations"])):
                        tasks.append(GeneratedTask(f"unit{unit}-L{slot+1}",
                                                   {"unit": unit, "slot": slot,
                                                    "level": level, "declaration": declaration}))
        finally:
            generator.PROFILES, generator.OUT = profiles, previous_out
        return tasks

    def validate(self, task):
        spec = task.payload["level"]
        if spec.get("level_id") != f"L{task.payload['slot']+1}":
            return ["level slot does not match identity"]
        if not spec.get("goal_specs"):
            return ["level has no goals"]
        return []

    def search(self, task, cap):
        from .implementation import oracle
        result = oracle.search(task.payload["level"], cap=cap, slot=task.payload["slot"])
        status = {"solvable": "candidate", "unsolved": "unsolvable", "unknown": "unknown"}[result["status"]]
        # Exhaustion is scoped to the declared public finite model, not the engine.
        return ModelSearch(status, tuple(result.get("actions") or ()), result["status"],
                           complete_model=True, exhausted=result["status"] == "unsolved",
                           evidence={"oracle_result": result})

    def replay(self, task, candidate):
        from .implementation import run_m3
        os.environ["ONLY_RESET_LEVELS"] = "true"
        result = run_m3.isolate_replay(task.payload["level"], task.payload["slot"],
                                      candidate.evidence["oracle_result"])
        return EngineVerdict(result["pass"], "accepted" if result["pass"] else "model_engine_mismatch",
                             tuple(result["trace"]), result.get("mismatch_step"),
                             {"game_source_sha256": sha(ROOT/"examples/ls20/game/ls20.py"),
                              "dependency_manifest_sha256": verify_delivery()})


def create_plugin():
    return LS20ProductionPlugin()
