# Trajectory-only rule analysis

Use only the supplied `arcagi-trajectory-packet/v1`. Do not open source code, level files, other runs, transcripts, excluded reasoning fields, or later audit documents. The packet's `evidence_id` values are the only valid evidence references.

Keep three kinds of statement separate:

- `direct_observation`: a fact visible in an observation, feedback field, or run outcome. Cite a trace evidence ID for observations/feedback or an `outcome_evidence` ID for run outcomes.
- `gameplay_recorded_inference`: an inference explicitly written in a saved note during the original run. Cite at least one `saved_note` evidence ID. Never infer what the original player believed from its actions.
- `post_run_hypothesis`: your own hypothesis formed after the run from the supplied observations/actions. Cite trace evidence, list at least one alternative explanation, and give a prediction that distinguishes them. Do not describe this as the original player's thought or learned rule.

If the packet says reasoning material was not imported, say it is unavailable in this analysis; do not claim the original run had no reasoning. Do not treat action sequences as a substitute for reasoning. For a legacy packet with observation_encoding, first reconstruct grid-delta/v1 observations from each row base_evidence_id and cell updates; do not interpret a delta as a complete board. You may use arcagi_pipeline.legacy_evidence.expand_trajectory_observations on the supplied packet.

Return JSON with this shape:

```json
{
  "schema_version": "arcagi-trajectory-claims/v1",
  "run_id": "<packet run_id>",
  "game_id": "<packet game.id>",
  "packet_sha256": "<SHA-256 printed by trajectory-export>",
  "claims": [
    {
      "claim_id": "claim-001",
      "statement": "...",
      "kind": "post_run_hypothesis",
      "evidence_refs": ["ev-000001"],
      "alternatives": [],
      "prediction": null,
      "confidence": 0.0
    }
  ]
}
```

For `post_run_hypothesis`, `alternatives` must contain at least one alternative and `prediction` must be a non-empty string. Confidence, when supplied, must be between 0 and 1. Do not add source findings or engine-probe results to this file.
