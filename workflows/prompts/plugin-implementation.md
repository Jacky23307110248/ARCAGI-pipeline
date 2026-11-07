# Implement the reviewed construction contract

This file documents the explicit legacy `design-production/v1` interface.
For playable generated-unit delivery use `generated-game-implementation.md`
and execution contract `generated-game/v2` with its exact API inputs.

Use only supplied evidence, rule hypotheses, blueprint and exact environment interface. Game rules, generator, search model, environment adapter and fulfillment checks belong in the implementation output, never generic core.

Return {"entrypoint":"plugin.py","factory":"create_plugin","files":[{"path":"plugin.py","content":"..."}]}. All file paths are relative, all content is text. Include helper files or import explicitly declared external dependencies. No credentials, installation commands or hidden workspace reads. The worker supports sibling and relative imports.

The factory returns a DesignProductionPlugin:
- generate(FrozenInputs, seed) returns Sequence[GeneratedTask], for example a list of GeneratedTask(task_id, payload, construction). The sequence must actually contain tasks covering all declared blueprint slots; metadata listing slots does not count as generation.
- construction contains slot_id, rule_ids, config, rationale and claims [{id,statement,check}]. Slot coverage and selected rules must match the reviewed blueprint. No fixed number of tasks per slot is required.
- validate(task) returns structural errors.
- search(task, cap) returns ModelSearch(candidate/unsolvable/unknown, actions, reason, complete_model, exhausted, evidence). Actions and state are game-owned. A bounded or incomplete search returns unknown; candidate is not solved.
- replay(task, candidate) executes in the bound environment and returns EngineVerdict(accepted, reason, trace, failed_action_index, binding). Trace and binding are mandatory for accepted strict production, including an initial-goal case. A model's own transition function is not independent environment verification.
- assess(task, candidate, verdict) returns FulfillmentVerdict(checks, reason), where checks is a dict keyed by exactly every construction claim ID: {claim_id: {"status": "pass" | "return" | "unknown", "evidence": {"kind": "<check method>", "data": <non-null JSON>}}}. Address every construction claim with status pass/return/unknown and evidence. Use observable probes/comparisons where applicable; necessity claims require an appropriate counterfactual or exhaustive argument, not just successful play. Preserve unknowns rather than fabricate proof.

Import these types from arcagi_pipeline.production. Follow its dataclass signatures. Files execute as ordinary local Python with the caller's permissions in a fresh child process; the worker is not a security sandbox. Keep writes in the provided execution workspace. The supervisor separately reviews code and claim evidence. Failures return revision_packet.json as an ordinary workflow input; revise the responsible artifacts without relabeling observations or changing unrelated evidence.

Fulfillment check evidence is an object `{kind: <open descriptive string>, data: <non-null JSON>}`. Kind describes the check method; data records observations, comparisons or bound artifact references. These are auditable plugin records, not a core proof of their truth. Bare strings and missing data are rejected.

The task must supply the actual current production_api and design_validation_api files as input contents, not just paths mentioned in feedback. These are machine-interface documentation, not game hints. If instructions conflict with those APIs, report the interface conflict rather than guessing. An empty candidate action sequence may be accepted only when the actual bound engine confirms the initial/reset state is already a goal; a payload flag or local model assertion is insufficient. There is no universal requirement to perform a nonempty action sequence.

Payloads, construction, actions, evidence and result fields must be finite JSON-compatible data. Use the actual declared dataclass result types and boolean flags, not strings standing for booleans. validate returns an error-string sequence, never a bare string. These are serialization/interface requirements, not game constraints.


Controlled production profile:
- The implementation task supplies exact production_api, design_validation_api and runtime_api files. Return create_plugin(context), using this context instead of discovering Python paths or copying original source into the bundle.
- Load declared source IDs through context.load_source(id). The source IDs and hashes are provided as deployment materials, not invented by the plugin.
- A replay adapter must expose source_object: an instance of a class in the actually loaded source module (subclasses allowed), or that module/a function it defines for functional backends. Its reset/step methods return JSON-compatible observations and invoke that original implementation. Wrap it with context.session(adapter,source_id). Record the reset result followed by each step result as EngineVerdict.trace; include session.session_id in binding. Do not replace the environment with a reimplementation or a self-asserted binding.
- Supply validate_unit(tasks,task_records,context) and adapter_smoke(context), each returning UnitVerdict(status,checks,reason). Each named check has status and non-null typed evidence. A pass verdict cannot contain failed or unknown checks. The smoke must really reset and step a context session; include its session_id in evidence.data. Distinguish interface/process success from game goals.
- Workflow feedback supplies actual check IDs, statuses and hash-bound results. When revising, return diagnosis {stage,root_cause_status:confirmed|unconfirmed,candidate_causes,completed_checks:[{check_id,evidence_ref,observed_status}],excluded_causes,missing_evidence,next_check:{operation},fact_refs}. Completed checks must match actual supplied machine records. Use a declared next operation: rerun_declared_checks, revise_and_rerun, search, engine_replay, validate_unit, adapter_smoke, or validate_structure. A next operation runs through the next bounded implementation/check cycle, not arbitrary shell commands.
- Unconfirmed causes remain your diagnostic responsibility. Do not request a supervisor on that basis, invent evidence, or silently change frozen observations. Machine infrastructure exits are controlled by independently repeated machine checks.
