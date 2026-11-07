import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

from arcagi_pipeline.runtime_control import RuntimeContext, RuntimeFault, preflight_runtime, runtime_command


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    src = tmp_path / "tiny_source.py"
    src.write_text("VALUE = 7\nclass Engine: pass\n", encoding="utf-8")
    config = {
        "schema_version": "arcagi-runtime/v1",
        "python": str(Path(sys.executable).resolve()),
        "python_sha256": _sha(Path(sys.executable).resolve()),
        "import_roots": [],
        "dependencies": [],
        "sources": [{"id": "tiny", "path": str(src), "sha256": _sha(src)}],
        "timeout_seconds": 20,
    }
    cfg = tmp_path / "runtime.json"
    cfg.write_text(json.dumps(config), encoding="utf-8")
    return cfg, src


def test_runtime_config_preflight_and_command_are_config_driven(tmp_path, monkeypatch):
    cfg, _ = _fixture(tmp_path)
    monkeypatch.setenv("PYTHONPATH", "must-not-leak")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    python, env, facts = runtime_command(cfg)
    assert Path(python) == Path(sys.executable).resolve()
    assert "PYTHONPATH" not in env and "OPENAI_API_KEY" not in env
    assert facts["sources"][0]["id"] == "tiny"
    result = preflight_runtime(cfg)
    assert result["ok"] is True and len(result["fingerprint"]) == 64
    assert result["sources"][0]["sha256"] == facts["sources"][0]["sha256"]


def test_context_loads_source_and_receipts_machine_owned_trace(tmp_path):
    cfg, _ = _fixture(tmp_path)
    context = RuntimeContext(cfg)
    module = context.load_source("tiny")
    assert module.VALUE == 7

    class Adapter:
        source_object=module.Engine()
        def reset(self): return {"state": 0}
        def step(self, action): return {"state": action}

    session = context.session(Adapter(), "tiny")
    session.reset()
    session.step(3)
    receipt = context.receipt()
    assert receipt["loaded_sources"][0]["sha256"] == _sha(Path(receipt["loaded_sources"][0]["path"]))
    record = receipt["sessions"][0]
    assert record["machine_owned"] is True
    assert [x["op"] for x in record["trajectory"]] == ["reset", "step"]


def test_artifact_tampering_fails_with_stable_fault(tmp_path):
    cfg, src = _fixture(tmp_path)
    src.write_text("VALUE = 8\n", encoding="utf-8")
    with pytest.raises(RuntimeFault) as exc:
        preflight_runtime(cfg)
    assert exc.value.code == "artifact_changed"


def test_bad_schema_is_sanitized(tmp_path):
    cfg = tmp_path / "bad.json"
    cfg.write_text('{"schema_version":"wrong"}', encoding="utf-8")
    with pytest.raises(RuntimeFault) as exc:
        runtime_command(cfg)
    assert exc.value.code == "config_invalid"


def test_wrong_interpreter_digest_fails_before_launch(tmp_path):
    cfg,_=_fixture(tmp_path);data=json.loads(cfg.read_text());data["python_sha256"]="0"*64;cfg.write_text(json.dumps(data))
    with pytest.raises(RuntimeFault) as caught:preflight_runtime(cfg)
    assert caught.value.code=="artifact_changed"

def test_declared_dependency_origin_is_not_just_version_label(tmp_path):
    import importlib.metadata
    cfg,_=_fixture(tmp_path);data=json.loads(cfg.read_text());data["dependencies"]=[{"name":"pytest","version":importlib.metadata.version("pytest"),"module":"json"}];cfg.write_text(json.dumps(data))
    with pytest.raises(RuntimeFault) as caught:preflight_runtime(cfg)
    assert caught.value.code=="declared_dependency_missing"

def test_unbound_adapter_is_rejected(tmp_path):
    cfg,_=_fixture(tmp_path);context=RuntimeContext(cfg);context.load_source("tiny")
    class Fake:
        source_object=object()
        def reset(self):return {}
        def step(self,a):return {}
    with pytest.raises(RuntimeFault) as caught:context.session(Fake(),"tiny")
    assert caught.value.code=="output_protocol_invalid"

def test_functional_source_can_be_bound_without_a_game_class(tmp_path):
    cfg,_=_fixture(tmp_path);context=RuntimeContext(cfg);source=context.load_source("tiny")
    class Adapter:
        source_object=source
        def reset(self):return {"value":source.VALUE}
        def step(self,a):return {"value":source.VALUE+a}
    session=context.session(Adapter(),"tiny");session.reset();session.step(2)
    assert context.receipt()["sessions"][0]["source_instance_type"]=="module"
