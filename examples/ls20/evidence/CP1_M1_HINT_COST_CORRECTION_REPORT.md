# M1 correction: L1 match-hint early return

## Scope

Corrected the M1 transition model for Tycho's L1 trigger callback. During the ordered destination scan, a shape, color, or rotation trigger that leaves the player matching any unfinished goal's three attributes starts the L1 match hint, without requiring the player to be at that goal. Tycho returns after the scan and movement/block outcome: an unblocked move leaves the player at the destination, while a later wall leaves the player at the prior position. Effects already processed remain applied. The return skips step decrement, pusher handling, goal completion, and exhaustion processing. The callback is L1-only; L2–L7 do not take this return. Hint visibility at action completion is not a reliable detector because the raw renderer clears the timer synchronously.

The change is limited to `deliverables/pseudo_ls20/runtime.py`, the M1 contract, its focused runtime test, and a paired Tycho differential script. No M2b candidate, generator, M3 oracle/search artifact, Tycho/Duck source, or synced `sources/` file was changed.

## Evidence and verification

Tycho source SHA-256: `67cc85888eeef68f125cbb5f5fb158e9ca455d6bca46b171790f556fcabf934e`.

The U1/L1 reproducer starts at `(10,20)`, rotation 2, with 11 steps. RIGHT lands on the rotation trigger at `(15,20)`, changes rotation to 3, and makes the player's attributes match an unfinished goal. Tycho and corrected M1 both end at `(15,20)`, rotation 3, 11 steps; the final raw Tycho hint timer is zero. The paired script is `deliverables/pseudo_ls20/diff_tycho_hint_cost.py`.

Focused model tests cover the free-turn behavior and verify that a matching trigger outside L1 does not exempt cost, while matched attributes without a trigger do not exempt cost. They also check skipped pusher and exhaustion processing, and the ordered case where a matching trigger precedes a wall: the player remains blocked, but the hint return still skips cost. A wall encountered before the trigger ends the scan, so that later trigger cannot activate the return.

Commands and results:

- `& '<HISTORICAL_PATH>` from `deliverables/pseudo_ls20`: **20 tests passed**.
- `& '<HISTORICAL_PATH>` from `deliverables/pseudo_ls20`: **passed**, Tycho/model states matched and the source hash assertion passed.

This is focused correction evidence, not full M1 re-acceptance. A mismatch-before-trigger ordering is represented as blocked movement with the later trigger's effects retained, then the hint callback returns before step cost; it was not separately paired against Tycho here. The new direct wall-order test covers the corresponding cost-skipping branch.
