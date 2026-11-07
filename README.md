# ARCAGI Pipeline

ARCAGI Pipeline takes gameplay evidence and source material through rule analysis, course design, playable generation, model-side search, and original-engine verification. The core manages the workflow, budgets, revisions, and evidence. Game plugins define configurations, actions, state, and goals.

## Install

Use CPython 3.13 for the game examples. From the repository root in an active virtual environment:

```sh
python -m pip install -e .
arcagi-pipeline --help
arcagi-pipeline demo --output-dir demo-output --seed 17
```

The synthetic demo exercises the shared interfaces offline. Use a new output directory for each run.

## Play or reproduce a game

| Case | Entry point | What runs |
| --- | --- | --- |
| Play pseudo-LS20 | [LS20 player and minimal download](players/ls20/README.md) | Three playable seven-level campaigns |
| Reproduce pseudo-LS20 | [LS20 production](examples/ls20/README.md) | Frozen design, generation, independent BFS, and original-engine replay |
| Verify generated TR87 | [TR87 offline replay](examples/tr87/README.md) | Six frozen generated levels, candidate replay, 32 behavior checks, continuous completion, and adapter smoke |

The published LS20 packages are in [the seed 22 archive](outputs/ls20/seed22/README.md). The [release check](docs/RELEASE_VALIDATION.md) states what was rerun and what the results establish.

## Work with a new game

- [Architecture](ARCHITECTURE.md) explains the boundary between the core and game plugins.
- [Generated game contract](docs/GENERATED_GAME_V2.md) and [validation programs](docs/VALIDATION_PROGRAM.md) describe the link from a blueprint through loaded configurations to runtime evidence.
- [Control plane](docs/CONTROL_PLANE.md) covers planning, running, pausing, and resuming a budgeted workflow. `workflow-plan` previews tasks and `workflow-run` executes explicitly configured model calls.
- [Upstream integrations](UPSTREAM.md) lists Tycho and Duck revisions for optional harness sessions.

LS20 is the historical production reference. The TR87 probe reached full generated-unit verification after workflow and machine revisions. These two cases establish executable paths on their respective configurations. Cross-game success rates and unattended operation on unseen games remain unmeasured.

## Test and package

```sh
python -m pip install pytest
python -m pytest -q tests
python tools/package_release.py
```

The ZIP includes the tracked source, the offline examples and the published LS20 packages. See [MIT](LICENSE) and [third-party notices](THIRD_PARTY_NOTICES.md).
