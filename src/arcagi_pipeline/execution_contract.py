"""Preflight exact game-neutral API inputs before implementation model calls."""
from pathlib import Path
import hashlib

CONTRACT = "design-production/v1"
FILES = {"production_api": "production.py", "design_validation_api": "blueprint.py", "runtime_api": "runtime_control.py"}
GENERATED_CONTRACT = "generated-game/v2"
GENERATED_FILES = {**FILES, "generated_game_api": "generated_game.py"}

def validate_execution_inputs(task, base, *, required=False):
    schema = task.get("output_schema", {})
    bundle_fields = set(schema.get("required", [])) | set(schema.get("properties", {}))
    required = required or {"entrypoint", "factory", "files"}.issubset(bundle_fields)
    value = task.get("execution_contract")
    if value is None and not required:
        return
    if value not in {CONTRACT, GENERATED_CONTRACT}:
        raise ValueError("implementation task requires a declared execution_contract")
    inputs = task.get("inputs", [])
    if not isinstance(inputs,list) or any(not isinstance(row,dict) for row in inputs):
        raise ValueError("implementation inputs must be object records")
    for role, name in (GENERATED_FILES if value == GENERATED_CONTRACT else FILES).items():
        rows = [row for row in inputs if row.get("role") == role]
        if len(rows) != 1 or not isinstance(rows[0].get("path"), str):
            raise ValueError("implementation task requires one actual file input for " + role)
        actual = (Path(base) / rows[0]["path"]).resolve()
        expected = Path(__file__).with_name(name)
        if not actual.is_file() or actual.read_bytes() != expected.read_bytes():
            raise ValueError("implementation API input differs from executing runtime: " + role)
        digest = hashlib.sha256(actual.read_bytes()).hexdigest()
        if rows[0].get("sha256") != digest:
            raise ValueError("implementation API input requires current pinned sha256: " + role)
    if task.get('validation_program'):
        if task['validation_program'] not in ('arcagi-validation-program/v1','arcagi-validation-program/v2'):raise ValueError('unknown validation program contract')
        rows=[r for r in inputs if r.get('role')=='validation_program_api']
        expected=Path(__file__).with_name('verification_program.py')
        if len(rows)!=1 or not isinstance(rows[0].get('path'),str):raise ValueError('validation_program_api input required')
        actual=(Path(base)/rows[0]['path']).resolve()
        if not actual.is_file() or actual.read_bytes()!=expected.read_bytes() or rows[0].get('sha256')!=hashlib.sha256(actual.read_bytes()).hexdigest():raise ValueError('validation_program_api must match pinned runtime')
