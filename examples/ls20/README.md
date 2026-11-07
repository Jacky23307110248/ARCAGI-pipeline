# Reproducible pseudo-LS20 production

This example restores the original accepted M2b v5 generator and M3 v5 oracle/replay implementation (three complete seven-level campaigns). It is separate from the later CP3/CP4 course generator. No model, API key, Tycho checkout or Duck checkout is required for production.

## Run from a fresh copy

Use CPython 3.13 (validated with 3.13.5). From the repository root:

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r examples/ls20/requirements.txt
.venv/Scripts/python -m examples.ls20.run --seed 22 --output-dir outputs/ls20-seed22
```

On Linux/macOS use `.venv/bin/python` instead. Output must not already exist. `--cap 250000` bounds each BFS. Exit 0 means all 21 isolated searches/replays, all three native seven-level campaigns (including reset), and nine model negative controls passed. A cap hit is unknown, never a proof of unsolvability. Negative controls establish model behavior, not engine proofs of impossibility.

Outputs: `packages/unit{1,2,3}_public.json`, corresponding supervisor manifests, and `report.json` with actions, per-step expected/engine states, campaign transitions, dependency versions and hashes. Public packages contain playable state; supervisor files and witnesses must not be exposed to a playing agent.

The generator has three fixed topology profiles, not an arbitrary new-layout generator. Seed changes the step-budget margin modulo three and supervisor seed identifiers. Do not interpret differing seed labels as guaranteed differing layouts.

## Frozen inference versus executable production

`evidence/blueprint.md`, `rule-contract.md`, CP0 audit and M1 implementation reports preserve Codex's historical source/trajectory analysis and subsequent rule corrections. `evidence/trajectory/*visible-input.json` includes historical visible traces and saved notes. Historic game-ID labels do not prove an exact historical source revision. Historic packet/ledger citations and hashes refer to the original workspace, are archival only, and are not loaded by production. Path-redacted copies are not the original frozen bundles; `origins.json` records original hashes.

The executable starts from the frozen rules/blueprint embodied in `implementation/generate_m2b.py` and `oracle.py`. It does not rerun inference or dynamically compile the Markdown contract. Codex originally performed rule synthesis, implementation and repair; that autonomous capability is not claimed here.

## Dependency boundary

- Game: `game/ls20.py` copied byte-for-byte from the Tycho cache, and `metadata.json` with its unused personal `local_dir` replaced by `.`, bound by `DEPENDENCIES.json`. The source contains its MIT notice.
- Engine: `vendor/arcengine/arcengine`, version 0.9.3, MIT notice retained. NumPy/Pydantic and transitive versions are pinned in requirements.
- Tycho/Duck histories are evidence inputs; original wrappers supplied source access, reset-policy context and instrumentation during development. Their complete solver/harness repositories are not production dependencies here.
- The portable adapter restores the original M3 setup, including explicit initial path direction after native level setup. Subsequent actions execute the original game/engine. This is an adapted generated environment, not an unmodified stock campaign. No source rules are patched. The entrypoint explicitly sets `ONLY_RESET_LEVELS=true`, preserving Tycho nonterminal reset behavior. All delivered game/engine/evidence hashes are checked before production.
- `historical-run.md` records old results only. Current success requires a newly written report.

See repository `THIRD_PARTY_NOTICES.md` and [the release check](../../docs/RELEASE_VALIDATION.md).
