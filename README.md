# ARCAGI Pipeline

ARCAGI Pipeline is a local toolkit for organizing game-rule analysis, game-specific plugin work, level generation, validation, and harness execution. The outer workflow is shared; game rules, state representations, actions, and level formats belong in separately supplied game plugins.

## What is in this main snapshot

- Versioned contracts for game sources, evidence, rule hypotheses, generated levels, search results, and harness sessions.
- Offline workflow stages and prompts for trajectory analysis, source audit, probe design, rule-contract review, course design, plugin implementation, and revision.
- A deterministic synthetic demo that exercises the generic interfaces without reading game sources, calling a model, or launching a harness.
- Tycho and Duck adapter code that operates on user-specified external checkouts. The upstream harness repositories and game data are not included in this clean main snapshot.

This snapshot does not include an original-game plugin or claim automatic generalization to every game. A game integration must provide its own version-bound rules, state/action model, level format, probes, and replay adapter through the documented interfaces.

## Install and inspect

```powershell
python -m pip install -e .
arcagi-pipeline --help
```

Run the offline synthetic example:

```powershell
arcagi-pipeline demo --output-dir .\demo-output --seed 17
```

The demo writes its artifacts to the selected output directory. It does not use API credentials or start external processes.

## Agent workflow and evidence

`workflow-plan` validates and previews a budgeted workflow. `workflow-run` executes explicitly configured agent tasks and records inputs, outputs, request budgets, and hashes. The prompts are workflow components: they help an agent perform analysis, but they do not turn uncertain rule hypotheses into verified engine behavior. Keep observed behavior, saved player notes, post-run hypotheses, source findings, and engine-probe results separately attributed.

For `legacy-trajectory-import`, keep its input artifacts inside one bundle root and write the packet under a child directory such as `bundle/trajectories/packet.json`. The packet stores bundle-relative paths and hashes; validation and freezing reject absolute paths, parent-directory escapes, and symlink escapes. Only pass a source-blind workflow bundles whose supporting files are themselves safe for that phase.

## Original-game harness sessions

`harness-prepare` accepts an external Tycho or Duck repository and produces a secret-free session preview. `harness-execute` is the explicit launch step; `harness-status` inspects progress. Credentials are inherited from the process environment. The adapters do not bundle game environments, API keys, model outputs, or upstream run data.

For Tycho, pass its checkout with `--repo` and a non-secret config with `--config`. For Duck, pass `--repo`, `--environments-dir`, `--model`, and `--max-actions`. The exact upstream revisions expected by the adapters are documented in [UPSTREAM.md](UPSTREAM.md).

## Layout

- `src/arcagi_pipeline/`: generic contracts, workflow, search, replay, and harness adapters.
- `schemas/`: JSON schemas for the outer pipeline records.
- `workflows/prompts/`: reusable agent-task prompts.
- `examples/`: a synthetic manifest and workflow example.
- `tests/`: game-neutral contract, workflow, search, and adapter tests.

Game implementations and game-specific campaigns are intentionally supplied outside this main snapshot.
