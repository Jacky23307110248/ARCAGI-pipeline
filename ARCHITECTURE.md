# Architecture

## Separation of responsibilities

The pipeline has a game-neutral outer layer and versioned game plugins. The outer layer records provenance, evidence, contracts, workflow stages, validation conclusions, and harness sessions. A plugin owns the game's state representation, legal actions, transition model, level schema, objective check, and any source-specific engine binding.

The generic contracts keep game identity and game-owned data in extension fields. The core does not prescribe a board shape, action set, mechanism list, or number of levels.

## Evidence workflow

A game workflow can register source and run materials, export a source-blind trajectory packet, freeze a ledger of observed behavior and cited claims, audit a bound source version, run engine probes, review a versioned rule contract, design a course, implement a plugin, generate levels, and record validation results. These are separate artifacts with hashes and explicit evidence references.

Trajectory actions and visible observations support behavioral analysis. Only notes explicitly saved during the original run can support claims about that player's contemporaneous rule understanding. Source findings and probes are kept separate from the frozen trajectory ledger. Unsupported, conflicting, or untested rules remain unknown.

## Search and engine replay

Search is provided through a generic domain interface. A found solution is reported as solved only when the bound engine accepts its replay. Unsolvable requires complete exhaustion of the declared finite model. Search limits, incomplete state abstractions, model/engine differences, or unsupported nondeterminism produce unknown.

The generic generated-level replay runner receives a plugin-supplied level loader and a version-bound engine probe. It checks source/runtime binding and input hashes before replay; game-specific loading and observation-to-goal interpretation stay inside the plugin.

## Harness adapters

Tycho and Duck adapters construct and inspect runs against externally installed upstream repositories. The pipeline stores session metadata and references to native artifacts; it does not copy upstream runs into the generic code package. External repository paths and revisions are explicit inputs. Credentials are passed through the process environment and are not written to session records.

## Current scope

The synthetic demo is an offline integration example, not evidence of cross-game generalization. The main snapshot contains no original-game plugin, game-specific campaign, or historical run record. A new integration must arrive as an explicit plugin and its own evidence package, then pass source-bound probes, generation checks, search, and engine replay before its results are claimed.