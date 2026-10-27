"""A spawned member must not receive the same work again in a new goal thread."""
import tests
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import os
import threading
import unittest
from unittest import mock

from crew import board, goal_actions, goals, runtimes, store, web
import test_chat_http as http_helpers
from test_codex_runtime import FakeClient
from test_shell import NOW, PLUGIN, ShellFixture


class MemberLaunchDedupTests(ShellFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        board.update_cc_name(self.conn, 's3', 'cc-lead')
        self.clients = []

        def client(**options):
            value = FakeClient(**options)
            value.thread_id = 'dedup-native-' + str(len(self.clients))
            self.clients.append(value)
            return value

        patcher = mock.patch('crew.codex_runtime._ObservedClient', side_effect=client)
        patcher.start()
        self.addCleanup(patcher.stop)

    def goal(self):
        value = goals.create(self.conn, 'demo', 's3', 'One experiment',
                             'Run the experiment once in the shared directory.', ['Evidence'])
        return goals.transition(self.conn, value['id'], 'running')

    def spawn_then_assign(self, runtime):
        goal = self.goal()
        created = self.manager.start(self.conn, 'demo', 'worker',
            'Read the task brief and run the experiment for goal ' + goal['id'],
            group='lead', runtime=runtime, caller_session_id='s3')
        sid = created['session_id']
        self.assertTrue(self.manager.busy(sid))
        original = self.manager.processes[sid]
        with self.assertRaisesRegex(board.Conflict, '总对话'):
            goal_actions.dispatch(self.home, 's3', None, 'assign', {
                'goal_id': goal['id'], 'to': 'worker', 'task': 'Run all six experiments', 'start': True},
                self.manager.goal_controller)
        self.assertIs(self.manager.processes[sid], original)
        self.assertEqual(set(self.manager.processes), {sid})
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM goal_executions WHERE session_id=?',
                                          (sid,)).fetchone()[0], 0)
        self.assertFalse(any(item['session_id'] == sid for item in goals.get(self.conn, goal['id'])['assignments']))
        self.assertEqual(len(self.clients) if runtime == 'codex' else len(self.processes), 1)

    def test_codex_spawn_then_goal_assign_rejects_second_thread(self):
        self.spawn_then_assign('codex')

    def test_claude_spawn_then_goal_assign_rejects_second_process(self):
        self.spawn_then_assign('claude')

    def goal_bound_spawn(self, runtime):
        goal = self.goal()
        created = self.manager.start(self.conn, 'demo', 'goal-worker', 'Run once',
            runtime=runtime, caller_session_id='s3', goal_id=goal['id'])
        sid, key = created['session_id'], created['execution_id']
        self.assertNotIn(sid, self.manager.processes)
        self.assertEqual(set(self.manager.processes), {key})
        self.assertTrue(self.manager.busy(key))
        with self.assertRaisesRegex(board.Conflict, '目标执行'):
            self.manager.wake(self.conn, board.session(self.conn, sid), 'Run again', caller_session_id='s3')
        self.assertEqual(set(self.manager.processes), {key})

    def test_codex_bound_spawn_and_reverse_wake_only_start_one_thread(self):
        self.goal_bound_spawn('codex')

    def test_claude_bound_spawn_and_reverse_wake_only_start_one_process(self):
        self.goal_bound_spawn('claude')

    def test_goal_spawn_cli_sends_explicit_goal_without_execution_environment(self):
        goal = self.goal()
        with mock.patch.dict(os.environ, {'CREW_SESSION_ID': 's3'}), \
                mock.patch('crew.spawn.request_start', return_value={'ok': True}) as request:
            code, _, errors = self.run_cli(['spawn', '--name', 'cli-worker', '--task', 'Run once', '--goal', goal['id']])
        self.assertEqual(code, 0, errors)
        self.assertEqual(request.call_args.args[-1]['goal_id'], goal['id'])
        self.assertNotIn('caller_execution_id', request.call_args.args[-1])

    def test_goal_spawn_dry_run_does_not_claim_a_general_native_command(self):
        goal = self.goal()
        code, output, errors = self.run_cli(['spawn', '--name', 'dry-worker', '--task', 'Run once',
                                            '--goal', goal['id'], '--dry-run'])
        self.assertEqual(code, 0, errors)
        plan = json.loads(output)
        self.assertEqual(plan['goal_id'], goal['id'])
        self.assertEqual(plan['spec']['group'], 'lead')
        self.assertIsNone(plan['command'])
        self.assertEqual(self.manager.processes, {})

    def test_paused_goal_rejects_spawn_before_creating_member(self):
        goal = self.goal()
        goals.request_pause(self.conn, goal['id'])
        with self.assertRaises(board.Conflict):
            self.manager.start(self.conn, 'demo', 'paused-worker', 'Run once',
                runtime='codex', caller_session_id='s3', goal_id=goal['id'])
        self.assertFalse(self.clients)
        self.assertIsNone(self.conn.execute("SELECT 1 FROM sessions WHERE crew_name='paused-worker'").fetchone())

    def test_concurrent_codex_wake_and_first_goal_dispatch_launch_one_thread(self):
        goal = self.goal()
        goals.assign(self.conn, goal['id'], 's1', 'Run once')
        runtimes.set(self.conn, 's1', 'codex')
        ready = threading.Barrier(2)

        def dispatch(kind):
            with closing(store.connect(self.home)) as conn:
                ready.wait(5)
                try:
                    if kind == 'wake':
                        self.manager.wake(conn, board.session(conn, 's1'), 'Run once', caller_session_id='s3')
                    else:
                        self.manager.goal_controller.send(goal['id'], 's1', 'Run once', source='crew')
                except board.Conflict:
                    return 'conflict'
                return 'started'

        with ThreadPoolExecutor(2) as workers:
            outcomes = list(workers.map(dispatch, ('wake', 'goal')))
        self.assertCountEqual(outcomes, ['started', 'conflict'])
        self.assertEqual(len(self.clients), 1)
        self.assertEqual(len(self.manager.processes), 1)


class MemberLaunchHttpTests(ShellFixture, unittest.TestCase):
    request = http_helpers.ChatHttpTests.request
    close_server = http_helpers.ChatHttpTests.close_server

    def setUp(self):
        super().setUp()
        self.clients = []

        def client(**options):
            value = FakeClient(**options)
            value.thread_id = 'http-dedup-' + str(len(self.clients))
            self.clients.append(value)
            return value

        patcher = mock.patch('crew.codex_runtime._ObservedClient', side_effect=client)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.server = web.create_server(self.home, PLUGIN, port=0, cwd=self.project,
            agents_provider=lambda: [], claude_root=self.claude, codex_root=self.codex,
            runs_file=self.runs, now=lambda: store.parse_iso(NOW), process_factory=self.process_factory)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.assertTrue(self.server.service.agents_ready.wait(5))
        self.url = self.server.url

    def test_cc_to_codex_duplicate_dispatch_returns_http_409_and_keeps_original(self):
        goal = goals.create(self.conn, 'demo', 's3', 'Experiment', 'Run once', ['Evidence'])
        goals.transition(self.conn, goal['id'], 'running')
        code, first, _ = self.request('/api/spawn?project=demo', {'name': 'http-worker',
            'task': 'Run once', 'runtime': 'codex', 'caller_session_id': 's3'})
        self.assertEqual(code, 200, first)
        code, second, _ = self.request('/api/goals/agent?project=demo', {'actor_session_id': 's3',
            'action': 'assign', 'data': {'goal_id': goal['id'], 'to': 'http-worker', 'task': 'Run again', 'start': True}})
        self.assertEqual(code, 409, second)
        self.assertIn('总对话', second['error'])
        self.assertEqual(len(self.clients), 1)
        self.assertEqual(set(self.server.service.chat.processes), {first['session_id']})
        with mock.patch.dict(os.environ, {'CREW_SESSION_ID': 's3'}):
            code, _, errors = self.run_cli(['goal', 'assign', goal['id'], '--data', json.dumps({
                'to': 'http-worker', 'task': 'Run again', 'start': True})])
        self.assertEqual(code, 3, errors)
        self.assertIn('总对话', errors)
        self.assertEqual(len(self.clients), 1)

    def test_explicit_goal_spawn_over_http_has_only_one_execution_and_retries_do_not_restart_it(self):
        goal = goals.create(self.conn, 'demo', 's3', 'Experiment', 'Run once', ['Evidence'])
        goals.transition(self.conn, goal['id'], 'running')
        code, result, _ = self.request('/api/spawn?project=demo', {'name': 'bound-worker',
            'task': 'Run once', 'runtime': 'codex', 'caller_session_id': 's3', 'goal_id': goal['id']})
        self.assertEqual(code, 200, result)
        manager = self.server.service.chat
        key = result['execution_id']
        self.assertEqual(set(manager.processes), {key})
        self.clients[0].finish()
        code, first, _ = self.request('/api/chat/send?project=demo', {'session_id': result['session_id'],
            'goal_id': goal['id'], 'text': 'Review current results', 'message_id': 'human-followup'})
        self.assertEqual(code, 200, first)
        for _ in range(3):
            code, receipt, _ = self.request('/api/chat/send?project=demo', {'session_id': result['session_id'],
                'goal_id': goal['id'], 'text': 'Review current results', 'message_id': 'human-followup'})
            self.assertEqual(code, 200, receipt)
            self.assertTrue(receipt['duplicate'])
        self.assertEqual(len(self.clients), 1)


if __name__ == '__main__':
    unittest.main()
