from __future__ import annotations

import unittest

from arcagi_pipeline.search import exact_bfs


class TinyDomain:
    deterministic = True
    complete_finite_model = True

    def __init__(self, edges, goal, replay=True, complete=True):
        self.edges = edges
        self.goal = goal
        self.replay = replay
        self.complete_finite_model = complete

    def initial_state(self):
        return "start"

    def state_key(self, state):
        return state

    def actions(self, state):
        return list(self.edges.get(state, {}))

    def transition(self, state, action):
        return self.edges[state][action]

    def is_goal(self, state):
        return state == self.goal

    def engine_replay_accepts(self, actions):
        return self.replay


class SearchTests(unittest.TestCase):
    def test_solved_requires_engine_replay(self):
        result = exact_bfs(TinyDomain({"start": {"go": "goal"}}, "goal"))
        self.assertEqual(result.status, "solved")
        self.assertEqual(result.actions, ("go",))
        self.assertTrue(result.replay_accepted)

    def test_non_boolean_model_flags_cannot_prove_exhaustion(self):
        domain=TinyDomain({},"goal",complete="true")
        self.assertEqual(exact_bfs(domain).status,"unknown")
        domain.deterministic="true"
        self.assertEqual(exact_bfs(domain).status,"unknown")

    def test_truthy_non_boolean_replay_cannot_solve(self):
        for result in ("false",1,{"accepted":True}):
            with self.subTest(result=result):
                found=exact_bfs(TinyDomain({"start":{"go":"goal"}},"goal",replay=result))
                self.assertEqual(found.status,"unknown")
                self.assertEqual(found.reason,"invalid_engine_replay_result")

    def test_replay_mismatch_is_unknown(self):
        result = exact_bfs(TinyDomain({"start": {"go": "goal"}}, "goal", replay=False))
        self.assertEqual(result.status, "unknown")
        self.assertEqual(result.reason, "engine_replay_mismatch")

    def test_pending_replay_preserves_candidate_path_as_unknown(self):
        result = exact_bfs(TinyDomain({"start": {"go": "goal"}}, "goal", replay=None))
        self.assertEqual(result.status, "unknown")
        self.assertEqual(result.actions, ("go",))
        self.assertEqual(result.reason, "engine_replay_pending")
        self.assertIsNone(result.replay_accepted)

    def test_incomplete_model_candidate_is_available_for_replay_as_unknown(self):
        result = exact_bfs(TinyDomain({"start": {"go": "goal"}}, "goal", replay=None, complete=False))
        self.assertEqual(result.status, "unknown")
        self.assertEqual(result.actions, ("go",))
        self.assertEqual(result.reason, "engine_replay_pending")
        self.assertIsNone(result.replay_accepted)

    def test_incomplete_model_exhaustion_cannot_prove_unsolvable(self):
        result = exact_bfs(TinyDomain({"start": {"stay": "start"}}, "goal", replay=None, complete=False))
        self.assertEqual(result.status, "unknown")
        self.assertEqual(result.actions, ())
        self.assertEqual(result.reason, "model_not_complete")

    def test_complete_exhaustion_proves_unsolvable(self):
        result = exact_bfs(TinyDomain({"start": {"stay": "start"}}, "goal"))
        self.assertEqual(result.status, "unsolvable")

    def test_incomplete_model_and_limit_are_unknown(self):
        incomplete = TinyDomain({"start": {"stay": "start"}}, "goal", complete=False)
        self.assertEqual(exact_bfs(incomplete).status, "unknown")
        limited = TinyDomain({"start": {"go": "goal"}}, "goal")
        self.assertEqual(exact_bfs(limited, max_expansions=0).reason, "expansion_limit")

    def test_nondeterministic_domain_is_unknown(self):
        domain = TinyDomain({}, "goal")
        domain.deterministic = False
        self.assertEqual(exact_bfs(domain).reason, "nondeterministic_domain")


if __name__ == "__main__":
    unittest.main()
