from __future__ import annotations

import sys
import os
import math
from pathlib import Path

from ..harness import CommandHarnessAdapter, HarnessCommand, HarnessRunSpec


class DuckAdapter(CommandHarnessAdapter):
    """Preview commands for Duck ARC3-Inference; launch only when explicitly executed."""

    adapter_id = "duck"

    def __init__(self, duck_root: Path, *, python: str | None = None):
        self.duck_root = duck_root.resolve()
        self.python = python or sys.executable
        self.inference_root = self.duck_root / "ARC3-Inference"
        self.pipeline_import_root = Path(__file__).resolve().parents[2]

    def _pythonpath(self) -> str:
        return os.pathsep.join((str(self.pipeline_import_root), str(self.inference_root)))

    def preflight_command(self, spec: HarnessRunSpec) -> HarnessCommand:
        args = [self.python, "-m", "inference.framework.run", "--list-games", "--game", spec.game_id]
        env: dict[str, str] = {"PYTHONPATH": str(self.inference_root)}
        if spec.config.get("environments_dir"):
            args += ["--environments-dir", str(spec.config["environments_dir"])]
            env["ARCAGI_GAME_ENV_DIR"] = str(spec.config["environments_dir"])
        return HarnessCommand(tuple(args), self.inference_root, env)

    def run_command(self, spec: HarnessRunSpec) -> HarnessCommand:
        cfg = spec.config
        model = str(cfg.get("model", "")).strip()
        if not model:
            raise ValueError("Duck run requires config.model; use preflight_command for offline check")
        environments_dir = str(cfg.get("environments_dir", "")).strip()
        if not environments_dir:
            raise ValueError("Duck offline run requires config.environments_dir")
        try:
            max_actions = int(cfg["max_actions"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Duck run requires a positive config.max_actions") from exc
        if max_actions <= 0:
            raise ValueError("Duck run requires a positive config.max_actions")
        try:
            max_runtime_minutes = float(cfg.get("max_runtime_minutes", 10.0))
        except (TypeError, ValueError) as exc:
            raise ValueError("Duck run requires a positive config.max_runtime_minutes") from exc
        if not math.isfinite(max_runtime_minutes) or max_runtime_minutes <= 0:
            raise ValueError("Duck run requires a positive config.max_runtime_minutes")
        args = [
            self.python, "-m", "inference.framework.run",
            "--agent", str(cfg.get("agent", "inference")),
            "--game", spec.game_id,
            "--model", model,
            "--run-name", str(cfg.get("run_name", "arcagi-pipeline")),
            "--experiment-dir", str(spec.output_dir.resolve()),
            "--environments-dir", environments_dir,
            "--max-actions", str(max_actions),
            "--max-runtime-minutes", f"{max_runtime_minutes:g}",
            "--n-passes", "1",
            "--concurrent-jobs", "1",
            "--deployment-target", "inline",
        ]
        return HarnessCommand(tuple(args), self.inference_root, {
            "PYTHONPATH": self._pythonpath(),
            "ARCAGI_GAME_ENV_DIR": environments_dir,
        })

    def scripted_smoke_command(self, spec: HarnessRunSpec, *, actions_path: Path) -> HarnessCommand:
        """Use the installed TAAF GameAPI to replay a fixed witness without a model call."""
        environments_dir = str(spec.config.get("environments_dir", "")).strip()
        if not environments_dir:
            raise ValueError("Duck scripted smoke requires config.environments_dir")
        pythonpath = self._pythonpath()
        return HarnessCommand(
            (
                self.python, "-m", "arcagi_pipeline.duck_runner",
                "--game-id", spec.game_id,
                "--environments-dir", environments_dir,
                "--output-dir", str(spec.output_dir.resolve()),
                "--actions", str(actions_path.expanduser().resolve(strict=True)),
            ),
            self.inference_root,
            {"PYTHONPATH": pythonpath, "ARCAGI_GAME_ENV_DIR": environments_dir},
        )
