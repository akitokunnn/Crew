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


class MemberNativeFixture(ShellFixture):
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
        current = self.manager.processes[key]
        process.finish(1)
        wait_until(current.reader_done.is_set, 'exit of ' + key)

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


class MemberNativeBindingTests(MemberNativeFixture, unittest.TestCase):
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
        general = [process for process in self.processes if sid in process.command]
        self.assertEqual(len(general), 1)
        general = general[0]
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


if __name__ == '__main__':
    unittest.main()
