# Independent source audit (after trajectory freeze)

Input is one arcagi-source-audit-task/v1 JSON file. Read only the exact game environment files listed under materials, resolving each relative path under source_root. Confirm that the observed SHA-256 matches the task before relying on a file. The task game.version, environment identifier, harness commit, and runtime package versions define the version being audited. Do not substitute a newer checkout or infer that a similarly named game is the same version.

The trajectory ledger in the task is frozen. Do not edit it, and do not describe source findings as rules the player had learned. Actions establish observed behavior, not private reasoning. Treat a gameplay-recorded inference as historical only when the frozen ledger cites a note explicitly saved during that run; all other interpretations are post-run comparisons.

For each mechanic or rule, record:
- the code/data condition that triggers it and the resulting state changes;
- boundary cases and interactions that can occur in the listed game data;
- a precise source location (relative path and one-based line range);
- whether the source finding confirms, extends, conflicts with, is not covered by, or cannot be compared with a frozen trajectory claim;
- the trajectory claim IDs used for a comparison, or mark uncovered_by_play: true when the run does not cover it.

Do not infer reachable combinations from source syntax alone when level data or runtime constraints rule them out. Check material_coverage in the task. If separate level data is absent, do not claim that a code-level combination is reachable in the game. Mark uncertain, unreachable, or version-unbound behavior unknown. Never add source-derived rules to the trajectory ledger.

Return exactly one JSON object with schema arcagi-source-audit-findings/v1:
{
  "schema_version": "arcagi-source-audit-findings/v1",
  "run_id": "<task run_id>",
  "game_id": "<task game.id>",
  "game_version": "<task game.version>",
  "task_sha256": "<SHA-256 of the exact task JSON file>",
  "trajectory_digest": "<task trajectory.digest>",
  "findings": [{
    "finding_id": "src-001",
    "statement": "Concise source-backed rule statement.",
    "source_version": "<task game.version>",
    "source_refs": ["<material_id from task>"],
    "location": {
      "path": "<relative path from the cited material>",
      "start_line": 1,
      "end_line": 3,
      "symbol": "optional function, class, or data key"
    },
    "condition": "The exact precondition or data condition.",
    "state_changes": ["Explicit state changes, or an empty list."],
    "boundary_cases": ["Relevant tested or code-derived boundaries, or an empty list."],
    "reachable_interactions": ["Interactions supported by the provided level data, or an empty list."],
    "uncovered_by_play": true,
    "trajectory_relation": "not_covered",
    "trajectory_claim_refs": []
  }]
}

Use only these trajectory_relation values: confirms, extends, conflicts, not_covered, unknown. For the first three, cite one or more frozen trajectory claim IDs and set uncovered_by_play to false. For not_covered, cite no claim IDs and set it to true. Keep uncertain comparisons as unknown; do not force a match.
