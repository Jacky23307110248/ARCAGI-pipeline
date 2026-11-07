# Pseudo-LS20 M1 implementation report

**Status: M1 implementation and all supplements passed independent acceptance and root review.** The pusher geometry/endpoint supplement changes only M1 files and evidence; it does not alter M2b candidates or M3 artifacts. Rule basis is CP0 plus frozen M0 Blueprint v0.4, Tycho `ls20-9607627b`, source SHA-256 `67cc85888eeef68f125cbb5f5fb158e9ca455d6bca46b171790f556fcabf934e`. See `CP1_M1_PUSHER_OFFSET_INDEPENDENT_ACCEPTANCE.md` and `CP1_M1_PUSHER_OFFSET_MAIN_REVIEW.md`.

## Deliverables

- `pseudo_ls20/runtime.py`: independent transition/runtime contract.
- `pseudo_ls20/CONTRACT.md`: executable semantics and observation boundary.
- `pseudo_ls20/fixtures.jsonl`: ordered-scan and course-level fixtures.
- `pseudo_ls20/test_runtime.py`: 17 model-level unit checks.
- `pseudo_ls20/test_path_links.py`: 4 model-level checks for path-bound trigger movement, rollback, reset, and exhaustion restoration.
- `pseudo_ls20/diff_tycho*.py`: direct, paired assertions against Tycho `perform_action`, actual renderer, wrapper reset policy, or goal predicate.

## Verification results

Executed from `deliverables/pseudo_ls20/` using the Tycho LS20 virtualenv Python with `PYTHONDONTWRITEBYTECODE=1`:

| Command | Result |
|---|---|
| `python test_runtime.py` | 17/17 pass |
| `python test_path_links.py` | 4/4 pass |
| `python diff_tycho.py` | 6/6 pass: trigger/wall order; refill/wall order; mismatch-goal/trigger order, comparing position, attribute, steps |
| `python diff_tycho_cycles.py` | 3/3 pass: shape/color/rotation; enter, leave, re-enter compared after each action |
| `python diff_tycho_push4.py` | 4/4 pass: all directions, player endpoint, pusher home, step counter, returned 17 frames |
| `python diff_tycho_pusher_edges.py` | 4/4 pass: zero-space, goal-stop, no-stop, near-edge; compares endpoint, pusher position, steps and frame count |
| `python diff_tycho_pusher.py` | 1/1 pass: rightward one-cell wall-stop case |
| `python diff_tycho_pusher_offsets.py` | pass: four 1px-offset directions, goal-stop, zero-space, no-stop, two-cell U1 L6 carry, carry-endpoint trigger/refill rescans, U1 L3 six-right route and post-WIN seventh input |
| `python diff_tycho_path.py` | 12/12 pass: actual L6 closed-loop linked path position after every action |
| `python diff_tycho_path_links.py` | 5/5 pass: linked trigger moves before collision scan, matches/fails destination, wall/mismatch rollback, explicit RESET |
| `python diff_tycho_multigoal.py` | 4/4 pass: both orders of two exact goals; completion predicate stays false after first and true after second |
| `python diff_tycho_campaign.py` | 7/7 pass: controlled matching wins advance L1–L6; L7 returns WIN/terminal; first win starts at step zero, confirming win-before-exhaustion. Final-WIN Tycho RESET also matches the model L1 state field by field. |
| `python diff_tycho_reset_life.py` | pass: Tycho `import tycho`/`ONLY_RESET_LEVELS=true`; new-level zero-action RESET, GAME_OVER RESET, WIN full reset to L1 with position/attributes/steps/lives/goal flags/action count, and 1→0 then 0→−1 exhaustion |
| `python diff_tycho_reset_fields.py` | pass: 1st/2nd/3rd exhaustion and field-by-field L6 reset of position, attributes, steps, lives, two goal flags, three refills, path coordinates/directions, level and reset scope |
| `python diff_tycho_feedback.py` | pass: actual mismatch overlay ROI `[true,true,true,true,true,false]` equals feedback clock frame by frame |
| `python diff_tycho_hint.py` | pass: L1 actual hint ROI `[true,true,true,true,true,false]`; L2 hint absent |
| `python diff_tycho_fog.py` | pass: actual fog overlay visibility at radius-19 and outside samples equals model `[true,false]` |

CP0 source probes were also rerun for refill exemption, Tycho reset policy, life reset, natural hint, focused runtime, collision/path rollback, all-direction pusher animation, and L6 cycle; all returned exit code 0. They supplement but do not substitute for the paired M1 checks listed above. Source SHA-256 remained unchanged.

## Important observed details represented

- All destination effects share explicit original sprite order. A wall stops later scanning; an earlier trigger/refill effect remains. A mismatch goal blocks but scanning continues. Goal matching uses attributes at action entry, so a later same-cell trigger does not retroactively satisfy the goal.
- Refill restores the initial counter and exempts that action from decrement. Counter 0 remains active; only a transition below 0 exhausts. Victory precedes exhaustion.
- Life starts at 3 per level; exhaustion keeps reduced lives through same-level restart; explicit Tycho RESET restores 3. Non-WIN RESET remains on the current level for zero-action/GAME_OVER; WIN RESET returns to L1.
- Pushers carry/return in one synchronous action; four directions and edge fixtures match. Path movement precedes player move; wall rollback/reset and L6 corner turns match.
- Goal attributes are exact and all multi-goals are required in either order. L1 hint and mismatch feedback have five visible frames followed by clear; fog affects rendering only. Campaign action count resets on level progression, as confirmed in the paired seven-level fixture.
- Coverage and win dependency remain separate fields; no slot/level generation is included.

## Reproduction commands

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
& '<HISTORICAL_PATH>
& '<HISTORICAL_PATH>
& '<HISTORICAL_PATH>
& '<HISTORICAL_PATH>
& '<HISTORICAL_PATH>
& '<HISTORICAL_PATH>
& '<HISTORICAL_PATH>
& '<HISTORICAL_PATH>
& '<HISTORICAL_PATH>
& '<HISTORICAL_PATH>
& '<HISTORICAL_PATH>
& '<HISTORICAL_PATH>
& '<HISTORICAL_PATH>
& '<HISTORICAL_PATH>
& '<HISTORICAL_PATH>
& '<HISTORICAL_PATH>
& '<HISTORICAL_PATH>
```

The complete list contains 17 commands: two model test scripts plus 15 Tycho differential scripts. All 17 returned exit code 0 in the post-supplement run. No Tycho/Duck source, M2b candidate, generator, BFS, oracle, witness, network, `.env`, or DeepSeek was used or modified for this supplement.

## M1 supplementary correction: path-linked trigger collider

The linked mover updated its `Path` cursor but originally left the associated collider at its previous coordinates. CP0 source `ls20.py` constructs each mover as `(overlay, trigger sprite)`, calls mover `.step()` before `txnfzvzetn(destination)`, and calls `.fwtnsrvkrz()` for every mover whenever destination collision blocks movement. The model now requires `Collider.path_id` to bind a trigger to a path; it advances the cursor and collider together before scanning, then restores both on any blocked move (wall or mismatch). Reset and exhaustion restore both from the initial state.

Paired Tycho harness: `diff_tycho_path_links.py`; model tests: `test_path_links.py`. Results:

- Open step where the linked trigger moves away from the destination: both player ends at `(14,35)`, trigger/overlay at `(19,35)`, rotation unchanged, 19 steps.
- Open step where the trigger moves onto the destination: both player and linked trigger end at `(19,35)`, rotation advances once, 19 steps.
- Wall block and mismatch-goal block: both restore player, cursor direction, overlay and linked collider to `(9,35)` / `(14,35)`; the mismatch case retains 20 steps while wall costs one.
- Explicit RESET after path movement restores player, linked sprite, direction and counter identically in actual engine and model. The runtime path-link unit checks also cover exhaustion reset.

The mismatch rollback is based on source control flow: `step()` invokes every linked mover's undo method whenever `txnfzvzetn` reports blocked, regardless of whether the blocker was a wall or mismatch goal. No inferred exception is added.

## M1 supplementary correction: RESET after final campaign WIN

`Runtime.reset()` now treats both `Status.WIN` and `Status.CAMPAIGN_COMPLETE` as Tycho's full campaign RESET case. `Campaign` retains an immutable initial-level snapshot so later RESET reconstructs L1's original player, goal flags, step budget, counters, and terminal state instead of reusing its mutated in-session state.

Paired assertion is part of `diff_tycho_campaign.py`: a real seven-level Tycho fixture is won level by level, then the final `GameState.WIN` is RESET under imported Tycho wrapper policy. The engine and model match on L1 index, player position, attributes, steps, lives, unfinished goal flags, action count 0, and `full_reset=true`. Result: pass. `test_runtime.py` includes a model case for terminal-campaign RESET.

## M1 supplementary correction: pusher bounding-box contacts and endpoint scan

Tycho source control flow (`ls20.py` lines 1595–1606, 1623–1647, 1649–1660, 1872–1908, 1912–1978) sets each pusher to bounding-box mode. A directional action first runs linked movers and the ordered destination collision scan; after a non-blocked move it charges at most one step, then iterates pushers in level sprite order and checks `Sprite.collides_with` against the player's new position. This means a pusher origin one pixel before/after a contact grid cell can overlap a 5x5 player box. With origin `(34,20)` and the player moving right, the first AABB overlap is already at player top-left `(30,20)`; origin plus grid point equality would miss it. The pusher sample ray starts at origin plus one pixel in its direction and then advances by its own 5px width/height. It stops at the first wall or goal among 11 samples. Carry distance is sample index minus one, and the player endpoint is the actual contact destination plus the carry displacement, not the pusher origin plus that displacement.

Once the pusher animation reaches its endpoint and returns home, Tycho calls the ordered collision scan again at the carried player position and then completes the action. Endpoint triggers apply after the initial step charge; endpoint refill restores the initial step counter. Neither endpoint effect consumes another action. Source animation durations are fixed at 8 outward frames and 6 return frames; both one-cell and two-cell carries return 17 raw frames. Zero-clear-cell and no-stop cases do not start a push animation.

Paired evidence in `diff_tycho_pusher_offsets.py` compares engine and model state for all four 1px directional offsets, a goal stop, a first-sample wall stop, a no-stop case, two-cell U1 L6 carry, endpoint shape-trigger and refill rescans, and the U1 L3 route. The U1 L3 trace from `(10,20)` shows first AABB contact on move 4 at destination `(30,20)`; the player is carried to `(35,20)`, reaches `(40,20)` on move 5, and wins at `(45,20)` on move 6. The seventh RIGHT is ignored after WIN. This reconciles the M2 manifest's sampled/contact coordinate `(35,20)` with the earlier first-overlap coordinate and avoids treating a manifest contact point as the sole actual trigger position.

Read-only screening of the three M2b public candidate manifests found geometrically possible carry-endpoint rotation triggers in U1 L6 `(30,15)->(40,15)`, U2 L6 `(30,35)->(40,35)`, and U3 L6 `(30,40)->(40,40)`; each has a two-cell right carry from pusher origins `(34,y)` with the third sampler point as the goal stop. Root's independent static route check reports those are also first route-reachable AABB contacts; it further reports route contact candidates in L3–L5 and L7, which require M3 actual play/replay before any reachability or solvability claim. U1 L3's possible AABB contact destinations `(30,20)` and `(35,20)` carry to `(35,20)` and `(40,20)` respectively and have no endpoint trigger/refill. These are geometry/route screens, not M3 witnesses. No M2b artifact was changed. The runtime model tests endpoint trigger/refill and carry timing; all comparisons use Tycho source SHA-256 `67cc85888eeef68f125cbb5f5fb158e9ca455d6bca46b171790f556fcabf934e`.



