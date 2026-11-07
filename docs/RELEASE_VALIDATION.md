# Release validation

The publication combines the generic machine checkpoint with the existing
GitHub LS20 player and seed 22 downloads. [Machine checkpoint hashes](machine-checkpoint.sha256)
identify the unchanged core, prompts, and tests. [TR87 case hashes](../examples/tr87/SHA256.json)
identify the portable frozen inputs and the bundled engine files.

| Check | Evidence exercised |
| --- | --- |
| Core regression | Installation, CLI help, synthetic demo, and the full game-neutral test suite |
| LS20 frozen production | Generation from seed 22, independent BFS and original-engine replay for 21 levels, three continuous campaigns, and model negative controls |
| LS20 published packages | Independent BFS and generated-game/v2 replay of all three published packages, plus a standalone player witness test |
| TR87 frozen replay | Six configuration-bound candidate paths accepted by the original engine, 32 passing behavior checks, continuous completion, and a separate adapter smoke test |

The commands are in the [repository README](../README.md), the [LS20 production
guide](../examples/ls20/README.md), the [LS20 v2 replay
guide](GENERATED_GAME_V2.md#ls20-reference-replay), and the [TR87 offline
guide](../examples/tr87/README.md). The release packaging command builds a file
manifest alongside the ZIP. Replays emit new local reports with source,
configuration, and runtime identities.

## Historical scope

LS20 established a frozen-design production path. An earlier TR87 repair3 run
established source access and stock first-level replay. The later TR87 revision
46 run produced a generated unit with configuration-bound engine verification.
The released offline case verifies that frozen unit. The model probe and
machine repairs share a development history. Unseen-game success rates and
unattended operation remain unmeasured.
