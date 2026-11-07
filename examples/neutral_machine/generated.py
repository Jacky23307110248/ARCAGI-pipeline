"""V2 neutral fixtures: variable counts, mixed actions and nonmonotonic roles."""
from dataclasses import replace
from arcagi_pipeline.generated_game import GeneratedUnit, GeneratedLevel, CONTRACT, digest
from arcagi_pipeline.production import ModelSearch

def design(count=3, mode="normal"):
    evidence = {"evidence": [{"id": "e", "kind": "source", "source_ref": "bound original neutral engine"}]}
    rules = {"rules": [{"id": "move", "statement": "Actions change the value and reaching the target advances", "status": "supported", "evidence_refs": ["e"]}]}
    slots = []
    for i in range(count):
        slots.append({"id": str(i), "purpose": ["combine", "introduce", "revisit"][i % 3],
                      "game_function": "Reach the declared value", "rationale": "Neutral original progression",
                      "prerequisites": [], "rule_ids": ["move"], "evidence_refs": ["e"], "teaching_hypotheses": [],
                      "behavior_requirements": [{"id": "progress", "kind": "behavior", "observation_path": ["completed"],
                                                  "predicate": "equals", "value": i + 1}]})
    return evidence, rules, {"production_target": {"contract": CONTRACT, "object": "playable_generated_unit"}, "slots": slots}

class Plugin:
    production_contract = CONTRACT
    def __init__(self, count=3, mode="normal"): self.count, self.mode = count, mode
    def generate_units(self, inputs, seed):
        levels = []
        for i in range(self.count):
            config = {"start": 2 + i * 3, "target": 3 + i * 3}
            if self.mode == "labels": config = {"start": 0, "target": 1, "label": str(i), "seed": seed}
            if self.mode == "initial": config["target"] = config["start"]
            construction = {"slot_id": str(i), "rule_ids": ["move"], "config": config,
                            "rationale": "A playable generated state", "claims": [{"id": "progress", "statement": "Reaches the target", "check": "machine native goal and observations"}]}
            levels.append(GeneratedLevel(str(i), str(i), config, {"completed": i + 1}, construction))
        kwargs = {"ignore_install": self.mode == "old_instance", "unit_failure": self.mode == "unit_fail", "reject": self.mode == "reject"}
        yield GeneratedUnit("u", "engine", "Engine", "source-protocol", tuple(levels), constructor=kwargs)
    def search_generated(self, level, cap):
        evidence = {"configuration_sha256": digest(level.configuration), "goal_sha256": digest(level.goal), "model_version": "neutral-value-model/v1"}
        if self.mode == "wrong_search": evidence["configuration_sha256"] = digest({"other": True})
        if self.mode == "unknown": return ModelSearch("unknown", reason="cap", evidence=evidence)
        actions = () if self.mode == "initial" else (({"delta": 1},) if int(level.task_id) % 2 else ("forward",))
        return ModelSearch("candidate", actions, evidence=evidence)
    def smoke_action(self, unit): return "forward"

def create_plugin(context): return Plugin()
