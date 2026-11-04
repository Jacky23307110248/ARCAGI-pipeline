from __future__ import annotations

import sys
from pathlib import Path
import os

from ..harness import CommandHarnessAdapter, HarnessCommand, HarnessRunSpec


class TychoAdapter(CommandHarnessAdapter):
    """Preview commands for pinned Tycho CLI; construction does not launch a run."""

    adapter_id = "tycho"

    def __init__(self, tycho_root: Path, *, python: str | None = None):
        self.tycho_root = tycho_root.resolve()
        self.python = python or sys.executable

    def preflight_command(self, spec: HarnessRunSpec) -> HarnessCommand:
        if spec.config.get("environments_dir"):
            project_root = Path(__file__).resolve().parents[3]
            pythonpath = os.pathsep.join((str(project_root / "src"), str(self.tycho_root)))
            return HarnessCommand(
                (self.python, "-m", "arcagi_pipeline.tycho_runner", "--help"),
                self.tycho_root,
                {
                    "PYTHONPATH": pythonpath,
                    "ENVIRONMENTS_DIR": str(spec.config["environments_dir"]),
                    "ARCAGI_GAME_ENV_DIR": str(spec.config["environments_dir"]),
                },
            )
        return HarnessCommand(
            (self.python, "-m", "tycho.harness.run_parallel", "--help"),
            self.tycho_root,
            {"PYTHONPATH": str(self.tycho_root)},
        )

    def run_command(
        self,
        spec: HarnessRunSpec,
        *,
        config_path: Path | None = None,
        resume: bool = False,
    ) -> HarnessCommand:
        cfg = spec.config
        environment_dir = str(cfg.get("environments_dir", "")).strip()
        if environment_dir and config_path is not None:
            if resume:
                raise ValueError("custom-environment Tycho sessions are single-run and do not support resume")
            project_root = Path(__file__).resolve().parents[3]
            pythonpath = os.pathsep.join((str(project_root / "src"), str(self.tycho_root)))
            args = [
                self.python, "-m", "arcagi_pipeline.tycho_runner",
                "--game-id", spec.game_id,
                "--tycho-root", str(self.tycho_root),
                "--environment-dir", environment_dir,
                "--output-dir", str(spec.output_dir.resolve()),
                "--config", str(config_path.resolve()) if config_path is not None else "",
                "--seed", str(int(cfg.get("seed", 0))),
            ]
            if not config_path:
                raise ValueError("custom Tycho environment runs require a config file")
            if cfg.get("max_actions") is not None:
                args += ["--max-actions", str(int(cfg["max_actions"]))]
            if cfg.get("actions_path"):
                args += ["--actions", str(Path(cfg["actions_path"]).resolve())]
            if cfg.get("require_win"):
                args.append("--require-win")
            return HarnessCommand(
                tuple(arg for arg in args if arg != ""), self.tycho_root,
                {
                    "PYTHONPATH": pythonpath,
                    "ENVIRONMENTS_DIR": environment_dir,
                    "ARCAGI_GAME_ENV_DIR": environment_dir,
                },
            )
        args = [
            self.python, "-m", "tycho.harness.run_parallel",
            "--approach", str(cfg.get("approach", "tycho")),
            "--games", spec.game_id,
            "--out-dir", str(spec.output_dir.resolve()),
            "--seed", str(int(cfg.get("seed", 0))),
            "--operation-mode", str(cfg.get("operation_mode", "offline")),
            "--max-workers", "1",
            "--no-auto-resume",
        ]
        if config_path is not None:
            args += ["--config", str(config_path.resolve())]
        if resume:
            args += ["--resume", "--resume-games", spec.game_id]
        return HarnessCommand(tuple(args), self.tycho_root, {"PYTHONPATH": str(self.tycho_root)})
