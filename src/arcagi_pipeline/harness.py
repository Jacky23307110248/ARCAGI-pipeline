from __future__ import annotations

import json
import os
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        safe = {}
        for key, item in value.items():
            if any(secret in str(key).lower() for secret in ("api_key", "secret", "token", "password", "authorization")):
                safe[key] = "<redacted>"
            else:
                safe[key] = _redact(item)
        return safe
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


@dataclass(frozen=True)
class HarnessRunSpec:
    game_id: str
    output_dir: Path
    config: dict[str, Any] = field(default_factory=dict)
    environment_keys: tuple[str, ...] = ()

    def public_dict(self) -> dict[str, Any]:
        """Credential values never enter the saved run specification."""
        return {
            "game_id": self.game_id,
            "output_dir": str(self.output_dir),
            "config": _redact(self.config),
            "environment_keys": list(self.environment_keys),
        }


@dataclass(frozen=True)
class HarnessCommand:
    command: tuple[str, ...]
    working_directory: Path
    environment_overrides: dict[str, str] = field(default_factory=dict)

    def preview(self) -> str:
        return shlex.join(self.command)


@dataclass(frozen=True)
class HarnessExecution:
    exit_code: int
    stdout: str
    stderr: str


class CommandHarnessAdapter:
    adapter_id = "command"

    def preflight_command(self, spec: HarnessRunSpec) -> HarnessCommand:
        raise NotImplementedError

    def run_command(self, spec: HarnessRunSpec) -> HarnessCommand:
        raise NotImplementedError

    def execute(self, command: HarnessCommand, *, timeout: float | None = None) -> HarnessExecution:
        """Runs only when called explicitly; inherits operator environment without logging secrets."""
        proc = subprocess.run(
            list(command.command),
            cwd=command.working_directory,
            env={**os.environ, **command.environment_overrides},
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return HarnessExecution(proc.returncode, proc.stdout, proc.stderr)


def write_run_spec(path: Path, spec: HarnessRunSpec) -> None:
    path.write_text(json.dumps(spec.public_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")