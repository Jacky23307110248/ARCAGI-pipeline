# Handoff chain and validation program v2

Production still constructs playable generated units. Design approval means the
declared construction and verification plan is coherent; it does not certify
runtime behavior. Game rules, actions, states and progression remain plugin data.

## Stage ownership and prior versions

Task `status=complete` records that its artifact was generated. Separate
`gate_status` and `gate_artifact_sha256` record checked handoff. A gate exception
contains the actual task and stage; a pending downstream task is not inferred to
be responsible. Controller revisions use these explicit identities.

Blueprint gates and production share `validate_target`. Missing production
targets, functions and required checks return to blueprint before implementation.
Omitted legacy `behavior_requirements` means an empty list everywhere. Rich-only
obligations are a supported production path, not a compatibility workaround.

Rejected outputs remain hashed prior inputs for correction. Commitment comparison
uses a separately marked gate-passed `commitment_baseline`. A malformed previous
artifact cannot poison its replacement; previously checked baselines survive
subsequent invalid versions. Structural change accountability does not certify
semantic faithfulness, which remains the model's review responsibility.

## Validation programs

New tasks opt into `arcagi-validation-program/v2` and receive the exact hashed
API. v1 artifacts and reports retain their historical semantics; their labels are
not upgraded. Both versions retain required function-anchor coverage, evidence
references, explicit commitment changes and phase-aware review.

The compiler rejects known non-Boolean check results, including nested incompatible
operator results. Unknown observed types remain runtime diagnostics. Design and
implementation obligations do not demand evidence from a stage that has not run.

In v2, `sequence.items` contains machine event references:

```json
{"op":"sequence","items":[
  {"op":"event_ref","trace":"failure","index":0},
  {"op":"event_ref","trace":"failure","index":2}
]}
```

`index` selects a recorded event, not an invented numeric timestamp. Compilation
requires one named timeline; evaluation checks the actual session and event
identities. Independent session counters cannot establish chronological order.
Historical v1 numeric sequences remain interpretable as their original checks.

Reserved evidence names are `candidate`, `unit`, `smoke` and `level_smoke`.
`smoke` is a single **unit-scoped** reset/step session and requires
`observation_scope="unit"` in v2. It is not duplicated as per-level evidence.
An obligation using `level_smoke` causes a fresh instance to reset, select and
initialize its own target before a real step. A plugin may implement
`level_smoke_action(unit, level)`; otherwise its existing `smoke_action(unit)` is
used. Each target has an independent session and actual position records.

Independent behavior, failure/reset and matched configuration probes retain the
guardian deadline and original-engine binding. Declared contrasts prove only
those contrasts. Universal necessity and teaching benefit do not become runtime
proof by changing labels.

## Feedback and diagnostic operations

Feedback separates goal replay, per-level obligations, actual whole-unit
execution, adapter smoke and aggregate completion. A passing unit execution is
not changed to failure because another obligation failed. Accepted candidates
remain recorded even when full production is incomplete.

Every failed obligation retains its identity, reason and bounded actual reads.
Successful details are compressed first. A large report is not replaced wholesale
by omitted markers. Hashed machine-authored evidence fragments are attached as
actual workflow inputs; a path or hash alone does not mean the model read it.
Configured request-input budgets still apply and stop oversized requests before
calling a provider.

Safe exception facts include type and hashed code locations. Raw exception text,
locals, provider bodies and unscreened logs are not automatically included.
Level-local failures permit sibling checks when their shared prerequisites remain
valid. A shared loading/configuration failure records blocked, unexecuted checks.

`diagnosis.next_check.operation` requests a declared operation. The machine
records a bounded dependency plan, keeps all mandatory final checks, and reports
actual check IDs and outcomes. Receipts distinguish unsupported operations,
missing prerequisites and executed checks; resource limitation remains a result,
not evidence that a check was never invoked. Model declarations never create
execution receipts. No arbitrary shell or expression executor is exposed.

Search cap cursors are bound to the producer and validation configuration.
Same-producer recovery preserves the cursor; a new producer starts at the first
declared cap. Requests and retries retain the single aggregate ledger.

## Recovery and boundaries

The repair prepares a new paused successor in the same run. Blind origins and
unaffected upstream artifacts remain pinned; the historical implementation remains
an input for adaptation, not proof under the new contract. Old configurations,
reports and events are preserved at their existing paths.

Ordinary artifact corrections, unexplained game failures and bounded unknowns
stay inside DS workflow. Only machine-confirmed infrastructure/integrity blocks
use the supervisor protocol. This repair does not add per-version Luna approval.
It is checked locally using scripted providers, neutral original engines, existing
LS20 regression and full tests; it makes no real DS request or TR87 production.

### Execution priority and evidence publication

For v2, the operation-plan hash is checked in the worker. Specific requests
run before remaining mandatory checks, with prerequisite reuse within the
attempt. Rerun requests retain the complete pipeline. The trace records
actual check order, blocked prerequisites and evidence hashes. V1 retains
its established order and reports priority operations as unsupported.

Production and machine reports are published atomically. The controller uses
only a valid production result confirmed by the machine report's hash.
Interrupted output is retained as a diagnostic artifact. Commitment baselines
are promoted only after rechecking the passing gate's original digest.

New revisions send check metadata once in the feedback input and diagnostic
facts once in a separate evidence fragment. The rendered request is checked
against the input ceiling after optional detail is compacted.
