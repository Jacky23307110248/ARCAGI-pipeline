# CP2: LS20 / generic main alignment

CP1 was executed and independently accepted; CP2 is interface/code comparison plus neutral tests, not a new real-game run or cross-game evaluation.

| Stage | Accepted LS20 implementation | Generic contract / gate |
|---|---|---|
| Rule inference | Historical visible inputs, CP0 audit, M1 contract, M0 blueprint; Codex synthesis/repairs | FrozenInputs receives reviewed rule contract and blueprint; hashes recorded, no inference claim |
| Generation | M2b v5 generate(), three seven-slot profiles | ProductionPlugin.generate(inputs, seed) -> GeneratedTask sequence |
| Structure | M2b validate(rows) | Plugin generate invokes same full-package check; validate(task) adds task checks |
| Hypothesis-space solving | M3 oracle.search(level, cap, slot) | ModelSearch: candidate / unsolvable / unknown; candidate remains unverified |
| Real-engine verification | M3 isolate_replay(), same original game/engine, state-by-state comparison | EngineVerdict with trace, binding and failed action index; only accepted=True passes |
| Return / pass | Original M3 stops or marks incomplete on failures | Runner persists feedback with task, stage, actions, failure index/reason; a revised run uses a new output directory |

`examples/ls20/bridge.py` implements the generic protocol by calling the CP1 functions, not a rewritten solver. It uses exactly the same seed mapping and generator validation. The generic engine gate may be stronger than mere legality: LS20 also requires model-state agreement. All game details remain in the example.

The generic per-task runner does not claim full campaign continuity. CP1's production entry additionally runs native seven-level transitions/reset and model negative controls; those campaign-level checks remain explicit LS20 extensions, not silently replaced by 21 isolated passes.

## Public API and CLI

`arcagi_pipeline.production` adds FrozenInputs, GeneratedTask, ModelSearch, EngineVerdict, ProductionPlugin and run_production. Payloads/actions are game-owned JSON data. Search evidence may carry dataclasses, converted by the runner through dataclass serialization. There is no fixed board, level count, action vocabulary, or model provider.

Install core (`python -m pip install -e .`) and LS20 requirements, then from the repository root:

```powershell
arcagi-pipeline production-run --plugin examples.ls20.bridge:create_plugin --rule-contract examples/ls20/evidence/rule-contract.md --blueprint examples/ls20/evidence/blueprint.md --seed 22 --cap 250000 --output-dir outputs/ls20-via-main
```

This is a supported reference command, statically compared in CP2; it was not executed as a second real-game evaluation. LS20 remains a source-tree example, not installed inside the core wheel. For the independently run full-campaign production use `python -m examples.ls20.run` as documented in the example.

An exhausted search is called model-unsolvable only when the plugin declares a complete model and complete exhaustion; otherwise unknown. A bounded/incomplete search never becomes an impossibility claim about the real engine. Exceptions and replay rejection return feedback. An initially satisfied task may have an empty action sequence and still needs engine replay.

## Legacy interfaces

Existing CLI commands remain. WorkflowRunner complete means schema-validated model output, now explicitly labeled in its summary. PipelineRun records artifact registration, explicitly `execution_verified=false`. Neither is an engine-verification gate. `production-run` is the executable gate. No autonomous model coding/repair loop is claimed; repairs can be supplied and rerun with preserved reports.

Production plugins must not mutate frozen inputs. Input hashes are checked before/after. Output directories are never overwritten. The LS20 delivery inventory binds the game, engine and historical evidence; optional Tycho model credentials come from the process environment, with no automatic neighboring .env lookup.
