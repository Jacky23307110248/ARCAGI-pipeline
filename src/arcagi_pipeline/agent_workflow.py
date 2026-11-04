from __future__ import annotations

import hashlib
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


class ProviderError(RuntimeError):
    """Provider failure that deliberately omits response bodies and credentials."""


@dataclass(frozen=True)
class Completion:
    content: str
    total_tokens: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


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
    for raw_line in path.read_text(encoding="utf-8").splitlines():
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
        values = {key: env.get(key, file_values.get(key, "")) for key in ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL")}
        provider = cls(values["DEEPSEEK_API_KEY"], values["DEEPSEEK_BASE_URL"] or "https://api.deepseek.com/v1")
        return provider, values["DEEPSEEK_MODEL"] or "deepseek-chat"

    def complete(self, messages: Sequence[Mapping[str, str]], *, model: str, max_tokens: int) -> Completion:
        url = self.base_url if self.base_url.endswith("/chat/completions") else f"{self.base_url}/chat/completions"
        body = json.dumps({
            "model": model,
            "messages": list(messages),
            "max_tokens": max_tokens,
            "stream": False,
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
            raise ProviderError(f"DeepSeek request failed with HTTP {exc.code}") from None
        except urllib.error.URLError as exc:
            reason = type(exc.reason).__name__
            raise ProviderError(f"DeepSeek connection failed ({reason})") from None
        except (TimeoutError, OSError):
            raise ProviderError("DeepSeek connection failed") from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ProviderError("DeepSeek returned an invalid response") from None
        try:
            message = payload["choices"][0]["message"]
            content = message.get("content")
            if not isinstance(content, str):
                raise TypeError
            usage = payload.get("usage", {})
            total = usage.get("total_tokens")
            prompt = usage.get("prompt_tokens")
            completion = usage.get("completion_tokens")
            return Completion(
                content=content,
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


class WorkflowRunner:
    """Resumable, game-neutral agent workflow executor with durable budgets and artifacts."""

    def __init__(self, spec_path: Path, run_dir: Path | None = None, *, provider: AgentProvider | None = None, env_file: Path | None = None, plan_only: bool = False):
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
        if self.provider is None:
            if self.provider_id != DeepSeekProvider.provider_id:
                raise WorkflowError(f"unsupported provider: {self.provider_id}")
            self.provider, env_model = DeepSeekProvider.from_environment(env_file)
            if not self.model:
                self.model = env_model
        if not self.model:
            raise WorkflowError("provider.model is required")
        self.provider_endpoint = getattr(self.provider, "base_url", None)
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
        if not isinstance(attempts, int) or isinstance(attempts, bool) or not 1 <= attempts <= MAX_UNIT_ATTEMPTS:
            raise WorkflowError(f"budget.max_attempts must be between 1 and {MAX_UNIT_ATTEMPTS}")
        if not isinstance(tokens, int) or isinstance(tokens, bool) or not 1 <= tokens <= MAX_UNIT_TOKENS:
            raise WorkflowError(f"budget.max_tokens must be between 1 and {MAX_UNIT_TOKENS}")
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
            prompt = task.get("prompt")
            if not isinstance(prompt, str) or not prompt.strip():
                raise WorkflowError(f"{prefix}.prompt is required")
            prompt_path = self._resolve(prompt)
            if not prompt_path.is_file():
                raise WorkflowError(f"{prefix}.prompt file does not exist")
            if not isinstance(task.get("max_output_tokens", 4096), int) or isinstance(task.get("max_output_tokens", 4096), bool) or task.get("max_output_tokens", 4096) < 1:
                raise WorkflowError(f"{prefix}.max_output_tokens must be a positive integer")
            input_ids: set[str] = set()
            for input_index, item in enumerate(inputs):
                if not isinstance(item, dict):
                    raise WorkflowError(f"{prefix}.inputs[{input_index}] must be an object")
                if not isinstance(item.get("id"), str) or not item["id"].strip():
                    raise WorkflowError(f"{prefix}.inputs[{input_index}].id is required")
                if item["id"] in input_ids:
                    raise WorkflowError(f"{prefix} has duplicate input ID {item['id']!r}")
                input_ids.add(item["id"])
                if not isinstance(item.get("role"), str) or not item["role"].strip():
                    raise WorkflowError(f"{prefix}.inputs[{input_index}].role is required")
                has_path = isinstance(item.get("path"), str)
                has_task = isinstance(item.get("from_task"), str)
                if has_path == has_task:
                    raise WorkflowError(f"{prefix}.inputs[{input_index}] must set exactly one of path or from_task")
                if has_path and not self._resolve(item["path"]).is_file():
                    raise WorkflowError(f"{prefix}.inputs[{input_index}] file does not exist")
                if has_task and item["from_task"] not in deps:
                    raise WorkflowError(f"{prefix}.inputs[{input_index}].from_task must be listed in depends_on")
                if task.get("evidence_lane", "general") == "trajectory_blind":
                    role = item["role"].strip().lower()
                    if has_path:
                        if role != "trajectory_packet":
                            raise WorkflowError(f"{prefix} is trajectory_blind and path inputs must be verified trajectory_packet artifacts")
                        _validate_trajectory_packet(self._resolve(item["path"]))
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
            tasks.append({
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
            "execution_started": False,
        }

    def run(self, *, retry_failed: bool = False) -> dict[str, Any]:
        self._recover_interrupted_attempts()
        task_state = {row["id"]: row for row in self.data["tasks"]}
        for task in self._topological_tasks():
            state = task_state[task["id"]]
            if state["status"] == "complete":
                fingerprint = self._fingerprint(task, task_state)
                if state.get("fingerprint") != fingerprint:
                    raise WorkflowError(f"completed task inputs or prompt changed: {task['id']}")
        self.data["status"] = "running"
        self._save()
        try:
            for task in self._topological_tasks():
                state = task_state[task["id"]]
                if state["status"] == "complete":
                    continue
                if state["status"] in {"failed", "interrupted"} and not retry_failed:
                    raise WorkflowError(f"task {task['id']} previously failed; pass --retry-failed to spend another attempt")
                for dep in task.get("depends_on", []):
                    if task_state[dep]["status"] != "complete":
                        raise WorkflowError(f"task {task['id']} is blocked by incomplete dependency {dep}")
                self._execute_task(task, state, task_state)
        except WorkflowError:
            if self.data.get("status") == "running":
                self.data["status"] = "blocked"
                self._save()
            raise
        self.data["status"] = "complete"
        self._save()
        return self.summary()

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
                budget["tokens_used"] += reserved
                attempt["status"] = "interrupted"
                attempt["charged_tokens"] = reserved
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
                    try:
                        packet = json.loads(content)
                    except json.JSONDecodeError:
                        raise WorkflowError("trajectory_blind input is not a valid pipeline packet") from None
                    _validate_trajectory_packet(path, packet if isinstance(packet, dict) else None)
            else:
                parent = task_state[item["from_task"]]
                path = (self.run_dir / parent["output_path"]).resolve()
                raw = path.read_bytes()
                content = raw.decode("utf-8")
                digest = sha256_bytes(raw)
            expected_hash = item.get("sha256")
            if expected_hash is not None and expected_hash != digest:
                raise WorkflowError(f"input hash mismatch for {item['id']}")
            payload.append({"id": item["id"], "role": item["role"], "sha256": digest, "content": content})
            hashes[item["id"]] = digest
        return payload, hashes

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

    def _execute_task(self, task: dict[str, Any], state: dict[str, Any], task_state: Mapping[str, dict[str, Any]]) -> None:
        budget = self.data["budget"]
        if budget["attempts_used"] >= budget["max_attempts"]:
            raise WorkflowError("workflow attempt budget is exhausted")
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
        user_content = json.dumps({
            "task": {"id": task["id"], "stage": task["stage"], "evidence_lane": task.get("evidence_lane", "general")},
            "input_artifacts": inputs,
            "required_output_schema": task["output_schema"],
            "response_instruction": "Return exactly one JSON value matching required_output_schema. Do not use Markdown fences.",
        }, ensure_ascii=False, separators=(",", ":"))
        system_content = (
            "Return concise, evidence-linked conclusions. Do not expose or save private chain-of-thought; "
            "do not include reasoning_content, chain_of_thought, cot, or thoughts fields.\n\n" + prompt
        )
        messages = [{"role": "system", "content": system_content}, {"role": "user", "content": user_content}]
        # UTF-8 byte count is a conservative upper bound on tokenizer pieces; framing allowance covers chat overhead.
        input_ceiling = sum(len(message["content"].encode("utf-8")) for message in messages) + 256
        remaining = budget["max_tokens"] - budget["tokens_used"] - budget["tokens_reserved"]
        if input_ceiling >= remaining:
            raise WorkflowError("workflow token budget cannot cover the next request")
        output_cap = min(int(task.get("max_output_tokens", 4096)), remaining - input_ceiling)
        if output_cap < 1:
            raise WorkflowError("workflow token budget cannot reserve a completion token")
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
        budget["attempts_used"] += 1
        budget["tokens_reserved"] += reservation
        attempt = {
            "task_id": task["id"], "attempt": budget["attempts_used"], "status": "running",
            "reserved_tokens": reservation, "reported_tokens": None, "charged_tokens": None,
            "prompt_sha256": prompt_hash, "input_hashes": input_hashes,
            "started_at": _utc_now(),
        }
        self.data["attempt_log"].append(attempt)
        state["status"] = "running"
        self.data["status"] = "running"
        self._save()
        try:
            completion = self.provider.complete(messages, model=str(task.get("model", self.model)), max_tokens=output_cap)
            charged = completion.total_tokens if completion.total_tokens is not None else reservation
            if charged > reservation:
                raise WorkflowError("provider usage exceeded the reserved request budget")
            parsed = json.loads(completion.content)
            private_fields = _private_reasoning_paths(parsed)
            if private_fields:
                raise WorkflowError("provider output contains a private-reasoning field")
            errors = _schema_errors(parsed, task["output_schema"])
            if errors:
                raise WorkflowError("provider output failed schema validation: " + "; ".join(errors[:8]))
            _atomic_json(staged_path, parsed)
            output_hash = sha256_file(staged_path)
            attempt.update({
                "status": "output_staged", "reported_tokens": completion.total_tokens,
                "prompt_tokens": completion.prompt_tokens, "completion_tokens": completion.completion_tokens,
                "charged_tokens": charged, "staged_output_path": staged_rel.as_posix(),
                "staged_output_sha256": output_hash, "finished_at": _utc_now(),
            })
            self._save()
            os.replace(staged_path, output_path)
            if sha256_file(output_path) != output_hash:
                raise WorkflowError("committed task output hash differs from the staged artifact")
            self._finalize_staged_output(state, attempt, output_path, output_rel, output_hash, charged)
        except Exception as exc:
            if attempt.get("status") == "running":
                budget["tokens_reserved"] = max(0, budget["tokens_reserved"] - reservation)
                has_completion = "completion" in locals() and isinstance(completion, Completion)
                reported = completion.total_tokens if has_completion else None
                charged = reported if reported is not None else reservation
                budget["tokens_used"] += charged
                attempt.update({
                    "status": "failed", "reported_tokens": reported,
                    "charged_tokens": charged, "error_code": type(exc).__name__, "finished_at": _utc_now(),
                })
                state["status"] = "failed"
                self.data["status"] = "budget_violation" if reported is not None and reported > reservation else "failed"
                self._save()
            if isinstance(exc, WorkflowError):
                raise
            raise WorkflowError(f"task {task['id']} failed ({type(exc).__name__})") from None

    def summary(self) -> dict[str, Any]:
        return {
            "workflow_id": self.data["workflow_id"], "unit_id": self.data["unit_id"],
            "status": self.data["status"], "run_dir": str(self.run_dir),
            "attempts_used": self.data["budget"]["attempts_used"], "tokens_used": self.data["budget"]["tokens_used"],
            "tasks": [{"id": row["id"], "stage": row["stage"], "status": row["status"], "output_path": row.get("output_path"), "output_sha256": row.get("output_sha256")} for row in self.data["tasks"]],
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
