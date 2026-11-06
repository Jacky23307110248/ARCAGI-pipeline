# Verified pseudo-LS20 sample outputs

Seed 22; three complete seven-level units (21 levels). Archived clean-directory validation: PASS_ALL_THREE; BFS cap 250000 per level. report.json preserves the complete validation data in compact JSON. No new game validation was run for this upload.

packages/unit*_public.json are playable public packages. packages/unit*_supervisor.json and report.json contain answers/witnesses; withhold them from a playing agent.

Three fixed topology profiles: different seeds do not guarantee different layouts. Source/implementation hashes, dependencies, reset policy, searches, replays and negative controls are recorded in report.json. The producing implementation is identified by these hashes; the existing GitHub CP9 main is not claimed to reproduce these samples. This commit adds outputs only, without updating machine code.
