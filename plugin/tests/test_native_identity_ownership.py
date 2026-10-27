"""Native histories have one owner across general conversations and goal executions."""
import tests
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import threading
import unittest

from crew import board, goals, runtimes, store
from test_goals import GoalFixture


class NativeIdentityOwnershipTests(GoalFixture):
    def execution(self, runtime):
        goal = self.goal()
        return goals.create_execution(self.conn, goal['id'], 'chief-id', runtime=runtime)

    def test_general_history_cannot_be_adopted_by_a_goal_even_for_the_same_person(self):
        for runtime in ('claude', 'codex'):
            with self.subTest(runtime=runtime):
                execution = self.execution(runtime)
                runtimes.set(self.conn, 'chief-id', runtime, 'general-' + runtime)
                with self.assertRaises(board.Conflict):
                    goals.bind_native(self.conn, execution['id'], runtime, 'general-' + runtime)

    def test_goal_history_cannot_be_adopted_by_a_general_conversation(self):
        for runtime in ('claude', 'codex'):
            with self.subTest(runtime=runtime):
                execution = self.execution(runtime)
                goals.bind_native(self.conn, execution['id'], runtime, 'goal-' + runtime)
                with self.assertRaises(board.Conflict):
                    runtimes.set(self.conn, 'member-id', runtime, 'goal-' + runtime)

    def test_implicit_legacy_claude_identity_is_reserved_from_goal_bindings(self):
        execution = self.execution('claude')
        with self.assertRaises(board.Conflict):
            goals.bind_native(self.conn, execution['id'], 'claude', 'member-id')

    def test_remembered_binding_stays_reserved_after_carrier_switch(self):
        runtimes.set(self.conn, 'member-id', 'codex', 'remembered-thread')
        runtimes.set(self.conn, 'member-id', 'claude', 'member-id')
        execution = self.execution('codex')
        with self.assertRaises(board.Conflict):
            goals.bind_native(self.conn, execution['id'], 'codex', 'remembered-thread')

    def test_runtime_namespaces_and_idempotent_owner_updates_remain_valid(self):
        runtimes.set(self.conn, 'member-id', 'claude', 'same-spelling')
        execution = self.execution('codex')
        for _ in range(2):
            goals.bind_native(self.conn, execution['id'], 'codex', 'same-spelling')
            runtimes.set(self.conn, 'member-id', 'claude', 'same-spelling')
        self.assertEqual(goals.resolve_native(self.conn, 'codex', 'same-spelling')['id'], execution['id'])

    def test_concurrent_general_and_goal_binding_have_only_one_owner(self):
        execution = self.execution('codex')
        ready = threading.Barrier(2)

        def bind(kind):
            with closing(store.connect(self.home)) as conn:
                ready.wait(5)
                try:
                    if kind == 'general':
                        runtimes.set(conn, 'member-id', 'codex', 'racing-native')
                    else:
                        goals.bind_native(conn, execution['id'], 'codex', 'racing-native')
                except board.Conflict:
                    return 'conflict'
                return 'bound'

        with ThreadPoolExecutor(2) as workers:
            outcomes = list(workers.map(bind, ('general', 'goal')))
        self.assertCountEqual(outcomes, ['bound', 'conflict'])


if __name__ == '__main__':
    unittest.main()
