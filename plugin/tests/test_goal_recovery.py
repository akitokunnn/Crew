"""Restart recovery respects real carrier ownership and persisted user intent."""
import unittest
from unittest import mock
import uuid

import tests
from crew import board, chat, goals
from test_shell import PLUGIN, ShellFixture


class GoalRecoveryTests(ShellFixture, unittest.TestCase):
    def running_goal(self, runtime='claude'):
        value = goals.create(self.conn, 'demo', 's3', 'Recovery', 'Keep original evidence.', ['Evidence remains'])
        goals.transition(self.conn, value['id'], 'running')
        execution = goals.create_execution(self.conn, value['id'], 's3', runtime=runtime)
        goals.set_execution_status(self.conn, execution['id'], 'running')
        native_id = str(uuid.uuid4())
        goals.bind_native(self.conn, execution['id'], runtime, native_id)
        self.manager.journal(execution['id']).set_status('working')
        return value, execution, native_id

    def test_manual_pause_never_resumes_and_live_external_goal_is_not_duplicated(self):
        value, execution, native = self.running_goal()
        self.manager.goal_controller.live_native = frozenset((native,))
        self.manager.auto_resume(self.conn, [{'sessionId': native}])
        self.assertEqual(self.processes, [])
        goals.request_pause(self.conn, value['id'])
        self.manager.goal_controller.live_native = frozenset()
        self.manager.auto_resume(self.conn, [])
        self.assertEqual(self.processes, [])
        self.assertTrue(goals.get(self.conn, value['id'])['manual_pause'])

    def test_second_service_restart_uses_new_recovery_id_without_repeating_within_epoch(self):
        _, execution, _ = self.running_goal()
        self.manager.goal_controller.live_native = frozenset()
        self.manager.auto_resume(self.conn, [])
        self.manager.auto_resume(self.conn, [])
        self.assertEqual(len(self.processes), 1)
        first = [e['id'] for e in self.manager.journal(execution['id']).events if e['type'] == 'message']
        self.manager.close()
        second = chat.Manager(self.home, PLUGIN, lambda: [], self.process_factory)
        self.addCleanup(second.close)
        second.goal_controller.live_native = frozenset()
        second.auto_resume(self.conn, [])
        messages = [e['id'] for e in second.journal(execution['id']).events if e['type'] == 'message']
        self.assertEqual(len(self.processes), 2)
        self.assertEqual(len(set(messages) - set(first)), 1)

    def test_general_coordinator_and_two_tasks_recover_without_retiring_each_other(self):
        _, first, _ = self.running_goal()
        _, second, _ = self.running_goal()
        self.manager.journal('s3').message('coordinator-before-restart', 'Coordinate the two tasks.')
        self.manager.journal('s3').set_status('working')
        self.manager.goal_controller.live_native = frozenset()
        self.manager.auto_resume(self.conn, [])
        self.assertTrue(all(self.manager.running(key) for key in ('s3', first['id'], second['id'])))
        self.assertEqual(len(self.processes), 3)
        self.manager.auto_resume(self.conn, [])
        self.assertEqual(len(self.processes), 3)

    def test_codex_recovers_while_claude_discovery_is_unavailable(self):
        _, cc, _ = self.running_goal()
        _, codex, _ = self.running_goal('codex')
        control = self.manager.goal_controller
        with mock.patch.object(control, 'send', return_value={'accepted': True}) as send:
            self.manager.auto_resume(self.conn, [], runtime='codex')
            self.assertEqual(send.call_args.args[0], codex['goal_id'])
            self.assertEqual(send.call_count, 1)
            self.assertIn(cc['id'], control.recovery)

    def test_cancel_exited_codex_after_restart_does_not_require_claude_discovery(self):
        value, _, _ = self.running_goal('codex')
        self.manager.goal_controller.live_native = None
        goals.request_cancel(self.conn, value['id'])
        self.manager.goal_controller.sync(self.conn)
        self.assertEqual(goals.get(self.conn, value['id'])['status'], 'cancelled')
        self.assertEqual(self.processes, [])

    def test_temporarily_owned_carrier_does_not_kill_discovery_or_flood_errors(self):
        self.running_goal('codex')
        control = self.manager.goal_controller
        with mock.patch.object(control, 'send', side_effect=board.Conflict('old native process still alive')):
            self.manager.auto_resume(self.conn, [], runtime='codex')
            self.manager.auto_resume(self.conn, [], runtime='codex')
        key = next(iter(control.recovery))
        self.assertEqual(len([e for e in self.manager.journal(key).events if e['type'] == 'error']), 1)


if __name__ == '__main__':
    unittest.main()
