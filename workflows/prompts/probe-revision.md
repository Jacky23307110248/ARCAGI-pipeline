# Probe and verification revision workflow

Use this workflow only after an offline differential run reports failed. Read the frozen source binding, frozen rule contract, frozen source-engine probe suite, generated-level definitions, and the complete differential result. Keep all source paths and hashes attached to the decision.

## Evidence boundaries

- Treat the original engine's observations under the bound source/runtime as the evidence for how that engine behaved in the tested cases.
- Treat the contract oracle as an implementation of the already frozen rule contract. It is independent of the plugin transition code, but it is not independent proof that the rule contract itself was inferred correctly.
- Treat historical Tycho/Duck actions and saved notes only according to their provenance in the trajectory packet. Do not invent internal reasoning or promote actions into saved thoughts.
- Never edit the source binding, frozen contract, historical trajectory packet, or prior probe results as part of a repair. A suspected evidence or contract problem must be reported for a separately reviewed re-freeze.

## Triage and repair

1. Find the earliest failed event in the first failed regression case. Record the case ID, event index, action, expected observation, plugin prediction, engine observation, and source/runtime hashes.
2. Follow the run's failure category and revision guidance:
   - rule_evidence: verify the referenced CP0 suite and its hash; stop if it is missing or changed.
   - rule_contract: compare the engine observation with the frozen contract oracle; report a contract conflict without silently changing frozen evidence.
   - plugin_transition: change only the game plugin transition model.
   - level_generation: inspect the generated definition and the level actually loaded into the engine.
   - engine_adapter: inspect the source/runtime binding and reset/step adapter.
3. Make the smallest repair in the component supported by the mismatch. Do not change unrelated mechanics or broaden the modeled state.
4. Record changed file paths and SHA-256 values, the reason for each change, and the expected regression impact. Preserve the failed run unchanged.
5. Run the failed case, then every frozen trigger, non-trigger, boundary, reachable-interaction, and generated-level case. Run the deliberate faulty-model negative control too; it must still be detected and localized to plugin_transition.
6. Create a new result directory. Never overwrite an earlier run. A repaired run passes only when every baseline case matches step by step and the negative control remains detectable.

## Stop conditions

Stop and report unknown or conflict if the pinned game source/runtime changed, evidence hashes do not match, the engine contradicts the frozen rule contract, or the available probe cannot distinguish competing explanations. Do not call a model API or start a Tycho/Duck player agent in this workflow.