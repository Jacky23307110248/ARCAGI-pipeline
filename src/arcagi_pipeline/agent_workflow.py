from __future__ import annotations

import hashlib
import http.client
import time
import math
import uuid
import json
import os
import re
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence
from urllib.parse import urlparse
from .diagnostics import traced, current, ArchiveWriteError
from .budget import AggregateBudget, file_lock

MAX_UNIT_ATTEMPTS = 50
MAX_UNIT_TOKENS = 50_000
_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_PRIVATE_REASONING_KEYS = {"reasoning", "reasoning_content", "chain_of_thought", "cot", "thoughts"}
_PACKET_FIELDS = {
    "schema_version", "run_id", "backend", "game", "run_status", "source_binding", "source_provenance",
    "trajectory_index", "observation_encoding", "trace", "saved_notes", "run_outcome", "outcome_evidence",
    "coverage", "interpretation_limits", "excluded_material", "source_artifacts",
}
_TRACE_FIELDS = {"evidence_id", "step", "source_ref", "observation", "action", "feedback", "terminal_result"}


class WorkflowError(ValueError):
    """A safe-to-display workflow validation or execution error."""
    def __init__(self,message,*,code=None):
        self.diagnostic_code=code
        super().__init__(message)


class ProviderError(RuntimeError):
    """Provider failure that deliberately omits response bodies and credentials."""
    def __init__(self,message,*,code=None,http_status=None,transport=None):
        self.diagnostic_code=code
        self.diagnostic_observed={"http_status":http_status} if http_status is not None else {}
        if transport is not None:
            self.diagnostic_observed.update(transport)
        super().__init__(message)


@dataclass(frozen=True)
class Completion:
    content: str
    total_tokens: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    finish_reason: str | None = None
    response_model: str | None = None


class AgentProvider(Protocol):
    provider_id: str

    def complete(self, messages: Sequence[Mapping[str, str]], *, model: str, max_tokens: int) -> Completion: ...


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_env_file(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    result: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in {"DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL"}:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if value:
            result[key] = value
    return result


def _transport_facts(exc):
    # Only types/numeric OS codes; never copy addresses, partial bodies or messages.
    result = {"transport_kind": "timeout" if isinstance(exc, TimeoutError) else "connection", "exception_type": type(exc).__name__}
    for field in ("errno", "winerror"):
        value = getattr(exc, field, None)
        if isinstance(value, int) and not isinstance(value, bool): result[field] = value
    return result


class DeepSeekProvider:
    """Minimal OpenAI-compatible DeepSeek client; only user-visible content is returned."""

    provider_id = "deepseek-chat-completions/v1"

    def __init__(self, api_key: str, base_url: str = "https://api.deepseek.com/v1", *, timeout: float = 120.0):
        if not api_key.strip():
            raise WorkflowError("DeepSeek API key is missing")
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise WorkflowError("DeepSeek base URL must be an HTTPS URL without credentials, query, or fragment")
        self._api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    @classmethod
    def from_environment(cls, env_file: Path | None = None, environ: Mapping[str, str] | None = None) -> tuple["DeepSeekProvider", str]:
        file_values = _read_env_file(env_file)
        env = os.environ if environ is None else environ
        values = {key: (file_values.get(key, "") if env_file is not None else env.get(key, "")) for key in ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL")}
        provider = cls(values["DEEPSEEK_API_KEY"], values["DEEPSEEK_BASE_URL"] or "https://api.deepseek.com/v1")
        return provider, values["DEEPSEEK_MODEL"] or "deepseek-chat"

    def complete(self, messages: Sequence[Mapping[str, str]], *, model: str, max_tokens: int) -> Completion:
        url = self.base_url if self.base_url.endswith("/chat/completions") else f"{self.base_url}/chat/completions"
        body = json.dumps({
            "model": model,
            "messages": list(messages),
            "max_tokens": max_tokens,
            "stream": False,
            "response_format": {"type":"json_object"},
        }, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise ProviderError(f"DeepSeek request failed with HTTP {exc.code}",code="provider_http",http_status=exc.code) from None
        except urllib.error.URLError as exc:
            raise ProviderError("DeepSeek connection failed",code="provider_transport",transport=_transport_facts(exc.reason)) from None
        except http.client.IncompleteRead as exc:
            raise ProviderError("DeepSeek response transfer incomplete",code="provider_transport",transport={"transport_kind":"incomplete_response","exception_type":type(exc).__name__}) from None
        except (TimeoutError, OSError) as exc:
            raise ProviderError("DeepSeek connection failed",code="provider_transport",transport=_transport_facts(exc)) from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ProviderError("DeepSeek returned an invalid response") from None
        try:
            choice = payload["choices"][0]
            message = choice["message"]
            content = message.get("content")
            if not isinstance(content, str):
                raise TypeError
            usage = payload.get("usage", {})
            total = usage.get("total_tokens")
            prompt = usage.get("prompt_tokens")
            completion = usage.get("completion_tokens")
            return Completion(
                content=content,
                finish_reason=choice.get("finish_reason"),
                response_model=payload.get("model") if isinstance(payload.get("model"),str) else None,
                total_tokens=total if isinstance(total, int) and not isinstance(total, bool) and total >= 0 else None,
                prompt_tokens=prompt if isinstance(prompt, int) and not isinstance(prompt, bool) and prompt >= 0 else None,
                completion_tokens=completion if isinstance(completion, int) and not isinstance(completion, bool) and completion >= 0 else None,
            )
        except (KeyError, IndexError, TypeError):
            raise ProviderError("DeepSeek response did not contain a text completion") from None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def _schema_errors(value: Any, schema: Mapping[str, Any], location: str = "$", *, depth: int = 0) -> list[str]:
    if depth > 40:
        return [f"{location}: schema nesting exceeds 40 levels"]
    errors: list[str] = []
    allowed_schema_keys = {
        "type", "title", "description", "enum", "required", "properties", "additionalProperties",
        "items", "minItems", "maxItems", "minLength", "maxLength",
    }
    unknown = set(schema) - allowed_schema_keys
    if unknown:
        return [f"{location}: unsupported schema keys: {', '.join(sorted(unknown))}"]
    expected = schema.get("type")
    type_map = {
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "null": lambda v: v is None,
    }
    if expected not in type_map:
        return [f"{location}: schema type must be one of {', '.join(type_map)}"]
    if not type_map[expected](value):
        return [f"{location}: expected {expected}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{location}: value is not in enum")
    if expected == "object":
        required = schema.get("required", [])
        properties = schema.get("properties", {})
        if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
            return [f"{location}: required must be a list of strings"]
        if not isinstance(properties, dict):
            return [f"{location}: properties must be an object"]
        for key in required:
            if key not in value:
                errors.append(f"{location}.{key}: required property is missing")
        for key, child in value.items():
            if key in properties:
                if not isinstance(properties[key], dict):
                    errors.append(f"{location}.{key}: property schema must be an object")
                else:
                    errors.extend(_schema_errors(child, properties[key], f"{location}.{key}", depth=depth + 1))
            elif schema.get("additionalProperties", True) is False:
                errors.append(f"{location}.{key}: additional property is not allowed")
    elif expected == "array":
        if isinstance(schema.get("minItems", 0), int) and len(value) < schema.get("minItems", 0):
            errors.append(f"{location}: requires at least {schema['minItems']} items")
        if isinstance(schema.get("maxItems"), int) and len(value) > schema["maxItems"]:
            errors.append(f"{location}: allows at most {schema['maxItems']} items")
        items = schema.get("items")
        if items is not None:
            if not isinstance(items, dict):
                errors.append(f"{location}: items schema must be an object")
            else:
                for index, item in enumerate(value):
                    errors.extend(_schema_errors(item, items, f"{location}[{index}]", depth=depth + 1))
    elif expected == "string":
        if isinstance(schema.get("minLength", 0), int) and len(value) < schema.get("minLength", 0):
            errors.append(f"{location}: string is too short")
        if isinstance(schema.get("maxLength"), int) and len(value) > schema["maxLength"]:
            errors.append(f"{location}: string is too long")
    return errors


def _schema_definition_errors(schema: Any, location: str = "$", *, depth: int = 0) -> list[str]:
    if depth > 40:
        return [f"{location}: schema nesting exceeds 40 levels"]
    if not isinstance(schema, dict):
        return [f"{location}: schema must be an object"]
    allowed = {
        "type", "title", "description", "enum", "required", "properties", "additionalProperties",
        "items", "minItems", "maxItems", "minLength", "maxLength",
    }
    unknown = set(schema) - allowed
    if unknown:
        return [f"{location}: unsupported schema keys: {', '.join(sorted(unknown))}"]
    schema_type = schema.get("type")
    if schema_type not in {"object", "array", "string", "integer", "number", "boolean", "null"}:
        return [f"{location}: invalid or missing schema type"]
    errors: list[str] = []
    if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"]):
        errors.append(f"{location}: enum must be a non-empty list")
    if schema_type == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if not isinstance(properties, dict):
            errors.append(f"{location}: properties must be an object")
        else:
            for key, child in properties.items():
                errors.extend(_schema_definition_errors(child, f"{location}.properties.{key}", depth=depth + 1))
        if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
            errors.append(f"{location}: required must be a list of strings")
        elif len(required) != len(set(required)):
            errors.append(f"{location}: required contains duplicates")
        if not isinstance(schema.get("additionalProperties", True), bool):
            errors.append(f"{location}: additionalProperties must be boolean")
        elif schema.get("additionalProperties", True) is False and isinstance(properties, dict) and not set(required).issubset(properties):
            errors.append(f"{location}: required keys must be declared when additionalProperties is false")
        if "items" in schema:
            errors.append(f"{location}: items is only valid for array schemas")
    elif schema_type == "array":
        if not isinstance(schema.get("items"), dict):
            errors.append(f"{location}: array schema requires an items object")
        else:
            errors.extend(_schema_definition_errors(schema["items"], f"{location}.items", depth=depth + 1))
        if "properties" in schema or "required" in schema:
            errors.append(f"{location}: properties and required are only valid for object schemas")
    elif any(key in schema for key in ("properties", "required", "items", "additionalProperties")):
        errors.append(f"{location}: object and array keywords used with {schema_type}")
    for key in ("minItems", "maxItems", "minLength", "maxLength"):
        if key in schema and (not isinstance(schema[key], int) or isinstance(schema[key], bool) or schema[key] < 0):
            errors.append(f"{location}: {key} must be a non-negative integer")
    if "minItems" in schema and "maxItems" in schema and schema["minItems"] > schema["maxItems"]:
        errors.append(f"{location}: minItems exceeds maxItems")
    if "minLength" in schema and "maxLength" in schema and schema["minLength"] > schema["maxLength"]:
        errors.append(f"{location}: minLength exceeds maxLength")
    return errors


def _private_reasoning_paths(value: Any, location: str = "$", *, depth: int = 0) -> list[str]:
    if depth > 40:
        return [f"{location}: output nesting exceeds 40 levels"]
    paths: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).strip().lower() in _PRIVATE_REASONING_KEYS:
                paths.append(f"{location}.{key}")
            paths.extend(_private_reasoning_paths(child, f"{location}.{key}", depth=depth + 1))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            paths.extend(_private_reasoning_paths(child, f"{location}[{index}]", depth=depth + 1))
    return paths


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise WorkflowError(f"{label} is not readable UTF-8 JSON") from None
    if not isinstance(value, dict):
        raise WorkflowError(f"{label} must contain a JSON object")
    return value


def _check_source_ref(root: Path, ref: Any) -> tuple[str, str]:
    if not isinstance(ref, dict) or not isinstance(ref.get("path"), str) or not isinstance(ref.get("sha256"), str):
        raise WorkflowError("trajectory packet contains an invalid source reference")
    raw_path = Path(ref["path"])
    if raw_path.is_absolute():
        raise WorkflowError("trajectory source references must be relative to their input bundle")
    path = (root / raw_path).resolve()
    if not path.is_relative_to(root.resolve()):
        raise WorkflowError("trajectory source reference escapes its input bundle")
    digest = ref["sha256"]
    if not re.fullmatch(r"[0-9a-f]{64}", digest) or not path.is_file() or sha256_file(path) != digest:
        raise WorkflowError("trajectory source reference is missing or has a hash mismatch")
    return path.as_posix(), digest


def _validate_trajectory_packet(packet_path: Path, packet: dict[str, Any] | None = None) -> None:
    packet_path = packet_path.resolve()
    packet = packet if packet is not None else _load_json_object(packet_path, "trajectory-blind input")
    if packet.get("schema_version") != "arcagi-trajectory-packet/v1":
        raise WorkflowError("trajectory_blind path input must be a pipeline trajectory packet")
    if set(packet) - _PACKET_FIELDS:
        raise WorkflowError("trajectory packet contains fields outside the public packet schema")
    for key in ("run_id", "backend", "game", "run_status", "trace", "saved_notes", "interpretation_limits", "source_artifacts"):
        if key not in packet:
            raise WorkflowError(f"trajectory packet is missing {key}")
    if not isinstance(packet["run_id"], str) or not isinstance(packet["game"], dict) or not isinstance(packet["source_artifacts"], list):
        raise WorkflowError("trajectory packet identity or source-artifact list is invalid")
    limits = packet["interpretation_limits"]
    required_limits = ("source_code_included", "engine_internal_fields_imported", "raw_model_reasoning_imported")
    if not isinstance(limits, dict) or any(limits.get(key) is not False for key in required_limits):
        raise WorkflowError("trajectory packet does not certify exclusion of source, engine internals, and raw reasoning")
    if not isinstance(packet["trace"], list) or not packet["trace"] or not isinstance(packet["saved_notes"], list):
        raise WorkflowError("trajectory packet has no visible trace or has invalid saved notes")
    for row in packet["trace"]:
        if not isinstance(row, dict) or set(row) - _TRACE_FIELDS or not {"evidence_id", "source_ref"}.issubset(row):
            raise WorkflowError("trajectory packet trace contains an invalid or unrecognized row")
    for row in packet["saved_notes"]:
        if not isinstance(row, dict) or set(row) - {"evidence_id", "text", "source_ref"} or not {"evidence_id", "text", "source_ref"}.issubset(row):
            raise WorkflowError("trajectory packet saved notes contain an invalid row")
    if packet["backend"] not in {"tycho", "duck"}:
        raise WorkflowError("trajectory packet backend is invalid")

    # A current pipeline export must live beside its run session and match its indexed artifacts.
    run_dir = packet_path.parent.parent if packet_path.parent.name == "analysis" else None
    session_path = run_dir / "session.json" if run_dir is not None else None
    if session_path is not None and session_path.is_file():
        session = _load_json_object(session_path, "trajectory packet session")
        if session.get("schema_version") != "arcagi-harness-session/v2" or run_dir.name != packet["run_id"]:
            raise WorkflowError("trajectory packet is not in its canonical pipeline run directory")
        if session.get("backend") != packet["backend"] or session.get("game", {}).get("id") != packet["game"].get("id") or session.get("status") != packet.get("run_status"):
            raise WorkflowError("trajectory packet identity does not match its harness session")
        index_ref = session.get("trajectory_index")
        if packet.get("trajectory_index") != index_ref or not isinstance(index_ref, dict):
            raise WorkflowError("trajectory packet index does not match its harness session")
        index_path, index_hash = _check_source_ref(run_dir, index_ref)
        index = _load_json_object(Path(index_path), "trajectory index")
        if index.get("schema_version") != "arcagi-trajectory-index/v1" or index.get("game_id") != packet["game"].get("id"):
            raise WorkflowError("trajectory index identity does not match its packet")
        if index.get("provenance") != f"{packet['backend']}_original_game_run":
            raise WorkflowError("trajectory index provenance does not match the harness backend")
        expected: list[tuple[str, str, str]] = []
        artifacts = index.get("artifacts")
        if packet["backend"] == "tycho" and isinstance(artifacts, dict):
            expected.extend((str(kind), ref.get("path"), ref.get("sha256")) for kind, ref in artifacts.items() if isinstance(ref, dict))
            expected.extend(("saved_note", ref.get("path"), ref.get("sha256")) for ref in index.get("saved_notes", []) if isinstance(ref, dict))
        elif packet["backend"] == "duck" and isinstance(artifacts, list):
            expected.extend((ref.get("kind"), ref.get("path"), ref.get("sha256")) for ref in artifacts if isinstance(ref, dict))
        actual = []
        for ref in packet["source_artifacts"]:
            if not isinstance(ref, dict) or set(ref) != {"kind", "path", "sha256"}:
                raise WorkflowError("trajectory packet source-artifact entry is invalid")
            path_value, digest = _check_source_ref(run_dir, ref)
            actual.append((ref["kind"], Path(path_value).relative_to(run_dir.resolve()).as_posix(), digest))
        expected_normalized = sorted((kind, path.replace("\\", "/"), digest) for kind, path, digest in expected)
        if sorted(actual) != expected_normalized:
            raise WorkflowError("trajectory packet source artifacts do not match the verified harness index")
        if sha256_file(Path(index_path)) != index_hash:
            raise WorkflowError("trajectory index changed while validating the packet")
        return

    # Historic packets are accepted only when explicitly marked unbound and re-linked to known run artifacts.
    binding = packet.get("source_binding")
    provenance = packet.get("source_provenance")
    if packet.get("run_status") != "legacy_recorded" or not isinstance(binding, dict) or binding.get("status") != "unbound" or not isinstance(provenance, dict) or provenance.get("code_version_status") != "unbound":
        raise WorkflowError("trajectory packet has no verifiable pipeline session or explicit legacy provenance")
    allowed_roles = {"game_record", "run_manifest", "saved_note", "raw_event_sidecar", "run_config", "viewer_data"}
    artifact_paths: dict[str, str] = {}
    verified_artifacts: list[tuple[dict[str, Any], Path]] = []
    legacy_root = packet_path.parent.parent.resolve()
    artifact_roles_by_ref: dict[tuple[str, str], str] = {}
    for ref in packet["source_artifacts"]:
        if not isinstance(ref, dict) or set(ref) != {"role", "path", "sha256"} or ref.get("role") not in allowed_roles:
            raise WorkflowError("legacy trajectory packet contains an unsupported source-artifact role")
        path_value, digest = _check_source_ref(legacy_root, {"path": ref["path"], "sha256": ref["sha256"]})
        artifact_paths[ref["role"]] = path_value
        verified_artifacts.append((ref, Path(path_value)))
        artifact_roles_by_ref[(str(Path(path_value).resolve()).casefold(), digest)] = ref["role"]

    def validate_legacy_evidence_ref(ref: Any, accepted_roles: set[str]) -> None:
        if not isinstance(ref, dict) or not isinstance(ref.get("path"), str) or not isinstance(ref.get("sha256"), str):
            raise WorkflowError("legacy trajectory evidence contains an invalid source reference")
        path_value, digest = _check_source_ref(legacy_root, {"path": ref["path"], "sha256": ref["sha256"]})
        role = artifact_roles_by_ref.get((str(Path(path_value).resolve()).casefold(), digest))
        if role not in accepted_roles:
            raise WorkflowError("legacy trajectory evidence reference is not backed by an allowed source artifact")

    trace_roles = {"game_record"} if packet["backend"] == "tycho" else {"raw_event_sidecar"}
    for row in packet["trace"]:
        validate_legacy_evidence_ref(row.get("source_ref"), trace_roles)
    for row in packet["saved_notes"]:
        validate_legacy_evidence_ref(row.get("source_ref"), {"saved_note"})
    for row in packet.get("outcome_evidence", []):
        if not isinstance(row, dict):
            raise WorkflowError("legacy trajectory outcome evidence row is invalid")
        validate_legacy_evidence_ref(row.get("source_ref"), {"game_record", "viewer_data"})

    for ref, path in verified_artifacts:
        if ref["role"] == "game_record":
            record = _load_json_object(path, "historic game record")
            if record.get("game_id") != packet["game"].get("id") or not isinstance(record.get("trace"), list):
                raise WorkflowError("historic game record does not match the trajectory packet")
        elif ref["role"] == "run_manifest":
            manifest = _load_json_object(path, "historic run manifest")
            games = manifest.get("games", [])
            game_id = packet["game"].get("id")
            if not isinstance(games, list) or game_id not in games and str(game_id).split("-", 1)[0] not in games:
                raise WorkflowError("historic run manifest does not match the trajectory packet")
        elif ref["role"] == "run_config":
            config = _load_json_object(path, "historic Duck config")
            if packet["game"].get("id") not in config.get("games", []):
                raise WorkflowError("historic Duck config does not match the trajectory packet")
        elif ref["role"] == "viewer_data":
            viewer = _load_json_object(path, "historic Duck viewer data")
            if viewer.get("game_id") not in {None, packet["game"].get("id")}:
                raise WorkflowError("historic Duck viewer data does not match the trajectory packet")
        elif ref["role"] == "raw_event_sidecar" and not path.name.endswith("_events.jsonl"):
            raise WorkflowError("historic Duck event sidecar has an invalid filename")
        elif ref["role"] == "raw_event_sidecar":
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    if line.strip() and not isinstance(json.loads(line), dict):
                        raise ValueError
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
                raise WorkflowError("historic Duck event sidecar is not valid JSONL") from None
        elif ref["role"] == "saved_note":
            if path.suffix.lower() not in {".md", ".txt", ".json"} or any(token in path.name.lower() for token in ("reasoning", "transcript", "analysis", "prompt")):
                raise WorkflowError("historic saved-note reference is not an allowed note file")
    if packet["backend"] == "tycho" and "game_record" not in artifact_paths:
        raise WorkflowError("historic Tycho packet has no verified game record")
    if packet["backend"] == "duck" and not {"raw_event_sidecar", "run_config"}.issubset(artifact_paths):
        raise WorkflowError("historic Duck packet has no verified event sidecar and run config")


def _validate_blind_import(task, depth=0):
    _validate_blind_graph(task)

def _validate_blind_revision_input(item, target_id, depth=0):
    """Only same-lane DS self-feedback, never supervisor or source audit notes."""
    origin=item.get("blind_revision_origin")
    if not isinstance(origin,dict):raise WorkflowError("blind revision input requires provenance",code="config_invalid")
    proxy={"frozen_origin":origin,"frozen_output":{"sha256":item.get("sha256")}}
    _validate_blind_graph(proxy,item,target_id)

def _validate_blind_graph(task, revision_item=None, target_id=None):
    # Iterative DAG walk avoids recursive depth limits and exponential rechecking
    # of shared review/import ancestors across long, bounded revision histories.
    pending=[(task,revision_item,target_id)];seen=set()
    try:
        while pending:
            current,item,target=pending.pop();origin=current["frozen_origin"]
            record_path=Path(origin["record_path"]);spec_path=Path(origin["spec_path"])
            if sha256_file(record_path)!=origin["record_sha256"] or sha256_file(spec_path)!=origin["spec_sha256"]:raise ValueError()
            record=json.loads(record_path.read_text(encoding="utf-8"));spec=json.loads(spec_path.read_text(encoding="utf-8"))
            if record["spec_sha256"]!=origin["spec_sha256"]:raise ValueError()
            prior=next(t for t in spec["tasks"] if t["id"]==origin["task_id"])
            row=next(t for t in record["tasks"] if t["id"]==origin["task_id"])
            path=(record_path.parent/row["output_path"]).resolve()
            if not path.is_relative_to(record_path.parent.resolve()) or row["status"]!="complete" or sha256_file(path)!=row["output_sha256"] or row["output_sha256"]!=current["frozen_output"]["sha256"] or prior.get("evidence_lane")!="trajectory_blind":raise ValueError()
            if item:
                if Path(item["path"]).resolve()!=path:raise ValueError()
                if item["role"]=="trajectory_review_feedback":
                    if prior.get("gate_review") is not True or target not in prior.get("depends_on",[]):raise ValueError()
                elif item["role"]=="prior_trajectory_claims":
                    if prior["id"]!=target:raise ValueError()
                else:raise ValueError()
            key=(str(record_path.resolve()),origin["record_sha256"],origin["task_id"])
            if key in seen:continue
            seen.add(key)
            if len(seen)>10000:raise ValueError()
            if prior.get("frozen_output"):pending.append((prior,None,None))
            for source in prior.get("inputs",[]):
                if "from_task" in source:
                    if source.get("role")!="trajectory_claims":raise ValueError()
                    dep=next(r for r in record["tasks"] if r["id"]==source["from_task"])
                    pending.append(({"frozen_origin":{**origin,"task_id":source["from_task"]},"frozen_output":{"sha256":dep["output_sha256"]}},None,None))
                elif source.get("role")=="trajectory_packet":
                    packet=(spec_path.parent/source["path"]).resolve()
                    if source.get("sha256") and sha256_file(packet)!=source["sha256"]:raise ValueError()
                    _validate_trajectory_packet(packet)
                elif source.get("role") in {"trajectory_review_feedback","prior_trajectory_claims"}:
                    pending.append(({"frozen_origin":source["blind_revision_origin"],"frozen_output":{"sha256":source["sha256"]}},source,prior["id"]))
                else:raise ValueError()
    except (KeyError,ValueError,OSError,StopIteration,TypeError):raise WorkflowError("blind provenance is invalid",code="artifact_changed") from None


def reconcile_workflow(spec_path,run_dir):
    """Offline bookkeeping only; record endpoint is not a connectivity assertion."""
    root=Path(run_dir).resolve()
    with file_lock(root/".workflow.lock"):
        runner=WorkflowRunner(Path(spec_path),root,plan_only=True)
        runner.state_path=root/"workflow_run.json"
        existing=json.loads(runner.state_path.read_text(encoding="utf-8"))
        runner.provider_id=existing.get("provider",{}).get("id")
        runner.provider_endpoint=existing.get("provider",{}).get("endpoint")
        ledger=runner.spec.get("budget",{}).get("aggregate_ledger")
        runner.aggregate=AggregateBudget((runner.spec_path.parent/ledger).resolve()) if ledger else None
        runner.data=runner._load_or_create_state()
        runner._recover_aggregate_orphans();runner._recover_interrupted_attempts()
        return runner.summary()

class WorkflowRunner:
    """Resumable, game-neutral agent workflow executor with durable budgets and artifacts."""

    @traced("workflow_preflight", "config_invalid")
    def __init__(self, spec_path: Path, run_dir: Path | None = None, *, provider: AgentProvider | None = None, env_file: Path | None = None, plan_only: bool = False, deadline: float | None = None, pause_check=None, diagnosis_receipts=None, diagnosis_observer=None):
        self.diagnosis_receipts=diagnosis_receipts or {}
        self.diagnosis_observer=diagnosis_observer
        self.pause_check = pause_check
        self.deadline = deadline
        self.spec_path = spec_path.resolve()
        self.run_dir = run_dir.resolve() if run_dir is not None else self.spec_path.parent / "runs" / "uninitialized"
        try:
            self.spec_bytes = self.spec_path.read_bytes()
            self.spec = json.loads(self.spec_bytes.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WorkflowError(f"cannot read workflow spec: {exc}") from None
        self.spec_hash = sha256_bytes(self.spec_bytes)
        self._validate_spec()
        self.provider = provider
        configured_provider = self.spec.get("provider", {})
        self.provider_id = getattr(provider, "provider_id", str(configured_provider.get("id", DeepSeekProvider.provider_id)))
        self.model = str(configured_provider.get("model", ""))
        if plan_only:
            self.run_dir = (run_dir or self.spec_path.parent / "runs" / self.spec["workflow_id"]).resolve()
            self.state_path = self.run_dir / "workflow_run.json"
            self.data: dict[str, Any] = {}
            return
        if run_dir is None:
            raise WorkflowError("run_dir is required for execution")
        if self.provider is None and any(not task.get("frozen_output") for task in self.spec["tasks"]):
            if self.provider_id != DeepSeekProvider.provider_id:
                raise WorkflowError(f"unsupported provider: {self.provider_id}")
            self.provider, env_model = DeepSeekProvider.from_environment(env_file)
            if not self.model:
                self.model = env_model
        if not self.model:
            raise WorkflowError("provider.model is required")
        if hasattr(self.provider,"timeout"):self.provider.timeout=configured_provider.get("timeout_seconds",120)
        self.provider_endpoint = getattr(self.provider, "base_url", None)
        ledger=self.spec.get("budget",{}).get("aggregate_ledger")
        self.aggregate=AggregateBudget((self.spec_path.parent/ledger).resolve()) if ledger else None
        self.state_path = self.run_dir / "workflow_run.json"
        self.data = self._load_or_create_state()

    def _validate_spec(self) -> None:
        spec = self.spec
        if not isinstance(spec, dict) or spec.get("schema_version") != "arcagi-agent-workflow/v1":
            raise WorkflowError("schema_version must be arcagi-agent-workflow/v1")
        for key in ("workflow_id", "unit_id"):
            if not isinstance(spec.get(key), str) or not spec[key].strip():
                raise WorkflowError(f"{key} is required")
        if not isinstance(spec.get("game"), dict):
            raise WorkflowError("game must be an object")
        provider = spec.get("provider", {})
        if not isinstance(provider, dict):
            raise WorkflowError("provider must be an object")
        budget = spec.get("budget", {})
        if not isinstance(budget, dict):
            raise WorkflowError("budget must be an object")
        attempts = budget.get("max_attempts", MAX_UNIT_ATTEMPTS)
        tokens = budget.get("max_tokens", MAX_UNIT_TOKENS)
        for key,value in (("max_attempts",attempts),("max_tokens",tokens),("max_input_tokens_per_request",budget.get("max_input_tokens_per_request",tokens)),("max_output_tokens_per_request",budget.get("max_output_tokens_per_request",tokens)),("max_elapsed_seconds",budget.get("max_elapsed_seconds",86400))):
            if isinstance(value,bool) or not isinstance(value,int) or value<1:raise WorkflowError("budget."+key+" must be a positive integer")
        if not isinstance(budget.get("allow_rework_reserve",False),bool):raise WorkflowError("allow_rework_reserve must be boolean")
        timeout=provider.get("timeout_seconds",120)
        if isinstance(timeout,bool) or not isinstance(timeout,(int,float)) or not math.isfinite(timeout) or timeout<=0:raise WorkflowError("provider.timeout_seconds must be finite and positive")
        if "aggregate_ledger" in budget and (not isinstance(budget["aggregate_ledger"],str) or not budget["aggregate_ledger"]):raise WorkflowError("aggregate_ledger must be a path")
        tasks = spec.get("tasks")
        if not isinstance(tasks, list) or not tasks:
            raise WorkflowError("tasks must be a non-empty list")
        ids: list[str] = []
        for index, task in enumerate(tasks):
            prefix = f"tasks[{index}]"
            if not isinstance(task, dict):
                raise WorkflowError(f"{prefix} must be an object")
            task_id = task.get("id")
            if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
                raise WorkflowError(f"{prefix}.id is invalid")
            if task_id in ids:
                raise WorkflowError(f"duplicate task id: {task_id}")
            ids.append(task_id)
            if not isinstance(task.get("stage"), str) or not task["stage"].strip():
                raise WorkflowError(f"{prefix}.stage is required")
            if task.get("evidence_lane", "general") not in {"trajectory_blind", "source_audit", "general"}:
                raise WorkflowError(f"{prefix}.evidence_lane is invalid")
            deps = task.get("depends_on", [])
            if type(task.get("gate_review", False)) is not bool:
                raise WorkflowError("gate_review must be boolean", code="config_invalid")
            inputs = task.get("inputs", [])
            if not isinstance(deps, list) or not all(isinstance(dep, str) for dep in deps):
                raise WorkflowError(f"{prefix}.depends_on must be a list of task IDs")
            if len(deps) != len(set(deps)):
                raise WorkflowError(f"{prefix}.depends_on contains duplicates")
            if not isinstance(inputs, list):
                raise WorkflowError(f"{prefix}.inputs must be a list")
            if not isinstance(task.get("output_schema"), dict):
                raise WorkflowError(f"{prefix}.output_schema must be an object")
            if task["output_schema"].get("type") != "object":
                raise WorkflowError(f"{prefix}.output_schema root type must be object")
            schema_errors = _schema_definition_errors(task["output_schema"])
            if schema_errors:
                raise WorkflowError(f"{prefix}.output_schema: {schema_errors[0]}")
            if task.get('diagnosis_contract'):
                from .diagnosis_protocol import feedback_for
                feedback_for(task['diagnosis_contract'])
            prompt = task.get("prompt")
            if not isinstance(prompt, str) or not prompt.strip():
                raise WorkflowError(f"{prefix}.prompt is required")
            prompt_path = self._resolve(prompt)
            if not prompt_path.is_file():
                raise WorkflowError(f"{prefix}.prompt file does not exist")
            if not isinstance(task.get("max_output_tokens", 4096), int) or isinstance(task.get("max_output_tokens", 4096), bool) or task.get("max_output_tokens", 4096) < 1:
                raise WorkflowError(f"{prefix}.max_output_tokens must be a positive integer")
            frozen = task.get("frozen_output")
            if frozen is not None:
                if task.get("evidence_lane", "general") == "trajectory_blind":
                    _validate_blind_import(task)
                if not isinstance(frozen,dict) or set(frozen) != {"path","sha256"} or not isinstance(frozen.get("path"),str) or not isinstance(frozen.get("sha256"),str):
                    raise WorkflowError("frozen_output requires path and sha256",code="config_invalid")
                path = self._resolve(frozen["path"])
                if not path.is_file() or sha256_file(path) != frozen["sha256"]:
                    raise WorkflowError("frozen_output hash mismatch",code="artifact_changed")
            from .execution_contract import validate_execution_inputs
            validate_execution_inputs(task, self.spec_path.parent)
            input_ids: set[str] = set()
            for input_index, item in enumerate(inputs):
                if not isinstance(item, dict):
                    raise WorkflowError(f"{prefix}.inputs[{input_index}] must be an object")
                if not isinstance(item.get("id"), str) or not item["id"].strip():
                    raise WorkflowError(f"{prefix}.inputs[{input_index}].id is required")
                if item["id"] in input_ids:
                    raise WorkflowError(f"{prefix} has duplicate input ID {item['id']!r}")
                input_ids.add(item["id"])
                if item.get("content_encoding","text") not in {"text","json_compact"}:
                    raise WorkflowError("input content_encoding is invalid",code="config_invalid")
                if not isinstance(item.get("role"), str) or not item["role"].strip():
                    raise WorkflowError(f"{prefix}.inputs[{input_index}].role is required")
                if "path" in item and not isinstance(item["path"],str):
                    raise WorkflowError(f"{prefix}.inputs[{input_index}].path must be a string",code="config_invalid")
                if "from_task" in item and not isinstance(item["from_task"],str):
                    raise WorkflowError(f"{prefix}.inputs[{input_index}].from_task must be a string",code="config_invalid")
                has_path = "path" in item
                has_task = "from_task" in item
                if has_path == has_task:
                    raise WorkflowError(f"{prefix}.inputs[{input_index}] must set exactly one of path or from_task")
                if has_path and not self._resolve(item["path"]).is_file():
                    raise WorkflowError(f"{prefix}.inputs[{input_index}] file does not exist")
                if has_task and item["from_task"] not in deps:
                    raise WorkflowError(f"{prefix}.inputs[{input_index}].from_task must be listed in depends_on")
                if task.get("evidence_lane", "general") == "trajectory_blind":
                    role = item["role"].strip().lower()
                    if has_path:
                        if role in {"trajectory_review_feedback","prior_trajectory_claims"}:
                            _validate_blind_revision_input(item,task_id)
                        elif role != "trajectory_packet":
                            raise WorkflowError(f"{prefix} is trajectory_blind and path inputs must be verified trajectory_packet artifacts")
                        else:_validate_trajectory_packet(self._resolve(item["path"]))
                    elif role != "trajectory_claims":
                        raise WorkflowError(f"{prefix} is trajectory_blind and task outputs must use the trajectory_claims role")
            if "model" in task and (not isinstance(task["model"], str) or not task["model"].strip()):
                raise WorkflowError(f"{prefix}.model must be a non-empty string")
            for dep in deps:
                if dep not in ids and dep not in [t.get("id") for t in tasks[index + 1:] if isinstance(t, dict)]:
                    raise WorkflowError(f"{prefix} references unknown dependency {dep!r}")
                dep_task = next((candidate for candidate in tasks if isinstance(candidate, dict) and candidate.get("id") == dep), None)
                if dep_task is not None and task.get("evidence_lane", "general") == "trajectory_blind" and dep_task.get("evidence_lane", "general") != "trajectory_blind":
                    raise WorkflowError(f"{prefix} cannot depend on non-trajectory-blind task {dep!r}")
        self._topological_tasks()

    def _resolve(self, raw_path: str) -> Path:
        candidate = Path(raw_path)
        return candidate.resolve() if candidate.is_absolute() else (self.spec_path.parent / candidate).resolve()

    def _topological_tasks(self) -> list[dict[str, Any]]:
        tasks = self.spec["tasks"]
        by_id = {task["id"]: task for task in tasks}
        remaining = {task_id: set(task.get("depends_on", [])) for task_id, task in by_id.items()}
        order: list[dict[str, Any]] = []
        while remaining:
            ready = [task["id"] for task in tasks if task["id"] in remaining and not remaining[task["id"]]]
            if not ready:
                raise WorkflowError("task dependency graph contains a cycle or an unknown dependency")
            for task_id in ready:
                order.append(by_id[task_id])
                remaining.pop(task_id)
                for dependencies in remaining.values():
                    dependencies.discard(task_id)
        return order

    def _load_or_create_state(self) -> dict[str, Any]:
        if self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raise WorkflowError("existing workflow state is invalid") from None
            if data.get("schema_version") != "arcagi-workflow-run/v1" or data.get("spec_sha256") != self.spec_hash:
                raise WorkflowError("workflow spec changed since this run was initialized; use a new run directory")
            if data.get("unit_id") != self.spec["unit_id"] or data.get("workflow_id") != self.spec["workflow_id"]:
                raise WorkflowError("workflow identity does not match existing run")
            original_provider = data.get("provider", {})
            if original_provider.get("id") != self.provider_id or original_provider.get("model") != self.model or original_provider.get("endpoint") != self.provider_endpoint:
                raise WorkflowError("provider, model, or endpoint changed since this run was initialized")
            task_states = {row.get("id"): row for row in data.get("tasks", [])}
            for task in self.spec["tasks"]:
                row = task_states.get(task["id"])
                if row is None:
                    raise WorkflowError("workflow state is missing a task record")
                if row.get("status") == "complete":
                    output_path = (self.run_dir / row["output_path"]).resolve()
                    if not output_path.is_relative_to(self.run_dir) or not output_path.is_file() or sha256_file(output_path) != row.get("output_sha256"):
                        raise WorkflowError(f"completed task artifact is missing or changed: {task['id']}")
            if data.get("budget", {}).get("max_attempts") != self.spec.get("budget", {}).get("max_attempts", MAX_UNIT_ATTEMPTS) or data.get("budget", {}).get("max_tokens") != self.spec.get("budget", {}).get("max_tokens", MAX_UNIT_TOKENS):
                raise WorkflowError("workflow budget differs from the original run")
            self.run_dir.mkdir(parents=True, exist_ok=True)
            return data
        if self.run_dir.exists() and any(self.run_dir.iterdir()):
            raise WorkflowError("run directory is not empty and has no workflow state")
        self.run_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "schema_version": "arcagi-workflow-run/v1",
            "workflow_id": self.spec["workflow_id"],
            "unit_id": self.spec["unit_id"],
            "game": self.spec["game"],
            "spec_sha256": self.spec_hash,
            "provider": {"id": self.provider_id, "model": self.model, "endpoint": self.provider_endpoint},
            "budget": {
                "max_attempts": self.spec.get("budget", {}).get("max_attempts", MAX_UNIT_ATTEMPTS),
                "max_tokens": self.spec.get("budget", {}).get("max_tokens", MAX_UNIT_TOKENS),
                "attempts_used": 0,
                "tokens_used": 0,
                "tokens_reserved": 0,
            },
            "status": "pending",
            "tasks": [{"id": task["id"], "stage": task["stage"], "status": "pending"} for task in self.spec["tasks"]],
            "attempt_log": [],
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
        }
        _atomic_json(self.state_path, data)
        return data

    def _save(self) -> None:
        self.data["updated_at"] = _utc_now()
        _atomic_json(self.state_path, self.data)

    def plan(self) -> dict[str, Any]:
        tasks = []
        for task in self._topological_tasks():
            prompt_path = self._resolve(task["prompt"])
            prompt_hash = sha256_file(prompt_path)
            inputs = []
            for item in task.get("inputs", []):
                if "path" in item:
                    path = self._resolve(item["path"])
                    inputs.append({"id": item["id"], "role": item["role"], "sha256": sha256_file(path), "source": item["path"]})
                else:
                    inputs.append({"id": item["id"], "role": item["role"], "from_task": item["from_task"]})
            if task.get("frozen_output"):
                inputs.append({"id":"$frozen_output","role":"frozen_output","sha256":sha256_file(self._resolve(task["frozen_output"]["path"])),"source":task["frozen_output"]["path"]})
            tasks.append({
                "execution_mode":"frozen_import" if task.get("frozen_output") else "model_request",
                "id": task["id"], "stage": task["stage"], "evidence_lane": task.get("evidence_lane", "general"),
                "depends_on": task.get("depends_on", []), "prompt_sha256": prompt_hash, "inputs": inputs,
            })
        return {
            "valid": True,
            "workflow_id": self.spec["workflow_id"],
            "unit_id": self.spec["unit_id"],
            "game": self.spec["game"],
            "provider": {"id": self.provider_id, "model": self.model or "from-env:DEEPSEEK_MODEL"},
            "budget": {
                "max_attempts": self.spec.get("budget", {}).get("max_attempts", MAX_UNIT_ATTEMPTS),
                "max_tokens": self.spec.get("budget", {}).get("max_tokens", MAX_UNIT_TOKENS),
            },
            "spec_sha256": self.spec_hash,
            "tasks": tasks,
            "execution_limits":{k:self.spec.get("budget",{}).get(k) for k in ("max_input_tokens_per_request","max_output_tokens_per_request","max_elapsed_seconds","aggregate_ledger","allow_rework_reserve")},
            "execution_started": False,
        }

    @traced("workflow_run")
    def run(self, *, retry_failed: bool = False) -> dict[str, Any]:
        with file_lock(self.run_dir/".workflow.lock"):
            self.data=self._load_or_create_state()
            self._recover_aggregate_orphans()
            return self._run_locked(retry_failed=retry_failed)

    def _run_locked(self, *, retry_failed=False):
        self._recover_interrupted_attempts()
        task_state = {row["id"]: row for row in self.data["tasks"]}
        for task in self._topological_tasks():
            state = task_state[task["id"]]
            if state["status"] == "complete":
                artifact = (self.run_dir / state.get("output_path", "")).resolve()
                if not artifact.is_relative_to(self.run_dir) or not artifact.is_file() or sha256_file(artifact) != state.get("output_sha256"):
                    raise WorkflowError("completed output is missing or changed",code="artifact_changed")
                fingerprint = self._fingerprint(task, task_state)
                if state.get("fingerprint") != fingerprint:
                    raise WorkflowError(f"completed task inputs or prompt changed: {task['id']}")
        self.data["status"] = "running"
        self._save()
        try:
            for task in self._topological_tasks():
                if self.pause_check: self.pause_check()
                state = task_state[task["id"]]
                try:
                    if state["status"] == "complete":
                        self._check_task_gates(task,state)
                        continue
                    if state["status"] in {"failed", "interrupted"} and not retry_failed:
                        from .diagnostics import Archive
                        attempt=next((a for a in reversed(self.data["attempt_log"]) if a["task_id"]==task["id"]),{})
                        ref=attempt.get("diagnostic_ref")
                        fact=Archive(ref["archive"]).read("events",ref["event_id"]) if ref else {}
                        failure=WorkflowError("task previously failed; pass --retry-failed for an explicit retry or use a new revision",code=fact.get("code"))
                        failure.diagnostic_ref=ref
                        if fact.get("observed",{}).get("protocol"):failure.protocol_facts=fact["observed"]["protocol"]
                        raise failure
                    for dep in task.get("depends_on", []):
                        if task_state[dep]["status"] != "complete":
                            raise WorkflowError(f"task {task['id']} is blocked by incomplete dependency {dep}")
                    self._execute_task(task, state, task_state)
                    self._check_task_gates(task,state)
                except Exception as original:
                    from .diagnostics import exception_facts,current,ArchiveWriteError
                    from .revision_protocol import PauseRequested
                    if isinstance(original,(PauseRequested,ArchiveWriteError)):raise
                    state['gate_status']='return'
                    state['gate_error']=exception_facts(original)
                    self.data['active_failure']={'task_id':task['id'],'stage':task['stage'],'artifact_sha256':state.get('output_sha256')}
                    self._save()
                    failure=original if isinstance(original,WorkflowError) else WorkflowError('task stage check failed',code=getattr(original,'diagnostic_code',None) or 'output_protocol_invalid')
                    failure.task_id=task['id'];failure.failed_stage=task['stage']
                    if not getattr(failure,'revision_targets',None) and not getattr(failure,'diagnosis_repair',None):failure.revision_targets=[task['id']]
                    if not getattr(failure,'diagnostic_ref',None) and current() is not None:
                        failure.diagnostic_ref=current().record('output_protocol_invalid','task_gate',task_id=task['id'],exc=original,observed=self.data['active_failure'])
                    raise failure
        except WorkflowError:
            if self.data.get("status") == "running":
                self.data["status"] = "blocked"
                self._save()
            raise
        self.data["status"] = "complete"
        self._save()
        return self.summary()

    def _check_task_gates(self,task,state):
        self._diagnosis_gate(task,state)
        if task.get('gate_review'):self._review_gate(task,state)
        state['gate_status']='pass';state['gate_artifact_sha256']=state.get('output_sha256')
        self.data.pop('active_failure',None);self._save()

    def _diagnosis_gate(self,task,state):
        if task.get('validation_program') in ('arcagi-validation-program/v1','arcagi-validation-program/v2') and task.get('stage') in ('blueprint','course_design','design'):
            from .verification_program import compile_obligations,commitment_changes
            blueprint=json.loads((self.run_dir/state['output_path']).read_text(encoding='utf-8'))
            issues=compile_obligations(blueprint,strict=True)
            roles=task.get('validation_inputs')
            if roles:
                from .generated_game import target_issues
                records={r['id']:r for r in self.data['tasks']}
                artifacts={role:json.loads((self.run_dir/records[tid]['output_path']).read_text(encoding='utf-8')) for role,tid in roles.items()}
                issues=target_issues(artifacts['evidence'],artifacts['rules'],blueprint,strict=True)
            for item in task.get('inputs',[]) if not issues else []:
                if item.get('role')=='commitment_baseline':
                    previous=json.loads(self._resolve(item['path']).read_text(encoding='utf-8'))
                    issues.extend(x for x in commitment_changes(previous,blueprint) if x.get('unacknowledged'))
            if issues:
                failure=WorkflowError('validation plan cannot be executed as declared',code='output_protocol_invalid')
                targets=set()
                for issue in issues:
                    role=issue.get("artifact","blueprint") if isinstance(issue,dict) else "blueprint"
                    targets.add(roles.get(role,task["id"]) if roles and role!="blueprint" else task["id"])
                failure.revision_targets=sorted(targets)
                failure.protocol_facts={'check':'validation_capabilities' ,'issues':issues}
                raise failure
        contract=task.get('diagnosis_contract')
        if not contract:return
        from .diagnosis_protocol import effective_bundle,feedback_for,validate,describe
        artifact=self.run_dir/state['output_path']
        value=effective_bundle(artifact,contract,self.diagnosis_receipts.get(task['id']))
        feedback=feedback_for(contract);issues=validate(value,feedback,contract.get('allow_stage_revision',True))
        if self.diagnosis_observer:self.diagnosis_observer(task['id'],{'path':str(artifact.resolve()),'sha256':sha256_file(artifact)},issues)
        if issues:
            failure=WorkflowError('diagnosis protocol correction required',code='output_protocol_invalid')
            failure.diagnosis_repair={'task_id':task['id'],'artifact_ref':{'path':str(artifact.resolve()),'sha256':sha256_file(artifact)},'contract':contract}
            failure.protocol_facts={'check':'diagnosis_protocol','issues':issues,'contract':describe(contract)}
            raise failure
        proposed=value['diagnosis'].get('revise_task_id')
        if proposed==task['id']:
            # The complete replacement artifact is already present. Validate it
            # through downstream gates; a self-address is not a new failure.
            receipt={'task_id':task['id'],'artifact_sha256':sha256_file(artifact),
                     'target':proposed,'reason':'replacement_artifact_already_produced'}
            handled=self.data.setdefault('applied_stage_revision_requests',[])
            if receipt not in handled:handled.append(receipt);self._save()
            return
        if proposed:
            target=next((t for t in self.spec['tasks'] if t['id']==proposed),None)
            if not target or target.get('evidence_lane')=='trajectory_blind':
                failure=WorkflowError('invalid diagnosis revision target',code='output_protocol_invalid')
                failure.diagnosis_repair={'task_id':task['id'],'artifact_ref':{'path':str(artifact.resolve()),'sha256':sha256_file(artifact)},'contract':contract}
                failure.protocol_facts={'check':'diagnosis_protocol','issues':[{'path':'diagnosis.revise_task_id','code':'invalid_revision_target'}],'contract':describe(contract)}
                raise failure
            # A frozen upstream request has already been dispatched when its
            # target is active in this revision. Validate the diagnosis above,
            # then allow that scheduled target to execute instead of dispatching
            # the identical request forever. Frozen targets still require a new
            # revision; newly generated requests are never silently consumed.
            if task.get('frozen_output') and not target.get('frozen_output'):
                receipt={'task_id':task['id'],'artifact_sha256':sha256_file(artifact),'target':proposed,
                         'reason':'frozen_request_target_scheduled'}
                handled=self.data.setdefault('applied_stage_revision_requests',[])
                if receipt not in handled:
                    handled.append(receipt);self._save()
                return
            failure=WorkflowError('model requests stage revision',code='model_revision_requested');failure.revision_targets=[proposed];raise failure

    def _response_schema(self,task):
        from .revision_protocol import review_schema
        from .diagnosis_protocol import output_schema
        value={**task,'output_schema':review_schema(task,self.spec['tasks']) if task.get('gate_review') else task['output_schema']}
        return output_schema(value)

    def _review_gate(self, task, state):
        from .revision_protocol import review_targets, ReviewProtocolError
        review = json.loads((self.run_dir/state["output_path"]).read_text(encoding="utf-8"))
        try: targets = review_targets(review, task, self.spec["tasks"])
        except ReviewProtocolError as violation:
            exc = WorkflowError("review protocol invalid", code="output_protocol_invalid")
            exc.revision_targets = [task["id"]]
            exc.protocol_facts = violation.facts
            raise exc from None
        if task.get('review_contract')=='phase-review/v1':
            producer=task.get('obligation_source_task');records={r['id']:r for r in self.data['tasks']}
            if producer not in records:raise WorkflowError('phase review requires declared obligation source',code='config_invalid')
            blueprint=json.loads((self.run_dir/records[producer]['output_path']).read_text(encoding='utf-8'))
            known={(s['id'],r['id']) for s in blueprint.get('slots',[]) for r in s.get('behavior_requirements',[])+[o for o in s.get('validation_obligations',[]) if o.get('required') and o.get('phase')!='research']}
            pending=[]
            for finding in review['findings']:
                if finding['category'] in {'implementation_obligation','runtime_obligation'}:
                    ref=finding['obligation_ref']
                    if (ref['slot_id'],ref['id']) not in known:
                        error=WorkflowError('deferred finding is not a declared blueprint obligation',code='output_protocol_invalid');error.revision_targets=[task['id']];raise error
                    pending.append(ref)
            self.data.setdefault('deferred_obligations',{})[task['id']]=pending;self._save()
        if review["verdict"] != "pass":
            scheduled={t['id'] for t in self.spec['tasks'] if not t.get('frozen_output')}
            if task.get('frozen_output') and targets and set(targets)<=scheduled:
                receipt={'task_id':task['id'],'artifact_sha256':sha256_file(self.run_dir/state['output_path']),
                         'targets':targets,'reason':'frozen_review_targets_scheduled'}
                handled=self.data.setdefault('applied_stage_revision_requests',[])
                if receipt not in handled:handled.append(receipt);self._save()
                return
            exc = WorkflowError("model self-review requests revision", code="model_review_return")
            exc.revision_targets = targets
            exc.review_task_id = task["id"]
            raise exc

    def _recover_aggregate_orphans(self):
        if self.aggregate is None:return
        rows=self.aggregate.snapshot().get("runtime_requests",{})
        known={a.get("request_id") for a in self.data["attempt_log"]}
        for rid,entry in rows.items():
            meta=entry["metadata"]
            if meta.get("workflow_state")!=str(self.state_path) or rid in known:continue
            settled=self.aggregate.settle(rid,status="interrupted") if entry["status"]=="reserved" else entry
            number=meta["attempt"]
            self.data["budget"]["attempts_used"]=max(self.data["budget"]["attempts_used"],number)
            self.data["budget"]["tokens_used"]+=settled["charged_tokens"]
            self.data["attempt_log"].append({"request_id":rid,"task_id":meta["task_id"],"attempt":number,"status":"interrupted","reserved_tokens":entry["reserved_tokens"],"charged_tokens":settled["charged_tokens"],"error_code":"orphan_request_reconciled"})
            task=next(t for t in self.data["tasks"] if t["id"]==meta["task_id"])
            task.update(status="interrupted",fingerprint=meta["fingerprint"])
        self._save()

    def _settle_aggregate(self,attempt,*,reported=None,prompt=None,completion=None,status="complete"):
        if self.aggregate is None or not attempt.get("request_id"):return None
        return self.aggregate.settle(attempt["request_id"],reported_total=reported,prompt_tokens=prompt,completion_tokens=completion,status=status)

    def _recover_interrupted_attempts(self) -> None:
        budget = self.data["budget"]
        task_state = {row["id"]: row for row in self.data.get("tasks", [])}
        for attempt in self.data.get("attempt_log", []):
            if attempt.get("status") != "output_staged":
                continue
            task_id = attempt.get("task_id")
            if task_id not in task_state:
                raise WorkflowError("staged workflow output references an unknown task")
            output_rel = Path("artifacts") / f"{task_id}.json"
            output_path = (self.run_dir / output_rel).resolve()
            staged_raw = attempt.get("staged_output_path")
            if not isinstance(staged_raw, str):
                raise WorkflowError("staged workflow output has no recorded path")
            staged_path = (self.run_dir / staged_raw).resolve()
            if not staged_path.is_relative_to(self.run_dir) or not output_path.is_relative_to(self.run_dir):
                raise WorkflowError("staged workflow output path escapes its run directory")
            expected_hash = attempt.get("staged_output_sha256")
            staged_ok = staged_path.is_file() and sha256_file(staged_path) == expected_hash
            output_ok = output_path.is_file() and sha256_file(output_path) == expected_hash
            if staged_ok and not output_ok:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged_path, output_path)
                output_ok = output_path.is_file() and sha256_file(output_path) == expected_hash
            elif staged_ok and output_ok:
                staged_path.unlink()
            if not output_ok:
                raise WorkflowError(f"staged model output cannot be recovered for task {task_id}")
            self._finalize_staged_output(
                task_state[task_id], attempt, output_path, output_rel,
                expected_hash, attempt.get("charged_tokens", attempt.get("reserved_tokens", 0)),
            )
        for attempt in self.data.get("attempt_log", []):
            if attempt.get("status") == "running":
                reserved = int(attempt.get("reserved_tokens", 0))
                budget["tokens_reserved"] = max(0, int(budget["tokens_reserved"]) - reserved)
                aggregate=self._settle_aggregate(attempt,status="interrupted")
                charged=aggregate["charged_tokens"] if aggregate else reserved
                budget["tokens_used"] += charged
                attempt["status"] = "interrupted"
                attempt["charged_tokens"] = charged
                attempt["error_code"] = "process_interrupted"
                task_id = attempt.get("task_id")
                attempt_number = attempt.get("attempt")
                if isinstance(task_id, str) and isinstance(attempt_number, int):
                    orphan = self.run_dir / "artifacts" / f".{task_id}.{attempt_number}.staged.json"
                    if orphan.is_file():
                        orphan.unlink()
        for row in self.data.get("tasks", []):
            if row.get("status") == "running":
                row["status"] = "interrupted"
        self._save()

    def _finalize_staged_output(
        self, state: dict[str, Any], attempt: dict[str, Any], output_path: Path,
        output_rel: Path, output_hash: str, charged_tokens: int,
    ) -> None:
        self._settle_aggregate(attempt,reported=attempt.get("reported_tokens"),prompt=attempt.get("prompt_tokens"),completion=attempt.get("completion_tokens"))
        reservation = int(attempt.get("reserved_tokens", 0))
        budget = self.data["budget"]
        budget["tokens_reserved"] = max(0, int(budget["tokens_reserved"]) - reservation)
        budget["tokens_used"] += int(charged_tokens)
        attempt.update({"status": "complete", "charged_tokens": int(charged_tokens), "finished_at": _utc_now()})
        state.update({
            "status": "complete", "output_path": output_rel.as_posix(),
            "output_sha256": output_hash, "completed_at": _utc_now(),
        })
        self.data["status"] = "running"
        self._save()

    def _input_payload(self, task: dict[str, Any], task_state: Mapping[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, str]]:
        payload = []
        hashes: dict[str, str] = {}
        for item in task.get("inputs", []):
            if "path" in item:
                path = self._resolve(item["path"])
                raw = path.read_bytes()
                content = raw.decode("utf-8")
                digest = sha256_bytes(raw)
                if task.get("evidence_lane", "general") == "trajectory_blind":
                    if item.get("role") in {"trajectory_review_feedback","prior_trajectory_claims"}:
                        _validate_blind_revision_input(item,task["id"])
                    else:
                        try:packet = json.loads(content)
                        except json.JSONDecodeError:raise WorkflowError("trajectory_blind input is not a valid pipeline packet") from None
                        _validate_trajectory_packet(path, packet if isinstance(packet, dict) else None)
            else:
                parent = task_state[item["from_task"]]
                path = (self.run_dir / parent["output_path"]).resolve()
                if not path.is_relative_to(self.run_dir) or parent.get("status") != "complete":
                    raise WorkflowError("dependency artifact is not a completed in-run output",code="artifact_changed")
                raw = path.read_bytes()
                content = raw.decode("utf-8")
                digest = sha256_bytes(raw)
                if digest != parent.get("output_sha256"):
                    raise WorkflowError("dependency artifact hash differs from its recorded output",code="artifact_changed")
            expected_hash = item.get("sha256")
            if expected_hash is not None and expected_hash != digest:
                raise WorkflowError(f"input hash mismatch for {item['id']}")
            if item.get("content_encoding")=="json_compact":
                try:content=json.dumps(json.loads(content),ensure_ascii=False,separators=(",",":"),allow_nan=False)
                except (ValueError,TypeError):raise WorkflowError("compact JSON input is invalid",code="config_invalid") from None
            payload.append({"id": item["id"], "role": item["role"], "sha256": digest, "rendered_sha256":sha256_bytes(content.encode("utf-8")), "content": content})
            hashes[item["id"]] = digest
        if task.get("frozen_output"):
            frozen = task["frozen_output"]
            digest = sha256_file(self._resolve(frozen["path"]))
            if digest != frozen["sha256"]:
                raise WorkflowError("frozen_output changed",code="artifact_changed")
            hashes["$frozen_output"] = digest
        return payload, hashes

    def _import_frozen(self, task, state, task_state):
        raw = self._resolve(task["frozen_output"]["path"]).read_bytes()
        digest = sha256_bytes(raw)
        if digest != task["frozen_output"]["sha256"]:
            raise WorkflowError("frozen_output changed",code="artifact_changed")
        try: value = json.loads(raw.decode("utf-8"))
        except (ValueError,UnicodeDecodeError):
            raise WorkflowError("frozen output is not JSON",code="output_protocol_invalid") from None
        from .revision_protocol import review_schema
        from .diagnosis_protocol import payload_schema,payload_value
        schema=review_schema(task,self.spec["tasks"]) if task.get("gate_review") and isinstance(value,dict) and "revision_targets" in value else task["output_schema"]
        if _schema_errors(payload_value(value,task),payload_schema({**task,'output_schema':schema})) or _private_reasoning_paths(value):
            raise WorkflowError("frozen output violates output protocol",code="output_protocol_invalid")
        _, hashes = self._input_payload(task,task_state)
        prompt_hash = sha256_file(self._resolve(task["prompt"]))
        fingerprint = self._fingerprint(task,task_state,prompt_hash=prompt_hash,input_hashes=hashes)
        if state.get("fingerprint") and state["fingerprint"] != fingerprint:
            raise WorkflowError("frozen import input changed",code="artifact_changed")
        rel = Path("artifacts") / (task["id"]+".json")
        path = (self.run_dir/rel).resolve()
        if not path.is_relative_to(self.run_dir): raise WorkflowError("invalid import destination",code="config_invalid")
        # Record intent first; after a crash only the identical pinned output is reused.
        recovering = state.get("execution_mode") == "frozen_import" and state.get("fingerprint") == fingerprint
        if path.exists() and (not recovering or sha256_file(path) != digest):
            raise WorkflowError("frozen import destination already exists or changed",code="artifact_changed")
        state.update(status="pending",execution_mode="frozen_import",fingerprint=fingerprint,prompt_sha256=prompt_hash,input_hashes=hashes)
        self._save()
        path.parent.mkdir(parents=True,exist_ok=True)
        if not path.exists():
            with path.open("xb") as stream: stream.write(raw);stream.flush();os.fsync(stream.fileno())
        state.update(status="complete",output_path=rel.as_posix(),output_sha256=digest,completed_at=_utc_now(),completion_scope="schema_validated_frozen_import",frozen_source=task["frozen_output"])
        self._save()

    def _fingerprint(
        self, task: dict[str, Any], task_state: Mapping[str, dict[str, Any]], *,
        prompt_hash: str | None = None, input_hashes: dict[str, str] | None = None,
    ) -> str:
        if prompt_hash is None:
            prompt_hash = sha256_bytes(self._resolve(task["prompt"]).read_bytes())
        if input_hashes is None:
            _, input_hashes = self._input_payload(task, task_state)
        dependency_hashes = {dep: task_state[dep].get("output_sha256", "") for dep in task.get("depends_on", [])}
        return sha256_bytes(json.dumps({
            "task": task, "prompt_sha256": prompt_hash, "input_hashes": input_hashes,
            "dependency_hashes": dependency_hashes,
        }, ensure_ascii=False, sort_keys=True).encode("utf-8"))

    @traced("model_request")
    def _execute_task(self, task: dict[str, Any], state: dict[str, Any], task_state: Mapping[str, dict[str, Any]]) -> None:
        if self.deadline is not None and time.time() >= self.deadline:raise WorkflowError("controller time limit",code="budget_limit")
        from .execution_contract import validate_execution_inputs
        validate_execution_inputs(task, self.spec_path.parent)
        if task.get("frozen_output"):
            return self._import_frozen(task,state,task_state)
        budget = self.data["budget"]
        if budget["attempts_used"] >= budget["max_attempts"]:
            raise WorkflowError("workflow attempt budget is exhausted",code="budget_limit")
        prompt_path = self._resolve(task["prompt"])
        prompt_bytes = prompt_path.read_bytes()
        prompt = prompt_bytes.decode("utf-8")
        prompt_hash = sha256_bytes(prompt_bytes)
        inputs, input_hashes = self._input_payload(task, task_state)
        fingerprint = self._fingerprint(task, task_state, prompt_hash=prompt_hash, input_hashes=input_hashes)
        if state.get("fingerprint") and state["fingerprint"] != fingerprint:
            raise WorkflowError(f"task inputs or prompt changed since its first attempt: {task['id']}")
        state["fingerprint"] = fingerprint
        state["prompt_sha256"] = prompt_hash
        state["input_hashes"] = input_hashes
        from .revision_protocol import review_schema, review_scope, review_instructions
        request = {
            "task": {"id": task["id"], "stage": task["stage"], "evidence_lane": task.get("evidence_lane", "general")},
            "input_artifacts": inputs,
            "required_output_schema": self._response_schema(task),
            "response_instruction": "Return exactly one JSON value matching required_output_schema. Do not use Markdown fences.",
        }
        if task.get("gate_review"):
            request["review_protocol"] = {"allowed_targets":review_scope(task,self.spec["tasks"]),"instruction":review_instructions(task)}
        if task.get('diagnosis_contract'):
            from .diagnosis_protocol import describe
            contract_context=describe(task['diagnosis_contract'])
            if any(item['role']=='execution_evidence_fragments' for item in inputs):
                for key in ('checks','evidence_refs','revision_targets'):
                    contract_context.pop(key,None)
                contract_context['feedback_context']='Read check IDs, evidence refs and revision targets in control_feedback.'
            request['diagnosis_contract']=contract_context
        user_content = json.dumps(request, ensure_ascii=False, separators=(",", ":"))
        system_content = (
            "Return concise, evidence-linked conclusions. Do not expose or save private chain-of-thought; "
            "do not include reasoning_content, chain_of_thought, cot, or thoughts fields.\n\n" + prompt
        )
        if task.get("gate_review"):
            system_content += "\n"+review_instructions(task)+"\n"
        messages = [{"role": "system", "content": system_content}, {"role": "user", "content": user_content}]
        # UTF-8 byte count is a conservative upper bound on tokenizer pieces; framing allowance covers chat overhead.
        input_ceiling = sum(len(message["content"].encode("utf-8")) for message in messages) + 256
        remaining = budget["max_tokens"] - budget["tokens_used"] - budget["tokens_reserved"]
        limits=self.spec.get("budget",{})
        ceiling=limits.get("max_input_tokens_per_request",limits.get("max_tokens",MAX_UNIT_TOKENS))
        if self.aggregate is not None:
            ceiling=min(ceiling,self.aggregate.snapshot()["ceilings"]["input_tokens_per_request"])
        if input_ceiling>ceiling:
            from .handoff_control import compact_feedback_fragment
            for item in inputs:
                if item['role']=='execution_evidence_fragments':
                    item['content']=json.dumps(compact_feedback_fragment(json.loads(item['content'])),ensure_ascii=False,separators=(",",":"))
                    item['rendered_sha256']=sha256_bytes(item['content'].encode("utf-8"))
            messages[1]['content']=json.dumps(request,ensure_ascii=False,separators=(",",":"))
            input_ceiling=sum(len(message['content'].encode('utf-8')) for message in messages)+256
        if input_ceiling>ceiling:
            raise WorkflowError(f"minimum request needs {input_ceiling} UTF-8 bytes, configured ceiling is {ceiling}",code="budget_limit")
        if input_ceiling >= remaining:
            raise WorkflowError("workflow token budget cannot cover the next request",code="budget_limit")
        output_cap = min(int(task.get("max_output_tokens", 4096)), remaining - input_ceiling,limits.get("max_output_tokens_per_request",limits.get("max_tokens",MAX_UNIT_TOKENS)))
        if output_cap < 1:
            raise WorkflowError("workflow token budget cannot reserve a completion token",code="budget_limit")
        output_rel = Path("artifacts") / f"{task['id']}.json"
        output_path = (self.run_dir / output_rel).resolve()
        if not output_path.is_relative_to(self.run_dir) or output_path.exists():
            raise WorkflowError("task output path already exists or escapes run directory")
        next_attempt_number = budget["attempts_used"] + 1
        staged_rel = Path("artifacts") / f".{task['id']}.{next_attempt_number}.staged.json"
        staged_path = (self.run_dir / staged_rel).resolve()
        if staged_path.exists():
            raise WorkflowError("staged task output already exists; inspect the run before retrying")
        reservation = input_ceiling + output_cap
        started=self.data.get("execution_started_at")
        if started and (datetime.now(timezone.utc)-datetime.fromisoformat(started)).total_seconds()>limits.get("max_elapsed_seconds",86400):raise WorkflowError("workflow elapsed limit",code="budget_limit")
        if self.deadline is not None and time.time()>=self.deadline:raise WorkflowError("controller time limit",code="budget_limit")
        if self.pause_check: self.pause_check()
        request_id=uuid.uuid4().hex
        if self.aggregate is not None:
            self.aggregate.reserve(request_id,reservation=reservation,input_ceiling=input_ceiling,output_cap=output_cap,rework=limits.get("allow_rework_reserve",False),metadata={"workflow_state":str(self.state_path),"workflow_id":self.spec["workflow_id"],"task_id":task["id"],"attempt":next_attempt_number,"fingerprint":fingerprint})
        self.data.setdefault("execution_started_at",_utc_now())
        budget["attempts_used"] += 1
        budget["tokens_reserved"] += reservation
        attempt = {
            "task_id": task["id"], "attempt": budget["attempts_used"], "status": "running", "request_id":request_id,
            "reserved_tokens": reservation, "reported_tokens": None, "charged_tokens": None,
            "prompt_sha256": prompt_hash, "input_hashes": input_hashes,
            "started_at": _utc_now(),
        }
        self.data["attempt_log"].append(attempt)
        state["status"] = "running"
        self.data["status"] = "running"
        self._save()
        try:
            if hasattr(self.provider,"timeout"):
                remaining_seconds=limits.get("max_elapsed_seconds",86400)-(datetime.now(timezone.utc)-datetime.fromisoformat(self.data["execution_started_at"])).total_seconds()
                if self.deadline is not None:remaining_seconds=min(remaining_seconds,self.deadline-time.time())
                self.provider.timeout=min(self.spec.get("provider",{}).get("timeout_seconds",120),max(.001,remaining_seconds))
            completion = self.provider.complete(messages, model=str(task.get("model", self.model)), max_tokens=output_cap)
            attempt.update(response_model=completion.response_model,finish_reason=completion.finish_reason,content_length=len(completion.content),content_sha256=sha256_bytes(completion.content.encode("utf-8")))
            charged = completion.total_tokens if completion.total_tokens is not None else reservation
            if charged > reservation:
                raise WorkflowError("provider usage exceeded the reserved request budget",code="budget_limit")
            if completion.finish_reason=="length":
                failure=WorkflowError("model output truncated",code="output_protocol_invalid")
                failure.protocol_facts={"check":"completion","reason":"output_truncated","output_cap":output_cap}
                raise failure
            if not completion.content.strip():
                failure=WorkflowError("model returned empty output",code="output_protocol_invalid")
                failure.protocol_facts={"check":"completion","reason":"empty_output"}
                raise failure
            parsed = json.loads(completion.content)
            private_fields = _private_reasoning_paths(parsed)
            if private_fields:
                raise WorkflowError("provider output contains a private-reasoning field",code="output_protocol_invalid")
            for input_id in task.get("preserve_evidence_inputs",[]):
                source_input=next((item for item in inputs if item["id"]==input_id),None)
                if source_input is None:raise WorkflowError("preserved evidence input is missing",code="config_invalid")
                originals=json.loads(source_input["content"]).get("evidence",[])
                actual=parsed.get("evidence",[]) if isinstance(parsed,dict) else []
                if any(item not in actual for item in originals):raise WorkflowError("evidence union modified or omitted original records",code="output_protocol_invalid")
            schema=review_schema(task,self.spec["tasks"]) if task.get("gate_review") and isinstance(parsed,dict) and "revision_targets" in parsed else task["output_schema"]
            from .diagnosis_protocol import payload_schema,payload_value
            errors = _schema_errors(payload_value(parsed,task), payload_schema({**task,'output_schema':schema}))
            if errors:
                failure = WorkflowError("provider output failed schema validation",code="output_protocol_invalid")
                failure.protocol_facts = {"check":"output_schema","errors":errors[:8]}
                raise failure
            _atomic_json(staged_path, parsed)
            output_hash = sha256_file(staged_path)
            attempt.update({
                "status": "output_staged", "reported_tokens": completion.total_tokens,
                "finish_reason":completion.finish_reason,"response_model":completion.response_model,
                "prompt_tokens": completion.prompt_tokens, "completion_tokens": completion.completion_tokens,
                "charged_tokens": charged, "staged_output_path": staged_rel.as_posix(),
                "staged_output_sha256": output_hash, "finished_at": _utc_now(),
            })
            self._save()
            os.replace(staged_path, output_path)
            if sha256_file(output_path) != output_hash:
                raise WorkflowError("committed task output hash differs from the staged artifact")
            self._finalize_staged_output(state, attempt, output_path, output_rel, output_hash, charged)
        except ArchiveWriteError: raise
        except Exception as exc:
            if isinstance(exc,json.JSONDecodeError):
                exc.protocol_facts={"check":"json_syntax","line":exc.lineno,"column":exc.colno,"reason":"invalid_json"}
            code=getattr(exc,"diagnostic_code",None) or "exception"
            if isinstance(exc,json.JSONDecodeError) and "completion" in locals(): code="output_protocol_invalid"
            ref=current().record(code,"model_request",task_id=task["id"],exc=exc,
                observed={"attempt":attempt["attempt"],"request_id":request_id,"input_hashes":input_hashes,"prompt_sha256":prompt_hash,"provider":getattr(exc,"diagnostic_observed",{}),"protocol":getattr(exc,"protocol_facts",None),"reported_total_tokens":completion.total_tokens if "completion" in locals() else None})
            exc.diagnostic_ref=ref
            attempt["diagnostic_ref"]=ref
            if attempt.get("status") == "running":
                budget["tokens_reserved"] = max(0, budget["tokens_reserved"] - reservation)
                has_completion = "completion" in locals() and isinstance(completion, Completion)
                reported = completion.total_tokens if has_completion else None
                charged = reported if reported is not None else reservation
                self._settle_aggregate(attempt,reported=reported,prompt=completion.prompt_tokens if has_completion else None,completion=completion.completion_tokens if has_completion else None,status="failed")
                budget["tokens_used"] += charged
                attempt.update({
                    "status": "failed", "reported_tokens": reported,
                    "charged_tokens": charged, "error_code": type(exc).__name__, "finished_at": _utc_now(),
                    "finish_reason":completion.finish_reason if has_completion else None,"response_model":completion.response_model if has_completion else None,
                })
                state["status"] = "failed"
                self.data["status"] = "budget_violation" if reported is not None and reported > reservation else "failed"
                self._save()
            if isinstance(exc, WorkflowError):
                raise
            wrapped=WorkflowError(f"task {task['id']} failed ({type(exc).__name__})",code=code)
            wrapped.diagnostic_ref=ref
            if getattr(exc,"protocol_facts",None):wrapped.protocol_facts=exc.protocol_facts
            raise wrapped from None

    def summary(self) -> dict[str, Any]:
        return {
            "completion_scope": "schema_validated_workflow_outputs" if any(t.get("frozen_output") for t in self.spec["tasks"]) else "schema_validated_model_outputs",
            "diagnostics": self._diagnostic_context.serial(),
            "workflow_id": self.data["workflow_id"], "unit_id": self.data["unit_id"],
            "status": self.data["status"], "run_dir": str(self.run_dir),
            "attempts_used": self.data["budget"]["attempts_used"], "tokens_used": self.data["budget"]["tokens_used"],
            "tasks": [{"id": row["id"], "stage": row["stage"], "status": row["status"], "execution_mode":row.get("execution_mode","model_request"), "output_path": row.get("output_path"), "output_sha256": row.get("output_sha256")} for row in self.data["tasks"]],
            "execution_started": any(row["status"] != "pending" for row in self.data["tasks"]),
        }


def workflow_status(run_dir: Path) -> dict[str, Any]:
    root = run_dir.resolve()
    record_path = root / "workflow_run.json"
    try:
        data = json.loads(record_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise WorkflowError("workflow run record was not found") from None
    except (OSError, json.JSONDecodeError):
        raise WorkflowError("workflow run record is unreadable") from None
    if data.get("schema_version") != "arcagi-workflow-run/v1":
        raise WorkflowError("workflow run record has an unsupported schema")
    task_rows = []
    for row in data.get("tasks", []):
        item = {key: row.get(key) for key in ("id", "stage", "status", "output_path", "output_sha256") if key in row}
        if row.get("output_path"):
            artifact = (root / row["output_path"]).resolve()
            if not artifact.is_relative_to(root) or not artifact.is_file() or sha256_file(artifact) != row.get("output_sha256"):
                raise WorkflowError(f"task artifact is missing or changed: {row.get('id', 'unknown')}")
        task_rows.append(item)
    budget = data.get("budget", {})
    attempts = data.get("attempt_log", [])
    return {
        "workflow_id": data.get("workflow_id"), "unit_id": data.get("unit_id"), "status": data.get("status"),
        "provider": data.get("provider"), "budget": {
            "max_attempts": budget.get("max_attempts"), "attempts_used": budget.get("attempts_used"),
            "max_tokens": budget.get("max_tokens"), "tokens_used": budget.get("tokens_used"),
            "tokens_reserved": budget.get("tokens_reserved"),
        },
        "tasks": task_rows,
        "attempts": [{key: row.get(key) for key in ("task_id", "attempt", "status", "reported_tokens", "charged_tokens", "error_code", "started_at", "finished_at") if key in row} for row in attempts],
        "record_sha256": sha256_file(record_path),
    }
