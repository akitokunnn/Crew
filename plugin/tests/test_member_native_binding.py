"""A goal-bound member's general conversation only binds a native that really exists.

Reproduces the 0.3.10 field failure: a member started by ``crew spawn --goal``
worked only in its task execution, yet its general conversation was bound to
its Crew ID. A later leader message or wake resumed that never-created Claude
conversation and failed with "No conversation found" on every attempt.
"""
import tests  # Install the production-service connection guard.
import json
import time
import unittest
from unittest import mock

from crew import board, chat, goals, runtimes
from test_codex_runtime import FakeClient
from test_shell import ShellFixture


def wait_until(predicate, label, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.02)
    raise AssertionError('Timed out waiting for ' + label)


class MemberNativeBindingTests(ShellFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        board.update_cc_name(self.conn, 's3', 'cc-lead')
        self.clients = []

        def client(**options):
            value = FakeClient(**options)
            value.thread_id = 'member-thread-' + str(len(self.clients))
            self.clients.append(value)
            return value

        patcher = mock.patch('crew.codex_runtime._ObservedClient', side_effect=client)
        patcher.start()
        self.addCleanup(patcher.stop)

    def goal(self):
        value = goals.create(self.conn, 'demo', 's3', 'Arithmetic A', 'Compute 1+1 and report it.', ['Answer'])
        return goals.transition(self.conn, value['id'], 'running')

    def finish_claude_turn(self, key, process, native_id, text='1 + 1 = 2.'):
        process.emit({'type': 'system', 'subtype': 'init', 'session_id': native_id})
        process.emit({'type': 'assistant', 'session_id': native_id,
                      'message': {'id': 'answer-' + key, 'content': [{'type': 'text', 'text': text}]}})
        process.emit({'type': 'result', 'subtype': 'success', 'session_id': native_id, 'is_error': False})
        wait_until(lambda: self.manager.journal(key).status == 'idle', 'turn end of ' + key)

    def missing_conversation(self, key, process, native_id):
        message = 'No conversation found with session ID: ' + native_id
        process.emit({'type': 'result', 'subtype': 'error_during_execution', 'is_error': True,
                      'session_id': native_id, 'errors': [message]})
        process.finish(1)
        wait_until(lambda: self.manager.journal(key).status == 'offline', 'exit of ' + key)

    def claude_goal_member(self):
        goal = self.goal()
        created = self.manager.start(self.conn, 'demo', 'arith', 'Compute 1+1', runtime='claude',
                                     caller_session_id='s3', goal_id=goal['id'])
        sid, key = created['session_id'], created['execution_id']
        native = goals.get_execution(self.conn, key)['bindings'][0]['native_id']
        self.finish_claude_turn(key, self.processes[0], native)
        return goal, sid, key, native

    def general_binding(self, sid):
        return [tuple(row) for row in self.conn.execute(
            'SELECT runtime,native_id FROM session_runtime_bindings WHERE session_id=?', (sid,))]

    def test_claude_goal_spawn_leaves_general_conversation_unbound(self):
        _, sid, key, native = self.claude_goal_member()
        self.assertEqual(len(self.processes), 1)
        command = self.processes[0].command
        self.assertEqual(command[command.index('--session-id') + 1], native)
        self.assertNotEqual(native, sid)
        self.assertEqual(runtimes.get(self.conn, sid), {'runtime': 'claude', 'native_id': None})
        self.assertEqual(self.general_binding(sid), [('claude', None)])

    def test_leader_message_to_goal_spawned_claude_member_starts_general_conversation_once(self):
        _, sid, key, native = self.claude_goal_member()
        self.manager.send_message('s3', sid, 'Compute 1+1 and send me the result.', kind='task')
        self.manager.deliver_messages()
        self.assertEqual(len(self.processes), 2)
        general = self.processes[1]
        self.assertIn('--session-id', general.command)
        self.assertNotIn('--resume', general.command)
        self.assertEqual(general.command[general.command.index('--session-id') + 1], sid)
        delivered = self.conn.execute('SELECT delivered_at,accepted,error FROM peer_messages WHERE target_session_id=?',
                                      (sid,)).fetchone()
        self.assertIsNotNone(delivered['delivered_at'])
        self.assertIsNone(delivered['error'])
        self.finish_claude_turn(sid, general, sid)
        self.assertEqual(runtimes.get(self.conn, sid), {'runtime': 'claude', 'native_id': sid})
        self.assertEqual(goals.get_execution(self.conn, key)['bindings'][0]['native_id'], native)

    def test_leader_wake_of_goal_spawned_claude_member_says_general_conversation_is_new(self):
        _, sid, _, _ = self.claude_goal_member()
        self.manager.wake(self.conn, board.session(self.conn, sid), 'Compute 1+1', caller_session_id='s3')
        general = self.processes[1]
        self.assertEqual(general.command[general.command.index('--session-id') + 1], sid)
        sent = json.loads(general.stdin.getvalue().splitlines()[-1])['message']['content']
        self.assertIn('总对话第一次启动', sent)
        self.assertNotIn('原来的会话进程已经结束', sent)

    def test_codex_goal_spawn_general_conversation_starts_its_own_thread(self):
        goal = self.goal()
        created = self.manager.start(self.conn, 'demo', 'arith', 'Compute 1+1', runtime='codex',
                                     caller_session_id='s3', goal_id=goal['id'])
        sid, key = created['session_id'], created['execution_id']
        self.assertEqual(runtimes.get(self.conn, sid), {'runtime': 'codex', 'native_id': None})
        self.clients[0].finish()
        wait_until(lambda: self.manager.journal(key).status == 'idle', 'codex execution turn end')
        self.manager.wake(self.conn, board.session(self.conn, sid), 'Compute 1+1', caller_session_id='s3')
        self.assertEqual(len(self.clients), 2)
        self.assertEqual(self.clients[1].calls[0][0], 'thread/start')
        self.assertEqual(runtimes.get(self.conn, sid), {'runtime': 'codex', 'native_id': 'member-thread-1'})
        self.assertEqual(goals.get_execution(self.conn, key)['bindings'][0]['native_id'], 'member-thread-0')
        sent = self.clients[1].calls[-1][1]['input'][-1]['text']
        self.assertIn('总对话第一次启动', sent)

    def test_missing_general_native_stops_automatic_retries_and_names_the_cause(self):
        self.manager.wake(self.conn, board.session(self.conn, 's1'), 'Continue')
        first = self.processes[0]
        self.assertEqual(first.command[first.command.index('--resume') + 1], 's1')
        self.missing_conversation('s1', first, 's1')
        notice = [event for event in self.manager.journal('s1').events if event.get('native_missing') == 's1']
        self.assertEqual(len(notice), 1)
        self.assertIn('原会话不存在', notice[0]['text'])

        self.manager.send_message('s3', 's1', 'Status?', kind='task')
        self.manager.deliver_messages()
        self.manager.deliver_messages()
        self.assertEqual(len(self.processes), 1)
        waiting = self.conn.execute("SELECT delivered_at,error FROM peer_messages WHERE target_session_id='s1'").fetchone()
        self.assertIsNone(waiting['delivered_at'])
        self.assertIn('原会话不存在', waiting['error'])
        with self.assertRaisesRegex(board.BoardError, '原会话不存在'):
            self.manager.wake(self.conn, board.session(self.conn, 's1'), 'Continue', caller_session_id='s3')
        self.assertEqual(len(self.processes), 1)

        # The user's explicit retry resumes the same ID; real output clears the report.
        self.manager.send(board.session(self.conn, 's1'), 'Retry after restoring the record')
        retry = self.processes[1]
        self.assertEqual(retry.command[retry.command.index('--resume') + 1], 's1')
        self.finish_claude_turn('s1', retry, 's1')
        self.assertIsNone(self.manager.missing_native(self.conn, board.session(self.conn, 's1')))

    def test_missing_goal_execution_native_stops_automatic_retries(self):
        goal, sid, key, native = self.claude_goal_member()
        self.processes[0].finish(0)
        wait_until(lambda: self.manager.journal(key).status == 'offline', 'execution exit')
        self.manager.goal_controller.live_native = frozenset()
        self.manager.goal_controller.send(goal['id'], sid, 'Continue the task', source='crew')
        resumed = self.processes[1]
        self.assertEqual(resumed.command[resumed.command.index('--resume') + 1], native)
        self.missing_conversation(key, resumed, native)
        with self.assertRaisesRegex(board.BoardError, '原会话不存在'):
            self.manager.goal_controller.send(goal['id'], sid, 'Continue again', source='crew')
        self.assertEqual(len(self.processes), 2)
        self.assertEqual(goals.get_execution(self.conn, key)['bindings'][0]['native_id'], native)


if __name__ == '__main__':
    unittest.main()
