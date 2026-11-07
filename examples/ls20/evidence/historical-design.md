# Prepared M3 full-campaign runner

Status: **M3 v5 rerun1 passed**. The full execution returned `PASS_ALL_THREE`; 21/21 levels and all three campaign/session/reset replays passed. M4 has not been run. v1 results and reviews remain preserved in `full_campaign_m3_v1`.

## Scope

- Reads the three seven-level public JSON campaign files from `deliverables/pseudo_ls20/m2b/unit{1,2,3}_public.json` at execution time, so the run manifest pins the integrated package bytes actually used.
- Uses an independent finite-state BFS copied from the corrected M3 L5 audits. The strict pusher test uses half-open AABB overlap, with 5×5 player/pusher sprites and `BOUNDING_BOX` behavior; it does not import `pseudo_ls20.runtime`.
- Searches all 21 levels with the actual 0-based campaign slot. `solvable` produces a private witness and a per-action Tycho replay; exhaustive `unsolved` is only emitted when the state queue drains; cap-hit `unknown` remains unknown.
- Replays each positive witness in an isolated Tycho level at its true slot, compares every modeled discrete field, and requires Tycho `WIN`.
- If all seven levels in a package are solvable, concatenates those witnesses through one Tycho campaign instance. Native deferred level transitions perform the `on_set_level`; the adapter injects the next level's public path heading immediately after that transition and before the next action. It records level index, score, start state, native path phase, and shared game/session identity.
- At the end of each complete campaign, issues `RESET` and checks that the same session returns to level 1 with reset progress and the public L1 path phase restored.
- The final reset frame must report `GameState.NOT_FINISHED` and `full_reset=true`.
- Runs negative controls for L1 rotation-trigger removal and L5 linked shape/path removal plus an out-of-domain goal-shape mutation. A control fails if the oracle labels the counterfactual `solvable`; cap-hit controls are recorded `unknown`, not `unsolved`.

## Campaign path phase handling

Tycho creates every newly installed path mover with native `_dir=0` (down). Public `start_direction` may differ. `tycho_adapter.inject_campaign_level_phase()` translates the declared public direction after each native transition; the next action then goes through the unmodified Tycho engine. The helper asserts the real 0-based level index before applying the heading. It is called for L1 at campaign construction, for L2–L7 after each observed level transition, and for L1 after campaign reset.

The per-level path adapter uses `game_for(level, slot=slot)`, whose filler levels preserve the same actual `level_index` as the campaign. This keeps L1-only hint behavior disabled for slots 1–6.

## Negative-control limits

The L1 control removes every rotation trigger and any path manager linked to a removed trigger, then reindexes remaining trigger links. The L5 path control translates a linked shape trigger and its entire linked path off the playable board. The goal control sets the first L5 goal shape to 99, outside the LS20 shape domain; it is explicitly an invalid-level counterfactual. These controls test that the checker does not return a false positive under the specified mutations; they do not establish challenge or learning effects.

## Reproduction after release

From the project root, using the verified Tycho interpreter:

```powershell
& '<HISTORICAL_ROOT>/Tycho\.venv\Scripts\python.exe' 'deliverables/pseudo_ls20/m3/full_campaign_m3_v5_rerun1/run_m3.py' --cap 100000
```

The command writes private traces under `deliverables/pseudo_ls20/m3/full_campaign_m3_v5_rerun1/results/`. It does not write witnesses to any public package. After a released run, invoke `make_manifest.py` in the same directory to record source/package/output hashes, interpreter, library version, cap, counts, and result hashes.

The default run selects all three units and reports `PASS_ALL_THREE` only if all 21 levels have Tycho-accepted witnesses, all three one-session campaign replays/reset checks pass, and every negative control avoids a false `solvable` result. A selected subset can report `PASS_SUBSET`; that is not a complete CP1 result. `INCOMPLETE` includes missing/unknown witnesses or a failed campaign/control check.

After any run, create the integrity record with:

```powershell
& '<HISTORICAL_ROOT>/Tycho\.venv\Scripts\python.exe' 'deliverables/pseudo_ls20/m3/full_campaign_m3_v5_rerun1/make_manifest.py'
```

The manifest refuses to proceed if a public input package changed after the M3 run.

The runner and manifest have not been executed. No search, Tycho replay, negative control, campaign transition, or model smoke has been run for this prepared runner.
