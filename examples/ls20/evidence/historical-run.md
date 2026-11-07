# M3 v5 rerun1 execution report

**Result: `PASS_ALL_THREE`.** The run used the canonical generator-integrated M2b v5 packages, the pinned Tycho Python 3.13.5 / arcengine 0.9.3 environment, and the copied v1 M3 source set. The copied source hashes were checked against v1 before execution. The preexisting `full_campaign_m3_v5/` folder was left untouched after initial inspection; it had no results, and its earlier review-file contents could not be confirmed. This run used the fresh `full_campaign_m3_v5_rerun1/` directory.

## M3 results

- All 21 level searches were `solvable`; all 21 isolated Tycho witness replays passed with action-by-action discrete state and terminal status agreement.
- All three native campaign sessions passed. Each advanced through six verified L1→L7 transitions in one session, finished at level index 6 with score 7 and `WIN`, then reset in the same session to `NOT_FINISHED`, level index 0, score 0.
- There were no `unknown` or cap-hit searches.
- All nine negative controls were exhaustively `unsolved` below the 100,000-state cap: L1 rotation-trigger removal, L5 linked shape/path moved offboard, and invalid out-of-domain L5 goal shape for each unit. The goal-shape control is explicitly an invalid-level counterfactual.

## Exact result hashes

- `results/full_campaign_m3_results.json`: `3f583b28eb8cdf945a0997d6ae7b9784186f8ee048eadd0620c6ff2c60814dbe`
- `results/run_manifest.json`: `54709852fc3e88bad107305bc707f5ebcc5ae3f2a32063b1bfd8045d9ff314a4`

After setting the design note to its final post-run status, the manifest was regenerated so its design source hash matches the final file. The result hash is unchanged.

The manifest records the three public input hashes and canonical campaign hashes, eight source hashes (runner, oracle, adapter, manifest builder, design, Tycho LS20 source, and two arcengine files), runtime identity, level status counts, campaign/reset summaries, and negative controls. Unit 3's public package SHA-256 is `f6f0e5369c853e0c991145d84d8341bb5c0a32741dcdd23216a5e7318aaabb89` and campaign canonical hash is `4530b119a7904bf590b475b81a5d8fc2c0770b06889ac2699459d7df900f3680`.

M4, Duck, and DeepSeek were not run. This report records M3 only and does not imply M4 acceptance.
