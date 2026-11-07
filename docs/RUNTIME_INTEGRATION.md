# Generic runtime integration

This version merges the generic parts of the older isolated runner without importing any game-specific analysis, prompts, levels or plugins.

## Workflow limits and provider receipts

Finite defaults remain 50 attempts / 50,000 tokens, but these are defaults, not hard upper bounds. budget may explicitly set max_attempts, max_tokens, max_input_tokens_per_request, max_output_tokens_per_request, max_elapsed_seconds and allow_rework_reserve. All numeric limits must be positive integers. provider.timeout_seconds is positive finite; actual HTTP timeout is capped by remaining workflow elapsed time. Existing spec hashes protect these fields on resume. Token input reservation uses conservative UTF-8 byte ceilings rather than claiming exact tokenizer counts.

The provider requests JSON mode. Truncated (finish_reason=length), empty, malformed and schema-invalid outputs fail the artifact protocol gate. Usage is still charged, and only sanitized content length/hash, finish_reason and response model are stored in attempt metadata; no raw failed response or private reasoning is stored. The requested model is explicit in the workflow; actual response_model is recorded only if supplied by the endpoint. An alias is not proof of a precise model revision.

## One aggregate ledger

budget.aggregate_ledger is an explicit path relative to the workflow spec (absolute paths are also supported). The file must already exist with ceilings: requests, total_tokens, input_tokens_per_request, output_tokens_per_request, elapsed_seconds, and optional rework_reserve_tokens. Existing requests_used, total_tokens_used, input_tokens_used, output_tokens_used, unreported_usage_tokens_reserved and historical attempts are retained.

New request IDs and receipts live under runtime_requests in that same file. Before a model call the runner locks the ledger and reserves input ceiling plus output cap, counting the request. In-flight reservations count toward limits across workflows. Normal work cannot spend the rework reserve unless allow_rework_reserve=true is explicit. Settlement charges reported total usage or, if absent, the full reservation. Unknown charges are also tracked in unreported_usage_tokens_reserved; that amount is already included in total_tokens_used and must not be added twice. Repeated settlement for the same ID never charges again. Authoritative later adjustments to old unknown receipts are not automatic.

A process-scoped OS file lock protects replacement; process death releases the lock, not the logical reservation. A separate workflow-run lock prevents two processes from spending from the same local state concurrently. On recovery, staged outputs settle once; running requests reconcile against settled receipts; reservations orphaned before the local state save are recovered conservatively under their original request IDs. Spec/provider/input fingerprint checks remain in force. These mechanisms preserve known historical charges rather than recreating a fresh budget. Ledger writes use atomic replacement and never log provider credentials.

## Native engine runtime profile

`game-bind --runtime-profile raw-arcengine` requires arcengine, numpy and pydantic rather than the optional wrapper distributions. Versions must match the discovered distribution inventory. Installed files are fingerprinted, and the actual resolvable module origin must belong to the selected distribution. Duplicate incompatible versions, missing source files and shadow metadata remain unverified. Neutral fixtures use the host's real dependency versions; negative tests retain conflicting-version and wrong-module-origin cases.

The profile is generic to native engine implementations and contains no game identity or rule fixture. The game-specific adapter still belongs in campaign output. Existing tycho/duck profiles remain available.

## Stage boundaries

Stage 1 is offline: new APIs are tested with synthetic provider responses and neutral distributions only. An independent review must pass before the new baseline is copied to the isolated workspace. Old output artifacts and historical fees are preserved and excluded from new model inputs. Live requests and real-game code generation occur only after that stage is released.


## System audit additions (2026-11-06)

An explicit --env-file is authoritative for provider key/base URL/model configuration; ambient provider environment variables are used only when no file is supplied. Explicit workflow model still overrides provider defaults. Transport diagnostics retain only exception type and numeric errno/winerror; incomplete HTTP response transfer is not evidence of model finish_reason=length. See SYSTEM_AUDIT.md for pinned frozen_output imports, which do not reserve or charge model requests and allow frozen design handoff without paid passthrough.
