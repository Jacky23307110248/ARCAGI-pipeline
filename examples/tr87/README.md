# TR87 generated six-level unit

This example replays the frozen `tr87-generated-v2-rule-composition` unit from the accepted revision 46 production run. It loads the six generated configurations into the original TR87 game and vendored ARCEngine 0.9.3.

From the repository root, use CPython 3.13 in an active virtual environment:

```sh
python -m pip install -e .
python -m pip install -r examples/tr87/requirements.txt
python -m examples.tr87.verify --output tr87-replay
```

Choose a new output directory. Exit 0 requires the expected unit identity, six accepted candidate replays, 32 required behavior checks, continuous completion and a separate adapter smoke test. The fresh evidence is in `tr87-replay/production/production.json`.

- `unit.json` is the generated playable configuration. `replay.json` holds the frozen candidate actions, their configuration and goal identities, and hashes from the archived run.
- `blueprint.json`, `rules.json` and `evidence.json` hold the design and behavior requirements. Historical workflow diagnostics are kept in the local run archive.
- `game/tr87.py` is the hash-bound original game source. `SHA256.json` pins the published case inputs and engine files.

The entry point replays supplied candidates and generates fresh engine evidence offline. Fresh rule inference, level generation and candidate construction are the model workflow in the [control plane](../../docs/CONTROL_PLANE.md). Historical trajectory source identity remains unbound.
