"""Config-driven verification and loading of external Python runtime artifacts.

This module provides process isolation from ambient Python path settings; it is
not a security sandbox for untrusted code.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import types
import uuid
from typing import Any
from .diagnostics import Fault


class RuntimeFault(Fault):
    """A sanitized runtime failure with a stable machine-readable code."""
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)

    def as_dict(self) -> dict[str, str]:
        return {"ok": False, "fault": self.code}


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _config(config_path: Path) -> tuple[dict[str, Any], Path]:
    config_path = Path(config_path).resolve()
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema_version") != "arcagi-runtime/v1":
            raise ValueError
        required = ("python", "python_sha256", "import_roots", "dependencies", "sources", "timeout_seconds")
        if any(k not in data for k in required):
            raise ValueError
        if not isinstance(data["python"], str) or not isinstance(data["python_sha256"], str):
            raise ValueError
        if not isinstance(data["import_roots"], list) or not isinstance(data["dependencies"], list) or not isinstance(data["sources"], list):
            raise ValueError
        if type(data["timeout_seconds"]) is not int or data["timeout_seconds"] <= 0:
            raise ValueError
        for dep in data["dependencies"]:
            if not isinstance(dep, dict) or not all(isinstance(dep.get(k), str) and dep[k] for k in ("name", "version", "module")):
                raise ValueError
        for src in data["sources"]:
            if not isinstance(src, dict) or not all(isinstance(src.get(k), str) and src[k] for k in ("id", "path", "sha256")):
                raise ValueError
        if len({s["id"] for s in data["sources"]}) != len(data["sources"]):
            raise ValueError
        return data, config_path.parent
    except RuntimeFault:
        raise
    except Exception:
        raise RuntimeFault("config_invalid") from None


def _resolve(value: str, base: Path) -> Path:
    p = Path(value)
    return (p if p.is_absolute() else base / p).resolve()


def _env() -> dict[str, str]:
    # Explicit allowlist prevents provider credentials and Python path overrides.
    keep = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"}
    return {k: v for k, v in os.environ.items() if k.upper() in keep and "PYTHON" not in k.upper()}


def runtime_command(config_path: Path) -> tuple[str, dict[str, str], dict[str, Any]]:
    data, base = _config(config_path)
    python = _resolve(data["python"], base)
    try:
        if not python.is_file() or _sha(python) != data["python_sha256"].lower():
            raise RuntimeFault("artifact_changed")
        if not all(isinstance(x,str) and _resolve(x,base).is_dir() for x in data["import_roots"]): raise RuntimeFault("config_invalid")
        roots = [_resolve(x, base) for x in data["import_roots"]]
        sources = [{"id": s["id"], "path": str(_resolve(s["path"], base)), "sha256": s["sha256"].lower()} for s in data["sources"]]
        facts = {"schema_version": data["schema_version"], "python_path": str(python), "python_sha256": _sha(python),
                 "import_roots": [str(p) for p in roots], "dependencies": data["dependencies"], "sources": sources,
                 "timeout_seconds": data["timeout_seconds"]}
        return str(python), _env(), facts
    except RuntimeFault:
        raise
    except Exception:
        raise RuntimeFault("config_invalid") from None


_PREFLIGHT = r'''import sys,json,hashlib,importlib.metadata as md,importlib.util,importlib,contextlib,io
from pathlib import Path
facts=json.loads(sys.stdin.read()); sys.path[:0]=facts["import_roots"]+[facts["core_src"]]
def sha(p):
 h=hashlib.sha256(); f=open(p,"rb")
 while True:
  b=f.read(1048576)
  if not b: break
  h.update(b)
 return h.hexdigest()
if sys.version_info < (3,12): print(json.dumps({"fault":"config_invalid"})); raise SystemExit
for s in facts["sources"]:
 p=Path(s["path"])
 if not p.is_file() or sha(p)!=s["sha256"]: print(json.dumps({"fault":"artifact_changed"})); raise SystemExit
dep_report=[]
for d in facts["dependencies"]:
 try:
  if md.version(d["name"])!=d["version"]: raise LookupError()
  with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
   module=importlib.import_module(d["module"])
  spec=module.__spec__
  if not spec or not spec.origin: raise LookupError()
  origin=Path(spec.origin).resolve()
  dist=md.distribution(d["name"])
  if not any(Path(dist.locate_file(f)).resolve()==origin for f in (dist.files or [])): raise LookupError()
  if d.get("module_sha256") and sha(origin)!=d["module_sha256"]: raise LookupError()
  inventory=[]
  for f in sorted(dist.files or [],key=str):
   p=Path(dist.locate_file(f))
   if "__pycache__" not in p.parts and p.suffix!=".pyc" and p.is_file():inventory.append([str(f).replace(chr(92),"/"),sha(p)])
  inventory_hash=hashlib.sha256(json.dumps(inventory,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
  if d.get("inventory_sha256") and inventory_hash!=d["inventory_sha256"]: raise LookupError()
  dep_report.append({"name":d["name"],"version":md.version(d["name"]),"module":d["module"],"module_path":str(origin),"module_sha256":sha(origin),"inventory_sha256":inventory_hash})
 except Exception: print(json.dumps({"fault":"declared_dependency_missing"})); raise SystemExit
print(json.dumps({"python_version":".".join(map(str,sys.version_info[:3])),"executable":str(Path(sys.executable).resolve()),"sources":[{"id":s["id"],"sha256":sha(s["path"])} for s in facts["sources"]],"dependencies":dep_report}))'''


def preflight_runtime(config_path: Path) -> dict[str, Any]:
    python, env, facts = runtime_command(config_path)
    data, base = _config(config_path)
    facts["core_src"] = str(Path(__file__).resolve().parents[1])
    try:
        result = subprocess.run([python, "-I", "-c", _PREFLIGHT], input=json.dumps(facts), env=env,
                                capture_output=True, text=True, timeout=data["timeout_seconds"], check=False)
        parsed = json.loads(result.stdout.strip()) if result.stdout.strip() else {}
        if parsed.get("fault"):
            raise RuntimeFault(parsed["fault"])
        if result.returncode or not parsed.get("python_version"):
            raise RuntimeFault("config_invalid")
        fingerprint = hashlib.sha256(json.dumps({"runtime": facts, "verified": parsed}, sort_keys=True).encode()).hexdigest()
        return {"ok": True, "fingerprint": fingerprint, **parsed}
    except RuntimeFault:
        raise
    except Exception:
        raise RuntimeFault("config_invalid") from None


class RuntimeContext:
    def __init__(self, config_path: Path):
        self.data, self.base = _config(config_path)
        self.python = _resolve(self.data["python"], self.base)
        try:
            if Path(sys.executable).resolve() != self.python or _sha(self.python)!=self.data["python_sha256"].lower() or sys.version_info < (3, 12):
                raise RuntimeFault("config_invalid")
            verified = preflight_runtime(config_path)
            self.facts = {"python_path": str(self.python), "python_sha256": _sha(self.python), "sources": [],
                          "identity": verified["fingerprint"]}
        except RuntimeFault:
            raise
        except Exception:
            raise RuntimeFault("artifact_changed") from None
        self.modules: dict[str, types.ModuleType] = {}
        self.sessions: list[dict[str, Any]] = []

    def load_source(self, source_id: str) -> types.ModuleType:
        src = next((s for s in self.data["sources"] if s["id"] == source_id), None)
        if src is None: raise RuntimeFault("config_invalid")
        path = _resolve(src["path"], self.base)
        try:
            digest = _sha(path)
            if digest != src["sha256"].lower(): raise RuntimeFault("artifact_changed")
            name = "_arcagi_runtime_source_" + hashlib.sha256(source_id.encode()).hexdigest()[:20]
            spec = importlib.util.spec_from_file_location(name, path)
            if spec is None or spec.loader is None: raise RuntimeFault("config_invalid")
            module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
            self.modules[source_id] = module
            self.facts["sources"] = [x for x in self.facts["sources"] if x["id"] != source_id] + [{"id": source_id, "path": str(path), "sha256": digest}]
            return module
        except RuntimeFault: raise
        except Exception: raise RuntimeFault("artifact_changed") from None

    def session(self, adapter: Any, source_id: str) -> Any:
        if source_id not in self.modules: raise RuntimeFault("config_invalid")
        module=self.modules[source_id]
        source_object=getattr(adapter,"source_object",None)
        classes=[value for value in vars(module).values() if isinstance(value,type) and value.__module__==module.__name__]
        linked = source_object is module or (callable(source_object) and getattr(source_object,"__module__",None)==module.__name__) or any(isinstance(source_object,cls) for cls in classes)
        if not linked:raise RuntimeFault("output_protocol_invalid")
        record = {"session_id": uuid.uuid4().hex, "source_id": source_id, "runtime_identity": self.facts["identity"], "machine_owned": True, "source_instance_type":type(source_object).__name__, "trajectory": []}
        self.sessions.append(record)
        class Session:
            session_id = record["session_id"]
            def reset(inner, *args, **kwargs):
                try:
                    value = adapter.reset(*args, **kwargs); record["trajectory"].append({"op":"reset","result":_jsonable(value)}); return value
                except Exception as exc:
                    record["trajectory"].append({"op":"reset","exception_type":type(exc).__name__}); raise
            def step(inner, *args, **kwargs):
                try:
                    value = adapter.step(*args, **kwargs); record["trajectory"].append({"op":"step","action":_jsonable({"args":args,"kwargs":kwargs}),"result":_jsonable(value)}); return value
                except Exception as exc:
                    record["trajectory"].append({"op":"step","exception_type":type(exc).__name__}); raise
        return Session()

    def receipt(self) -> dict[str, Any]:
        for source in self.facts["sources"]:
            if _sha(Path(source["path"])) != source["sha256"]:raise RuntimeFault("artifact_changed")
        return {"runtime": dict(self.facts), "loaded_sources": list(self.facts["sources"]), "sessions": json.loads(json.dumps(self.sessions))}


def _jsonable(value: Any) -> Any:
    try: return json.loads(json.dumps(value,allow_nan=False))
    except Exception: raise RuntimeFault("output_protocol_invalid") from None
