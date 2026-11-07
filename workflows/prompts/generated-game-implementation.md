# Build a playable generated unit

Execution contract: `generated-game/v2`. Read the supplied exact
`generated_game_api`, `production_api`, `design_validation_api` and `runtime_api`.
Return the normal implementation bundle with entrypoint, factory and text files.
The factory accepts runtime context and returns a plugin with
`production_contract = "generated-game/v2"`.

Implement `generate_units(inputs, seed)`, `search_generated(level, cap)`,
`smoke_action(unit)` and, for native engines, `compile_levels(unit, source)`.
GeneratedUnit contains playable GeneratedLevel configurations mapped to blueprint
slots. Native configurations include canonical `engine_level` snapshots; keep
independent model data alongside them. Compilation must reproduce the declared
snapshot. Provide data-only initializations when original game state needs setup.
The machine owns native reset, loading, actions, observations and completion.
Bind planned native game-state observations using each GeneratedLevel's
observation_bindings: alias -> data path from the original engine instance.
The core reads attributes and list/dict entries and records them under
observation.projected[alias]. No method calls are permitted. Blueprint behavior
observation_path is then ["projected", alias, ...]. These are implementation
obligations, not evidence until the machine records actual values. Keep required
original functions when repairing missing bindings; fix the binding or report a
specific unmet behavior rather than silently narrowing the production target.

Search evidence pins configuration_sha256, goal_sha256 and model_version.
Bounded searches return unknown. A candidate becomes an engine-verified goal
solution only after the machine plays it in the corresponding generated instance.
Supply novelty probe actions when initial observable content alone does not
distinguish the generated level from original levels. Similar solution paths
remain valid; observed content and loaded configuration establish identity.

Construction claims map to blueprint executable behavior requirements. Design
each requirement to test the promised behavior using machine observation paths.
Keep claims about teaching benefits as hypotheses. A self-comparison of payload
facts supports field consistency only. Whole-unit execution must satisfy its
declared continuous or reset_each transition policy and original engine goal.

Diagnose and revise from machine feedback within the existing bounded workflow.
Preserve source-blind evidence boundaries and historical source-unbound status.
Use the shared diagnosis schema and metadata correction protocol. Do not request
supervisor intervention merely because a game artifact remains unresolved.
# Independent execution obligations

When validation_program_api is supplied, implement the blueprint's named probe
plan as GeneratedLevel.probes, with concrete reset/step actions and finite budgets.
Keep candidate replay, independent failure/reset evidence, matched configuration
contrasts, whole-unit execution and smoke distinct. Compile variants from explicit
configuration in a fresh factory process; do not rely on generation-time caches.
Read the exact API for observation scope, proof limits and required evidence.

