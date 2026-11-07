# Evidence-grounded unit blueprint

Infer the source unit structure from supplied observations and source findings, keeping their provenance distinct. Do not assume a fixed slot count, rule vocabulary, board, action set, or increasing accumulation of rules. Explain each inferred slot purpose, its relation to preceding slots, alternative interpretations and uncertainty. Purpose/rationale are open text: contrast, reuse, combination, omission or other relations are conclusions to justify, not required patterns.

Return an object with slots. Each slot has id, purpose, rationale, prerequisites (earlier slot IDs), rule_ids (selected rule IDs), evidence_refs, and optional extension for game-owned details. Derive selection from the reviewed rules and input evidence. Do not import another game's blueprint. Unsupported teaching intent stays a hypothesis; do not claim learning gains.

The implementation must construct new tasks from this blueprint and the rule set. Require construction claims with check descriptions. Distinguish rule presence, observed interaction and necessity for solving. A path proves existence of a solution, not educational effectiveness or necessity of every present rule. Preserve all uncertainties for later review.
# Generated-game/v2 delivery requirements

Separate original-function anchors from proposed construction and observation
plans. Preserve source-supported original functions when revising bindings.
Propose observable aliases and their source-supported quantities here. A concrete
Python attribute path may remain pending implementation; the implementation task
reads the source and must resolve it before runtime acceptance.
Read the exact generated_game_api. A planned native observation may use
observation_bindings (alias -> original-engine data path) and behavior path
["projected",alias]. Record its source basis and pending implementation status.
Runtime availability and mechanism fulfillment are checked after implementation;
do not assert a planned projection has already been observed. Do not delete
required functions merely because their adapter has not yet been implemented.

Derive original level game functions, selected rules and transitions from the
provided original materials. Each slot includes game_function, evidence_refs,
executable behavior_requirements, and separately stated teaching_hypotheses.
Set production_target to {"contract":"generated-game/v2","object":"playable_generated_unit"}.
Behavior requirements carry unique IDs, kind="behavior", observation_path and
a supported predicate (equals, changes, contains). Choose observations that test
the promised game behavior. Unestablished necessity or teaching claims remain
hypotheses requiring separate experiments. Analysis questions and audit tasks
belong to evidence material; delivery slots represent playable game content.

# Executable validation frontier

When validation_program_api is supplied, use its versioned function anchors,
commitment changes, named probe plans and closed validation obligations. Compile
against supported capabilities before implementation. Keep design consistency
separate from implementation bindings and runtime evidence; preserve mandatory
original game functions when improving a verification plan.

