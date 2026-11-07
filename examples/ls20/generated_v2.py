"""Thin v2 adapter over the frozen LS20 generator, independent BFS and Levels."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

from .run import ROOT, verify_delivery
from .implementation import tycho_adapter as adapter, oracle, generate_m2b as generator
from arcagi_pipeline.generated_game import GeneratedUnit, GeneratedLevel, CONTRACT, digest, level_configuration, run_generated_production
from arcagi_pipeline.production import FrozenInputs, ModelSearch
from arcagi_pipeline.runtime_control import RuntimeContext


class Plugin:
    production_contract = CONTRACT
    def __init__(self, packages): self.packages = packages
    def generate_units(self, inputs, seed):
        for unit_index, package in enumerate(self.packages, 1):
            levels = []
            for i, spec in enumerate(package["levels"]):
                config = {"engine_level": level_configuration(adapter.build_level(spec)), "model": spec}
                claims = [{"id": "progress", "statement": "Native completion of this generated level", "check": "native progress"}]
                construction = {"slot_id": str(i), "rule_ids": ["frozen"], "config": config,
                                "rationale": generator.ROLES[i], "claims": claims}
                patches = tuple({"path": ["wsoslqeku", j, "_dir"], "value": (2,1,0,3)[p["start_direction"]]}
                                for j,p in enumerate(spec["path_instances"]))
                levels.append(GeneratedLevel("unit"+str(unit_index)+"-"+str(i), str(i), config,
                                             {"completed": i+1}, construction, patches))
            yield GeneratedUnit("unit"+str(unit_index), "original", "Ls20", "raw-arcengine", tuple(levels))
    def compile_levels(self, unit, source):
        return [adapter.build_level(l.configuration["model"]) for l in unit.levels]
    def search_generated(self, level, cap):
        slot = int(level.slot_id)
        result = oracle.search(level.configuration["model"], cap=cap, slot=slot)
        actions = tuple({"up":1,"down":2,"left":3,"right":4}[a] for a in result.get("actions") or ())
        return ModelSearch({"solvable":"candidate","unsolved":"unsolvable","unknown":"unknown"}[result["status"]], actions,
                           complete_model=True, exhausted=result["status"]=="unsolved",
                           evidence={"configuration_sha256":digest(level.configuration),"goal_sha256":digest(level.goal),
                                     "model_version":"frozen-independent-bfs", "oracle_result":result})
    def smoke_action(self, unit): return 1


def run(output, package_dir, cap=250000):
    verify_delivery()
    output.mkdir(parents=True, exist_ok=False)
    packages = [json.loads((package_dir/f"unit{i}_public.json").read_text()) for i in (1,2,3)]
    evidence = {"evidence":[{"id":"frozen","kind":"historical_frozen_design","source_ref":"examples/ls20/evidence/blueprint.md"}]}
    rules = {"rules":[{"id":"frozen","statement":"Frozen LS20 rule contract", "status":"supported","evidence_refs":["frozen"]}]}
    blueprint = {"production_target":{"contract":CONTRACT,"object":"playable_generated_unit"},"slots":[
        {"id":str(i),"purpose":role,"game_function":role,"rationale":"Frozen reference slot", "prerequisites":[],
         "rule_ids":["frozen"],"evidence_refs":["frozen"],"teaching_hypotheses":["Historical teaching role"],
         "behavior_requirements":[{"id":"progress","kind":"behavior","observation_path":["completed"],"predicate":"equals","value":i+1}]}
        for i,role in enumerate(generator.ROLES)]}
    for name,value in (("evidence",evidence),("rules",rules),("blueprint",blueprint)):
        (output/(name+".json")).write_text(json.dumps(value),encoding="utf-8")
    source = ROOT/"examples/ls20/game/ls20.py"
    runtime = {"schema_version":"arcagi-runtime/v1","python":sys.executable,"python_sha256":hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
               "import_roots":[str(ROOT/"vendor/arcengine"),str(ROOT/"src"),str(ROOT)],"dependencies":[],
               "sources":[{"id":"original","path":str(source),"sha256":hashlib.sha256(source.read_bytes()).hexdigest()}],"timeout_seconds":600}
    (output/"runtime.json").write_text(json.dumps(runtime),encoding="utf-8")
    result = run_generated_production(Plugin(packages),FrozenInputs(output/"rules.json",output/"blueprint.json",output/"evidence.json"),seed=22,
                                      output_dir=output/"production",runtime_context=RuntimeContext(output/"runtime.json"),cap=cap)
    print(json.dumps({"status":result["status"],"tasks":len(result["tasks"]),"feedback":result["feedback"]}))
    return result

if __name__ == "__main__":
    p=argparse.ArgumentParser();p.add_argument("--output",type=Path,required=True);p.add_argument("--packages",type=Path,required=True)
    args=p.parse_args(); raise SystemExit(0 if run(args.output,args.packages)["status"]=="pass" else 1)
