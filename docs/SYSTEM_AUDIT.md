# Generic main systemic audit and repair — 2026-11-06

## Scope and evidence

Primary execution chain reviewed: CLI -> workflow preflight/input materialization -> provider -> task state/budget recovery -> machine collection/materialization/worker -> production structure/search/replay/fulfillment -> diagnostics/revision. Cross-checked prompts/docs/contracts and neutral tests. Read the interrupted tr87验证main性能 thread and its actual revision specs/feedback as operational reference, not as game design input. No real provider requests, game-rule edits or game execution were performed in this repair.

Supporting read-only Luna high reviews covered agent_workflow/budget/execution_contract and prompts/tests; machine/production/blueprint/CLI/diagnostics/provenance; search/contracts/plugins/curriculum; engine_probe/generated_level_replay/rule_contract and backend adapters. Legacy trajectory/session/export and harness entrypoints were also inspected for boundary/documentation consistency. This is a code and interface audit with selected fault-injection tests, not exhaustive proof of every backend or dependency.

## Findings and disposition

| Area | Finding | Repair / disposition |
|---|---|---|
| Input handoff | Previous revisions lacked actual API inputs and prompt misstated generation shape | Previous interface repair retained; exact runtime API/hash gates apply; implementation prompt now also states finite JSON/type requirements |
| Config | path/from_task type ambiguity silently ignored a malformed field; malformed contract input could cause AttributeError before structured validation | Explicit key type validation and contract input record validation |
| Dependencies | Reload already checked completed output hashes; direct downstream reads lacked an additional in-run parent status/path/hash check | Added read-time check; final-output tamper test also confirms existing reload protection. Do not claim reload protection was absent |
| Frozen inputs | Same accepted designs repeatedly passed through paid model tasks | New frozen_output={path,sha256} local import, exact byte copy, schema/private-field check, explicit frozen_import provenance, fingerprint/restore; zero model attempts/tokens. Importing a file does not establish that it was independently accepted |
| Network | Safe diagnostics lost OS codes; partial response transfer surfaced as an opaque error | Numeric errno/winerror and exception type/kind only. Incomplete response transport is distinct from model finish_reason=length, with no partial body/address/error text archived |
| Provider config | Ambient provider environment could override explicitly selected config file | Explicit env-file is now authoritative; environment fallback only when no file is selected. Synthetic tests only, real credential file unread |
| Plugin protocol | Wrong dataclass/field/JSON types could create generic exceptions or prevent report persistence | Definite shape errors become output_protocol_invalid/artifact_revision. Boolean flags are real booleans; finite JSON-compatible payload/evidence/actions; generation/coverage and fulfillment guard malformed values |
| Worker report | Invalid production JSON was parsed twice; second parse could abort final reports. Unknown statuses copied unchecked | Parse once, validate schema/status/tasks/feedback and contradictory empty pass, retain invalid file hash ref, persist machine/revision diagnosis |
| Python bundle | Syntax errors reached import worker as an ambiguous load error | ast.parse before materialization; known syntax protocol error archived, no code execution needed |
| Search | truthy non-boolean replay result could mark solved | Only boolean True can certify replay; malformed result stays unknown |
| Generic replay | truthy goal predicate and hardcoded GAME_OVER/LOSE terminal names | Strict boolean goal predicate; optional provider-owned is_terminal_observation; no generic terminal label guessing. Replay exception summary records type, not raw message |
| Trust boundary | Plugin/worker manifests are local integrity checks, not security isolation | Kept explicit: local worker has caller permissions; hashes do not independently prove real environment invocation or authenticate plugin-supplied claims |

## Local frozen import interface

A normal workflow task may add:

```json
{"frozen_output":{"path":"accepted/evidence.json","sha256":"<exact SHA-256>"}}
```

It retains the ordinary task ID, dependencies, inputs, prompt file and output_schema. The prompt is fingerprinted but not sent to a model; the pinned JSON artifact is validated and copied byte-for-byte into the run's artifacts. Other inputs/upstream hashes are checked. Import is explicitly forbidden in trajectory_blind tasks. Accepted provenance is a caller/reviewer responsibility; the generic core only certifies pinned bytes and schema. Imports neither reserve/settle requests nor start the model execution clock. An import-only run does not load provider credentials. Mixed workflows keep the same canonical aggregate ledger and count only actual model requests. A crash after publishing identical pinned bytes can resume without a model request.

For a repair implementation workflow, keep the distinct evidence/rules/blueprint/implementation role mapping but use frozen imports for the three already accepted design artifacts. Do not use a model to repeat those artifacts. Implementation remains a model task with exact production_api/design_validation_api file inputs. Preserve old workflow specs and runs; use new versioned files/directories and do not resume old request fingerprints with changed APIs.

## Supervision and validation behavior

Safe code can enter normal machine-handoff to obtain real structure/search/engine/claim outcomes. Static review need not prove successful play in advance. Definite unsafe behavior or known protocol incompatibility can still block execution. Initial goals require real bound-engine confirmation, not a universal nonempty action requirement.

Two consecutive identical interface failures require an offline effective-input/contract check before another request. This operating rule is not an automated semantic loop detector. Feedback privacy, game semantics and actual engine/source binding still require independent review; do not infer a machine defect from repetition. Normal game errors stay with DeepSeek. No new Sol role is introduced.

## Limits

This audit cannot guarantee no future errors. Unmarked opaque-schema direct workflows can bypass the implementation API gate; callers must use machine-plan and marked implementation tasks. Plugin evidence is not independent truth, and engine trace presence alone is not engine authentication. Replay identity/goal/terminal callbacks must be independently audited against the bound runtime. Backend-specific Tycho/TAAF uppercase action syntax remains inside their adapters; the DesignProductionPlugin API accepts game-owned actions/states. No external harness installation, live model behavior, TR87 unit generation or cross-game performance was evaluated here.
