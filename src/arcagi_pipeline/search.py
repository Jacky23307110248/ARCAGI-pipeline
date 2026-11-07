from __future__ import annotations

from collections import deque
from typing import Any

from .contracts import SearchDomain, SearchResult


def exact_bfs(domain: SearchDomain, *, max_expansions: int | None = None) -> SearchResult:
    """BFS over a declared deterministic model; only original-engine replay certifies a path."""
    if domain.deterministic is not True:
        return SearchResult("unknown", reason="nondeterministic_domain")
    if max_expansions is not None and (isinstance(max_expansions,bool) or not isinstance(max_expansions,int) or max_expansions < 0):
        raise ValueError("max_expansions must be non-negative or None")

    initial = domain.initial_state()
    start_key = domain.state_key(initial)
    states = {start_key: initial}
    parent: dict[Any, tuple[Any, Any] | None] = {start_key: None}
    queue = deque([start_key])
    expanded = 0
    goal_key = start_key if domain.is_goal(initial) else None

    while goal_key is None and queue:
        if max_expansions is not None and expanded >= max_expansions:
            return SearchResult("unknown", expanded_states=expanded, reason="expansion_limit")
        key = queue.popleft()
        state = states[key]
        expanded += 1
        for action in domain.actions(state):
            next_state = domain.transition(state, action)
            next_key = domain.state_key(next_state)
            if next_key in parent:
                continue
            states[next_key] = next_state
            parent[next_key] = (key, action)
            if domain.is_goal(next_state):
                goal_key = next_key
                break
            queue.append(next_key)

    if goal_key is None:
        if domain.complete_finite_model is True:
            return SearchResult("unsolvable", expanded_states=expanded, reason="exhaustive_state_space")
        return SearchResult("unknown", expanded_states=expanded, reason="model_not_complete")

    reverse_actions = []
    cursor = goal_key
    while parent[cursor] is not None:
        prior, action = parent[cursor]
        reverse_actions.append(action)
        cursor = prior
    actions = tuple(reversed(reverse_actions))
    try:
        replay_result = domain.engine_replay_accepts(actions)
    except Exception:
        replay_result = False
    if replay_result is None:
        # A candidate found in an incomplete model is still useful to replay,
        # but it is never a proof about the original engine.
        return SearchResult("unknown", actions=actions, expanded_states=expanded,
                            reason="engine_replay_pending", replay_accepted=None)
    if not isinstance(replay_result,bool):
        return SearchResult("unknown",actions=actions,expanded_states=expanded,reason="invalid_engine_replay_result",replay_accepted=None)
    accepted = replay_result
    if not accepted:
        return SearchResult("unknown", actions=actions, expanded_states=expanded,
                            reason="engine_replay_mismatch", replay_accepted=False)
    return SearchResult("solved", actions=actions, expanded_states=expanded,
                        reason="engine_replay_accepted", replay_accepted=True)
