# M3 v5 Independent Acceptance

**Verdict: PASS.** The v5 full-campaign M3 result is independently reproduced against the current canonical M2b v5 packages. This verdict covers M3 search, Tycho witness/campaign replay, reset assertions, and negative-control checks only. It is not M4, Duck, or DeepSeek acceptance.

## Executor artifact integrity

The executor result and refreshed manifest hashes match the execution report and each other:

- `results/full_campaign_m3_results.json`: `3f583b28eb8cdf945a0997d6ae7b9784186f8ee048eadd0620c6ff2c60814dbe`
- `results/run_manifest.json`: `54709852fc3e88bad107305bc707f5ebcc5ae3f2a32063b1bfd8045d9ff314a4`
- The manifest parses as UTF-8 JSON and its `result_sha256` matches the result file.

All eight manifest source hashes match the current files: `run_m3.py` `839d621864f27a7c87e8fcac739df403fc2b6b6aa6a36765a03c35886b5c8ba1`; `oracle.py` `29aa4aa58149fb1ed472a2ceab0e5623b3c52104993719e5f9d1754e0b74abbc`; `tycho_adapter.py` `f2a0661fb4163f241c6b092bfd028a7b3f495444ea46e9dedd2289a516dc3788`; `make_manifest.py` `d335bdf55a5746aab1d84c343aa07c72382fb2642504676a908a1c5abc99fbc4`; `DESIGN.md` `9df8329ee0752908fee39fdf7056ccd626c27c17967a39b1b8be51038cfb3595`; Tycho `ls20.py` `67cc85888eeef68f125cbb5f5fb158e9ca455d6bca46b171790f556fcabf934e`; Arcengine `base_game.py` `5b5f41f3bec4c0c97a727fda7326114991dc0d4a5642d489b40ee4aa87e6b15f`; and Arcengine `sprites.py` `ea9d3d02c7db0814954134a0e0ea6a3cb9b80066930dbdbfc5865befb54c493f`.

All three manifest-bound public and supervisor package files match their current SHA-256 values:

| Unit | Public package | Supervisor package |
|---|---|---|
| U1 | `8a88cb53a4c961450f6d20573ded15a141d93ca8d6fe3a3359349f103b2a89db` | `57a0211227ed06b537c11a8a4da28e2a37ee2ee1ac7730d16e88146ecce7e6cf` |
| U2 | `3193829b508d49408395e3453c83c89e95d967fc7c021f4e83d06d59c0c45ebd` | `2f01299362444181f9731d2b5f3d267d90894f4395788f5ba012f4f51f0e89be` |
| U3 | `f6f0e5369c853e0c991145d84d8341bb5c0a32741dcdd23216a5e7318aaabb89` | `4687af8230d99b0495ea94296fdcd4f50b8735923f0039e4abe43d9767efe36a` |

## Independent reproduction

I copied the manifest-bound M3 runner, oracle, adapter, manifest builder, and design into the fresh directory `full_campaign_m3_v5_independent_audit_20261102/` and ran the full three-unit command with the pinned Tycho Python environment:

```powershell
& '<HISTORICAL_ROOT>/Tycho\.venv\Scripts\python.exe' 'deliverables/pseudo_ls20/m3/full_campaign_m3_v5_independent_audit_20261102/run_m3.py' --cap 100000
```

The fresh run returned `PASS_ALL_THREE`, 21 levels, three campaigns, and nine controls. I generated a manifest for the independent run. Its result SHA-256 is `ae4f7ae5e19791918dec8330a9e31e1b21e4d9a99ca18cbc63322b9e520ea306`; its manifest SHA-256 is `cf728bb6819adb553c6c52e1178912690d9688b7b8f77e3adbe2d9869f2d1446`. All eight source and all six package hashes in that independent manifest also match current disk files.

A recursive comparison of executor and independent results, omitting only timing fields, is identical. All 21 searches are `solvable`; each witness, oracle trace, and isolated Tycho replay record matches, and every replay passes. The three L6 searches are the largest, but all completed under the 100,000-state cap (U1 86,093 states; U2 91,769; U3 95,782).

Each native campaign run used one stable Tycho game/session, logged six verified L1→L7 transitions and six path-phase events, and ended at level index 6 with score 7 and `GameState.WIN`. The subsequent reset assertion passed in each same session and restored `GameState.NOT_FINISHED`, level index 0, and score 0. Transition action records and campaign traces match the executor result.

All nine negative controls are `unsolved` and safely below the cap: for each unit, L1 rotation-trigger removal; L5 linked shape/path moved offboard; and the invalid out-of-domain L5 goal-shape control. Each exhausted search has `expanded == states < cap` and `did_not_false_positive == true`.

## Evidence limits

The runner asserts `reset_frame.full_reset` during execution but does not serialize that boolean in the result record. The successful independent replay confirms the assertion executed; the saved summary itself only records reset state, level index, score, and same-session identity. Negative-control records likewise do not include an explicit `search_exhausted` field; exhaustion is evidenced by `unsolved`, `expanded == states`, and `states < cap`. The goal-shape controls are invalid-level counterfactuals as stated in the run design. These schema limits do not change the reproduced M3 verdict.
