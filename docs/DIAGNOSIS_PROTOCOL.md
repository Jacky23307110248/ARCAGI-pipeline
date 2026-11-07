# Diagnosis protocol and compatible recovery

`arcagi-diagnosis/v1` is the shared contract for model requests, schema validation, feedback and correction workflows. `diagnosis_protocol.py` owns its schema, operation vocabulary and reference checks.

## Request contract

Tasks requiring diagnosis carry `diagnosis_contract` with `schema_version`, a hash-pinned `feedback_ref`, and `allow_stage_revision`. The API request includes the complete `required_output_schema` and a rendered `diagnosis_contract` containing executed checks, evidence references, allowed operations, eligible revision targets and a complete example using this run's references.

Diagnosis fields:

| Field | Value |
|---|---|
| `stage` | Stage name |
| `root_cause_status` | `confirmed` or `unconfirmed` |
| `candidate_causes` | Nonempty array of concise cause statements |
| `completed_checks` | Nonempty array of `{check_id, evidence_ref, observed_status}` copied from executed check records |
| `excluded_causes`, `missing_evidence` | Arrays of statements |
| `next_check` | `{operation, reason?}`; operation is a member of the vocabulary below |
| `fact_refs` | Nonempty array of `{path, sha256}` evidence objects |
| `revise_task_id` | Optional eligible production stage; available during normal production diagnosis |

Operation vocabulary: `rerun_declared_checks`, `revise_and_rerun`, `search`, `engine_replay`, `validate_unit`, `adapter_smoke`, `validate_structure`. These name diagnostic intent. `completed_checks` references already executed checks; the production controller schedules the declared checks in dependency order.

Validation gathers structural and reference errors together. Feedback identifies field paths, expected types or values, exact executed status values and admissible evidence. Valid references are hash checked. Missing check context is a configuration error detected during preflight.

## Early validation and correction receipts

Each task's diagnosis gate runs immediately after output persistence and on cached output reuse, before dependent tasks execute. Payload structure and diagnosis metadata have separate checks. A valid game payload with invalid diagnosis retains its original bytes and enters a bounded `diagnosis-correction` workflow.

Correction requests receive the prior diagnosis, existing machine feedback and complete contract. They return a diagnosis object. Successful corrections produce an immutable receipt binding the original artifact, contract digest, correction output, workflow spec and completed workflow record. The controller applies that diagnosis when checking metadata; execution continues to use the original game-artifact bytes. This preserves hashes used by existing implementations.

Requests, failures and retries use the existing aggregate ledger. Correction attempts have versioned directories and a persisted execution intent. Recovery reuses a committed receipt or a completed correction output. `protocol_attempts_used` is added to the production revision count for the configured revision limit. `diagnosis_max_output_tokens` defaults to 8192 and can be configured within provider and budget limits. Resource exhaustion produces `limited`.

Normal stage revisions rebuild affected payloads and descendants; receipts for affected tasks are invalidated. Unaffected byte-identical imports retain compatible receipts. Status and resume checks verify receipt contents, output hashes, origin records and evidence references.

## Recovery entry point

```text
control-recover --source-run SOURCE --checkpoint-workflow COMPLETED_WORKFLOW
                --run-dir NEW_RUN --previous-core ARCHIVED_PACKAGE
                --history-manifest INVENTORY --additional-revisions 100
```

This offline operation requires a paused source run and settled requests. It checks the archived core, predecessor baselines, history inventory, selected workflow spec, task fingerprints, output hashes and passing review gates. It compares domain task definitions while accounting for known machine-generated diagnosis wrappers.

Compatible completed stages are imported byte-for-byte. Stages with legacy diagnosis metadata receive the current contract and enter correction during the next resume. The recovery receipt records all reused artifacts and the selected complete version. Start time, aggregate ledger and historical revision count carry forward; the explicit additional revision allowance sets the new cumulative limit. The predecessor is retired and the successor is prepared as `paused`.

The candidate payloads proceed to normal structural, search, engine, fulfillment, unit and adapter checks during resumed production. Execution results determine subsequent artifact revisions.
