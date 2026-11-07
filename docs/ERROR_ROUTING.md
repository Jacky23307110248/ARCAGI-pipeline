# Error routing, facts and continuation

## Responsibility and evidence

Failures have two separate dimensions: the stage that observed the failure and the current attribution. Core structured codes choose initial routes, never exception-message keywords or retry counts. artifact / artifact_revision means the game-owned output failed a declared check. runtime / machine_diagnosis means environment/protocol prerequisites require inspection; it does not authorize changing core code. undetermined / diagnosis_required means evidence is insufficient. Engine mismatch, module import failure, unexpected exception, timeout and worker crash default to undetermined. Plugin attribution hints are untrusted claims. Neither repetition nor a plugin's claim escalates a failure into permission to edit main.

Model JSON/schema/private-field violations map to artifact_revision when the workflow already defines the output protocol. A configured budget limit or provider transport/HTTP failure maps to machine_diagnosis. A contradictory schema/configuration is a preflight runtime issue. Provider protocol/envelope failures without a specific code remain undetermined. Unknown bounded/incomplete searches are limited_result events, not machine faults. Exhaustion only yields model-unsolvable under the existing complete-model/exhaustion gate.

## Append-only archive

Default location is `<output-or-run-parent>/error-archive` (for preflight, the config/spec parent). CLI invocations calculate this before dispatch. Set --log-dir explicitly to keep multiple attempts in one archive. Direct Python entrypoints accept log_dir=, previous_run=, pending_events=; nested workflow/production/worker calls share a run, each with a separate attempt ID. Existing workflow request attempt counts and token charges are retained unchanged.

Collections: runs, attempts, events, attributions, outcomes, packets, resolutions, attachments. Each record has a random ID; files are published exclusively, so existing files are not overwritten. Facts include time, run/attempt/task/stage, code/kind, known expected/observed data, file hashes/trace references, and exception type/location. Unknown fields stay absent/null; no root cause is invented. Initial attribution and each reclassification are separate records. Every attribution produces its own immutable route packet; show exposes the current packet so a corrected route does not depend on stale instructions. Final report attachments bind trace/result files by hash, with integrity shown by the reader. Attribution records bind the fact hash, and the reader detects later fact changes. A stale supersedes ID is rejected; any concurrent attribution fork is surfaced for review rather than silently selected.

Fact files are append-only through this API, not tamper-proof storage or an OS access-control boundary. Private provider bodies, environment variables, raw exception messages/locals, stdout and stderr are not archived. Structured fields are additionally scrubbed for sensitive keys and credential patterns. Existing allowed gameplay artifacts are still inputs, but diagnosis logs reference their hashes instead of copying them. Generated code runs with local permissions; this is not a security sandbox.

Every failure also creates a route packet in archive/packets, including failures before an execution output exists. After handoff, machine output includes artifact_revision.json, machine_diagnosis.json and diagnosis_required.json plus a backward-compatible revision_packet.json. These packages are ordinary workflow inputs. The instruction only states the recorded route and facts; it does not supply a game-level diagnosis or repair. The umbrella packet includes all routes, so a reviser must use the indicated lane. No automatic model calls/retries or budget changes occur.

## Inspect, reclassify and continue

```powershell
arcagi-pipeline diagnostic-show --log-dir <archive> --event-id <event-id>
arcagi-pipeline diagnostic-attribute --log-dir <archive> --event-id <event-id> --category artifact --basis <evidence-list.json> --source independent_review --supersedes <current-attribution-id> --confidence confirmed
```

Show is strictly read-only and lists facts, attribution history/current route, successor runs, outcome records and resolution evidence integrity. Reclassification requires a nonempty JSON list of structured basis records; callers must provide actual observations/probe/file evidence. Plugin-origin classification is refused. These records preserve human/reviewer judgments; the core does not certify their semantic truth.

```powershell
arcagi-pipeline machine-run --config <config> --workflow-run <new-or-resumed-workflow-run> --output-dir <new-execution-output> --log-dir <same-archive> --previous-run <run-id> --pending-event <event-id> --env-file <explicit-provider-config>
```

Unchanged workflow failures can use existing --retry-failed, preserving charged attempts. Changed artifacts/specs require a new revision workflow and a new run directory. Output directories are never reused. Continuation flags link runs/events, not instructions to skip a failed check, reset budget, or infer resolution. Multiple --pending-event flags are supported. Previous-run and event IDs must exist in the same archive. Running a passing successor does not silently resolve earlier events.

## Resolve with subsequent check evidence

Use diagnostic-resolve --log-dir <archive> --event-id <id> --successor-run <new-id> --check-file <check.json>. The successor must explicitly list the event in pending_events. A check file must identify event_id and run_id, have status=pass and a nonempty check description, and include evidence_refs [{path,sha256}]. Evidence files must exist and match their hashes. Failed checks, unrelated successor runs and evidence-free claims cannot resolve an event. The archive saves resolution records rather than rewriting the fact. If a check or its evidence changes later, show marks evidence_intact=false and no longer presents the event as resolved. Independent validation of the check's meaning remains the reviewer's responsibility.

## Failure to record

Archive writes fail explicitly as ArchiveWriteError. CLI emits diagnostic_archive_write_failed with facts_saved=unknown and archive_complete=false and exits 2; it never claims the archive was completed. Temporary records are published atomically (exclusive Windows rename or POSIX hard link). Interrupted unpublished files are not treated as valid facts. Recover the storage/permissions first. If a lower-level worker cannot archive an error, the parent must also preserve the failure rather than report successful production.

## Compatibility and local validation

Existing production feedback fields remain; event_id, run_id, attempt_id, category, route, attribution_id, archive and packet_id are added. Raw exception strings have been replaced by exception type and recorded locations. revision_packet is version 2 and retains machine/production fields with explicit routing. Workflow budget counters and completed-task reuse semantics remain unchanged.

All examples and fault injection in this delivery are synthetic/offline. Tests cover fact immutability, reclassification, integrity, redaction, preflight failures, provider failure, schema failure/retry budgets, timeout/crash/import failure, limited results, route packets, continuation and resolution. This change does not update another workspace or execute a real-game/model probe.

## Accepted local scope

The error-routing checkpoint was reviewed independently. See [the release check](RELEASE_VALIDATION.md) for the public regression results.


## Probe supervision boundary

See [SUPERVISION_PROTOCOL.md](SUPERVISION_PROTOCOL.md). Detailed internal audits must not be forwarded to model revision workflows. Model-visible feedback identifies failed artifact items and declared checks without adding trajectory locations, corrected observations, game explanations or repair suggestions. This is an operational protocol, not an automated semantic filter. Existing execution facts remain governed by their declared interfaces.
