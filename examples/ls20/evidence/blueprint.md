# CP1 M0 — pseudo-LS20 Course and Data Contract

**Blueprint version:** 0.4 (step-zero and life-reset semantics)  
**Status:** CP0 and M0 v0.4 passed independent and primary-agent review. This file remains specification-only; M1 runtime work is tracked separately.  
**Rule basis:** CP0 base audit and both accepted supplements for Tycho cached game `ls20-9607627b` (see `CP0_FINAL_AUDIT.md`); reset semantics follow Tycho's wrapper policy, not bare ARCBaseGame defaults. The game is the source of verified mechanics, not a claim about the original designer’s teaching intent.  
**Scope:** Three campaign units, each with seven ordered levels. All share the course blueprint and guaranteed original-mechanic coverage below; each has a distinct deterministic layout/parameter assignment.

## 1. Design commitments

1. A level’s mechanism set is selected by its course slot. Seeds may instantiate that set but may not choose arbitrary mechanisms or reorder slots.
2. Rule truth, candidate generation constraints, hidden oracle state, witness paths, and verifier results are private provenance. Agent-facing observations contain only normal game-visible state and permitted feedback.
3. The course is a hypothesis about how ordered exposure may affect formal-agent performance. This specification makes no claim that an agent will learn or improve. Any such claim requires later controlled performance evaluation.
4. Preserve LS20’s non-monotonic course shape: L4 is a selective contrast that introduces shape and retains color while suspending rotation; it is not “L3 plus one rule.” L5 reunites all three transformations. L6 changes the task to two distinct goals. L7 returns to one goal and adds fog/partial visibility.

## 2. Frozen mechanic IDs and verified meanings

The IDs below are stable schema labels; they do not expose CP0’s opaque sprite/tag names as agent instructions.

| Mechanic ID | Verified rule represented |
|---|---|
| `grid_movement` | Directional actions move the player by one 5-pixel grid unit on the 64×64 board. |
| `wall_collision` | Walls block movement and terminate the remaining destination-sprite scan. A wall-blocked directional action normally incurs the configured step decrement; if a refill was already scanned and collected earlier in that same scan, its pickup exemption applies and the decrement is waived. A wall encountered before a refill prevents that pickup from being collected. |
| `shape_cycle` | Entering a shape trigger advances shape one position in the six-state cycle. |
| `color_cycle` | Entering a color trigger advances color one position in the four-state cycle. |
| `rotation_cycle` | Entering a rotation trigger advances rotation one position in the four-angle cycle. |
| `target_match` | A goal requires exact player position and matching shape, color, and rotation. A mismatch blocks movement without spending a step. In a multi-goal level, every goal must be completed; order is unrestricted. |
| `step_budget` | A directional action normally decrements by the configured value; wall-blocked actions normally cost steps (except when a refill earlier in the same sprite scan exempts the action), and mismatch-blocked goals do not. A same-action refill pickup restores the initial counter and exempts that pickup action from the decrement. Reaching exactly 0 does not yet consume a life; a later decrement that takes the counter below 0 triggers exhaustion. Check for victory before exhaustion handling, so a winning action succeeds even if that decrement crossed below 0. |
| `step_refill` | A refill is one-use; scanning/collecting it restores that level’s initial counter and waives the current directional action’s decrement. It returns on reset/death. Whether reaching/using it is necessary to win is declared separately in `mechanics_win_dependency`. |
| `pusher` | A directional pusher can carry the player outward and return to its start; nearest wall or goal bounds its travel. One player action costs one decrement. Zero-space/no-stop cases do not launch the push. |
| `path_link` | A hidden path can move linked triggers before the player action; blocked movement rolls path motion back. Reset/death restores path position and direction. |
| `fog_visibility` | Fog limits rendered visibility around the player; it changes information available through the display, not goal or movement rules. |
| `first_level_match_hint` | L1-only optional scaffold: visual hint when current attributes match an unfinished goal; it is feedback, not a rule or oracle. It must be absent after L1. |
| `life_and_exhaustion` | Each level initialization begins with three lives. A nonterminal step exhaustion consumes one life and restores the current level state/start while preserving the reduced life count; the third exhaustion enters `GAME_OVER`. The counter may reach 0 without exhaustion; the next decrement below 0 exhausts. Victory is checked before exhaustion, including on a decrement that would cross below 0. |
| `level_reset` | Every level initialization sets lives to 3. Under the Tycho harness policy (`ONLY_RESET_LEVELS=true`), RESET in any state except `WIN` restarts the current level, restoring player attributes, steps, pickups, goal completion, path state, and lives to 3, even when the level has had zero actions or is `GAME_OVER`. RESET in `WIN` performs a full campaign reset to L1. The bare ARCBaseGame default differs when `ONLY_RESET_LEVELS` is unset; pseudo-LS20 follows Tycho's effective policy. |
| `campaign_progression` | Winning a level advances to the next ordered level in the same campaign; finishing L7 reaches campaign terminal state. |

These names summarize only CP0-confirmed mechanics. Include exact collision/trigger ordering, action/reset semantics, and any selected mechanic’s verified boundary conditions in the executable contract at M1; do not silently simplify those semantics when implemented. In particular, wall blocking stops the remaining destination-sprite scan, but does not undo a refill that appeared earlier in that scan; that earlier pickup exempts the action from decrement. Reset policy and per-level action count are transition-relevant oracle state because they determine whether RESET returns to L1 or restarts the current level.

## 3. Seven-slot course blueprint

The slot table separates two concepts: **guaranteed coverage** means the mechanic is instantiated and at least one relevant interaction/state effect is audited in that slot; **win dependency** means the valid winning solution must depend on that mechanic. Coverage does not imply win dependency. Every row also includes the shared `grid_movement`, `wall_collision`, `target_match`, `step_budget`, `life_and_exhaustion`, `level_reset`, and `campaign_progression` contract. A mechanism can be “introduced” or “reinforced” through a verified interaction even when it is not required to win. Each slot has the stated goal count and finite step budget.

| Slot | Course role | Slot-specific rules and course role | Guaranteed coverage and interaction evidence | Win dependency |
|---|---|---|---|---|
| L1 | Low-interference entry: discover that a transformation can help satisfy an exact-match target. | Introduces rotation and target matching; first-level hint scaffold only. No shape/color trigger. | Guarantee a rotation-trigger entry and the CP0-confirmed transient match-hint display/clear behavior. | Rotation-trigger use is win-essential; hint is feedback-only and not route/action instruction. |
| L2 | Rotation practice across a different layout; test reuse beyond L1. | Reinforces rotation; introduces refill; no shape/color trigger; hint is off. | Guarantee collection of at least one refill; verify it restores the counter to the initial value and exempts the collection action from that turn’s decrement; verify reset/death return behavior. | Rotation is win-essential. Refill is not automatically win-essential: mark `win_dependency` per instance. If marked essential, prove no-refill unsolvability within the finite budget; otherwise demonstrate the collectible’s counter effect without claiming it is needed. |
| L3 | Transition from one transformation to joint attribute planning. | Introduces color and pusher; reinforces rotation; combines color+rotation. | Guarantee pusher interaction and its outward carry, return, and single action cost. | Color and rotation are win-essential; pusher interaction is coverage-only unless the instance separately declares and proves pusher dependency. |
| L4 | **Non-monotonic selective contrast/transfer.** | Introduces shape, reinforces color, and suspends rotation. No rotation trigger. | Guarantee at least one pusher interaction, reinforcing L3. | Shape and color are win-essential; rotation and pusher are not win-essential unless separately declared (rotation dependency is prohibited for this slot). Preserve the contrast; do not present it as monotonic accumulation. |
| L5 | Full combination after the selective contrast. | Combines all three transformations; introduces path-linked trigger movement. | Guarantee pusher and path interactions; demonstrate path movement and blocked-move rollback. | All three transformations are win-essential. Pusher/path are coverage-only unless an instance explicitly declares and proves dependency. |
| L6 | Multi-goal coordination with different attribute requirements. | Exactly two different goals; combines all transformations; reinforces path behavior. | Guarantee pusher interaction and path interaction with observable state progression; every unit must include at least one closed-loop path topology. M4 must verify full loop transition behavior, including traversal through the loop’s unique legal direction at corners. | Both goals and all three transformations are win-essential. Pusher/path are coverage-only unless declared and proved. Either goal order remains valid, but both are required. |
| L7 | Partial visibility and planning under fog. | Returns to exactly one goal; reinforces all transformations, pusher, and path; introduces fog. | Guarantee pusher and path interactions and verify fog observation boundary; fog is an observation condition, not a state-transition/goal rule. | The one goal and all three transformations are win-essential. Fog is excluded from `mechanics_win_dependency`; it is a guaranteed visibility/observation condition only. Pusher/path are coverage-only unless separately declared and proved. |

Mechanic presence follows the slot table, not raw object counts. In every unit, pusher is introduced at L3 and guaranteed/reinforced in every slot L3–L7, matching CP0’s L3–L7 occurrence. Refill is introduced at L2 and guaranteed/reinforced in every slot L2–L7. Path is guaranteed in L5–L7. The L1 match hint is guaranteed at L1 only. These commitments guarantee coverage and audit, not win dependency. Any instance-specific win dependency must be separately declared and proven.

### Cross-slot mechanism coverage matrix (applies identically to each of the three units)

| Mechanic | L1 | L2 | L3 | L4 | L5 | L6 | L7 |
|---|---|---|---|---|---|---|---|
| Grid movement (5px step) | coverage/audit | coverage/audit | coverage/audit | coverage/audit | coverage/audit | coverage/audit | coverage/audit |
| Wall collision | coverage/audit | coverage/audit | coverage/audit | coverage/audit | coverage/audit | coverage/audit | coverage/audit |
| Exact target match | introduce | reinforce | reinforce/combine | reinforce/contrast | combine | combine, two goals | reinforce under fog |
| Rotation cycle | introduce | reinforce | combine with color | **absent** | combine | combine across goals | combine under fog |
| Color cycle | absent | absent | introduce/combine | reinforce with shape | combine | combine across goals | combine under fog |
| Shape cycle | absent | absent | absent | introduce/contrast | combine | combine across goals | reinforce under fog |
| Step budget | introduce | reinforce | reinforce | reinforce | combine | combine | combine under fog |
| Refill | absent | **introduce; verify reset + no-cost collection action** | **reinforce; verify reset + no-cost collection action** | **reinforce; verify reset + no-cost collection action** | **reinforce; verify reset + no-cost collection action** | **reinforce; verify reset + no-cost collection action** | **reinforce; verify reset + no-cost collection action** |
| Pusher | absent | absent | **introduce; guaranteed interaction** | **reinforce; guaranteed interaction** | **reinforce; guaranteed interaction** | **reinforce; guaranteed interaction** | **reinforce; guaranteed interaction** |
| Path link | absent | absent | absent | absent | **introduce; guaranteed interaction** | **reinforce; guaranteed interaction + at least one verified closed loop** | **reinforce; guaranteed interaction** |
| L1 match hint | **introduce; guaranteed** | absent | absent | absent | absent | absent | absent |
| Multi-goal completion | absent | absent | absent | absent | absent | **introduce; exactly two** | absent; returns to one |
| Fog visibility | absent | absent | absent | absent | absent | absent | **introduce; guaranteed** |
| Three lives / exhaustion | coverage/audit | coverage/audit | coverage/audit | coverage/audit | coverage/audit | coverage/audit | coverage/audit |
| Current-level RESET | coverage/audit | coverage/audit | coverage/audit | coverage/audit | coverage/audit | coverage/audit | coverage/audit |
| Campaign progression / terminal | coverage/audit | coverage/audit | coverage/audit | coverage/audit | coverage/audit | coverage/audit | coverage/audit |

The three units must each instantiate every non-absent matrix entry, while preserving the fixed absences and transitions (especially rotation absent at L4, multi-goal absent again at L7). Each unit’s L6 path must include at least one closed loop; coordinates and shape may vary, but loop topology may not be omitted. Matrix entries promise presence plus an audited interaction/effect, not that the mechanic is necessary to win. Win dependency is specified separately in each course row and per-instance manifest. The matrix is not an invitation to add other original mechanisms in arbitrary slots.

## 4. Data contract (field design only)

This is a field-level contract, not a code schema. M1 may choose serialization details but must preserve these fields and visibility boundaries.

### Campaign manifest

Required supervisor-manifest fields (the manifest itself is supervisor-only unless a field is explicitly exported):

- `schema_version`, `rule_contract_version`, `course_blueprint_version`
- `campaign_id`, `unit_index` (1–3), `seed`, `generator_version` (`seed` and generation provenance are supervisor-only and never included in the agent-visible package or metadata)
- `ordered_level_ids` (exactly seven, L1 through L7)
- `public_campaign_hash` over canonical public game content only
- `private_oracle_bundle_hash` and private `oracle_artifact_ref`, stored only in supervisor metadata and excluded from agent-visible package and metadata
- `ruleset_id` and CP0 provenance reference identifying the audited LS20 cache/build and audit document
- `variation_profile_id` documenting the assigned deterministic layout/parameter profile
- `continuity_contract`: ordered level advance behavior, campaign terminal behavior, Tycho same-level RESET policy except at WIN, three-life exhaustion, and per-level life initialization semantics
- `artifact_visibility_map`: each artifact category and the consumer roles permitted to read it

### Level record

Each ordered level requires:

- Identity/order: `level_id`, `campaign_id`, `slot_index`, `course_role`
- Learning tags: `introduces`, `reinforces`, `contrasts`, `combines`, `transfers`
- Mechanic declaration (separate axes): `mechanics_coverage_required` (must be instantiated and interaction/effect audited), `mechanics_win_dependency` (must be relied on by a winning witness), and `mechanics_optional` (may be instantiated but is not counted toward coverage or dependency)
- Task/rules contract: `start_position`, `goal_count`, `goal_specs`, `step_budget`, `steps_decrement`, `life_budget` (3), `exhaustion_semantics`, `explicit_reset_semantics`, `reset_policy` (`tycho_same_level_unless_win`), `level_transition_semantics`, `visibility_mode`, `hint_mode`
- Public game instance: `initial_player_state`, `board_layout`, `grid_step_pixels` (5), `trigger_instances`, `pusher_instances`, `refill_instances`, `path_instances`, `visible_goal_instances`, `walls`, `render/observation settings`
- Deterministic provenance: `level_seed`, `variation_dimensions`, `canonical_level_hash`, `source_blueprint_slot`, `generation_attempt` (supervisor-only)
- Validation references: `expected_mechanic_coverage`, `counterfactual_cases`, `oracle_artifact_ref`, `validation_report_ref` (supervisor-only)

Use normalized state fields for position (on the 5-pixel grid), shape, color, and rotation. Goal specs record exact position and all three required attributes. Multi-goal completion is represented per goal, not as one ambiguous goal flag. Trigger/path/pusher instances carry typed mechanic IDs, coordinates/direction/topology, and any parameters needed by the rule contract. Walls, grid movement, life budget, exhaustion, explicit reset, and level transition semantics are explicit per-level rule contract fields and agree with the campaign continuity contract; none may be left as implicit generator behavior. The M3 oracle state must include lives, current level, per-level action count, reset policy, reset-relevant state, and terminal status.

`level_seed`, `variation_dimensions`, `source_blueprint_slot`, `generation_attempt`, and provenance-bearing level hashes are supervisor-only fields. Agent-facing packages contain no campaign/level seed, private oracle reference/hash, witness, verifier result, mechanic labels, or generation provenance. Any needed agent-visible identifier is an opaque value that cannot look up a supervisor manifest or private artifact.

### Hidden oracle record (never agent-readable)

Per level, store separately:

- `oracle_state_model_version`, `initial_hidden_state`, `goal_predicate`, and complete transition-relevant hidden variables
- `witness_actions` and resulting state sequence, if found
- `solvability_status` with values such as `solvable`, `unsolved` only after exhaustive finite search, or `unknown` when bounded search cannot decide
- `search_bounds`, `visited_state_count`, `counterfactual_results`, `engine_replay_result`, `engine_build_identity`
- `oracle_hash`, `verifier_version`, and validation timestamps/identity

An oracle artifact must be stored outside all agent-readable prompts, observations, files, logs, and tool responses. The runtime may use it for supervisor validation only; the game agent receives no witness, search result, hidden state, validation label, mechanic labels, generation constraints, or private seed-to-answer mapping.

### Provenance record

Record `source_game_id`, audited source and metadata hashes where available, `CP0_FINAL_AUDIT.md` reference, evidence scope/limitations, mechanic evidence references, generation and runtime versions, seeds, hashes, validation outputs, and any rejected generation attempts. Distinguish verified game behavior from course-design hypotheses. Provenance is supervisor-only except for non-informative version/identity metadata explicitly approved for normal game presentation.

## 5. Constraints against unrelated rules and answer leakage

- The generator can select only a slot’s declared mechanic IDs. There are no random rule toggles, off-blueprint mechanics, or post-hoc level shuffling.
- `mechanics_coverage_required` promises presence plus an executed, audited interaction/effect; it does not mean the mechanic is needed to win. `mechanics_win_dependency` is the only field that claims a mechanic is necessary to a valid winning witness, and it must pass counterfactual removal/change checks. Mere sprite presence counts for neither field.
- Optional mechanisms must be declared before generation and kept outside coverage and win-dependency claims unless explicitly promoted. Never add an unexplained object to increase clutter.
- Each level must meet its slot’s target count, mechanic requirements, scaffold/visibility mode, and verified rule boundaries. Reject and regenerate failures; do not relabel a failed candidate as a different course role.
- M4 must maintain two separate checks: (a) coverage review confirms every guaranteed matrix entry in every campaign with a witnessed interaction/state effect or, for hint/fog, an observation-level effect; (b) win-dependency review checks only mechanics listed in `mechanics_win_dependency` using witness inspection and counterfactual removal/change. For each refill L2–L7, compare a paired ordinary-action and collection-action trace: with no refill, 7→6 under decrement 1; collecting from 7 restores to the configured initial value (for example, 20) and does not then decrement to 19. Also verify order-sensitive co-location: refill then wall keeps the restored counter and removes the refill, whereas wall then refill costs a step and preserves the refill. If refill is declared win-essential in an instance, additionally prove the no-refill counterfactual cannot finish within budget. Do not claim necessity when the counterfactual remains solvable. For each L6 closed loop, verify all loop transitions and corner direction behavior against the frozen rule contract.
- M4 must also audit the shared base contract: 5-pixel grid movement, wall blocking and step cost, the 1→0 and 0→below-zero step boundary, victory-before-exhaustion ordering, three-life exhaustion and terminal game over, exhaustion reset preserving remaining lives, next-level initialization at 3 lives, and ordered campaign advance through L7. Under the frozen Tycho policy, verify non-`WIN` RESET stays on the current level after an action, immediately after entering a new level with zero actions, and from `GAME_OVER`; verify RESET after `WIN` returns to L1. Also preserve the documented bare-engine default difference as runtime provenance, not as generated-game behavior.
- Agent-facing content is limited to ordinary rendered observations, legal action interface, and ordinary game feedback. Exclude schema, mechanism IDs, learning tags, target solution state not normally visible, seeds, generation logs, oracle/witness files, verifier/search traces, and provenance containing answers.
- Fog must be applied in the observation/rendering path; hidden map cells cannot leak through alternate metadata, debug output, frame descriptions, or bundled files.
- L1 hint communicates only its CP0-confirmed transient match feedback. It cannot reveal the needed trigger, path, action sequence, or future level rules. It is disabled in L2–L7.
- `public_campaign_hash`/public level hashes cover only canonical public game content, with no private oracle hashes/references, witness data, seeds, or supervisor-only provenance as inputs. `private_oracle_bundle_hash` and artifact references are stored separately in supervisor-only metadata. Agent-facing metadata contains neither private references nor seeds/provenance; public hashes must not enable lookup of answer artifacts available to the agent.
- M3 must independently search the frozen state model, replay positive witnesses in the actual engine, and use negative controls. Reaching search limits without a witness is `unknown`, never silently `unsolvable`.

## 6. Deterministic variation across the three units

All three units use this exact seven-slot sequence and mechanic contract. Variation is restricted to deterministic instance parameters selected by per-unit seeds and a frozen variation profile:

- board geometry and placement of start, goals, walls, and open corridors, subject to reachability and slot constraints;
- coordinates, spacing, and legal topology of trigger instances, pushers, refill items, and linked paths;
- initial player shape/color/rotation, goal attributes, and step-budget values within frozen slot ranges;
- path orientation/starting phase and pusher direction/available travel where those mechanisms are selected;
- visual layout details that do not alter mechanic semantics or visibility rules.

The three profiles must be pairwise distinct in layout and at least one additional substantive parameter dimension, while preserving identical course roles and required interaction coverage and win-dependency policy. Variation may not change mechanic identity, rule semantics, number of goals per slot, L4’s absence of rotation, L6’s two-goal structure, L7’s fog condition, or the order/scaffold contract. Seeds, parameter ranges, canonicalization, and variation profiles are frozen before generation; same profile plus seed and versions must reproduce identical canonical content and hashes. No random sampling may escape constraints or introduce novel mechanics.

### Frozen profile assignments (specification-level only)

Every campaign is assigned exactly one profile ID before generation. These profiles constrain variation dimensions; they do not prescribe board coordinates, object placements, or instantiate levels.

| Unit | Frozen profile ID | Allowed distinguishing dimensions |
|---|---|---|
| Unit 1 | `PSEUDO_LS20_PROFILE_A` | Board topology family `open_corridor`; start/goal/wall placement within slot validity constraints; baseline trigger spacing; baseline permitted step-budget band; baseline initial/goal attribute assignments. |
| Unit 2 | `PSEUDO_LS20_PROFILE_B` | Board topology family `bent_corridor`; start/goal/wall placement; trigger spacing and ordering; alternate permitted step-budget band; distinct initial/goal attribute assignments. |
| Unit 3 | `PSEUDO_LS20_PROFILE_C` | Board topology family `split_route`; start/goal/wall placement; trigger spacing and ordering; alternate pusher directions/path orientation where present; distinct permitted step-budget band and initial/goal attribute assignments. |

The profile labels name only variation classes. In M2 generation, each profile must resolve to a deterministic, recorded parameter tuple from its frozen profile rules and campaign seed. The three resolved campaigns must have pairwise distinct board layouts and differ in at least one non-layout parameter dimension; all retain the identical seven-slot course, required interaction coverage, win-dependency policy, and fixed mechanic absences. These named topology families are permitted layout variation categories, not level instances or specific coordinate patterns. A profile may not move a mechanic to a different slot, alter fog semantics, make fog win-essential, change goal counts, or reorder levels.

## 7. M0 acceptance criteria

M0 passes only when review confirms all of the following:

1. Seven ordered slots have explicit learning roles, guaranteed coverage interactions, separate win-dependency declarations, and introduction/reinforcement/contrast/combination tags.
2. Every selected mechanic is traceable to the current CP0 supplemental PASS; refill action cost includes the pickup-turn exemption and order-sensitive co-located collision behavior. Fog is treated only as an observation condition, never as a win dependency.
3. The L4 selective non-monotonic contrast is explicit; L5 is the first three-way transformation combination; L6 has exactly two different goal requirements; L7 has exactly one goal with fog and restores no second goal.
4. Campaign, level, hidden oracle, and provenance field groups are specified, including seeds, canonical hashes, witnesses/status, rule/course versions, source identity, and reset/transition contract.
5. Public game data and private oracle/provenance are separated by consumer visibility, with concrete safeguards against answer, rule-label, hidden-map, and witness leakage.
6. Three frozen variation profile IDs map to deterministic, distinct layout/parameter dimensions; allowed variation and invariants are explicit, without embedding any level instances.
7. The cross-slot coverage matrix guarantees every original mechanic category in all three units, including fixed L1 hint, L2–L7 refill, L3–L7 pusher, L5–L7 path coverage, and at least one verified closed-loop path in each L6. M4 separately audits interaction coverage and win dependency; refill has a paired counterfactual and L6 verifies full loop transitions.
8. The shared base contract explicitly represents 5-pixel grid movement, wall collision, start position, the step-counter zero boundary, three-life exhaustion, final-step win ordering, remaining-life preservation on exhaustion, life restoration on explicit RESET and next-level initialization, and campaign progression/terminal state in fields and oracle state.
9. No runtime/generator/verifier code or level instances are part of this deliverable. No claim of learning gain, challenge, or agent improvement is made.

## Revision notes

- Reopened version 0.2 after the CP0 supplemental audit found the refill pickup action exempts its own decrement; prior M0 PASS applies only to version 0.1.
- Revised `wall_collision` in version 0.3 to state the exact priority: wall stops the remaining destination-sprite scan; a previously scanned refill remains collected and exempts the action, while a wall scanned first prevents pickup and incurs the normal decrement. This addresses the sole blocking v0.2 supervisory finding.
- Version 0.4 adds the CP0 life/step-boundary and reset-scope supplement: counter 0 remains active until a later decrement crosses below zero; exhaustion consumes a life and restores level state without replenishing the remaining lives; explicit RESET/next-level initialization restore three lives; Tycho wrapper policy makes non-WIN RESET level-only, while WIN resets the campaign. Bare-engine default behavior is distinguished.
- Made L1 match hint, L2 refill, L3 pusher, L5–L7 path links, L6 path reinforcement, and L7 pusher reinforcement fixed coverage commitments in every unit.
- Added the cross-slot mechanism coverage matrix and M4 effect checks, including a paired refill counterfactual. These mechanics need not all be win-essential, but their confirmed behavior must be exercised and audited.
- Separated per-level guaranteed interaction coverage from win dependency; set refill coverage to L2–L7 and pusher coverage to L3–L7, matching CP0 occurrence, with path at L5–L7.
- Added explicit base-rule IDs and per-level/campaign fields for 5-pixel movement, wall collision, start position, three-life exhaustion, final-step victory, current-level RESET, and campaign progression/terminal state.
- Excluded fog from L7 win dependency; it remains a guaranteed observation condition and must not affect goal truth.
- Froze three specification-level profile IDs and their allowed layout/parameter dimensions without specifying coordinates or constructing levels.
- Separated `public_campaign_hash` (public content only) from supervisor-only `private_oracle_bundle_hash`/artifact references; clarified that seeds and provenance stay out of agent-visible package metadata.
- Required an L6 closed-loop path in each unit and full M4 loop-transition verification; layout coordinates may vary.
- Added the exact refill counter semantics to the rule table, including wall/refill precedence, L2–L7 coverage, cross-slot matrix, and M4 order/counter checks.
- Preserved the non-monotonic L4 contrast, L5 three-rule combination, L6 two-goal structure, and L7 single-goal fog structure.

**M0 decision:** Version 0.2 failed independent review for an unconditional wall-cost wording conflict; v0.3 passed independent review on that point. Version 0.4 incorporates the accepted CP0 step-zero/life-reset/Tycho reset-scope semantics and has passed fresh independent supervisory and primary-agent review. M1 is released; its own independent rule-contract acceptance remains a hard gate before M2a/M2b generation.
