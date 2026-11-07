"""Portable frozen-rule LS20 production. No model calls or original checkout needed."""
from __future__ import annotations
import argparse, hashlib, importlib.metadata, json, os, platform, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "vendor" / "arcengine"))
from .implementation import generate_m2b as generator

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def verify_delivery():
    manifest = json.loads((ROOT/"examples/ls20/DEPENDENCIES.json").read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        path = (ROOT/entry["path"]).resolve()
        if not path.is_relative_to(ROOT) or sha(path) != entry["sha256"]:
            raise ValueError("delivery fingerprint mismatch: " + entry["path"])
    return sha(ROOT/"examples/ls20/DEPENDENCIES.json")

def produce(seed: int, output: Path, cap: int = 250_000):
    if cap < 1: raise ValueError("cap must be positive")
    inventory_sha = verify_delivery()
    os.environ["ONLY_RESET_LEVELS"] = "true"  # explicit historical Tycho wrapper policy
    output = output.resolve()
    if output.exists(): raise ValueError("output must not already exist")
    # The original generator has three fixed topology profiles. Seed changes
    # the step-budget margin, not the frozen topology or rule semantics.
    output.mkdir(parents=True)
    generator.OUT = output / "packages"
    original_profiles = generator.PROFILES
    generator.PROFILES = [(a,b,seed+i,c,d+seed%3) for i,(a,b,_,c,d) in enumerate(original_profiles)]
    try:
        rows = generator.generate()
        generator.validate(rows)
    finally:
        generator.PROFILES = original_profiles
    from .implementation import run_m3
    paths = [generator.OUT / f"unit{i}_public.json" for i in (1,2,3)]
    result = run_m3.run(cap=cap, unit_paths=paths)
    result["dependency_manifest_sha256"] = inventory_sha
    result["reset_policy"] = "ONLY_RESET_LEVELS=true"
    result["seed"] = seed
    result["seed_semantics"] = "three fixed topology profiles; seed varies step margin modulo 3 and supervisor seeds"
    result["frozen_inputs"] = {p.name:sha(p) for p in (ROOT/"examples"/"ls20"/"evidence").glob("*.md")}
    result["dependencies"] = {"python":platform.python_version(),"numpy":importlib.metadata.version("numpy"),"pydantic":importlib.metadata.version("pydantic"),"arcengine":"0.9.3 vendored"}
    result["source_sha256"] = sha(ROOT/"examples"/"ls20"/"game"/"ls20.py")
    result["implementation_sha256"] = {p.name:sha(p) for p in (ROOT/"examples"/"ls20"/"implementation").glob("*.py")}
    (output/"report.json").write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    return result

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed",type=int,default=21)
    parser.add_argument("--output-dir",type=Path,required=True)
    parser.add_argument("--cap",type=int,default=250_000)
    args=parser.parse_args()
    try:
        result=produce(args.seed,args.output_dir,args.cap)
    except Exception as exc:
        print(json.dumps({"result":"RETURN","stage":"production","error":type(exc).__name__,"message":str(exc)}))
        return 1
    print(json.dumps({"result":result["result"],"levels":len(result["level_results"]),"output":str(args.output_dir)},indent=2))
    return 0 if result["result"]=="PASS_ALL_THREE" else 1
if __name__=="__main__": raise SystemExit(main())
