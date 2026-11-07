# Generic main working machine

## Gold standard

The core must not encode any game's rules, level count, slot schedule, mechanism names, board or action format. A unit can reuse, omit or combine previously selected rules in any evidence-supported order. Open-text purpose/rationale and game-owned extensions express that inference; the core validates references and execution, not a preset teaching theory.

## Stages and what passing means

WorkflowRunner handles model requests, dependency inputs, budgets, hashes and resumable JSON outputs. Model inference remains the provider's responsibility. Use separate workflow tasks for evidence, rule hypotheses, blueprint and implementation. Source findings and historical in-run knowledge remain distinct. The machine config names the four task IDs; other analysis/review tasks may precede them. Before any model call, planning checks distinct role IDs, positive resource limits and explicit upstream artifact inputs (rules read evidence, blueprint reads evidence/rules, implementation reads rules/blueprint). Field examples are documented in workflows/prompts/evidence-to-rules.md, course-design.md and plugin-implementation.md.

The machine validates evidence/rule/slot references and earlier prerequisites. It materializes the implementation's text files, checks paths and file hashes, then loads its factory in a separate local Python process with a declared timeout. No pip install, shell-command generation or credential discovery occurs. Dependencies must already be supplied by the caller. This is ordinary local code execution, not a security sandbox: review generated code before executing untrusted output.

Production separately records structure checks, model candidate/unknown/exhaustion, actual environment replay, and fulfillment of construction claims. Strict acceptance requires replay trace/binding and an assessment addressing all claims with evidence. Selected rules must match the declared slot and generated tasks must cover every slot; the number of tasks per slot is open. Rule selection/claim rationale must be reviewed against source evidence, beyond these structural checks.

Fulfillment evidence is supplied by the plugin and is not an independent scientific review. The supervisor checks its quality and whether the environment adapter is real and correctly bound. A successful witness does not establish rule necessity or learning gains. Bare strings labeled pass cannot replace recorded check evidence. Unknowns remain unknown.

## Commands

Install the core from this repository or set PYTHONPATH=src. A machine config is JSON:

```json
{"workflow_spec":"workflow.json","roles":{"evidence":"evidence-task","rules":"rules-task","blueprint":"blueprint-task","implementation":"implementation-task"},"seed":0,"cap":250000,"timeout_seconds":300}
```

Task IDs are caller-defined and must be distinct. The workflow spec uses the existing arcagi-agent-workflow/v1 format; downstream task inputs use from_task and depends_on. Model, request count and token limits are explicit in that spec. Blueprint fields and plugin method signatures are in the prompts and blueprint.py/production.py. Object status tags and IDs are interface vocabulary; no game mechanism is a core field.

```powershell
arcagi-pipeline machine-plan --config campaign/machine.json
arcagi-pipeline machine-run --config campaign/machine.json --workflow-run outputs/model-attempt1 --output-dir outputs/execution-attempt1 --env-file <explicit-provider-config>
```

For code review before execution, run the existing workflow-plan/workflow-run commands first, inspect its implementation artifact, then call:

```powershell
arcagi-pipeline machine-handoff --config campaign/machine.json --workflow-run outputs/model-attempt1 --output-dir outputs/execution-attempt1
```

Handoff reads only complete workflow task artifacts whose current hashes match the recorded outputs. It does not call a model. materialized files and their hashes are saved beside machine.json, production/production.json, and revision_packet.json. Worker failures preserve exception type and code location without copying stdout/stderr or private provider output into logs. Successful processes returning unknown/rejection still yield machine status unknown/return.

Feed revision_packet.json as an input with role revision_feedback to the responsible task in a new revision workflow. Include the prior artifacts in that task's inputs, preserve unrelated evidence, and use new run/output directories. No hidden retries or automatic changes to budgets occur. Existing workflow-run --retry-failed resumes failed unchanged requests; it is not a replacement for a changed revision workflow. A provider failure retains the normal workflow error/state; no implementation executes until the required task outputs are complete.

## Compatibility

production-run retains its old engine-only behavior, explicitly labeled completion_scope=engine_only. Add --evidence and --require-design to require blueprint/claim checks. FrozenInputs gains optional evidence; GeneratedTask gains optional construction. DesignProductionPlugin extends ProductionPlugin with assess. Existing plugins can continue using the engine-only API; they cannot claim strict design completion without implementing the new interface. Existing course/tracker APIs remain available.

## Offline neutral exercise

```powershell
python -m examples.neutral_machine.demo --output-dir outputs/neutral-main
```

This uses a scripted provider with explicitly synthetic evidence and small reference environment. It exercises four actual WorkflowRunner outputs, materialized multi-file code (including relative imports), construction/search/replay/claim gates and revision artifacts. It does not test model reasoning or any real game. Tests additionally cover variable slot counts, nonmonotonic selection, missing checks, hash tampering, path escapes, environment rejection, unknown search and a second workflow consuming the first revision packet.

The accepted core will be frozen with hashes and independently reviewed. Updating another workspace and running a real-game probe require separate release authorization.

Fulfillment check evidence is an object `{kind: <open descriptive string>, data: <non-null JSON>}`. Kind describes the check method; data records observations, comparisons or bound artifact references. These are auditable plugin records, not a core proof of their truth. Bare strings and missing data are rejected.

## Diagnostic routing upgrade

See ERROR_ROUTING.md for append-only facts, separate attribution and continuation. Failed/unknown feedback now carries event/route references. revision_packet.json v2 preserves machine/production data and groups artifact_revision, machine_diagnosis and diagnosis_required; do not treat every packet as permission to edit game implementation. Pre-execution failures also have archived route packets.


## Probe supervision boundary

See [SUPERVISION_PROTOCOL.md](SUPERVISION_PROTOCOL.md). Detailed internal audits must not be forwarded to model revision workflows. Model-visible feedback identifies failed artifact items and declared checks without adding trajectory locations, corrected observations, game explanations or repair suggestions. This is an operational protocol, not an automated semantic filter. Existing execution facts remain governed by their declared interfaces.


## Exact implementation API inputs (interface handoff correction)

Implementation tasks now declare `execution_contract: "design-production/v1"` and include exactly one path input of role `production_api` and one of role `design_validation_api`. Each must pin `sha256` and contain the exact current runtime production.py / blueprint.py bytes. They are sent as actual file contents in model input, not merely mentioned in a prompt. machine-plan/run require this marker; workflow-plan/run validate marked tasks before calls and again immediately before each request. Old machine specs must be migrated into new files/runs, never edited under an existing request fingerprint. The output schema naming entrypoint/factory/files also triggers this requirement in direct workflow calls. Ordinary non-machine workflows remain compatible; an unmarked direct workflow with an opaque object-only schema can still bypass this gate and must not be used for implementation.

The implementation prompt now states Sequence[GeneratedTask] and the exact claim-keyed FulfillmentVerdict.checks mapping. Empty actions are not universally invalid: the actual original engine must confirm an initial/reset goal before acceptance. Runtime trace/source identity and semantic correctness still need execution and independent review; this gate does not prove them.

After execution-safety review, use the existing handoff/production validator to obtain structured interface and execution results. Do not require a speculative static proof of every successful play before allowing normal validation. A known protocol violation can still be rejected offline. Do not provide game fixes in feedback.


## System audit release

See [SYSTEM_AUDIT.md](SYSTEM_AUDIT.md) for frozen_output imports (zero model requests), stricter plugin/worker result protocols, provider diagnostics/config selection, search/replay boolean gates and migration. Frozen imports are schema/provenance handoffs, not semantic acceptance. Existing model-generated results remain historical evidence; new specs/API hashes require new runs.
