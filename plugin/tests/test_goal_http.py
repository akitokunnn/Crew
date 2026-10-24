"""Goal boundaries exercised through HTTP and owned stream processes."""
import json
import threading
import unittest
from urllib.parse import urlencode

import tests
from unittest import mock
from crew import board, goals, hooks, store, web
import test_chat_http as chat_helpers
from test_shell import ShellFixture, PLUGIN, NOW


class GoalHttpTests(ShellFixture, unittest.TestCase):
    # Reuse transport/cleanup helpers, not another suite's test cases.
    close_server = chat_helpers.ChatHttpTests.close_server
    request = chat_helpers.ChatHttpTests.request
    finish_turn = chat_helpers.ChatHttpTests.finish_turn

    def setUp(self):
        super().setUp()
        self.server = web.create_server(self.home, PLUGIN, port=0, cwd=self.project,
            agents_provider=mock.Mock(return_value=[]), claude_root=self.claude, codex_root=self.codex,
            runs_file=self.runs, now=lambda: store.parse_iso(NOW), process_factory=self.process_factory)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.assertTrue(self.server.service.agents_ready.wait(5))
        self.url = self.server.url

    def goal(self):
        code, value, _ = self.request('/api/goals/create?project=demo', {
            'leader_session_id': 's3', 'title': 'Speaker test', 'request': 'Measure short replies; do not delete recordings.',
            'acceptance': ['Identify the second speaker']})
        self.assertEqual(code, 200, value)
        return value['goal']

    def send_goal(self, goal, text='Begin', **data):
        return self.request('/api/chat/send?project=demo', {'session_id': 's3', 'goal_id': goal['id'], 'text': text, **data})

    def test_reading_goal_and_empty_chat_never_starts_ai_and_serves_real_ui(self):
        goal = self.goal()
        for path in ('/api/goals', '/api/goal?' + urlencode({'id': goal['id']})):
            code, value, _ = self.request(path + ('&' if '?' in path else '?') + 'project=demo')
            self.assertEqual(code, 200, value)
        code, history, _ = self.request('/api/chat/history?' + urlencode({'project': 'demo', 'session_id': 's3', 'goal_id': goal['id']}))
        self.assertEqual(code, 200, history)
        self.assertIsNone(history['execution_id'])
        self.assertEqual(self.processes, [])
        for path in ('/goal-ui.js', '/goal-ui.css'):
            self.assertEqual(self.request(path, raw=True)[0], 200)

    def test_two_goals_have_distinct_native_history_and_stable_person(self):
        first, second = self.goal(), self.goal()
        receipts = [self.send_goal(goal, message_id='human-' + str(index))[1] for index, goal in enumerate((first, second))]
        self.assertTrue(all(r.get('accepted') for r in receipts), receipts)
        self.assertNotEqual(receipts[0]['execution_id'], receipts[1]['execution_id'])
        self.assertEqual(len(self.processes), 2)
        native = [process.options['env']['CREW_SESSION_ID'] for process in self.processes]
        self.assertEqual(native, ['s3', 's3'])
        self.assertNotEqual(self.processes[0].options['env']['CREW_EXECUTION_ID'], self.processes[1].options['env']['CREW_EXECUTION_ID'])
        code, state, _ = self.request('/api/state?project=demo')
        self.assertEqual(code, 200, state)
        self.assertEqual(len([s for s in state['leaders'] if s['session_id'] == 's3']), 1)
        self.assertTrue(next(s for s in state['leaders'] if s['session_id'] == 's3')['busy'])
        identity = next(s for s in state['leaders'] if s['session_id'] == 's3')
        self.assertEqual((identity['where'], identity['status']), ('bg', 'working'))

    def test_pause_is_durable_before_interrupt_and_late_native_hooks_cannot_reopen(self):
        goal = self.goal()
        code, receipt, _ = self.send_goal(goal, message_id='start')
        self.assertEqual(code, 200, receipt)
        execution = goals.get_execution(self.conn, receipt['execution_id'])
        process = self.processes[-1]
        code, result, _ = self.request('/api/goals/action?project=demo', {
            'goal_id': goal['id'], 'action': 'pause', 'version': goals.get(self.conn, goal['id'])['version']})
        self.assertEqual(code, 200, result)
        self.assertEqual(result['goal']['status'], 'pausing')
        self.assertTrue(result['goal']['manual_pause'])
        commands = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
        self.assertEqual(commands[-1]['request']['subtype'], 'interrupt')
        before = len(commands)
        self.server.service.chat.goal_controller.sync(self.conn)
        self.assertEqual(len(process.stdin.getvalue().splitlines()), before, 'interrupt only once while awaiting native acknowledgement')
        self.assertEqual(self.send_goal(goal, 'old task')[0], 409)
        binding = execution['bindings'][0]
        data = {'session_id': binding['native_id'], 'cwd': str(self.project), 'tool_name': 'Write', 'tool_input': {'file_path': str(self.project / 'x.txt')}}
        self.assertEqual(hooks.handle('guard', data, self.home, self.server.service.plugin_root, process.options['env'])[0], 2)
        self.assertEqual(hooks.handle('stop', data, self.home, self.server.service.plugin_root, process.options['env'])[0], 0)
        self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'pausing')
        self.finish_turn(receipt['execution_id'], process)
        self.server.service.chat.goal_controller.sync(self.conn)
        self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'paused')

    def test_duplicate_human_input_does_not_reinject_context_or_native_prompt(self):
        goal = self.goal()
        code, receipt, _ = self.send_goal(goal, message_id='once')
        self.assertEqual(code, 200, receipt)
        before = self.processes[-1].stdin.getvalue()
        code, again, _ = self.send_goal(goal, message_id='once')
        self.assertEqual(code, 200, again)
        self.assertTrue(again['duplicate'])
        self.assertEqual(self.processes[-1].stdin.getvalue(), before)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM goal_context_manifests WHERE goal_id=?', (goal['id'],)).fetchone()[0], 1)

    def test_exited_process_must_drain_its_reader_before_replacing_goal_runtime(self):
        goal = self.goal()
        _, receipt, _ = self.send_goal(goal)
        manager = self.server.service.chat
        previous = manager.processes[receipt['execution_id']]
        previous.process.returncode = 0  # Native exit observed; stdout still deliberately open.
        with mock.patch.object(previous.reader_done, 'wait', return_value=False):
            code, result, _ = self.send_goal(goal, 'Continue', message_id='after-exit')
        self.assertEqual(code, 400, result)
        self.assertIn('输出尚未排空', result['error'])
        self.assertEqual(len(self.processes), 1)
        self.assertIs(manager.processes[receipt['execution_id']], previous)
        previous.process.finish()
        self.assertTrue(previous.reader_done.wait(3))
        code, result, _ = self.send_goal(goal, 'Continue', message_id='after-exit')
        self.assertEqual(code, 200, result)
        self.assertEqual(len(self.processes), 2)
        self.assertTrue(manager.busy(receipt['execution_id']))

    def test_rotating_long_native_history_preserves_exact_human_supplement(self):
        goal = self.goal()
        _, first, _ = self.send_goal(goal, 'Do not push a PR. Keep every original recording.', message_id='first-rule')
        self.finish_turn(first['execution_id'], self.processes[-1])
        with mock.patch.object(self.server.service.chat, 'context', return_value={'used': 65001, 'max': 1000000}):
            code, second, _ = self.send_goal(goal, 'Continue the measurement.', message_id='after-rotation')
        self.assertEqual(code, 200, second)
        self.assertNotEqual(first['execution_id'], second['execution_id'])
        native_input = json.loads(self.processes[-1].stdin.getvalue().splitlines()[-1])['message']['content']
        self.assertIn('Do not push a PR. Keep every original recording.', native_input)
        self.assertIn(goal['request'], native_input)
        self.assertEqual(len(self.processes), 2)

    def test_inspection_reports_native_application_instead_of_prepared_manifest_snapshot(self):
        goal = self.goal()
        _, receipt, _ = self.send_goal(goal, message_id='native-ack')
        execution = goals.get_execution(self.conn, receipt['execution_id'])
        manifest_id = self.conn.execute('SELECT id FROM goal_context_manifests WHERE execution_id=?', (execution['id'],)).fetchone()[0]
        from crew import goal_context
        goal_context.mark_applied(self.conn, manifest_id, execution_id=execution['id'])
        code, result, _ = self.request('/api/goal?' + urlencode({'project': 'demo', 'id': goal['id']}))
        self.assertEqual(code, 200, result)
        self.assertTrue(result['context']['manifest']['applied'])
        self.assertIsNotNone(result['context']['manifest']['applied_at'])

    def test_ordinary_member_chat_does_not_register_a_goal(self):
        code, result, _ = self.request('/api/chat/send?project=demo', {'session_id': 's1', 'text': 'What does this measurement mean?', 'message_id': 'discussion'})
        self.assertEqual(code, 200, result)
        self.assertEqual(goals.list_goals(self.conn, 'demo'), [])

    def test_cancel_waits_for_actual_stop_then_durably_finishes(self):
        goal = self.goal()
        _, receipt, _ = self.send_goal(goal)
        code, result, _ = self.request('/api/goals/action?project=demo', {'goal_id': goal['id'], 'action': 'cancel',
            'version': goals.get(self.conn, goal['id'])['version']})
        self.assertEqual(code, 200, result)
        self.assertEqual(result['goal']['status'], 'pausing')
        self.finish_turn(receipt['execution_id'], self.processes[-1])
        self.server.service.chat.goal_controller.sync(self.conn)
        self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'cancelled')

    @staticmethod
    def report():
        return {'outcome': 'completed', 'summary': 'Measured short reply', 'delivered': ['Short reply measurement'],
            'unresolved': [], 'acceptance': [{'criterion_id': 'a1', 'status': 'passed',
            'evidence': [{'label': 'Measurement', 'reference': 'sample.json'}]}],
            'examples': [{'kind': 'actual', 'input': 'A pauses then B speaks', 'expected': 'B', 'actual': 'B', 'conclusion': 'Passed'}],
            'artifacts': ['sample.json'], 'tradeoffs': [], 'follow_up': 'User can replay sample.',
            'investment': {'usage': None, 'roles': []}}

    def test_finish_report_is_visible_but_cannot_claim_completion_before_native_result(self):
        goal = self.goal()
        _, receipt, _ = self.send_goal(goal)
        code, value, _ = self.request('/api/goals/agent?project=demo', {'actor_session_id': 's3',
            'execution_id': receipt['execution_id'], 'action': 'finish', 'data': {'report': self.report()}})
        self.assertEqual(code, 200, value)
        self.assertTrue(value['pending_finish'])
        self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'running')
        self.assertEqual(self.send_goal(goal, 'another job')[0], 409)
        self.finish_turn(receipt['execution_id'], self.processes[-1])
        self.server.service.chat.goal_controller.sync(self.conn)
        self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'completed')
        path = '/api/goals/report?' + urlencode({'project': 'demo', 'goal_id': goal['id'], 'report_id': value['report_id']})
        code, html, headers = self.request(path, raw=True)
        self.assertEqual(code, 200)
        self.assertIn(b'A pauses then B speaks', html)
        self.assertIn('sandbox', headers['Content-Security-Policy'])

    def test_resume_paused_pending_finish_completes_saved_report_without_another_model_turn(self):
        goal = self.goal()
        _, receipt, _ = self.send_goal(goal)
        self.request('/api/goals/agent?project=demo', {'actor_session_id': 's3', 'execution_id': receipt['execution_id'],
            'action': 'finish', 'data': {'report': self.report()}})
        self.request('/api/goals/action?project=demo', {'goal_id': goal['id'], 'action': 'pause',
            'version': goals.get(self.conn, goal['id'])['version']})
        self.finish_turn(receipt['execution_id'], self.processes[-1])
        self.server.service.chat.goal_controller.sync(self.conn)
        before = self.processes[-1].stdin.getvalue()
        code, result, _ = self.request('/api/goals/action?project=demo', {'goal_id': goal['id'], 'action': 'resume',
            'version': goals.get(self.conn, goal['id'])['version']})
        self.assertEqual(code, 200, result)
        self.assertEqual(result['goal']['status'], 'completed')
        self.assertEqual(self.processes[-1].stdin.getvalue(), before)
        self.assertEqual(len(self.processes), 1)


if __name__ == '__main__':
    unittest.main()
