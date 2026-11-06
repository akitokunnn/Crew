"""A lost Claude conversation is replaced by a clearly labelled new one, never resumed by guesswork.

User decision 2026-11-06: when Claude reports the conversation to resume does
not exist, Crew opens a new native conversation, hands the task background in
and says so; it does not stop at "original conversation missing" nor pretend
the original was resumed. Goal executions reuse generation+1 with handoff
material. Also covers the one-time cleanup of Claude general bindings that
0.3.10 prefilled for goal-spawned members.
"""
import tests  # Install the production-service connection guard.
import json
import threading
import time
import unittest
from contextlib import closing
from unittest import mock

from crew import board, chat, goals, runtimes, store, web
import test_chat_http as chat_helpers
from test_member_native_binding import MemberNativeFixture, wait_until
from test_shell import NOW, PLUGIN


def sent(process):
    return [json.loads(line)['message']['content'] for line in process.stdin.getvalue().splitlines()
            if json.loads(line).get('type') == 'user']


class NativeReplacementTests(MemberNativeFixture, unittest.TestCase):
    def launched(self, count, label):
        """The count-th process, once its first input has been written."""
        return wait_until(lambda: len(self.processes) == count and sent(self.processes[-1]) and self.processes[-1], label)

    def events(self, key, kind=None):
        journal = self.manager.journal(key)
        with journal.condition:
            journal.refresh()
            return [event for event in journal.events if kind is None or event['type'] == kind]

    def test_lost_general_conversation_opens_new_session_and_hands_over_the_wake(self):
        self.manager.wake(self.conn, board.session(self.conn, 's1'), 'Compute 1+1 and report it')
        first = self.processes[0]
        self.assertEqual(first.command[first.command.index('--resume') + 1], 's1')
        self.missing_conversation('s1', first, 's1')

        replacement = self.launched(2, 'replacement launch')
        self.assertNotIn('--resume', replacement.command)
        self.assertEqual(replacement.command[replacement.command.index('--session-id') + 1], 's1')
        handed = sent(replacement)
        self.assertEqual(len(handed), 1)
        self.assertIn('原会话 s1 已丢失', handed[0])
        self.assertIn('这是新开的会话', handed[0])
        self.assertIn('Compute 1+1 and report it', handed[0])
        lost = [event for event in self.events('s1', 'error') if event.get('native_missing') == 's1']
        self.assertEqual(len(lost), 1)
        self.assertIn('Crew 会新开一个原生会话', lost[0]['text'])
        self.assertNotIn('由用户在此会话直接发送即可重试', lost[0]['text'])
        resend = self.events('s1', 'message')[-1]
        self.assertEqual(resend['source'], 'crew')
        self.assertIn('原会话丢失', resend['text'])

        # The notice names the new session only after Claude confirms it exists.
        self.assertEqual(self.events('s1', 'native_replaced'), [])
        self.finish_claude_turn('s1', replacement, 's1')
        notice = self.events('s1', 'native_replaced')
        self.assertEqual(len(notice), 1)
        self.assertEqual(notice[0]['lost_native'], 's1')
        self.assertIn('这是新开的原生会话', notice[0]['text'])
        self.assertIsNone(self.manager.missing_native(self.conn, board.session(self.conn, 's1')))

        # Later turns resume the new session normally; nothing is handed over again.
        replacement.finish(0)
        wait_until(lambda: self.manager.journal('s1').status == 'offline', 'replacement exit')
        self.manager.send(board.session(self.conn, 's1'), 'Next step')
        later = self.processes[2]
        self.assertEqual(later.command[later.command.index('--resume') + 1], 's1')
        self.assertNotIn('原会话', sent(later)[0])

    def test_peer_message_to_lost_general_conversation_is_resent_into_the_new_session(self):
        self.manager.send_message('s3', 's1', 'Status of the arithmetic task?', kind='task')
        self.manager.deliver_messages()
        first = self.processes[0]
        self.missing_conversation('s1', first, 's1')
        replacement = self.launched(2, 'replacement launch')
        self.assertIn('Status of the arithmetic task?', sent(replacement)[0])
        self.assertEqual(len(self.processes), 2)
        self.manager.deliver_messages()
        self.assertEqual(len(self.processes), 2)

    def test_replacement_that_fails_differently_does_not_start_another_session(self):
        self.manager.wake(self.conn, board.session(self.conn, 's1'), 'Continue')
        self.missing_conversation('s1', self.processes[0], 's1')
        replacement = self.launched(2, 'replacement launch')
        replacement.emit({'type': 'result', 'subtype': 'error_during_execution', 'is_error': True,
                          'session_id': 's1', 'errors': ['API Error: overloaded']})
        replacement.finish(1)
        wait_until(lambda: self.manager.journal('s1').status == 'offline', 'replacement exit')
        time.sleep(.2)
        self.assertEqual(len(self.processes), 2)
        # Still no confirmed new native: the next input opens it fresh again, with the handoff.
        self.manager.send(board.session(self.conn, 's1'), 'Try again')
        retry = self.processes[2]
        self.assertNotIn('--resume', retry.command)
        self.assertIn('原会话 s1 已丢失', sent(retry)[0])

    def test_lost_goal_execution_continues_in_next_generation_with_handoff(self):
        goal, sid, key, native = self.claude_goal_member()
        self.processes[0].finish(0)
        wait_until(lambda: self.manager.journal(key).status == 'offline', 'execution exit')
        self.manager.goal_controller.live_native = frozenset()
        self.manager.goal_controller.send(goal['id'], sid, 'Continue the task', source='crew')
        resumed = self.processes[1]
        self.assertEqual(resumed.command[resumed.command.index('--resume') + 1], native)
        self.missing_conversation(key, resumed, native)

        replacement = self.launched(3, 'new generation launch')
        old = goals.get_execution(self.conn, key)
        self.assertEqual(old['status'], 'failed')
        self.assertIn('原会话丢失', old['error'])
        self.assertEqual(old['bindings'][0]['native_id'], native)
        current = self.manager.goal_controller.current(self.conn, goal['id'], sid)
        self.assertNotEqual(current['id'], key)
        self.assertEqual(current['generation'], old['generation'] + 1)
        fresh = current['bindings'][0]['native_id']
        self.assertNotEqual(fresh, native)
        self.assertNotIn('--resume', replacement.command)
        self.assertEqual(replacement.command[replacement.command.index('--session-id') + 1], fresh)
        handed = sent(replacement)[0]
        self.assertIn('Compute 1+1 and report it.', handed)  # Original requirement.
        self.assertIn('Answer', handed)  # Acceptance criterion.
        self.assertIn('原生会话 %s 已丢失' % native, handed)
        self.assertIn('1 + 1 = 2.', handed)  # Last result of the lost execution.
        self.assertIn('Continue the task', handed)  # The input the lost session never answered.
        self.finish_claude_turn(current['id'], replacement, fresh)
        notice = self.events(current['id'], 'native_replaced')
        self.assertEqual(len(notice), 1)
        self.assertEqual(notice[0]['lost_native'], native)
        self.assertEqual(notice[0]['lost_execution'], key)
        self.assertEqual(len(self.processes), 3)


class UnstartedMemberBindingTests(MemberNativeFixture, unittest.TestCase):
    def prefilled_member(self, name='arith'):
        """A 0.3.10 goal-spawned member: general bound to its own ID, never started."""
        goal = self.goal()
        created = self.manager.start(self.conn, 'demo', name, 'Compute 1+1', runtime='claude',
                                     caller_session_id='s3', goal_id=goal['id'])
        sid, key = created['session_id'], created['execution_id']
        self.finish_claude_turn(key, self.processes[-1], goals.get_execution(self.conn, key)['bindings'][0]['native_id'])
        runtimes.set(self.conn, sid, 'claude', sid)
        journal = self.manager.journal(sid)
        journal.set_status('idle')
        journal.message('peer:old', 'Compute 1+1', source='peer', sender='lead')
        journal.emit('error', text='No conversation found with session ID: ' + sid)
        journal.emit('turn_result', status='failed', success=False)
        return sid, key

    def bindings(self, sid):
        return (runtimes.get(self.conn, sid), [tuple(row) for row in self.conn.execute(
            'SELECT runtime,native_id FROM session_runtime_bindings WHERE session_id=? ORDER BY runtime', (sid,))])

    def cleared(self, sid):
        return [json.loads(row['detail']) for row in self.conn.execute(
            "SELECT detail FROM events WHERE kind='session:native-binding-cleared' AND session_id=?", (sid,))]

    def test_prefilled_goal_member_binding_is_cleared_once_and_first_start_follows(self):
        sid, _ = self.prefilled_member()
        self.assertEqual(chat.clear_unstarted_member_bindings(self.home), [sid])
        self.assertEqual(self.bindings(sid), ({'runtime': 'claude', 'native_id': None}, [('claude', None)]))
        self.assertEqual(len(self.cleared(sid)), 1)
        self.assertEqual(chat.clear_unstarted_member_bindings(self.home), [])
        self.assertEqual(len(self.cleared(sid)), 1)

        self.manager.send_message('s3', sid, 'Compute 1+1 and send me the result.', kind='task')
        self.manager.deliver_messages()
        general = next(process for process in self.processes if sid in process.command)
        self.assertNotIn('--resume', general.command)
        self.assertEqual(general.command[general.command.index('--session-id') + 1], sid)
        self.assertNotIn('原会话', sent(general)[0])

    def test_service_start_runs_the_cleanup(self):
        sid, _ = self.prefilled_member()
        manager = chat.Manager(self.home, self.manager.plugin_root, lambda: [], self.process_factory)
        self.addCleanup(manager.close)
        self.assertEqual(manager.native_binding_cleanup, [sid])
        self.assertEqual(self.bindings(sid)[0]['native_id'], None)

    def test_concurrent_cleanups_clear_each_binding_exactly_once(self):
        sids = [self.prefilled_member('arith-' + letter)[0] for letter in 'abc']
        results, errors = [], []
        barrier = threading.Barrier(4)

        def run():
            try:
                barrier.wait()
                results.append(chat.clear_unstarted_member_bindings(self.home))
            except Exception as error:  # Surface any thread failure in the assertion below.
                errors.append(error)

        threads = [threading.Thread(target=run) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(20)
        self.assertEqual(errors, [])
        self.assertEqual(sorted(sid for result in results for sid in result), sorted(sids))
        for sid in sids:
            self.assertEqual(len(self.cleared(sid)), 1)
            self.assertIsNone(runtimes.get(self.conn, sid)['native_id'])

    def test_cleanup_leaves_every_other_binding_alone(self):
        # Real general output: the conversation exists.
        started, _ = self.prefilled_member('worked')
        self.manager.journal(started).emit('assistant', id='m1', content=[{'type': 'text', 'text': 'done'}])
        # No goal execution: an ordinary member conversation.
        board.join(self.conn, 'plain', 'demo', 'plain', self.project, self.project, 'main', now='2026-11-06T00:00:00Z')
        board.roster(self.conn, 'demo', 'move', 'lead', ['plain'])
        runtimes.set(self.conn, 'plain', 'claude', 'plain')
        # A leader with goal executions.
        goal = self.goal()
        self.manager.goal_controller.send(goal['id'], 's3', 'Begin', source='crew')
        runtimes.set(self.conn, 's3', 'claude', 's3')
        self.assertEqual(chat.clear_unstarted_member_bindings(self.home), [])
        for sid in (started, 'plain', 's3'):
            self.assertEqual(runtimes.get(self.conn, sid)['native_id'], sid)

    def test_cleanup_without_database_does_nothing(self):
        with closing(store.connect(self.home)):
            pass
        empty = self.root / 'empty-home'
        self.assertEqual(chat.clear_unstarted_member_bindings(empty), [])
        self.assertFalse(store.db_path(empty).exists())



class MemberPageStateTests(MemberNativeFixture, unittest.TestCase):
    """The member page knows which task executions to show when the general conversation is empty."""
    close_server = chat_helpers.ChatHttpTests.close_server
    request = chat_helpers.ChatHttpTests.request

    def setUp(self):
        super().setUp()
        poll = mock.patch.object(web.Service, '_poll_notices', return_value=None)
        poll.start()
        self.addCleanup(poll.stop)
        self.server = web.create_server(self.home, PLUGIN, port=0, cwd=self.project,
            agents_provider=mock.Mock(return_value=[]), claude_root=self.claude, codex_root=self.codex,
            runs_file=self.runs, now=lambda: store.parse_iso(NOW), process_factory=self.process_factory)
        self.manager = self.server.service.chat
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.assertTrue(self.server.service.agents_ready.wait(5))
        self.url = self.server.url

    def member_state(self, sid):
        code, state, _ = self.request('/api/state?project=demo')
        self.assertEqual(code, 200, state)
        return next(item for item in state['members'] if item['session_id'] == sid)

    def test_goal_member_lists_its_executions_until_the_general_conversation_speaks(self):
        first, sid, key, _ = self.claude_goal_member()
        second = goals.transition(self.conn, goals.create(self.conn, 'demo', 's3', 'Arithmetic B', 'Compute 2+3.',
                                                          ['Answer'])['id'], 'running')
        goals.assign(self.conn, second['id'], sid, 'Compute 2+3', actor='s3')
        other = self.manager.goal_controller.send(second['id'], sid, 'Compute 2+3', source='crew')['execution_id']
        self.finish_claude_turn(other, self.processes[-1], goals.get_execution(self.conn, other)['bindings'][0]['native_id'],
                                text='2 + 3 = 5.')
        state = self.member_state(sid)
        self.assertFalse(state['general_has_output'])
        self.assertEqual([(item['goal_id'], item['execution_id'], item['active']) for item in state['execution_records']],
                         [(second['id'], other, True), (first['id'], key, True)])
        self.assertEqual(state['execution_records'][0]['title'], 'Arithmetic B')
        code, history, _ = self.request('/api/chat/history?project=demo&session_id=%s&goal_id=%s' % (sid, first['id']))
        self.assertEqual(code, 200, history)
        self.assertEqual(history['execution_id'], key)
        self.assertIn('1 + 1 = 2.', json.dumps(history['events'], ensure_ascii=False))

        self.manager.wake(self.conn, board.session(self.conn, sid), 'Hello', caller_session_id='s3')
        self.finish_claude_turn(sid, self.processes[-1], sid, text='Hi')
        self.assertTrue(self.member_state(sid)['general_has_output'])


if __name__ == '__main__':
    unittest.main()
