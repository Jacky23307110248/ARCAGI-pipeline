"""Replay the frozen six-level TR87 unit with the original ARC engine."""
import argparse
import json
import sys
from hashlib import sha256
from pathlib import Path

from arcagi_pipeline.generated_game import (
    CONTRACT, GeneratedLevel, GeneratedUnit, digest, run_generated_production,
)
from arcagi_pipeline.production import FrozenInputs, ModelSearch
from arcagi_pipeline.runtime_control import RuntimeContext

CASE = Path(__file__).resolve().parent
ROOT = CASE.parents[1]
ENGINE = ROOT / "vendor" / "arcengine"
PACKAGE_MODULES = {
    "numpy": "numpy",
    "pydantic": "pydantic",
    "pydantic-core": "pydantic_core",
    "typing-extensions": "typing_extensions",
    "annotated-types": "annotated_types",
    "typing-inspection": "typing_inspection",
}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def file_hash(path):
    return sha256(path.read_bytes()).hexdigest()


def dependencies():
    pins = (CASE / "requirements.txt").read_text(encoding="utf-8").splitlines()
    return [
        {"name": name, "version": version, "module": PACKAGE_MODULES[name]}
        for name, version in (pin.split("==") for pin in pins)
    ]


class FrozenPlugin:
    production_contract = CONTRACT

    def __init__(self, unit, candidates):
        self.unit = unit
        self.candidates = candidates

    def generate_units(self, inputs, seed):
        yield self.unit

    def compile_levels(self, unit, source):
        from .adapter import level_from_snapshot

        return [level_from_snapshot(level.configuration["engine_level"]) for level in unit.levels]

    def search_generated(self, level, cap):
        candidate = self.candidates[level.task_id]
        evidence = candidate["evidence"]
        actions = tuple(candidate["actions"])
        if (candidate["status"] != "candidate"
                or evidence["configuration_sha256"] != digest(level.configuration)
                or evidence["goal_sha256"] != digest(level.goal)
                or list(actions) != level.configuration["model"]["solution_actions"]):
            raise ValueError(f"Frozen candidate does not match {level.task_id}")
        if len(actions) > cap:
            return ModelSearch("unknown", reason="candidate_exceeds_cap", evidence=evidence)
        return ModelSearch("candidate", actions, reason="frozen_offline_candidate", evidence=evidence)

    def smoke_action(self, unit):
        return 4


def verify(output):
    for name, expected in read_json(CASE / "SHA256.json").items():
        if file_hash(ROOT / name) != expected:
            raise ValueError(f"Published input hash mismatch for {name}")

    frozen = read_json(CASE / "unit.json")
    replay = read_json(CASE / "replay.json")
    if digest(frozen) != replay["unit_sha256"]:
        raise ValueError("Frozen unit identity mismatch")
    levels = tuple(GeneratedLevel(**row) for row in frozen.pop("levels"))
    unit = GeneratedUnit(**frozen, levels=levels)
    if len(levels) != 6 or set(replay["candidates"]) != {level.task_id for level in levels}:
        raise ValueError("Expected one frozen candidate per level in the six-level unit")

    source = CASE / "game" / "tr87.py"
    python = Path(sys.executable)
    sys.path.insert(0, str(ENGINE))
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    config = output / "runtime.json"
    config.write_text(json.dumps({
        "schema_version": "arcagi-runtime/v1",
        "python": str(python),
        "python_sha256": file_hash(python),
        "import_roots": [str(ENGINE), str(ROOT / "src"), str(ROOT)],
        "dependencies": dependencies(),
        "sources": [{"id": unit.source_id, "path": str(source), "sha256": file_hash(source)}],
        "timeout_seconds": 600,
    }), encoding="utf-8")
    report = run_generated_production(
        FrozenPlugin(unit, replay["candidates"]),
        FrozenInputs(CASE / "rules.json", CASE / "blueprint.json", CASE / "evidence.json"),
        seed=0, output_dir=output / "production", runtime_context=RuntimeContext(config),
    )
    behavior_checks = [
        check for row in report["tasks"] for check in row.get("fulfillment", {}).values()
    ]
    passed = (
        report["status"] == "pass"
        and report["completion_scope"] == "generated_unit_verified/v2"
        and len(report["tasks"]) == 6 and len(behavior_checks) == 32
        and all(check["status"] == "pass" for check in behavior_checks)
        and all(row["unit_artifact_sha256"] == replay["unit_sha256"]
                and row["verified_solution"] is True for row in report["tasks"])
        and len(report["units"]) == 1
        and report["units"][0]["unit_execution"]["status"] == "pass"
        and report["units"][0]["adapter_smoke"]["status"] == "pass"
    )
    print(json.dumps({
        "status": "pass" if passed else "return", "levels": len(report["tasks"]),
        "behavior_checks": len(behavior_checks), "completion_scope": report["completion_scope"],
        "report": str(output / "production" / "production.json"),
    }))
    return passed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    return 0 if verify(parser.parse_args().output) else 1


if __name__ == "__main__":
    raise SystemExit(main())
