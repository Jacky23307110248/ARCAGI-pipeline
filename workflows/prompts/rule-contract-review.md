# Rule-contract proposal after evidence freeze

Use only the frozen trajectory ledger(s), version-bound source findings, version-bound original-engine probe suite, and source-binding manifest. Keep the evidence roles separate:

- A trajectory claim says what was visible or what an analyst hypothesizes from the saved run. It does not establish private reasoning or engine truth.
- A source finding says what the exact bound code/data implements. It does not establish that a player learned that rule.
- A probe case says what the original engine did under the stated setup and actions. Cite its case ID and input/result hash.
- If evidence is absent, conflicting, version-unbound, or outside tested reachable configurations, preserve status unknown or conflict.

Return one JSON object with schema_version arcagi-rule-contract/v1, game_id, game_version, source_sha256 from the source-binding manifest, and a non-empty rules array. Every rule must contain:

- rule_id: unique stable identifier.
- statement: concise behavior statement.
- condition: exact trigger or precondition.
- state_changes: explicit list, possibly empty only when supported evidence confirms no state change.
- boundary_cases: trigger and non-trigger boundaries, including repeated entry where relevant.
- reachable_interactions: interactions shown reachable by the supplied level data or dynamic probes; do not infer reachability from code alone.
- status: supported, conflict, or unknown.
- evidence_refs: claim_id, finding_id, or probe case_id from supplied frozen inputs. Supported/conflict rules need at least one citation. Unknown rules may have an empty list.

Do not add source-derived conclusions to a trajectory ledger. Do not claim a complete rule model from a single mechanism slice. The contract-freeze command checks structure and hash bindings; an independent reviewer must still assess whether the cited evidence supports the rule semantics.
