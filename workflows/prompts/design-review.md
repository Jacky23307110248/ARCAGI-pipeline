# Review design before implementation

Check rule/evidence consistency, original game functions and progression,
construction intent, and planned behavioral requirements. Return the declared
review schema and concrete responsible targets. Review all slots together.

Keep three categories separate: source/trajectory facts; proposed construction
and observation bindings; actual execution evidence. Proposed adapter fields
must be identified as planned, with a source-state or rendered-observation basis
and a requirement for implementation and execution validation. Their absence
before implementation is not itself a design failure. Do reject invented facts,
unsupported source mappings, contradictory predicates and untestable promises.
Require an evidence-backed quantity and coherent proposed alias; do not require
its concrete Python attribute path or an adapter execution receipt before the
implementation stage. Those remain explicit implementation obligations.

Retain original function/progression anchors while revising check designs. Do
not resolve missing runtime evidence by deleting required game behavior or merely
renaming the function to match weaker checks. Such behavior needs implementation
and later validation, or an explicit unresolved design limitation. Semantic
agreement in this review does not certify execution or learning benefits.

Runtime binding and behavior checks occur after implementation. Pass a coherent
executable plan to implementation with explicit obligations; return unresolved
design contradictions with concrete findings, without demanding outputs from
an implementation stage that has not run yet.
# Phase review contract

When phase-review/v1 is declared, classify each finding using the supplied exact
API. Only design_contradiction blocks implementation. Carry required implementation
and runtime work by an existing mandatory obligation_ref; do not demand its result
before implementation. Research hypotheses do not discharge behavioral commitments.
Check capability support and explicit commitment changes without deleting original
functions to fit a weaker check.

