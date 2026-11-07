# Phase-aware validation programs

Read the exact validation_program_api and generated_game_api. The capabilities
object is the supported frontier; unknown operations are a design protocol
failure, not an execution result. Keep hypotheses separate from production proof.

Blueprint uses validation_program_version="arcagi-validation-program/v2",
positive commitment_version, and function_anchors [{id,statement,evidence_refs}]
grounded in original materials. Every slot lists function_anchor_ids. Required
behavior_requirements or validation_obligations cover those anchors through
anchor_refs. Changes to original anchors, function labels or existing checks
require commitment_changes [{slot_id,obligation_id?,anchor_id?,reason}] and a
higher commitment_version. Compare the supplied prior artifact; do not delete
source-supported functions to avoid implementing validation.

A slot may use legacy behavior_requirements with kind=behavior, observation_path
and predicate equals/changes/contains. Richer checks use validation_obligations:
{id,phase:implementation|runtime|research,required,function_ref:<slot ID>,
anchor_refs,evidence_refs,proof_scope,traces:[names],expression:<closed AST>}.
Research items have required=false and do not discharge mandatory behavior.
Design consistency is checked by compilation and phase review before code exists;
do not label a runtime trace expression as a design-stage result.

AST nodes are literal {op,value}, read {op,trace,path}, operators {op,args}, and
sequence {op,items}. Follow exact arity and types in the API. No arbitrary Python,
method execution or plugin self-reported PASS is a validation expression. Checks
must read actual declared machine evidence. Paths start at a named trace record,
for example ["trajectory",1,"observation","projected","alias"].

Reserved traces: candidate, unit, smoke, level_smoke. Other names are declared in probe_plan
[{id,kind:behavior|reset_failure|counterfactual,baseline?}]. Implementation returns
matching GeneratedLevel.probes with concrete steps [{op:step,action:...}|{op:reset}],
max_steps and max_elapsed_seconds. reset means full reset plus target selection;
use an ordinary step with the native reset action for the game's own reset policy.
Each probe is a fresh original-engine instance. Source code, effective config,
runtime, initialization, goal, actions and actual observations are recorded.

Proof scopes:
- observed_witness uses original-configuration traces.
- trace_comparison reads at least two original-configuration traces.
- declared_comparison is a baseline/variant configuration contrast. The variant
  names an ordinary probe baseline, carries an explicit changed configuration,
  and uses identical steps. Read both traces. This establishes that declared
  contrast, not universal causal necessity or control of hidden randomness.

Counterfactual variants may declare configuration, initializations and
observation_bindings. Native variants include their full engine_level snapshot.
Compile functions are factory-ready, pure translators of unit config/source;
they must not depend on generate_units having populated hidden caches. Probes
run in separate processes under an independent guardian deadline, including
source loading, factory, compilation, reset/step and goal evaluation. Worker
process isolation is a resource/lifecycle boundary, not a security sandbox.

Required checks are evaluated when their named evidence exists. A requirement
on unit/smoke must not prevent those executions from running. Candidate goal
acceptance, probe outcomes, whole-unit completion and smoke are distinct results.
Missing evidence remains unknown. Projected observations use the actual target
position; observation_scope=unit is allowed only for unit/smoke traces.

Search state-cap exhaustion may report evidence.limit_kind="state_cap" only when
that limit was actually reached. The machine can use its finite declared cap
schedule without new model requests. Other unknowns remain bounded diagnosis or
limited completion. Invalid capability declarations repeating unchanged reach a
declared limit; they are not automatically machine faults or supervisor issues.

Phase review findings use category design_contradiction,
implementation_obligation, runtime_obligation or research_hypothesis. Only design
contradictions produce verdict=return with their legal revision_targets. Other
findings use verdict=pass and empty revision_targets, while implementation/runtime
findings name an existing obligation_ref {slot_id,id}. This passes design scope,
not execution. Proposed aliases may wait for implementation, but their source
quantity and validation plan must be coherent.


## V2 handoff requirements

The shared design/production target check runs before implementation. Omitted
behavior_requirements means []. A rich-only blueprint is valid when its required
obligations cover the functions. Each check expression must return Boolean;
known incompatible nested types are rejected before execution.

V2 sequence items are {op:event_ref,trace:<one named timeline>,index:<event index>},
not bare numbers or arbitrary reads. All items belong to the same machine session.
Independent local counters do not prove event order.

smoke is unit-scoped: use observation_scope=unit. Per-target obligations use
level_smoke, which creates a fresh selected target instance. An optional
level_smoke_action(unit,level) chooses its action; otherwise smoke_action(unit)
is used. Unit smoke is not evidence that every target was reset and stepped.

Only machine operation receipts and supplied checks prove execution.
next_check.operation is a bounded request with prerequisites; blocked checks
cannot be reported as completed. Evidence fragments are actual workflow inputs.
Use the explicit failing task/stage; an unstarted downstream task is not blamed
for an upstream check. Rejected prior artifacts remain correction inputs;
commitment_baseline names a separately checked prior version.

A v2 next_check request changes actual execution order. The worker first runs
the requested check and its prerequisites across generated units, reusing
checks already performed in the same attempt. It then runs all remaining
required checks. A receipt distinguishes executed checks from missing
prerequisites and includes the execution sequence and evidence hash.
Read check IDs and permitted revision targets in control_feedback. Read
observations and diagnostic facts in control_evidence. Both inputs are pinned.
